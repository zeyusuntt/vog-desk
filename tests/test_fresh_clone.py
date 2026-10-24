"""A fresh clone has no data/derived and no model pickle; the desk must still load in SAMPLE_MODE."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from vogdesk import model, sources

ROOT = Path(__file__).resolve().parents[1]


def test_params_model_matches_the_pickle_on_every_development_night():
    pkl = model.MODEL_DIR / "lr_2025.joblib"
    if not pkl.exists() or not (sources.DERIVED / "aqs_hawaii_so2.parquet").exists():
        pytest.skip("needs the local pickle and the full derived data")
    from vogdesk import dataset
    X, _ = dataset.build(sources.load(sources.DERIVED), "dev")
    m, pm = model.load(pkl), model.ParamsModel(json.loads(model.PARAMS.read_text()))
    assert np.max(np.abs(m.probability(X) - pm.probability(X))) < 1e-9
    assert np.max(np.abs(m.raw_probability(X) - pm.raw_probability(X))) < 1e-9


def test_model_load_falls_back_to_the_parameter_export(tmp_path):
    m = model.load(tmp_path / "missing.joblib")
    assert isinstance(m, model.ParamsModel) and m.C == pytest.approx(0.1) and len(m.features) == 12


def test_sources_load_from_sample_slices_covers_the_recorded_nights(monkeypatch):
    monkeypatch.setenv("SAMPLE_MODE", "true")
    src = sources.load()
    manifest = json.loads((ROOT / "samples" / "manifest.json").read_text())
    from vogdesk.features import features_for
    for d in sorted({c["date"] for c in manifest["cases"]}):
        f = features_for(src, pd.Timestamp(d))
        assert not np.isnan(f["log_pahala_max_24h"]) and not np.isnan(f["gfs925_speed_summit"]), d
