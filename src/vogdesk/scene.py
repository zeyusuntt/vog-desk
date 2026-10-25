"""Data for the night scene on the map on web/: where the monitors are, tonight's forecast wind and inversion, and
the measured hours once the window has closed.

The forecast part uses the same evidence functions and the same as-of cutoff as the desk, so the scene shows
only what the desk could see at 18:00 HST. Measured hours are added only when the night's verification time
has passed (or when building a replay of a past night).
"""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd

from vogdesk import sources
from vogdesk.asof import issue_time_utc
from vogdesk.desk.data import LocalBackend
from vogdesk.desk.evidence import LEAD_HST, LEADS, episode_item, gfs_item, sounding_item
from vogdesk.desk.verifier import verification_time

VENT = {"name": "Halemaʻumaʻu", "lat": 19.4072, "lon": -155.2834}
STATIONS = [
    {"id": "pahala", "name": "Pāhala", "lat": 19.2039, "lon": -155.4802, "role": "the forecast's monitor"},
    {"id": "hvnp", "name": "Volcanoes NP", "lat": 19.4308, "lon": -155.2578, "role": "near the summit"},
    {"id": "ocean_view", "name": "Ocean View", "lat": 19.1176, "lon": -155.7781, "role": "farther downwind"},
]
HOURS = 18


def _num(x):
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else x


def measured_hours(src: sources.Sources, issue_date: pd.Timestamp) -> dict[str, list[float | None]]:
    t0 = issue_time_utc(issue_date)
    out = {}
    for st in STATIONS:
        s = sources.station_series(src, st["id"])
        out[st["id"]] = [_num(float(v)) if (v := s.get(t0 + pd.Timedelta(hours=k))) is not None and not pd.isna(v) else None
                         for k in range(HOURS)]
    return out


def build_scene(src: sources.Sources, issue_date: str | pd.Timestamp, *, now_utc: datetime | None = None,
                replay: bool = False) -> dict:
    d = pd.Timestamp(issue_date).normalize()
    as_of = issue_time_utc(d)
    backend = LocalBackend(src)
    g = gfs_item("E6", backend, d, as_of).values
    snd = sounding_item("E7", backend, as_of).values
    ep = episode_item("E5", backend, as_of)
    wind = []
    for lead in LEADS:
        if f"summit_925_from_deg_f{lead}" not in g:
            continue
        wind.append({"hst": LEAD_HST[lead].replace(" HST", ""), "from_deg": g[f"summit_925_from_deg_f{lead}"],
                     "speed_ms": g[f"summit_925_speed_ms_f{lead}"], "toward_pahala_ms": g[f"summit_925_toward_pahala_ms_f{lead}"],
                     "pahala_boundary_layer_m": _num(g.get(f"pahala_hpbl_m_f{lead}"))})
    inversion = None
    if snd.get("inversion_base_m") is not None and not snd.get("none_found"):
        inversion = {"base_m": round(float(snd["inversion_base_m"])), "launch_utc": snd["launch_utc"]}
    now = pd.Timestamp(now_utc or datetime.now(timezone.utc))
    closed = replay or now >= verification_time(d)
    return {
        "issue_date_hst": d.strftime("%Y-%m-%d"), "issue_utc": as_of.isoformat(), "replay": bool(replay),
        "vent": VENT, "stations": STATIONS,
        "wind": wind, "wind_source": "GFS 18Z run, 925 hPa at the summit point, as available at 18:00 HST",
        "inversion": inversion, "inversion_source": "Hilo radiosonde, latest launch before 18:00 HST",
        "episode": ep.summary,
        "hours": measured_hours(src, d) if closed else None,
        "hours_source": "AirNow preliminary and EPA AQS hourly SO2, hour beginning HST" if closed else None,
        "hours_start_hst": "18:00",
    }
