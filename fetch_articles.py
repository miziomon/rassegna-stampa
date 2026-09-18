"""
Newsletter fetcher: scarica i feed RSS/Atom delle fonti in sources.json,
scarica i singoli articoli e li archivia in un DB SQLite per una successiva
rassegna stampa.

Uso:
    python fetch_articles.py                # run normale
    python fetch_articles.py --dry-run      # solo feed, niente download/insert
    python fetch_articles.py --limit 5      # max 5 nuovi articoli per fonte
    python fetch_articles.py --retry-errors # ritenta gli articoli in errore
    python fetch_articles.py -v             # log DEBUG anche su console

Dipendenze: vedi requirements.txt.
Le fonti con "stealth": true in sources.json vengono scaricate con Scrapling
(StealthyFetcher, browser headless Camoufox) invece che con requests: utile
per siti con protezione anti-bot. Le fonti con "use_feed_content": true non
scaricano la pagina: il testo e' preso dal contenuto embedded nel feed.
Le altre usano HTTP semplice (piu' veloce).

Output:
    data/newsletter.db  -> tabelle `articles` e `feeds`
    logs/fetch.log      -> log rotante dettagliato
"""

import argparse
import json
import logging
import re
import sqlite3
import sys
import threading
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import feedparser
import requests
import trafilatura
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_SOURCES = BASE_DIR / "sources.json"
DEFAULT_DB = BASE_DIR / "data" / "newsletter.db"
LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "fetch.log"

USER_AGENT = "newsletter-fetcher/1.0 (+https://maurizio.mavida.com)"
REQUEST_TIMEOUT = (10, 30)  # (connect, read) secondi — per-socket
HARD_TIMEOUT = 90           # secondi wall-clock max per singola richiesta HTTP
DEFAULT_LIMIT = 20          # max nuovi articoli scaricati per fonte per run

# Parametri di tracking rimossi nella normalizzazione URL (per la dedup)
TRACKING_PARAMS = re.compile(r"^(utm_|fbclid|gclid|mc_cid|mc_eid|ref$|ref_)", re.IGNORECASE)
TAG_RE = re.compile(r"<[^>]+>")

log = logging.getLogger("fetcher")


# --------------------------------------------------------------------------
# Setup
# --------------------------------------------------------------------------

def setup_logging(verbose: bool) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log.setLevel(logging.DEBUG)

    file_handler = RotatingFileHandler(
        LOG_FILE, maxBytes=2_000_000, backupCount=5, encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    )

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.DEBUG if verbose else logging.INFO)
    console_handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))

    log.addHandler(file_handler)
    log.addHandler(console_handler)


