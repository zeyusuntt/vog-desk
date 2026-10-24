"""Write forecasts and verifications to the institutional Postgres tables that Zetaris exposes.

The desk's own records are data too: `pahala_exposure.forecast_verification` is read back through
Zetaris for the results page and for the Verifier's running tallies. Connection details come from .env
(POSTGRES_*); when Postgres is not reachable the caller gets False and the forecast still stands.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import psycopg

from vogdesk.desk.state import CaseState, Verification


def dsn() -> str:
    return (f"host={os.environ.get('POSTGRES_HOST', '127.0.0.1')} port={os.environ.get('POSTGRES_PORT', '5433')} "
            f"dbname={os.environ.get('POSTGRES_DB', 'vogdesk')} user={os.environ.get('POSTGRES_USER', 'vogdesk')} "
            f"password={os.environ.get('POSTGRES_PASSWORD', 'vogdesk-local-only')}")


def record_forecast(state: CaseState, forecast_kind: str = "full_desk", model_version: str | None = None) -> bool:
    dec = state["decision"]
    try:
        with psycopg.connect(dsn(), connect_timeout=5) as conn:
            conn.execute(
                """INSERT INTO institutional.forecasts (case_id, issue_date_hst, issue_utc, probability, action_level, decision_kind,
                       discussion, model_version, trace_path, forecast_kind, created_utc)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (case_id) DO UPDATE SET probability = EXCLUDED.probability, action_level = EXCLUDED.action_level,
                       decision_kind = EXCLUDED.decision_kind, discussion = EXCLUDED.discussion, trace_path = EXCLUDED.trace_path""",
                (state["case_id"], state["issue_date_hst"], state["issue_utc"], dec["p"], dec["level"], dec["kind"],
                 dec["discussion"], model_version or "lr_2025", state.get("trace_path"), forecast_kind, datetime.now(timezone.utc)))
        return True
    except psycopg.Error:
        return False


def record_verification(case_id: str, ver: Verification, label_source: str) -> bool:
    try:
        with psycopg.connect(dsn(), connect_timeout=5) as conn:
            conn.execute(
                """INSERT INTO institutional.verifications (case_id, observed_max_ppb, n_hours, event, outcome, brier, memory_record_id,
                       label_source, created_utc)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (case_id) DO UPDATE SET observed_max_ppb = EXCLUDED.observed_max_ppb, n_hours = EXCLUDED.n_hours,
                       event = EXCLUDED.event, outcome = EXCLUDED.outcome, brier = EXCLUDED.brier, memory_record_id = EXCLUDED.memory_record_id""",
                (case_id, ver.observed_max_ppb, ver.n_hours, ver.event, ver.outcome, ver.brier, ver.memory_record_id, label_source,
                 datetime.now(timezone.utc)))
        return True
    except psycopg.Error:
        return False
