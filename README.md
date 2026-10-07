# The Hook

NFL and college football spreads with betting splits (ticket % vs money %), measured against DraftKings' Tuesday line, updating until kickoff.

- `index.html` — the page (GitHub Pages)
- `photos.html` — upload page for the weekly photo strip (`images/weekly/`, cleared Tuesday morning)
- `scripts/fetch_hook.py` — pulls Action Network's public betting data; run by `.github/workflows/pull.yml`
- `data/` — what the page reads (`live.json`, `record.json`) plus the stored Tuesday and kickoff lines
