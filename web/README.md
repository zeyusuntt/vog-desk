# web

A static page that shows the vog-desk output for Pāhala, Hawaiʻi Island: tonight's forecast card, the evidence the desk queried, the agent trace as a timeline, and a log of every published night. It is one file (`index.html`) with inline CSS and vanilla JavaScript. There is no build step and no external library, font or CDN. The only network requests are relative fetches of files under `live/`.

## Run it

Serve the repository root, not the `web/` folder, because the page reads `../live/...`:

```bash
python3 -m http.server 8765        # from the repo root
# open http://127.0.0.1:8765/web/index.html
```

GitHub Pages works the same way if it publishes the repository root. The page is then at `<site>/web/index.html`.

## Data it reads

- `live/index.json`: a list of issue dates, newest first, for example `["2026-10-24", "2026-10-23"]`. If the file is missing the page falls back to `2026-10-23`.
- `live/<date>/forecast.json`: the forecast record. Model-only nights have `kind: "model_only"` and no usable `desk` block. Full desk nights add `desk.decision`, `desk.guidance`, `desk.flags`, `desk.llm_calls` and `desk.regime_summary`.
- `live/<date>/trace.jsonl`: optional, one JSON object per line as written by `src/vogdesk/trace.py`. Without it the evidence and trace sections show a short message.

Evidence items are read from trace lines whose `step` starts with `query:` or `requery:`, using `output.id`, `output.summary`, `output.rows_hash` and `output.available_utc`, plus the `sql` list. Chips such as `[E3]` and `[G1]` in the discussion scroll to the matching evidence or guidance item.

## Notes

- To add a night, put its files under `live/<date>/` and add the date to the front of `live/index.json`.
- A missing `trace.jsonl` is an expected case. The page handles it, but a browser may still print the 404 for that request in its network panel.
- All dynamic text is inserted with `textContent`, so the data files cannot inject markup.
