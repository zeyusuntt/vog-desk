"""SAMPLE_MODE replays the recorded cases byte for byte, with no network and no keys.

Skipped when the recordings are absent (run scripts/record_samples.py first). The replay uses the local
Parquet backend, the in-process memory seed and the recorded model replies; every model call must come
from the recording (cached) and the decision must match the recorded case.
"""
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "samples" / "manifest.json"


@pytest.mark.skipif(not MANIFEST.exists(), reason="no recorded samples")
@pytest.mark.parametrize("review", [True, False])
def test_recorded_case_replays_from_samples_without_network(monkeypatch, review):
    manifest = json.loads(MANIFEST.read_text())
    case = next(c for c in manifest["cases"] if c["review"] is review)
    monkeypatch.setenv("SAMPLE_MODE", "true")
    monkeypatch.setenv("SAMPLE_ROOT", str(ROOT / "samples" / "llm"))
    monkeypatch.delenv("RECORD_SAMPLES", raising=False)
    monkeypatch.setenv("METERLESS_RUNTIME_TOKEN", "unused-in-sample-mode")
    sys.path.insert(0, str(ROOT))
    from run import build_context
    from vogdesk.desk.graph import run_case

    ctx = build_context("local", "sample", "fake", ROOT / manifest["memory_seed"], float(manifest["t_star"]))
    state = run_case(ctx, case["date"], review_enabled=review, case_id=f"replay-{case['date']}-{'full' if review else 'noreview'}")
    recorded = json.loads((ROOT / case["case"]).read_text())
    assert all(c["cached"] for c in state["llm_calls"]), "a model call was not served from the recording"
    assert state["decision"]["p"] == pytest.approx(recorded["decision"]["p"])
    assert state["decision"]["level"] == recorded["decision"]["level"]
    assert state["decision"]["kind"] == recorded["decision"]["kind"]
    assert [g["p"] for g in state["guidance"]] == [g["p"] for g in recorded["guidance"]]
