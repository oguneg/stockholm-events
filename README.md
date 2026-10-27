# Stockholm events

Combined view of the Stockholm "nearby" events from every Luma category page (ai, tech, games, arts, ...), grouped by date.

- `scrape.py` calls the same API the Luma category pages use and writes `events.json`.
- `index.html` renders it. Preview: `python -m http.server 8000`.
- `.github/workflows/refresh.yml` re-runs the scrape once a day. Serve with GitHub Pages (branch `main`, root).
- Add a category by appending its slug to `CATEGORIES` in `scrape.py`.
