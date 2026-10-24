"""Write the night scene for web/3d.html: live/<D>/scene.json, or web/replay/<D>.json with --replay.

Usage: .venv/bin/python scripts/scene.py --issue 2026-10-23 [--replay]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from vogdesk import sources  # noqa: E402
from vogdesk.scene import build_scene  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--issue", required=True)
    ap.add_argument("--replay", action="store_true", help="a past night shown with its measured hours, written under web/replay/")
    args = ap.parse_args()
    scene = build_scene(sources.load(), args.issue, replay=args.replay)
    out = (ROOT / "web" / "replay" / f"{args.issue}.json") if args.replay else (ROOT / "live" / args.issue / "scene.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(scene, indent=1, ensure_ascii=False))
    print(out.relative_to(ROOT), json.dumps({k: scene[k] for k in ("wind", "inversion", "episode")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
