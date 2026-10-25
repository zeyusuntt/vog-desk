"""Two defects found on the first live nights (DEVLOG, 24 October 2026).

1. A data_gap flag was marked "gap filled on re-query" when any re-queried table returned rows, although the
   re-query of the missing source itself was still empty.
2. The duty lead's number check rejected numbers that appear in the review's own flag details (the analog
   split's "6 events and 14 quiet nights"), so a reply citing them fell back to the template.
"""
from datetime import datetime, timezone

from vogdesk.desk.duty_lead import decision_numbers, resolve_flag
from vogdesk.desk.state import EvidenceItem, Flag, Guidance

AS_OF = datetime(2026, 10, 25, 4, tzinfo=timezone.utc)


def item(eid: str, query: str, summary: str, rows_hash: str = "h", **values) -> EvidenceItem:
    return EvidenceItem(id=eid, kind="sql", as_of_utc=AS_OF, source="test", summary=summary, rows_hash=rows_hash, values={"query": query, **values})


SOUNDING_GAP = item("E7", "sounding", "Hilo sounding: none in the last 18 hours (data gap)", n=0)
PRIOR = {"E7": SOUNDING_GAP}


def gap_flag() -> Flag:
    return Flag(code="data_gap", detail="no Hilo sounding in the last 18 hours", evidence_ids=["E7"])


def test_a_gap_stays_open_when_the_missing_source_is_still_empty_on_re_query():
    requeried = [item("E8", "gfs", "GFS 18Z ...", rows=105), item("E10", "sounding", "Hilo sounding: none (data gap)", n=0),
                 item("E11", "pahala_24h", "Pahala: last 24 usable hours ...", n_hours=24)]
    resolved, text = resolve_flag(gap_flag(), PRIOR, requeried)
    assert not resolved
    assert "E10" in text and "still" in text


def test_a_gap_closes_when_the_missing_source_returns_rows():
    requeried = [item("E8", "gfs", "GFS 18Z ...", rows=105), item("E10", "sounding", "Hilo sounding 2026-10-25 00:00Z: inversion base at 1100 m", n=1)]
    resolved, text = resolve_flag(gap_flag(), PRIOR, requeried)
    assert resolved
    assert "E10" in text and "E8" not in text


def test_a_disagreement_flag_is_settled_by_the_same_rows_as_before():
    prior = {"E6": item("E6", "gfs", "GFS ...", rows_hash="abc")}
    fl = Flag(code="analog_split", detail="6 events and 14 quiet nights among 20 analogs", evidence_ids=[])
    resolved, text = resolve_flag(fl, prior, [item("E8", "gfs", "GFS ...", rows_hash="abc")])
    assert resolved and "same rows" in text


def test_numbers_in_flag_details_may_be_cited_by_the_duty_lead():
    guidance = [Guidance(id="G2", stream="memory_model", p=0.2456)]
    flags = [Flag(code="analog_split", detail="6 events and 14 quiet nights among 20 analogs", resolved=True,
                  resolution="re-query returned the same rows (E8, E9); the disagreement is real, not a data error")]
    allowed = decision_numbers(guidance, flags, t_star=0.18, base=0.2456, analog_max=None)
    assert {6.0, 14.0, 20.0} <= allowed
    assert 0.25 in allowed and 0.36 in allowed
