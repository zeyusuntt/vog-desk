"""Zetaris REST client and the one SQL tool the agents are allowed to use.

Zetaris is the data layer: every number an agent cites comes back from `run_sql`, which only accepts
read-only statements against the `pahala_exposure` semantic-layer views and rewrites each view
reference to filter `available_utc <= as_of` (the information cutoff frozen in PREREG). Agents never
see base tables and never choose the as-of time; the desk does.

Request shape (from zetaris/getting-started scripts/zetaris_sql.py): POST {REST_URL}/sql-editor/sqls/run
with {"queryId": uuid, "sql": ..., "source": "SqlEditor", "limit": n}, bearer API key, X-Org-ID,
fresh X-Request-ID. Credentials come from .env and are never logged.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import pandas as pd

SEMANTIC_SCHEMA = "pahala_exposure"
USL_PREFIX = "lightning.metastore.vog.pahala_exposure"  # the activated USL tables behind the agent-facing name
ASOF_VIEWS = {"so2_hourly", "station_wind", "episodes_known", "gfs_points", "sounding_levels"}
PLAIN_VIEWS = {"stations", "action_plan_levels", "forecast_verification"}
ALLOWED_VIEWS = ASOF_VIEWS | PLAIN_VIEWS
FORBIDDEN = re.compile(r"\b(insert|update|delete|drop|create|alter|truncate|merge|grant|revoke|compile|activate|cache|uncache|register)\b", re.I)


class ZetarisError(RuntimeError):
    pass


class SqlRefused(ValueError):
    """The agent asked for something the tool does not allow."""


@dataclass
class SqlResult:
    sql: str
    rows: list[dict]
    columns: list[str]
    sql_hash: str
    rows_hash: str
    elapsed_ms: int
    as_of: str | None = None
    note: str = ""

    def frame(self) -> pd.DataFrame:
        return pd.DataFrame(self.rows, columns=self.columns)

    def summary(self) -> dict:
        return {"sql_hash": self.sql_hash, "rows": len(self.rows), "rows_hash": self.rows_hash, "as_of": self.as_of,
                "elapsed_ms": self.elapsed_ms}


@dataclass
class Zetaris:
    base_url: str
    api_key: str = field(repr=False)
    org_id: str = "1"
    timeout_s: float = 180.0

    @classmethod
    def from_env(cls) -> "Zetaris":
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parents[2] / ".env")
        try:
            return cls(base_url=os.environ["ZETARIS_REST_URL"].rstrip("/"), api_key=os.environ["ZETARIS_API_KEY"],
                       org_id=os.environ.get("ZETARIS_ORG_ID", "1"))
        except KeyError as e:
            raise ZetarisError(f"missing {e.args[0]} in .env") from e

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}", "X-Org-ID": self.org_id,
                "X-Request-ID": str(uuid.uuid4()), "Content-Type": "application/json"}

    def execute(self, sql: str, limit: int = 1000) -> SqlResult:
        """Run any statement (admin use: DDL, loads). Agents use run_sql instead."""
        body = {"queryId": str(uuid.uuid4()), "sql": sql, "source": "SqlEditor", "limit": limit}
        import time
        t0 = time.perf_counter()
        try:
            r = httpx.post(f"{self.base_url}/sql-editor/sqls/run", headers=self._headers(), json=body, timeout=self.timeout_s)
        except httpx.TransportError as e:
            raise ZetarisError(f"cannot reach Zetaris at {self.base_url}: {e}") from e
        if r.status_code >= 400:
            raise ZetarisError(f"HTTP {r.status_code}: {r.text[:400]}")
        rows, columns = parse_result(r.json())
        sql_hash = hashlib.sha256(sql.encode()).hexdigest()[:16]
        rows_hash = hashlib.sha256(json.dumps(rows, sort_keys=True, default=str).encode()).hexdigest()[:16]
        return SqlResult(sql=sql, rows=rows, columns=columns, sql_hash=sql_hash, rows_hash=rows_hash,
                         elapsed_ms=int((time.perf_counter() - t0) * 1000))

    def run_sql(self, sql: str, as_of: pd.Timestamp | str, limit: int = 1000) -> SqlResult:
        """The agent tool: read-only SQL on the semantic-layer views, cut at `as_of`."""
        rewritten = rewrite_asof(sql, pd.Timestamp(as_of))
        out = self.execute(rewritten, limit=limit)
        out.as_of = pd.Timestamp(as_of).isoformat()
        return out


def parse_result(payload) -> tuple[list[dict], list[str]]:
    """The REST route returns {"headers": [...], "data": [[...], ...], "total": n, "timeUsed": ms} (seen 23 Oct 2026).
    Values arrive as strings. A list of row dicts is accepted too."""
    if isinstance(payload, dict) and "headers" in payload:
        cols = [str(c) for c in payload["headers"]]
        return [dict(zip(cols, r)) for r in payload.get("data") or []], cols
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        data = payload["data"]
        return data, list(data[0].keys()) if data and isinstance(data[0], dict) else []
    if isinstance(payload, list):
        return payload, list(payload[0].keys()) if payload and isinstance(payload[0], dict) else []
    return [], []


def rewrite_asof(sql: str, as_of: pd.Timestamp) -> str:
    """Refuse anything but a single SELECT/WITH over allowed views; wrap as-of views in a cutoff subquery."""
    s = sql.strip().rstrip(";").strip()
    if ";" in s:
        raise SqlRefused("one statement at a time")
    if not re.match(r"^(select|with)\b", s, re.I):
        raise SqlRefused("only SELECT or WITH statements")
    if FORBIDDEN.search(s):
        raise SqlRefused("statement contains a keyword the tool does not allow")
    if as_of.tzinfo is None:
        raise SqlRefused("as_of must be timezone-aware")
    refs = re.findall(rf"\b{SEMANTIC_SCHEMA}\.(\w+)", s, re.I)
    if not refs:
        raise SqlRefused(f"queries must read from {SEMANTIC_SCHEMA}.* views")
    bad = {r for r in refs if r.lower() not in ALLOWED_VIEWS}
    if bad:
        raise SqlRefused(f"unknown view(s): {sorted(bad)}")
    other = re.findall(r"\b(?:from|join)\s+([a-zA-Z_][\w]*\.[\w]+(?:\.[\w]+)?)", s, re.I)
    for o in other:
        if not o.lower().startswith(SEMANTIC_SCHEMA + "."):
            raise SqlRefused(f"base table reference not allowed: {o}")
    ts = as_of.tz_convert("UTC").strftime("%Y-%m-%d %H:%M:%S")

    def wrap(m: re.Match) -> str:
        keyword, view = m.group(1), m.group(2)
        if view.lower() == "episodes_known":
            # Each boundary has its own publication time. An episode is visible once its start was
            # published; its end (and the end's publication time) stays NULL until the end was published.
            # Fountain height and volume come from the retrospective USGS table and are never shown.
            return (f"{keyword} (SELECT episode, start_utc, start_known_utc, start_known_source, "
                    f"CASE WHEN end_known_utc <= TIMESTAMP '{ts}' THEN end_utc END AS end_utc, "
                    f"CASE WHEN end_known_utc <= TIMESTAMP '{ts}' THEN end_known_utc END AS end_known_utc, "
                    f"CASE WHEN end_known_utc <= TIMESTAMP '{ts}' THEN end_known_source END AS end_known_source "
                    f"FROM {USL_PREFIX}.{view} WHERE start_known_utc <= TIMESTAMP '{ts}') {view}")
        if view.lower() in ASOF_VIEWS:
            return f"{keyword} (SELECT * FROM {USL_PREFIX}.{view} WHERE available_utc <= TIMESTAMP '{ts}') {view}"
        return f"{keyword} {USL_PREFIX}.{view} {view}"

    # Only table references after FROM or JOIN are wrapped; column qualifiers such as so2_hourly.site_id
    # keep working because each subquery is aliased with the view name.
    return re.sub(rf"\b(from|join)\s+{SEMANTIC_SCHEMA}\.(\w+)", wrap, s, flags=re.I)


def smoke() -> None:
    z = Zetaris.from_env()
    for sql in ["SELECT 1 AS one", "SHOW LIGHTNING DATABASES"]:
        r = z.execute(sql, limit=50)
        print(sql, "->", len(r.rows), "rows", r.columns[:6], r.rows[:3])
    for view in sorted(ALLOWED_VIEWS):
        try:
            r = z.execute(f"SELECT count(*) AS n FROM {USL_PREFIX}.{view}", limit=1)
            print(f"{SEMANTIC_SCHEMA}.{view}: {r.rows[0]['n']} rows ({r.elapsed_ms} ms)")
        except ZetarisError as e:
            print(f"{SEMANTIC_SCHEMA}.{view}: not available ({str(e)[:120]})")
    t = pd.Timestamp("2025-09-03 04:00", tz="UTC")
    r = z.run_sql(f"SELECT max(available_utc) AS latest, count(*) AS n FROM {SEMANTIC_SCHEMA}.so2_hourly WHERE site_id = '150012016'", t)
    print("as-of check at", t, "->", r.rows, "(latest must be <= as_of)")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "smoke":
        smoke()
    else:
        print("usage: python -m vogdesk.zetaris smoke")
