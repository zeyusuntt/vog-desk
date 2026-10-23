"""Write data/MANIFEST.tsv: every raw file with its SHA-256, size and source."""
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES = {
    "aqs": "https://aqs.epa.gov/aqsweb/airdata/ (hourly_42401_YYYY.zip)",
    "airnow": "https://files.airnowtech.org/airnow/YYYY/YYYYMMDD/HourlyData_YYYYMMDDHH.dat (Hawaii rows only)",
    "hans": "https://volcanoes.usgs.gov/hans-public/api/search/search (obsAbbr=hvo, volcCd=hi3)",
    "usgs": "https://www.usgs.gov/volcanoes/kilauea/science/eruption-information",
    "doh": "https://health.hawaii.gov/cab/notification-of-exceedance-of-a-national-ambient-air-quality-standard/ (exceedances_2025.pdf, exceedances_2026_10_15_17.pdf)",
    "raob": "https://mesonet.agron.iastate.edu/cgi-bin/request/raob.py (station PITO)",
    "gfs": "https://noaa-gfs-bdp-pds.s3.amazonaws.com/ (pgrb2.0p25, 18Z runs, f010/f016/f022, point values)",
}
lines = ["path\tbytes\tsha256\tsource"]
for p in sorted((ROOT / "data" / "raw").rglob("*")):
    if p.is_file() and not p.name.startswith("_") and p.stat().st_size > 64:
        h = hashlib.sha256(p.read_bytes()).hexdigest()
        src = SOURCES.get(p.relative_to(ROOT / "data" / "raw").parts[0], "")
        lines.append(f"{p.relative_to(ROOT)}\t{p.stat().st_size}\t{h}\t{src}")
(ROOT / "data" / "MANIFEST.tsv").write_text("\n".join(lines) + "\n")
print(f"{len(lines) - 1} files listed")
