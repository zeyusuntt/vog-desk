"""Cut the Kaʻū terrain used by web/3d.html out of the Windward island heightmap.

Source: Windward's `public/assets/terrain-height.png` (USGS 3DEP, public domain), 4096 x 3862, elevation packed
as value16 = R*256 + G and metres = value16 / 65535 * elevMax. This script crops the box that holds the
Kīlauea summit, Pāhala, Ocean View, the Volcanoes NP monitor and the south coast, averages it down to about
200 m per pixel, and writes the same packing so the page can decode it. The output is small and committed;
this script only has to be run again to change the box.

Usage: .venv/bin/python scripts/terrain.py --source <windward>/public/assets/terrain-height.png
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SRC_EXTENT = {"lonMin": -156.243, "latMin": 18.849, "lonMax": -154.668, "latMax": 20.334}
SRC_ELEV_MAX = 4201.515625
BOX = {"lonMin": -155.95, "lonMax": -155.10, "latMin": 18.90, "latMax": 19.56}
OUT_W = 448


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, type=Path)
    ap.add_argument("--out", default=ROOT / "web" / "assets", type=Path)
    args = ap.parse_args()
    Image.MAX_IMAGE_PIXELS = None
    rgb = np.asarray(Image.open(args.source).convert("RGB")).astype(np.float64)
    h, w = rgb.shape[:2]
    elev = (rgb[..., 0] * 256 + rgb[..., 1]) / 65535 * SRC_ELEV_MAX
    col = lambda lon: (lon - SRC_EXTENT["lonMin"]) / (SRC_EXTENT["lonMax"] - SRC_EXTENT["lonMin"]) * w
    row = lambda lat: (SRC_EXTENT["latMax"] - lat) / (SRC_EXTENT["latMax"] - SRC_EXTENT["latMin"]) * h
    c0, c1 = int(round(col(BOX["lonMin"]))), int(round(col(BOX["lonMax"])))
    r0, r1 = int(round(row(BOX["latMax"]))), int(round(row(BOX["latMin"])))
    crop = elev[r0:r1, c0:c1].astype(np.float32)
    km_w = (BOX["lonMax"] - BOX["lonMin"]) * 111.32 * np.cos(np.radians((BOX["latMin"] + BOX["latMax"]) / 2))
    km_h = (BOX["latMax"] - BOX["latMin"]) * 110.6
    out_h = int(round(OUT_W * km_h / km_w))
    small = np.asarray(Image.fromarray(crop, mode="F").resize((OUT_W, out_h), Image.Resampling.BOX))
    emax = float(small.max())
    v16 = np.clip(np.round(small / emax * 65535), 0, 65535).astype(np.uint32)
    packed = np.zeros((out_h, OUT_W, 3), np.uint8)
    packed[..., 0], packed[..., 1] = v16 >> 8, v16 & 255
    args.out.mkdir(parents=True, exist_ok=True)
    Image.fromarray(packed, "RGB").save(args.out / "kau-terrain.png", optimize=True)
    meta = {"box": BOX, "widthPx": OUT_W, "heightPx": out_h, "kmWidth": round(km_w, 2), "kmHeight": round(km_h, 2),
            "elevMaxM": round(emax, 1), "packing": "value16 = R*256 + G; metres = value16 / 65535 * elevMaxM",
            "source": "USGS 3DEP via the Windward island heightmap (public domain)"}
    (args.out / "kau-terrain.json").write_text(json.dumps(meta, indent=1))
    print(json.dumps(meta), (args.out / "kau-terrain.png").stat().st_size, "bytes")


if __name__ == "__main__":
    main()
