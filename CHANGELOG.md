# Changelog

Tutte le modifiche rilevanti a questo progetto sono documentate in questo file.
Il formato è basato su [Keep a Changelog](https://keepachangelog.com/it/1.0.0/).

## [0.3] - 2026-09-18

### Aggiunto

- `query_articles.py`: filtro `--ids 12,34,56` per rileggere una selezione
  puntuale di articoli (contenuto integrale o marcatura per id). Serve alla
  fase 2 della rassegna: prima si scorre il lotto con gli estratti, poi si
  recuperano per id solo i candidati scelti.

### Corretto

- Con `--count` (o `--format count`) il `LIMIT` non si applica più: il
  conteggio riflette l'intera selezione e `--mark-processed --format count`
  marca tutte le righe filtrate, non solo le prime 100.

## [0.2] - 2026-09-18

Ampliamento delle fonti: da 10 a 45.

### Aggiunto

- 35 nuove fonti AI: blog dei laboratori (Google AI, Microsoft Research,
  NVIDIA, Apple ML, Meta Engineering), blog tecnici (Hugging Face, PyTorch,
  TensorFlow, fast.ai, KDnuggets, PyImageSearch, EleutherAI), blog personali
  (Simon Willison, Lilian Weng, Jay Alammar, Chip Huyen, Eugene Yan),
  newsletter (SemiAnalysis, Raschka, Interconnects, Import AI, One Useful
  Thing, AI Snake Oil, Latent Space, Algorithmic Bridge, Last Week in AI),
  testate (The Decoder, TechCrunch AI, Ars Technica AI, IEEE Spectrum AI,
  AI4Business) e testate italiane (Agenda Digitale, Valigia Blu,
  Cybersecurity360)

### Modificato

- **Meta AI Blog** → sostituito con "Meta Engineering (AI)"
  (`engineering.fb.com/feed/` con filtro su `AI Research`/`ML Applications`):
  ai.meta.com non espone un feed RSS
- **Agenda Digitale**: il feed di sezione non esiste; si usa il feed
  principale con `include_categories: ["Intelligenza Artificiale"]`
- **Valigia Blu**: feed sostituito con quello di tag AI
  (`/tag/intelligenza-artificiale/feed/`), già specifico per tema
- **Cybersecurity360**: aggiunto `include_categories` sui tag AI
  (`AI`, `AI Act`, `AI generative`, `Intelligenza Artificiale`,
  incluso il refuso `intelligenza arficiale` usato occasionalmente)

### Rimosso

- **Fully Connected (Weights & Biases)**: feed vuoto/dismesso, nessun URL
  alternativo funzionante

### Note

- Run di verifica sulle 45 fonti: 579 articoli scaricati, 0 errori, 0
  duplicati. Le 2 entry "vuote" di IEEE Spectrum sono link a webinar, non
  articoli (salvate con status `empty`, non verranno riscaricate)
- Le righe `No Cloudflare challenge found` nel log di Agenda Digitale e
  AI4Business provengono dal logger interno di Scrapling e sono benigna
  cosmesi: i download stealth sono andati tutti a buon fine

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