def init_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS articles (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            url             TEXT NOT NULL,
            url_normalized  TEXT NOT NULL UNIQUE,
            guid            TEXT,
            source_name     TEXT NOT NULL,
            feed_url        TEXT NOT NULL,
            title           TEXT,
            author          TEXT,
            published_at    TEXT,
            fetched_at      TEXT NOT NULL,
            summary         TEXT,
            content_text    TEXT,
            content_length  INTEGER,
            status          TEXT NOT NULL DEFAULT 'ok',
            error           TEXT,
            processed       INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_articles_source    ON articles(source_name);
        CREATE INDEX IF NOT EXISTS idx_articles_published ON articles(published_at);
        CREATE INDEX IF NOT EXISTS idx_articles_guid      ON articles(guid);

        CREATE TABLE IF NOT EXISTS feeds (
            feed_url        TEXT PRIMARY KEY,
            source_name     TEXT,
            etag            TEXT,
            last_modified   TEXT,
            last_checked_at TEXT,
            last_status     TEXT,
            error           TEXT
        );
        """
    )
    return conn


def load_sources(path: Path) -> list[dict]:
    sources = json.loads(path.read_text(encoding="utf-8"))
    enabled = [s for s in sources if s.get("enabled", True)]
    log.info(
        "Fonti caricate da %s: %d totali, %d abilitate",
        path.name, len(sources), len(enabled),
    )
    return enabled


def make_http_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/rss+xml,application/atom+xml,application/xml;q=0.9,*/*;q=0.8",
    })
    retries = Retry(
        total=2,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    session.mount("http://", HTTPAdapter(max_retries=retries))
    session.mount("https://", HTTPAdapter(max_retries=retries))
    return session


class HardTimeoutError(TimeoutError):
    pass


def hard_get(http: requests.Session, url: str, **kwargs) -> requests.Response:
    """
    GET con timeout wall-clock. Il timeout di requests e' per-socket: un
    server "tarpit" (es. Cloudflare che trickla pochi byte alla volta per i
    bot) lo aggira e bloccherebbe il run per sempre. Qui la richiesta gira
    in un thread daemon: se supera HARD_TIMEOUT si solleva eccezione e si va
    avanti; il thread orfano muore con il processo.
    """
    kwargs.setdefault("timeout", REQUEST_TIMEOUT)
    result: dict = {}

    def target():
        try:
            result["response"] = http.get(url, **kwargs)
        except Exception as e:
            result["error"] = e

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(HARD_TIMEOUT)
    if t.is_alive():
        raise HardTimeoutError(f"Timeout wall-clock ({HARD_TIMEOUT}s) su {url}")
    if "error" in result:
        raise result["error"]
    return result["response"]


# --------------------------------------------------------------------------
# Utility
# --------------------------------------------------------------------------

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_url(url: str) -> str:
    """Rimuove parametri di tracking e fragment: base della dedup."""
    parts = urlparse(url.strip())
    query = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not TRACKING_PARAMS.match(k)
    ]
    return urlunparse((
        parts.scheme.lower(),
        parts.netloc.lower(),
        parts.path,
        parts.params,
        urlencode(query),
        "",  # niente fragment
    ))


def strip_html(text: str | None) -> str | None:
    if not text:
        return None
    return TAG_RE.sub("", text).strip() or None


def entry_date_iso(entry) -> str | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed:
        return datetime(*parsed[:6], tzinfo=timezone.utc).isoformat(timespec="seconds")
    return None


def article_already_seen(conn, url_normalized: str, guid: str | None) -> bool:
    row = conn.execute(
        "SELECT status FROM articles WHERE url_normalized = ?", (url_normalized,)
    ).fetchone()
    if row:
        return True
    if guid:
        row = conn.execute(
            "SELECT status FROM articles WHERE guid = ?", (guid,)
        ).fetchone()
        if row:
            return True
    return False


# --------------------------------------------------------------------------
# Feed
# --------------------------------------------------------------------------

def update_feed_state(conn, source: dict, status: str,
                      etag=None, last_modified=None, error=None) -> None:
    conn.execute(
        """
        INSERT INTO feeds (feed_url, source_name, etag, last_modified,
                           last_checked_at, last_status, error)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(feed_url) DO UPDATE SET
            source_name     = excluded.source_name,
            etag            = COALESCE(excluded.etag, feeds.etag),
            last_modified   = COALESCE(excluded.last_modified, feeds.last_modified),
            last_checked_at = excluded.last_checked_at,
            last_status     = excluded.last_status,
            error           = excluded.error
        """,
        (source["feed_url"], source["name"], etag, last_modified,
         utc_now_iso(), status, error),
    )
    conn.commit()


def fetch_feed(http: requests.Session, conn, source: dict, dry_run: bool = False):
    """
    Scarica e parsa il feed di una fonte con conditional GET (ETag /
    Last-Modified salvati nella tabella feeds). Restituisce la lista delle
    entry, oppure None se il feed non e' cambiato / non raggiungibile.

    In dry-run non invia gli header condizionali (serve vedere le entry) e
    non salva ETag/Last-Modified: altrimenti il run successivo riceverebbe
    un 304 e salterebbe articoli mai scaricati.
    """
    feed_url = source["feed_url"]

    headers = {}
    if not dry_run:
        state = conn.execute(
            "SELECT etag, last_modified FROM feeds WHERE feed_url = ?", (feed_url,)
        ).fetchone()
        if state and state["etag"]:
            headers["If-None-Match"] = state["etag"]
        if state and state["last_modified"]:
            headers["If-Modified-Since"] = state["last_modified"]

    try:
        response = hard_get(http, feed_url, headers=headers)
    except (requests.RequestException, HardTimeoutError) as e:
        log.error("[%s] Feed non raggiungibile: %s", source["name"], e)
        update_feed_state(conn, source, "connection_error", error=str(e))
        return None

    if response.status_code == 304:
        # Sanity check: un 304 e' valido solo se il feed e' stato consumato
        # almeno una volta. Se non c'e' nessun articolo per questo feed
        # (run precedente interrotto, dry-run, ecc.) si rifa' una GET piena.
        n_articles = conn.execute(
            "SELECT COUNT(*) FROM articles WHERE feed_url = ?", (feed_url,)
        ).fetchone()[0]
        if n_articles == 0:
            log.info("[%s] 304 ma nessun articolo nel DB: riprovo senza "
                     "conditional GET.", source["name"])
            try:
                response = hard_get(http, feed_url)
            except (requests.RequestException, HardTimeoutError) as e:
                log.error("[%s] Feed non raggiungibile al secondo tentativo: %s",
                          source["name"], e)
                update_feed_state(conn, source, "connection_error", error=str(e))
                return None
        else:
            log.info("[%s] Feed non modificato (304), salto.", source["name"])
            update_feed_state(conn, source, "not_modified")
            return None

    if response.status_code != 200:
        log.error("[%s] Feed HTTP %s da %s", source["name"],
                  response.status_code, feed_url)
        update_feed_state(conn, source, f"http_{response.status_code}")
        return None

    parsed = feedparser.parse(response.content)
    if parsed.bozo and not parsed.entries:
        log.error("[%s] Feed non parsabile: %s", source["name"],
                  parsed.get("bozo_exception"))
        update_feed_state(conn, source, "parse_error",
                          error=str(parsed.get("bozo_exception")))
        return None

    log.info("[%s] Feed OK: %d entry.", source["name"], len(parsed.entries))
    if dry_run:
        update_feed_state(conn, source, "dry_run")
    else:
        update_feed_state(
            conn, source, "ok",
            etag=response.headers.get("ETag"),
            last_modified=response.headers.get("Last-Modified"),
        )
    return parsed.entries


# --------------------------------------------------------------------------
# Articoli
# --------------------------------------------------------------------------

def fetch_article_html(http: requests.Session, source: dict, url: str) -> str:
    if source.get("stealth"):
        from scrapling.fetchers import StealthyFetcher
        log.debug("[%s] Download stealth: %s", source["name"], url)
        page = StealthyFetcher.fetch(
            url, headless=True, network_idle=True,
            solve_cloudflare=True, timeout=90000,
        )
        return page.html_content if hasattr(page, "html_content") else str(page)

    response = hard_get(http, url)
    response.raise_for_status()
    return response.text


def entry_feed_content(entry) -> str | None:
    """Contenuto HTML completo embedded nel feed (content:encoded), se presente."""
    if entry.get("content"):
        return entry.content[0].get("value") or None
    return None


def extract_article(html: str, url: str) -> dict:
    """Estrazione testo/metadati con trafilatura (fallback: campi del feed)."""
    data = trafilatura.bare_extraction(
        html, url=url, include_comments=False, include_tables=False,
        favor_recall=True,
    )
    if data is None:
        return {"title": None, "author": None, "date": None, "text": None}
    # trafilatura 2.x restituisce un oggetto Document (attributi, non dict)
    text = (getattr(data, "text", None) or "").strip()
    return {
        "title": getattr(data, "title", None),
        "author": getattr(data, "author", None),
        "date": getattr(data, "date", None),
        "text": text or None,
    }


def save_article(conn, source: dict, entry, html: str | None, error: str | None) -> str:
    """Inserisce l'articolo nel DB. Restituisce lo stato: ok / empty / error."""
    url = entry.get("link", "").strip()
    extracted = extract_article(html, url) if html else {}

    content_text = extracted.get("text")
    status = "ok" if content_text else ("error" if error else "empty")

    conn.execute(
        """
        INSERT INTO articles (url, url_normalized, guid, source_name, feed_url,
                              title, author, published_at, fetched_at, summary,
                              content_text, content_length, status, error)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            url,
            normalize_url(url),
            entry.get("id") or None,
            source["name"],
            source["feed_url"],
            extracted.get("title") or strip_html(entry.get("title")),
            extracted.get("author"),
            entry_date_iso(entry) or extracted.get("date"),
            utc_now_iso(),
            strip_html(entry.get("summary")),
            content_text,
            len(content_text) if content_text else None,
            status,
            error,
        ),
    )
    conn.commit()
    return status


def process_source(http: requests.Session, conn, source: dict,
                   limit: int, dry_run: bool, retry_errors: bool) -> dict:
    stats = {"new": 0, "skipped": 0, "filtered": 0, "ok": 0, "empty": 0, "errors": 0}
    entries = fetch_feed(http, conn, source, dry_run=dry_run)
    if not entries:
        return stats

    include_categories = source.get("include_categories")
    if include_categories:
        include_categories = {c.lower() for c in include_categories}

    limit_hit = False
    for entry in entries:
        url = (entry.get("link") or "").strip()
        if not url:
            continue

        # Filtro per categoria/tag del feed (es. Wired: solo AI)
        if include_categories:
            tags = {(t.get("term") or "").lower() for t in entry.get("tags", [])}
            if not tags & include_categories:
                stats["filtered"] += 1
                continue
        url_normalized = normalize_url(url)
        guid = entry.get("id") or None

        if article_already_seen(conn, url_normalized, guid):
            stats["skipped"] += 1
            continue

        stats["new"] += 1
        if dry_run:
            log.info("[%s] (dry-run) nuovo: %s", source["name"], url)
            continue
        if stats["ok"] + stats["empty"] + stats["errors"] >= limit:
            log.info("[%s] Limite %d nuovi articoli raggiunto, gli altri "
                     "saranno presi al prossimo run.", source["name"], limit)
            limit_hit = True
            break

        feed_content = entry_feed_content(entry)

        if source.get("use_feed_content"):
            # La fonte pubblica il testo completo nel feed: niente download
            html, error = feed_content, None
            if not html:
                error = "feed senza contenuto embedded"
                log.warning("[%s] use_feed_content ma entry senza content: %s",
                            source["name"], url)
        else:
            try:
                html = fetch_article_html(http, source, url)
                error = None
            except Exception as e:
                error = f"{type(e).__name__}: {e}"
                log.error("[%s] Download fallito %s -> %s", source["name"], url, e)
                # Fallback: se il feed embedda il testo completo, usa quello
                html = feed_content
                if html:
                    error = None
                    log.info("[%s] Uso il contenuto embedded del feed per %s",
                             source["name"], url)

        try:
            status = save_article(conn, source, entry, html, error)
        except sqlite3.IntegrityError:
            # Corsa o URL duplicato dentro lo stesso feed: gia' registrato
            stats["skipped"] += 1
            continue

        stats[{"ok": "ok", "empty": "empty", "error": "errors"}[status]] += 1
        if status == "ok":
            log.info("[%s] Scaricato: %s", source["name"],
                     entry.get("title", url)[:100])
        elif status == "empty":
            log.warning("[%s] Nessun testo estratto da %s", source["name"], url)

        time.sleep(1)  # gentilezza verso i server

    if limit_hit and not dry_run:
        # Sono rimaste entry non scaricate: invalida il conditional GET,
        # altrimenti un 304 al prossimo run le salterebbe per sempre.
        conn.execute(
            "UPDATE feeds SET etag = NULL, last_modified = NULL WHERE feed_url = ?",
            (source["feed_url"],),
        )
        conn.commit()

    # retry degli errori dei run precedenti (stessa fonte)
    if retry_errors and not dry_run:
        rows = conn.execute(
            "SELECT id, url FROM articles WHERE source_name = ? AND status = 'error'",
            (source["name"],),
        ).fetchall()
        for row in rows:
            try:
                html = fetch_article_html(http, source, row["url"])
                extracted = extract_article(html, row["url"])
                if extracted.get("text"):
                    conn.execute(
                        """UPDATE articles SET content_text = ?, content_length = ?,
                           status = 'ok', error = NULL,
                           title = COALESCE(?, title),
                           author = COALESCE(?, author),
                           fetched_at = ?
                           WHERE id = ?""",
                        (extracted["text"], len(extracted["text"]),
                         extracted.get("title"), extracted.get("author"),
                         utc_now_iso(), row["id"]),
                    )
                    conn.commit()
                    stats["ok"] += 1
                    log.info("[%s] Retry riuscito: %s", source["name"], row["url"])
            except Exception as e:
                log.error("[%s] Retry fallito %s -> %s", source["name"],
                          row["url"], e)
            time.sleep(1)

    return stats


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, default=DEFAULT_SOURCES,
                        help=f"File JSON delle fonti (default: {DEFAULT_SOURCES.name})")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB,
                        help="Percorso del DB SQLite")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                        help=f"Max nuovi articoli scaricati per fonte per run "
                             f"(default: {DEFAULT_LIMIT})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Legge solo i feed: niente download ne' insert nel DB")
    parser.add_argument("--retry-errors", action="store_true",
                        help="Ritenta il download degli articoli in stato 'error'")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Log DEBUG anche su console")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    setup_logging(args.verbose)

    log.info("=" * 60)
    log.info("Avvio run (dry_run=%s, limit=%d)", args.dry_run, args.limit)

    if not args.sources.exists():
        log.error("File fonti non trovato: %s", args.sources)
        return 1

    conn = init_db(args.db)
    http = make_http_session()
    sources = load_sources(args.sources)

    totals = {"new": 0, "skipped": 0, "filtered": 0, "ok": 0, "empty": 0, "errors": 0}
    for source in sources:
        log.info("--- Fonte: %s (%s)", source["name"], source["feed_url"])
        try:
            stats = process_source(http, conn, source, args.limit,
                                   args.dry_run, args.retry_errors)
            for k in totals:
                totals[k] += stats[k]
            log.info(
                "[%s] Fine fonte: nuovi=%d (ok=%d, vuoti=%d, errori=%d), "
                "gia' noti=%d, filtrati=%d",
                source["name"], stats["new"], stats["ok"], stats["empty"],
                stats["errors"], stats["skipped"], stats["filtered"],
            )
        except Exception:
            log.exception("[%s] Errore inatteso durante l'elaborazione della fonte",
                          source["name"])

    conn.close()
    log.info(
        "Run completato: nuovi=%d (ok=%d, vuoti=%d, errori=%d), "
        "gia' noti=%d, filtrati=%d",
        totals["new"], totals["ok"], totals["empty"], totals["errors"],
        totals["skipped"], totals["filtered"],
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
