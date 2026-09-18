#!/usr/bin/env python3
"""
query_articles.py — Interroga il DB SQLite degli articoli raccolti da
fetch_articles.py e restituisce i contenuti con filtri su processed, data,
fonte e stato. Pensato per essere usato sia da umani sia da agenti/LLM
(es. la procedura che genera la rassegna stampa): vedi SKILL.md.

USO BASE
--------
    python query_articles.py                          # articoli non ancora processati
    python query_articles.py --processed all          # tutti gli articoli
    python query_articles.py --source wired           # filtro per fonte (substring, case-insensitive)
    python query_articles.py --since 2026-09-01       # pubblicati dal 1° settembre
    python query_articles.py --until 2026-09-18       # pubblicati entro il 18 settembre
    python query_articles.py --format json --full-content

FILTRI
------
    --processed {0,1,all}   Flag rassegna stampa (default: 0 = da elaborare)
    --since DATE            Data minima (YYYY-MM-DD o ISO 8601), su published_at
                            (fallback fetched_at se manca)
    --until DATE            Data massima, stesso formato
    --source TEXT           Fonte: substring case-insensitive su source_name.
                            Ripetibile: --source wired --source openai
    --status {ok,empty,error,all}
                            Stato estrazione (default: ok)
    --limit N               Max righe (default: 100)
    --order {asc,desc}      Ordinamento per data (default: desc = piu' recenti)

OUTPUT
------
    --format {table,json,jsonl,csv,markdown}
                            table = elenco compatto (default), json/jsonl per
                            macchine, markdown per bozze di rassegna
    --full-content          Includi content_text integrale (default: estratto
                            di 300 caratteri)
    --count                 Stampa solo il numero di articoli trovati
    --db PATH               DB alternativo (default: data/newsletter.db)

AZIONI
------
    --mark-processed        Dopo la selezione, imposta processed=1 sulle righe
                            restituite (le "consuma" per la rassegna stampa).
                            Da usare DOPO averle effettivamente elaborate.

ESEMPI PER LA RASSEGNA STAMPA
-----------------------------
    # Quanti articoli nuovi ci sono?
    python query_articles.py --count

    # Prendi i nuovi articoli AI di Wired dell'ultima settimana, in JSON:
    python query_articles.py --source wired --since 2026-09-11 --format json --full-content

    # Genera una bozza markdown e marchia gli articoli come processati:
    python query_articles.py --format markdown --full-content > rassegna.md
    python query_articles.py --mark-processed --format count

CODICI DI USCITA
----------------
    0 = ok (anche se zero risultati), 1 = errore (DB mancante, argomenti errati)
"""

import argparse
import csv
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB = BASE_DIR / "data" / "newsletter.db"
EXCERPT_LEN = 300

COLUMNS = [
    "id", "source_name", "title", "url", "author",
    "published_at", "fetched_at", "summary",
    "content_text", "content_length", "status", "processed",
]


def normalize_date(value: str, end_of_day: bool = False) -> str:
    """Accetta YYYY-MM-DD o ISO 8601; restituisce stringa ISO confrontabile."""
    value = value.strip()
    if len(value) == 10:  # YYYY-MM-DD
        return value + ("T23:59:59" if end_of_day else "T00:00:00")
    # validazione blanda: deve essere parsabile come ISO
    datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value


def build_query(args) -> tuple[str, list]:
    where, params = [], []

    if args.processed != "all":
        where.append("processed = ?")
        params.append(int(args.processed))
    if args.status != "all":
        where.append("status = ?")
        params.append(args.status)
    if args.since:
        where.append("COALESCE(published_at, fetched_at) >= ?")
        params.append(normalize_date(args.since))
    if args.until:
        where.append("COALESCE(published_at, fetched_at) <= ?")
        params.append(normalize_date(args.until, end_of_day=True))
    if args.source:
        clause = " OR ".join("LOWER(source_name) LIKE ?" for _ in args.source)
        where.append(f"({clause})")
        params.extend(f"%{s.lower()}%" for s in args.source)

    sql = f"SELECT {', '.join(COLUMNS)} FROM articles"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += (" ORDER BY COALESCE(published_at, fetched_at) "
            + ("ASC" if args.order == "asc" else "DESC"))
    sql += " LIMIT ?"
    params.append(args.limit)
    return sql, params


