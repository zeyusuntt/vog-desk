# web/

One static page, no build step. Serve the repository root and open `web/`:

```bash
python3 -m http.server 8765    # then http://127.0.0.1:8765/web/
```

It reads `live/index.json`, `live/<date>/forecast.json` and `trace.jsonl`, and `results/heldout_*_v4.csv`. Add a night by running `scripts/live.py --issue <date> --desk`; `scripts/verify_live.py` fills in the measured hours the next afternoon and refreshes `live/index.json`.

Design notes: the hero is the probability scale painted in the schools SO2 plan's own bands, with tonight's desk marker and the four guidance streams on the same axis; the second figure is the night window itself, 18 hourly slots filled once measured. One typeface (Public Sans), basalt ink on a vog-haze ground, one ocean-blue accent; the plan's colours are reserved for action levels and always carry a text label. The level palette was checked with a colour-vision validator in both light and dark mode. Charts draw at the container's pixel width so their text keeps its size on a phone.
