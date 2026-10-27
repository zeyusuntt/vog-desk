# web/

The page a school's designated person reads at 18:00, and the place judges see the desk work. One static page, no build step. Serve the repository root and open `web/`:

```bash
python3 -m http.server 8765    # then http://127.0.0.1:8765/web/
```

It reads `live/index.json`, `live/<date>/forecast.json`, `trace.jsonl` and `scene.json`, and `results/heldout_*_v4.csv`. A night is added by `scripts/live.py --issue <date> --desk`; `scripts/verify_live.py` fills in the measured hours the next afternoon and refreshes `live/index.json`.

What is on it, top to bottom:

- The call: tonight's probability as a sentence, the schools plan's recommendation, and once verified, what happened.
- At a glance: how the desk decided (roles, queries, re-query rounds, Nemotron calls), the live record from 23 to 26 October, and the held-out score.
- Where tonight sits on the plan's bands, with the four guidance streams on the same scale.
- The night over Kaʻū: a three.js map with the Kīlauea summit vent, the Pāhala, Volcanoes NP and Ocean View monitors, the GFS 925 hPa wind at the summit and the inversion from the Hilo sounding, as available at 18:00. After verification each monitor's measured hour is a beam whose height grows with the square root of SO2 and whose colour follows the plan's bands, with a halo that pulses above 75 ppb. The 18-hour strip below is the timeline, and "Play the night" steps through it. The drift from the vent only carries the forecast wind forward and is labelled as an illustration, not a dispersion model.
- Who did what tonight: the five roles (evidence agent, forecaster, review, duty lead, verifier), what each did, read from the trace.
- Why the desk said this: the discussion with every number tagged by its evidence or stream; the review's findings; the model calls.
- The evidence, the run step by step, the held-out record and the live log.

Details:

- Replay of a past night: `?replay=2025-09-02` reads `web/replay/2025-09-02.json` (`scripts/scene.py --issue 2025-09-02 --replay`). `&hour=11` opens at 05:00 HST and `&autoplay=1` plays the night, for recording. `?night=<date>` opens a live night.
- three.js 0.169.0 and its post-processing add-ons load from jsDelivr. With reduced motion the drift is drawn as still lines; without WebGL the strip stays and the map is replaced by a note.
- Terrain: `assets/kau-terrain.png`, cut from USGS 3DEP elevation by `scripts/terrain.py` (public domain), drawn with a two-light hillshade, an elevation tint, 250 m contours and a coast line; vertical scale ×2.
- Type and colour: Public Sans, basalt ink on a vog-haze ground, one ocean-blue accent; the plan's colours are reserved for action levels and always carry a text label, checked with a colour-vision validator in light and dark mode.
- `2d.html` is the earlier version without the map, kept for comparison.
