"""Build the Zetaris data layer for vog-desk: sources, the pahala_exposure semantic layer, data quality rules.

Runs the SQL files in this folder one statement at a time through the REST API, filling ${...}
placeholders from .env at run time so credentials never sit in a committed file. "Already exists"
errors are reported and skipped so the script can be re-run. Stops at the first other error.

Usage: .venv/bin/python infra/zetaris/setup.py [01_sources.sql ...] [--dry-run] [--continue]
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "src"))

from vogdesk.zetaris import Zetaris, ZetarisError  # noqa: E402

EXISTS = re.compile(r"already exist|ALREADY_EXISTS|duplicate", re.I)


def placeholders() -> dict[str, str]:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    return {
        "S3_ACCESS_KEY": os.environ.get("MINIO_ROOT_USER", "vogdesk"),
        "S3_SECRET_KEY": os.environ.get("MINIO_ROOT_PASSWORD", "vogdesk-local-only"),
        "S3_ENDPOINT": os.environ.get("ZETARIS_S3_ENDPOINT", "http://s3:9000"),
        "PG_DB": os.environ.get("POSTGRES_DB", "vogdesk"),
        "PG_USER": os.environ.get("POSTGRES_USER", "vogdesk"),
        "PG_PASSWORD": os.environ.get("POSTGRES_PASSWORD", "vogdesk-local-only"),
    }


def statements(text: str) -> list[str]:
    """Split on semicolons at end of line, dropping comment-only lines; keeps multi-line DDL together.

    A `COMPILE USL ... DDL` block contains several CREATE TABLE statements and must be sent whole, so it
    runs from its first line to a line reading exactly `-- END USL`.
    """
    out, buf, in_usl = [], [], False
    for ln in text.splitlines():
        if ln.strip() == "-- END USL":
            stmt = "\n".join(buf).strip()
            if stmt:
                out.append(stmt)
            buf, in_usl = [], False
            continue
        if ln.strip().startswith("--"):
            continue
        if not buf and ln.strip().upper().startswith("COMPILE USL"):
            in_usl = True
        buf.append(ln)
        if not in_usl and ln.rstrip().endswith(";"):
            stmt = "\n".join(buf).strip().rstrip(";").strip()
            if stmt:
                out.append(stmt)
            buf = []
    tail = "\n".join(buf).strip()
    if tail:
        out.append(tail)
    return out


def redact(text: str, values: dict[str, str]) -> str:
    for k, v in values.items():
        if "SECRET" in k or "PASSWORD" in k:
            text = text.replace(v, "***")
    return text


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*", default=["01_sources.sql", "02_usl.sql", "03_dq.sql"])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--continue", dest="keep_going", action="store_true", help="do not stop at the first error")
    args = ap.parse_args()
    vals = placeholders()
    z = None if args.dry_run else Zetaris.from_env()
    failures = 0
    for name in args.files:
        path = HERE / name
        for stmt in statements(path.read_text()):
            filled = re.sub(r"\$\{(\w+)\}", lambda m: vals[m.group(1)], stmt)
            head = " ".join(stmt.split())[:90]
            if args.dry_run:
                print(f"[dry] {head}")
                continue
            try:
                r = z.execute(filled, limit=5)
                print(f"ok   {head}  ({r.elapsed_ms} ms, {len(r.rows)} rows)")
            except ZetarisError as e:
                msg = redact(str(e), vals)
                if EXISTS.search(msg):
                    print(f"skip {head}  (exists)")
                    continue
                failures += 1
                print(f"FAIL {head}\n     {msg[:600]}")
                if not args.keep_going:
                    return 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
