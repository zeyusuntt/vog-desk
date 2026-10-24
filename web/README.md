# web/

One static page, no build step. Serve the repository root and open `web/`:

```bash
python3 -m http.server 8765    # then http://127.0.0.1:8765/web/
```

It reads `live/index.json`, `live/<date>/forecast.json` and `trace.jsonl`, and `results/heldout_*_v4.csv`. Add a night by running `scripts/live.py --issue <date> --desk`; `scripts/verify_live.py` fills in the measured hours the next afternoon and refreshes `live/index.json`.

Design notes: the hero is the probability scale painted in the schools SO2 plan's own bands, with tonight's desk marker and the four guidance streams on the same axis; the second figure is the night window itself, 18 hourly slots filled once measured. One typeface (Public Sans), basalt ink on a vog-haze ground, one ocean-blue accent; the plan's colours are reserved for action levels and always carry a text label. The level palette was checked with a colour-vision validator in both light and dark mode. Charts draw at the container's pixel width so their text keeps its size on a phone.

## 3d.html, a second version for comparison

`3d.html` is the same page with one change: the night section is a map of Kaʻū drawn with three.js (loaded from jsDelivr, version 0.169.0). It shows the Kīlauea summit vent, the Pāhala, Volcanoes NP and Ocean View monitors, the GFS 925 hPa wind at the summit and the inversion from the Hilo sounding, as available at 18:00, and after verification each monitor's measured hour as a column. The 18-hour strip below it is the timeline; "Play the night" steps through it. The drift dots only carry the forecast wind forward from the vent and are labelled as an illustration, not a dispersion model. With reduced motion the dots are drawn as still lines; without WebGL the strip stays and the map is replaced by a note.

- Data: `live/<date>/scene.json`, written by `scripts/live.py` at issue and refreshed by `scripts/verify_live.py`; `scripts/scene.py --issue <date>` writes one by hand.
- Replay of a past night: `3d.html?replay=2025-09-02` reads `web/replay/2025-09-02.json` (`scripts/scene.py --issue 2025-09-02 --replay`). `&hour=11` opens at 05:00 HST and `&autoplay=1` plays the night, for recording.
- Terrain: `assets/kau-terrain.png`, cut from USGS 3DEP elevation by `scripts/terrain.py` (public domain). It is drawn with hillshade, an elevation tint and 250 m contours; vertical scale ×2.

