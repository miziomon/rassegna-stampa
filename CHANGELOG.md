# Changelog

Tutte le modifiche rilevanti a questo progetto sono documentate in questo file.
Il formato è basato su [Keep a Changelog](https://keepachangelog.com/it/1.0.0/).

## [0.1] - 2026-09-18

Prima versione funzionante, testata end-to-end sulle 10 fonti iniziali.

### Aggiunto

- `fetch_articles.py`: polling dei feed RSS/Atom da `sources.json`, download
  degli articoli, estrazione del testo con trafilatura e archiviazione in
  SQLite (`data/newsletter.db`), con log rotante in `logs/fetch.log`
- Dedup articoli su URL normalizzato (senza parametri di tracking) + guid
- Conditional GET sui feed (ETag / Last-Modified) con sanity check: un 304
  senza articoli nel DB forza un re-fetch completo
- Timeout wall-clock di 90s per richiesta HTTP (`hard_get`): protegge dai
  server "tarpit" che aggirano il timeout per-socket di requests
- Opzioni per fonte in `sources.json`: `enabled`, `stealth` (download via
  Scrapling/Camoufox con `solve_cloudflare`), `use_feed_content` (testo dal
  contenuto embedded nel feed, con fallback automatico se il download
  fallisce), `include_categories` (filtro per tag/categoria del feed, usato
  da Wired Italia per tenere solo gli articoli AI)
- Opzioni CLI di `fetch_articles.py`: `--dry-run`, `--limit`, `--retry-errors`,
  `--verbose`, `--sources`, `--db`
- `query_articles.py`: interrogazione dell'archivio con filtri su
  `processed`, data (`--since`/`--until`), fonte e stato; output
  table/json/jsonl/csv/markdown; `--mark-processed` per la rassegna stampa.
  Documentato per uso da agente in `SKILL.md`
- Campo `processed` nella tabella `articles` per la futura procedura di
  rassegna stampa via email

### Modificato (rispetto all'elenco fonti originale)

- Feed Google DeepMind: sostituito il vecchio ai.googleblog.com (dismesso,
  HTTP 404) con `https://deepmind.google/blog/rss.xml`

### Note

- AI Trends: le pagine articolo restituiscono 503 ai bot (Cloudflare, anche
  via Camoufox); la fonte usa `use_feed_content` perché il feed include il
  testo integrale
