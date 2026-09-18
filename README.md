# newsletter-fetcher

Raccoglitore automatico di articoli da feed RSS/Atom: scarica i feed delle
fonti in `sources.json`, scarica i singoli articoli e li archivia in un DB
SQLite, pronti per una seconda procedura che genera la rassegna stampa via
email.

## Installazione

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
```

`scrapling` serve solo per le fonti con `"stealth": true`; se nessuna fonte
lo usa non serve nemmeno `.venv/Scripts/scrapling install` (download del
browser Camoufox).

## Uso

```bash
.venv/Scripts/python fetch_articles.py                 # run normale
.venv/Scripts/python fetch_articles.py --dry-run       # solo feed, niente download/insert
.venv/Scripts/python fetch_articles.py --limit 5       # max 5 nuovi articoli per fonte
.venv/Scripts/python fetch_articles.py --retry-errors  # ritenta gli articoli in errore
.venv/Scripts/python fetch_articles.py -v              # log DEBUG anche su console
```

Per il polling periodico, schedula il comando con Utilità di pianificazione
di Windows (es. ogni ora):

```
C:/Users/maurizio.MAVIDA/Claude/experiments/newsletter/.venv/Scripts/python.exe
  C:/Users/maurizio.MAVIDA/Claude/experiments/newsletter/fetch_articles.py
```

## Fonti: `sources.json`

Lista di oggetti:

```json
{
  "name": "OpenAI Blog",
  "description": "...",
  "site_url": "https://openai.com/blog",
  "feed_url": "https://openai.com/news/rss.xml",
  "enabled": true,
  "stealth": false,
  "use_feed_content": false
}
```

- `enabled: false` esclude la fonte senza cancellarla
- `stealth: true` scarica gli articoli con Scrapling (browser headless
  Camoufox, con `solve_cloudflare` attivo) invece che con HTTP semplice:
  utile contro protezioni anti-bot, ma molto più lento. Richiede
  `scrapling install` (una tantum)
- `use_feed_content: true` non scarica affatto la pagina dell'articolo:
  estrae il testo dal contenuto completo embedded nel feed
  (`content:encoded`). Ideale per fonti che pubblicano il full-text nel
  feed o le cui pagine sono irraggiungibili (es. AI Trends, le cui pagine
  restituiscono 503 ai bot). Se il download normale fallisce ma il feed
  contiene il testo completo, quello viene comunque usato come fallback
- `include_categories: ["Intelligenza artificiale"]` (opzionale) tiene solo
  le entry del feed con almeno uno dei tag/categorie elencati (match
  case-insensitive). Usato da Wired Italia, il cui feed è generalista

## Interrogare l'archivio: `query_articles.py`

```bash
.venv/Scripts/python query_articles.py --count                 # articoli da elaborare
.venv/Scripts/python query_articles.py --since 2026-09-11 --format markdown --full-content
.venv/Scripts/python query_articles.py --source wired --format json
.venv/Scripts/python query_articles.py --mark-processed --format count
```

Filtri disponibili: `--processed`, `--status`, `--since`/`--until`,
`--source` (ripetibile), `--limit`, `--order`, `--format`
(table/json/jsonl/csv/markdown/count), `--full-content`,
`--mark-processed`. Documentazione completa per uso umano e da agente:
[`SKILL.md`](./SKILL.md) e `query_articles.py --help`.

## Database: `data/newsletter.db`

### Tabella `articles`

| campo | significato |
|---|---|
| `url` / `url_normalized` | URL originale / normalizzato (senza `utm_*` ecc., UNIQUE, base dedup) |
| `guid` | id dell'entry nel feed (secondo criterio di dedup) |
| `source_name` / `feed_url` | fonte di provenienza |
| `title`, `author`, `published_at`, `summary` | metadati (feed + trafilatura) |
| `fetched_at` | timestamp del download |
| `content_text`, `content_length` | testo pulito estratto con trafilatura |
| `status` | `ok` / `empty` (download ok ma nessun testo) / `error` |
| `error` | messaggio d'errore se `status = 'error'` |
| `processed` | flag per la rassegna stampa: 0 = da elaborare, 1 = già incluso |

### Tabella `feeds`

Stato dell'ultimo check per feed: `etag`, `last_modified` (per il
conditional GET: se il feed non cambia, il run successivo lo salta con un
304), `last_checked_at`, `last_status`, `error`.

## Log: `logs/fetch.log`

Log rotante (5 file da 2 MB) con livello DEBUG: ogni richiesta feed (esito
HTTP, 304, errori di connessione), ogni articolo scaricato/saltato/fallito,
e il riepilogo per fonte e di fine run. Su console esce solo INFO+ (o tutto
con `-v`).

## Note

- Primo run: scarica fino a `--limit` articoli per fonte (default 20). I run
  successivi prendono solo i nuovi.
- Ogni richiesta HTTP ha un timeout wall-clock di 90s (`HARD_TIMEOUT` nel
  codice): il timeout di `requests` da solo non ferma i server "tarpit"
  (es. Cloudflare che risponde un byte alla volta ai bot). Chi supera i 90s
  viene loggato come errore e il run prosegue.
- Gli articoli con `status = 'error'` (download fallito) vengono ritentati
  solo con `--retry-errors`.
- Il feed "Google DeepMind" del CSV originale (ai.googleblog.com) è stato
  dismesso da Google (404): è stato sostituito con
  `https://deepmind.google/blog/rss.xml`.
