"""scripts/prove.py re-checks the headline numbers from committed files; every check must pass."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_every_headline_number_is_reproduced_from_committed_files():
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "prove.py")], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    assert "FAIL" not in r.stdout