def excerpt(text: str | None) -> str | None:
    if not text:
        return None
    text = text.strip()
    return text if len(text) <= EXCERPT_LEN else text[:EXCERPT_LEN].rstrip() + "…"


def format_rows(rows: list[dict], args) -> str:
    if not args.full_content:
        for r in rows:
            r["content_text"] = excerpt(r["content_text"])

    if args.format == "json":
        return json.dumps(rows, ensure_ascii=False, indent=2)

    if args.format == "jsonl":
        return "\n".join(json.dumps(r, ensure_ascii=False) for r in rows)

    if args.format == "csv":
        import io
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
        return buf.getvalue().rstrip("\n")

    if args.format == "markdown":
        parts = []
        for r in rows:
            parts.append(f"## {r['title'] or '(senza titolo)'}\n")
            parts.append(
                f"- **Fonte:** {r['source_name']}\n"
                f"- **Data:** {r['published_at'] or r['fetched_at']}\n"
                f"- **Autore:** {r['author'] or '—'}\n"
                f"- **URL:** {r['url']}\n"
            )
            if r["content_text"]:
                parts.append(f"\n{r['content_text']}\n")
            parts.append("\n---\n")
        return "\n".join(parts)

    # table (default)
    lines = []
    for r in rows:
        date = (r["published_at"] or r["fetched_at"] or "")[:10]
        title = (r["title"] or "(senza titolo)")[:80]
        lines.append(
            f"[{r['id']:>5}] {date} | {r['source_name'][:28]:<28} | "
            f"{(r['content_length'] or 0):>6} car. | {title}"
        )
        lines.append(f"         {r['url']}")
    return "\n".join(lines)


def parse_args():
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--processed", choices=["0", "1", "all"], default="0",
                   help="Filtro sul flag processed (default: 0 = da elaborare)")
    p.add_argument("--since", help="Data minima YYYY-MM-DD o ISO 8601")
    p.add_argument("--until", help="Data massima YYYY-MM-DD o ISO 8601")
    p.add_argument("--source", action="append",
                   help="Fonte (substring, case-insensitive). Ripetibile")
    p.add_argument("--status", choices=["ok", "empty", "error", "all"], default="ok",
                   help="Stato estrazione (default: ok)")
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--order", choices=["asc", "desc"], default="desc")
    p.add_argument("--format",
                   choices=["table", "json", "jsonl", "csv", "markdown", "count"],
                   default="table")
    p.add_argument("--full-content", action="store_true",
                   help="Includi content_text integrale invece dell'estratto")
    p.add_argument("--count", action="store_true",
                   help="Stampa solo il numero di articoli trovati")
    p.add_argument("--mark-processed", action="store_true",
                   help="Imposta processed=1 sulle righe restituite")
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    return p.parse_args()


def main() -> int:
    args = parse_args()

    if not args.db.exists():
        print(f"Errore: DB non trovato: {args.db}", file=sys.stderr)
        print("Esegui prima fetch_articles.py.", file=sys.stderr)
        return 1

    try:
        sql, params = build_query(args)
    except ValueError as e:
        print(f"Errore nei parametri data: {e}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(sql, params)]

    if args.count or args.format == "count":
        print(len(rows))
    elif not rows:
        print("Nessun articolo trovato con questi filtri.", file=sys.stderr)
    else:
        print(format_rows(rows, args))

    if args.mark_processed and rows:
        ids = [r["id"] for r in rows]
        conn.execute(
            f"UPDATE articles SET processed = 1 WHERE id IN ({','.join('?' * len(ids))})",
            ids,
        )
        conn.commit()
        print(f"[mark-processed] {len(ids)} articoli marcati come processati.",
              file=sys.stderr)

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
