---
name: newsletter-db-query
description: >
  Legge gli articoli raccolti dal DB SQLite di newsletter-fetcher
  (data/newsletter.db) con filtri su processed, data, fonte e stato, e li
  restituisce in table/json/jsonl/csv/markdown. Usare quando serve generare
  una rassegna stampa, ispezionare gli articoli scaricati o marcarli come
  processati.
---

# newsletter-db-query

Interroga l'archivio articoli prodotto da `fetch_articles.py`. Entry point:
`query_articles.py` (nessuna dipendenza oltre la stdlib; usare il venv del
progetto: `.venv/Scripts/python`).

## Decisioni rapide

- Vuoi gli articoli **non ancora inclusi in una rassegna** → default
  (`--processed 0 --status ok`), non serve specificare nulla.
- Vuoi i contenuti **integrali** → aggiungi `--full-content` (altrimenti
  `content_text` è troncato a 300 caratteri).
- Output per **una LLM/un documento** → `--format markdown` o `json`.
- Dopo aver **usato** gli articoli in una rassegna → rilancia la stessa
  query con `--mark-processed` per non rileggerli la volta dopo.

## Comandi tipici

```bash
# Conteggio rapido degli articoli da elaborare
.venv/Scripts/python query_articles.py --count

# Rassegna degli ultimi 7 giorni in markdown (bozza)
.venv/Scripts/python query_articles.py --since $(date -d "-7 days" +%F) \
    --format markdown --full-content > rassegna.md

# JSON per elaborazione automatica, solo alcune fonti
.venv/Scripts/python query_articles.py --source openai --source deepmind \
    --format json --full-content

# Marchiare come processati gli articoli appena usati (stessa selezione!)
.venv/Scripts/python query_articles.py --since 2026-09-11 --mark-processed --format count

# Ispezione di errori di scaricamento
.venv/Scripts/python query_articles.py --status error --processed all
```

## Riferimento filtri

| flag | significato | default |
|---|---|---|
| `--processed {0,1,all}` | flag rassegna stampa | `0` |
| `--status {ok,empty,error,all}` | esito estrazione | `ok` |
| `--since` / `--until` | data (YYYY-MM-DD o ISO), su `published_at` (fallback `fetched_at`) | — |
| `--source TEXT` | substring case-insensitive, ripetibile (OR) | — |
| `--limit N` | max righe | 100 |
| `--order {asc,desc}` | per data | `desc` |
| `--format` | table, json, jsonl, csv, markdown, count | `table` |
| `--mark-processed` | UPDATE processed=1 sulle righe restituite | off |
| `--db PATH` | DB alternativo | `data/newsletter.db` |

## Note operative

- Il filtro date confronta stringhe ISO: funziona perché `published_at` è
  salvato in formato ISO 8601 UTC.
- `--mark-processed` stampa il conteggio su **stderr** e marchia esattamente
  le righe della selezione corrente (stessi filtri): verifica sempre prima
  con `--count` quali righe stai per marchiare.
- Exit code: 0 anche con zero risultati; 1 solo per errori (DB mancante,
  date malformate).
- Schema completo della tabella `articles`: vedi README.md del progetto.
