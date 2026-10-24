"""Pure-code parts of the forecast desk: action mapping, guidance combination, number validation, flags,
scoring and the trace writer. Inputs are built by hand; nothing here touches the network, the LLM or memory."""
import json
import time
from datetime import datetime, timezone

import pandas as pd
import pytest

from vogdesk.desk.duty_lead import (
    HANDOVER_GAP, MAX_ROUNDS, ORANGE_ANALOG_PPB, RECOMMENDATION, _parse_json, action_level, combine,
    must_handover, requery_targets, template_decision,
)
from vogdesk.desk.evidence import FREE_NUMBERS, evidence_table, numbers_in, template_summary, validate_numbers
from vogdesk.desk.forecaster import data_gap_flags, persistence_flag
from vogdesk.desk.state import Decision, EvidenceItem, Flag, Guidance
from vogdesk.desk.verifier import outcome_of, verification_time
from vogdesk.trace import Trace

UTC = timezone.utc
AS_OF = datetime(2026, 1, 5, 4, 0, tzinfo=UTC)
LEVEL_ORDER = {"normal": 0, "prepare_yellow": 1, "prepare_orange": 2}


def ev(eid: str, query: str, summary: str = "evidence summary", dq=None, **values) -> EvidenceItem:
    return EvidenceItem(id=eid, kind="sql", as_of_utc=AS_OF, source="test", summary=summary,
                        values={"query": query, **values}, dq_flags=dq or [])


def pahala_24h(eid="E1", n_hours=24, max_ppb=84.0, hours75=1, **kw) -> EvidenceItem:
    return ev(eid, "pahala_24h", n_hours=n_hours, max_ppb=max_ppb, hours_at_or_above_75=hours75, **kw)


def streams(lr=None, analog=None, recent=None) -> list[Guidance]:
    out = [Guidance(id="G1", stream="lr", p=lr), Guidance(id="G2", stream="analog", p=analog)]
    if recent is not None:  # the live desk always has a G3 value (climatology when memory is thin)
        out.append(Guidance(id="G3", stream="recent_rate", p=recent))
    return out


def flag(code: str, resolved: bool = False, detail: str = "detail") -> Flag:
    return Flag(code=code, detail=detail, resolved=resolved)


# ---------------------------------------------------------------- action_level

def test_action_level_never_goes_backwards_as_p_rises():
    for t_star in (0.12, 0.25, 0.4):
        last = 0
        for i in range(101):
            level = LEVEL_ORDER[action_level(i / 100, t_star, None)]
            assert level >= last, f"level fell at p={i / 100} with t*={t_star}"
            last = level
        assert last == 2 or 2 * t_star > 1.0


def test_action_level_just_below_t_star_is_normal():
    assert action_level(0.25 - 1e-9, 0.25, None) == "normal"
    assert action_level(0.0, 0.25, None) == "normal"


def test_action_level_exactly_t_star_is_yellow():
    assert action_level(0.25, 0.25, None) == "prepare_yellow"
    assert action_level(0.5 - 1e-9, 0.25, None) == "prepare_yellow"


def test_action_level_exactly_two_t_star_is_orange():
    assert action_level(0.5, 0.25, None) == "prepare_orange"
    assert action_level(1.0, 0.25, None) == "prepare_orange"


def test_action_level_analog_maximum_at_210_gives_orange_even_when_p_is_small():
    assert ORANGE_ANALOG_PPB == 210.0
    assert action_level(0.01, 0.25, 210.0) == "prepare_orange"
    assert action_level(0.01, 0.25, 400.0) == "prepare_orange"


def test_action_level_analog_maximum_below_210_does_not_force_orange():
    assert action_level(0.01, 0.25, 209.9) == "normal"
    assert action_level(0.3, 0.25, 209.9) == "prepare_yellow"


def test_action_level_missing_analog_maximum_is_ignored():
    assert action_level(0.01, 0.25, None) == "normal"


# ---------------------------------------------------------------- combine

def test_combine_normalises_weights():
    g = streams(lr=0.2, analog=0.6)
    assert combine(g, {"lr": 0.5, "analog": 0.5}) == pytest.approx(0.4)
    assert combine(g, {"lr": 1, "analog": 1}) == pytest.approx(0.4)
    assert combine(g, {"lr": 3, "analog": 1}) == pytest.approx(0.3)


def test_combine_ignores_a_stream_with_no_probability():
    g = streams(lr=0.2, analog=None)
    assert combine(g, {"lr": 0.5, "analog": 0.5}) == pytest.approx(0.2)


def test_combine_ignores_negative_weights():
    g = streams(lr=0.2, analog=0.6)
    assert combine(g, {"lr": -1.0, "analog": 1.0}) == pytest.approx(0.6)


def test_combine_ignores_weights_for_streams_not_in_the_guidance():
    g = streams(lr=0.2, analog=0.6)
    assert combine(g, {"lr": 1.0, "persistence": 5.0}) == pytest.approx(0.2)


def test_combine_empty_weights_fall_back_to_the_lr_stream():
    g = streams(lr=0.2, analog=0.6)
    assert combine(g, {}) == pytest.approx(0.2)


def test_combine_all_zero_weights_fall_back_to_the_lr_stream():
    g = streams(lr=0.2, analog=0.6)
    assert combine(g, {"lr": 0.0, "analog": 0.0}) == pytest.approx(0.2)


def test_combine_all_negative_weights_fall_back_to_the_lr_stream():
    g = streams(lr=0.2, analog=0.6)
    assert combine(g, {"lr": -1.0, "analog": -2.0}) == pytest.approx(0.2)


def test_combine_weight_only_on_an_empty_stream_falls_back_to_lr():
    g = streams(lr=0.2, analog=None)
    assert combine(g, {"analog": 1.0}) == pytest.approx(0.2)


def test_combine_without_an_lr_stream_falls_back_to_the_first_stream_with_a_value():
    g = [Guidance(id="G2", stream="analog", p=0.7), Guidance(id="G3", stream="recent_rate", p=0.1)]
    assert combine(g, {}) == pytest.approx(0.7)


def test_combine_result_is_clipped_to_zero_and_one():
    assert combine(streams(lr=1.5), {"lr": 1.0}) == 1.0
    assert combine(streams(lr=-0.2), {"lr": 1.0}) == 0.0


# ---------------------------------------------------------------- validate_numbers

def test_validate_numbers_accepts_an_evidence_number_with_its_citation():
    items = [pahala_24h()]
    assert validate_numbers("Pahala reached a maximum of 84.0 ppb [E1].", items) == []


def test_validate_numbers_rejects_an_evidence_number_without_its_citation():
    problems = validate_numbers("Pahala reached a maximum of 84.0 ppb.", [pahala_24h()])
    assert len(problems) == 1
    assert "without its [E#]" in problems[0]


def test_validate_numbers_rejects_a_number_that_is_not_in_the_evidence():
    problems = validate_numbers("Pahala reached a maximum of 123.4 ppb [E1].", [pahala_24h()])
    assert problems == ["number 123.4 is not in the evidence"]


def test_validate_numbers_free_numbers_need_no_citation():
    text = "Three hours were at or above 75 ppb and 0 were above 210 ppb. The plan uses 90 percent intervals."
    assert validate_numbers(text, [pahala_24h()]) == []
    for n in (0, 1, 3, 10, 75, 210, 90):
        assert float(n) in FREE_NUMBERS


def test_validate_numbers_accepts_a_value_rounded_to_one_decimal():
    items = [pahala_24h(max_ppb=84.04)]
    assert validate_numbers("Pahala reached a maximum of 84.0 ppb [E1].", items) == []


def test_validate_numbers_accepts_a_whole_number_rounding_of_an_evidence_value():
    items = [pahala_24h(max_ppb=84.4)]
    assert validate_numbers("Pahala reached a maximum of 84 ppb [E1].", items) == []


def test_validate_numbers_extra_allowed_numbers_pass_without_a_citation():
    text = "The desk probability is 0.37 for tonight."
    assert validate_numbers(text, [pahala_24h()]) != []
    assert validate_numbers(text, [pahala_24h()], extra_allowed={0.37}) == []


def test_validate_numbers_extra_allowed_does_not_excuse_other_numbers():
    problems = validate_numbers("The desk probability is 0.37 and the max was 555 ppb.", [pahala_24h()], extra_allowed={0.37})
    assert any("555" in p for p in problems)


def test_validate_numbers_checks_each_sentence_for_its_own_citation():
    text = "Pahala reached 84.0 ppb [E1]. Pahala also reached 84.0 ppb again."
    problems = validate_numbers(text, [pahala_24h()])
    assert len(problems) == 1
    assert "without its [E#]" in problems[0]


def test_validate_numbers_ignores_digits_inside_citation_ids():
    assert numbers_in("See [E12] and [G3].") == set()
    assert numbers_in("Values 1.5 and -2 here.") == {1.5, -2.0}


def test_validate_numbers_sees_a_number_at_the_very_end_of_a_sentence():
    # Fixed 23 Oct 2026: a figure that closes a sentence ("... was 999.") used to be invisible to the validator.
    assert numbers_in("The maximum was 999.") == {999.0}
    assert numbers_in("It read 84.0 ppb. Then 12.") == {84.0, 12.0}
    problems = validate_numbers("The maximum was 999.", [pahala_24h()])
    assert any("999" in p for p in problems)


def test_template_summary_cites_every_item_and_passes_validation():
    items = [ev("E1", "pahala_24h", summary="Pahala: last 24 usable hours, max 84.0 ppb", max_ppb=84.0),
             ev("E2", "gfs", summary="GFS 18Z: 2 of 3 leads blow toward Pahala", rows=10)]
    text = template_summary(items)
    assert text == "Pahala: last 24 usable hours, max 84.0 ppb [E1]. GFS 18Z: 2 of 3 leads blow toward Pahala [E2]."
    assert validate_numbers(text, items) == []


def test_evidence_table_lists_ids_and_data_quality_flags():
    items = [ev("E1", "pahala_24h", summary="first"), ev("E2", "hvnp_6h", summary="second", dq=["analyzer_upper_range"])]
    assert evidence_table(items).splitlines() == ["[E1] first", "[E2] second (DQ: analyzer_upper_range)"]


# ---------------------------------------------------------------- _parse_json

def test_parse_json_plain_object():
    assert _parse_json('{"kind": "issue", "weights": {"lr": 1}}') == {"kind": "issue", "weights": {"lr": 1}}


def test_parse_json_inside_a_markdown_fence():
    text = 'Here you go\n```json\n{"kind": "issue", "weights": {"lr": 0.5, "analog": 0.5}}\n```\n'
    assert _parse_json(text) == {"kind": "issue", "weights": {"lr": 0.5, "analog": 0.5}}


def test_parse_json_after_leading_prose():
    assert _parse_json('After weighing the evidence I conclude: {"weights": {"lr": 1.0}, "discussion": "ok"}') == \
        {"weights": {"lr": 1.0}, "discussion": "ok"}


def test_parse_json_invalid_text_returns_none():
    assert _parse_json("no braces here at all") is None
    assert _parse_json("{this is not json}") is None
    assert _parse_json('{"weights": {"lr": 1.0}') is None
    assert _parse_json("") is None


# ---------------------------------------------------------------- requery_targets

def test_requery_targets_deduplicates_and_keeps_first_seen_order():
    flags = [flag("model_analog_disagree"), flag("analog_split"), flag("data_gap")]
    assert requery_targets(flags) == ["hvnp_6h", "gfs", "episode", "pahala_6h", "sounding", "pahala_24h"]


def test_requery_targets_order_follows_the_flag_order():
    assert requery_targets([flag("analog_split"), flag("model_analog_disagree")]) == ["gfs", "pahala_6h", "hvnp_6h", "episode"]


def test_requery_targets_unknown_code_contributes_nothing():
    unknown = Flag.model_construct(code="made_up", detail="x", evidence_ids=[], resolved=False, resolution="")
    assert requery_targets([unknown]) == []
    assert requery_targets([unknown, flag("stale_data")]) == ["pahala_6h"]


def test_requery_targets_analogs_sparse_alone_is_empty():
    assert requery_targets([flag("analogs_sparse")]) == []
    assert requery_targets([]) == []


# ---------------------------------------------------------------- must_handover

BIG_GAP = streams(lr=0.1, analog=0.6)


@pytest.mark.parametrize("code", ["model_analog_disagree", "analog_split"])
def test_must_handover_after_max_rounds_with_a_big_gap_and_an_unresolved_flag(code):
    reason = must_handover(BIG_GAP, [flag(code)], MAX_ROUNDS)
    assert reason is not None
    assert str(HANDOVER_GAP)[:3] in reason and "review rounds" in reason


def test_must_handover_is_none_before_the_last_round_even_with_a_big_gap():
    for r in range(MAX_ROUNDS):
        assert must_handover(BIG_GAP, [flag("model_analog_disagree")], r) is None


def test_must_handover_is_none_when_the_flag_is_resolved():
    assert must_handover(BIG_GAP, [flag("model_analog_disagree", resolved=True)], MAX_ROUNDS) is None


def test_must_handover_is_none_when_the_gap_is_not_larger_than_the_limit():
    exactly = streams(lr=0.2, analog=0.5)  # 0.5 - 0.2 == 0.3 == HANDOVER_GAP in floating point
    assert must_handover(exactly, [flag("model_analog_disagree")], MAX_ROUNDS) is None
    assert must_handover(streams(lr=0.5, analog=0.2), [flag("analog_split")], MAX_ROUNDS) is None


def test_must_handover_is_none_for_other_flag_codes_or_no_analog_value():
    assert must_handover(BIG_GAP, [flag("persistence_contradiction")], MAX_ROUNDS) is None
    assert must_handover(streams(lr=0.1, analog=None), [flag("model_analog_disagree")], MAX_ROUNDS) is None
    assert must_handover([Guidance(id="G1", stream="lr", p=0.1)], [flag("model_analog_disagree")], MAX_ROUNDS) is None


def test_must_handover_missing_gfs_after_max_rounds_names_the_gfs_run():
    gfs_gap = flag("data_gap", detail="GFS 18Z run not available at issue")
    reason = must_handover(streams(lr=0.2, analog=0.2), [gfs_gap], MAX_ROUNDS)
    assert reason is not None and "GFS" in reason


def test_must_handover_missing_gfs_is_none_before_max_rounds_or_when_resolved():
    assert must_handover(streams(lr=0.2), [flag("data_gap", detail="GFS 18Z run not available at issue")], MAX_ROUNDS - 1) is None
    assert must_handover(streams(lr=0.2), [flag("data_gap", resolved=True, detail="GFS 18Z run not available at issue")], MAX_ROUNDS) is None


def test_must_handover_other_data_gaps_do_not_force_a_handover():
    sounding_gap = flag("data_gap", detail="no Hilo sounding in the last 18 hours")
    assert must_handover(streams(lr=0.2), [sounding_gap], MAX_ROUNDS) is None


# ---------------------------------------------------------------- template_decision

def test_template_decision_base_follows_the_stream_order_recent_before_lr():
    # v3 (24 Oct 2026): the base probability is the first available stream in memory model, recent rate, frozen model order.
    d = template_decision(streams(lr=0.2, analog=0.4, recent=0.3), [], 0.25, None, "issue_with_caveat", None)
    assert isinstance(d, Decision)
    assert d.weights == {"recent_rate": 1.0} and d.base_p == pytest.approx(0.3) and d.adjustment == 0.0
    assert d.p == pytest.approx(0.3)
    assert d.level == "prepare_yellow"
    assert d.recommendation == RECOMMENDATION["prepare_yellow"]


def test_template_decision_prefers_the_memory_model_when_present():
    g = streams(lr=0.2, analog=0.4, recent=0.3) + [Guidance(id="G2", stream="memory_model", p=0.45)]
    d = template_decision(g, [], 0.25, None, "issue", None)
    assert d.weights == {"memory_model": 1.0} and d.p == pytest.approx(0.45)


def test_template_decision_falls_back_to_lr_when_memory_streams_are_missing():
    d = template_decision(streams(lr=0.2, analog=None, recent=None), [], 0.25, None, "issue", None)
    assert d.weights == {"lr": 1.0}
    assert d.p == pytest.approx(0.2)
    assert d.level == "normal"
    only_lr = template_decision([Guidance(id="G1", stream="lr", p=0.2)], [], 0.25, None, "issue", None)
    assert only_lr.weights == {"lr": 1.0}


def test_template_decision_discussion_mentions_the_fallback_and_cites_guidance_ids():
    d = template_decision(streams(lr=0.2, analog=0.4, recent=0.1), [flag("analog_split")], 0.25, None, "issue", None)
    assert "template" in d.discussion
    for gid in ("[G1]", "[G2]", "[G3]"):
        assert gid in d.discussion
    assert "analog_split" in d.discussion
    assert d.cites == ["G1", "G2", "G3"]


def test_template_decision_omits_the_analog_sentence_when_there_is_no_analog_value():
    d = template_decision(streams(lr=0.2, analog=None, recent=0.1), [], 0.25, None, "issue", None)
    assert "[G2]" not in d.discussion
    assert "[G1]" in d.discussion and "[G3]" in d.discussion


def test_template_decision_passes_kind_and_handover_reason_through():
    d = template_decision(streams(lr=0.1, analog=0.6), [], 0.25, None, "handover", "the models disagree")
    assert d.kind == "handover"
    assert d.handover_reason == "the models disagree"
    plain = template_decision(streams(lr=0.1, analog=0.6), [], 0.25, None, "issue_with_caveat", None)
    assert plain.kind == "issue_with_caveat"
    assert plain.handover_reason is None


def test_template_decision_high_analog_maximum_gives_orange_with_low_p():
    d = template_decision(streams(lr=0.02, analog=0.04), [], 0.25, 250.0, "issue", None)
    assert d.p == pytest.approx(0.02)  # base is the frozen model here (no memory model, no recent rate)
    assert d.level == "prepare_orange"


def test_template_decision_survives_a_recent_rate_stream_with_no_value():
    # Fixed 23 Oct 2026: the fallback must always produce a decision, even when G3 has no value.
    g = [Guidance(id="G1", stream="lr", p=0.2), Guidance(id="G3", stream="recent_rate", p=None)]
    dec = template_decision(g, [], 0.25, None, "issue", None)
    assert dec.p == pytest.approx(0.2) and "[G3]" not in dec.discussion


# ---------------------------------------------------------------- persistence_flag

def test_persistence_flag_fires_when_recent_hours_were_high_but_both_guidances_are_low():
    f = persistence_flag([pahala_24h(hours75=1)], 0.10, 0.12)
    assert f is not None
    assert f.code == "persistence_contradiction"
    assert f.evidence_ids == ["E1"]
    assert "1 of the last 24 hours" in f.detail


def test_persistence_flag_fires_for_low_model_when_there_is_no_analog_value():
    assert persistence_flag([pahala_24h(hours75=2)], 0.10, None) is not None


def test_persistence_flag_does_not_fire_when_one_guidance_is_not_low():
    assert persistence_flag([pahala_24h(hours75=1)], 0.10, 0.20) is None
    assert persistence_flag([pahala_24h(hours75=1)], 0.15, 0.10) is None


def test_persistence_flag_does_not_fire_when_no_recent_hour_reached_75():
    assert persistence_flag([pahala_24h(max_ppb=30.0, hours75=0)], 0.05, 0.05) is None


def test_persistence_flag_fires_in_the_reverse_case_of_quiet_recent_and_high_guidance():
    f = persistence_flag([pahala_24h(max_ppb=3.0, hours75=0)], 0.60, 0.70)
    assert f is not None
    assert f.code == "persistence_contradiction"
    assert f.evidence_ids == ["E1"]
    assert "above 0.5" in f.detail


def test_persistence_flag_reverse_case_needs_both_guidances_above_half_and_a_quiet_night():
    quiet = pahala_24h(max_ppb=3.0, hours75=0)
    assert persistence_flag([quiet], 0.60, None) is None
    assert persistence_flag([quiet], 0.60, 0.50) is None
    assert persistence_flag([pahala_24h(max_ppb=10.0, hours75=0)], 0.60, 0.70) is None


def test_persistence_flag_is_none_without_a_usable_pahala_24h_item():
    assert persistence_flag([], 0.05, 0.05) is None
    assert persistence_flag([ev("E1", "pahala_6h", n_hours=6, max_ppb=90.0, hours_at_or_above_75=3)], 0.05, 0.05) is None
    assert persistence_flag([pahala_24h(n_hours=0, hours75=0, max_ppb=0.0)], 0.9, 0.9) is None


# ---------------------------------------------------------------- data_gap_flags

def test_data_gap_flags_missing_gfs_run():
    flags = data_gap_flags([ev("E6", "gfs", rows=0)])
    assert [(f.code, f.evidence_ids) for f in flags] == [("data_gap", ["E6"])]
    assert "GFS" in flags[0].detail


def test_data_gap_flags_missing_sounding():
    flags = data_gap_flags([ev("E7", "sounding", n=0)])
    assert [(f.code, f.evidence_ids) for f in flags] == [("data_gap", ["E7"])]
    assert "sounding" in flags[0].detail


def test_data_gap_flags_short_pahala_24h_series():
    flags = data_gap_flags([pahala_24h(eid="E1", n_hours=13)])
    assert [(f.code, f.evidence_ids) for f in flags] == [("data_gap", ["E1"])]
    assert "13 of 24" in flags[0].detail
    assert data_gap_flags([pahala_24h(n_hours=14)]) == []


def test_data_gap_flags_item_with_dq_flags_gives_dq_failed():
    flags = data_gap_flags([ev("E2", "pahala_6h", dq=["analyzer_upper_range"], n_hours=6)])
    assert [(f.code, f.evidence_ids) for f in flags] == [("dq_failed", ["E2"])]
    assert "analyzer_upper_range" in flags[0].detail


def test_data_gap_flags_healthy_evidence_gives_no_flags():
    items = [pahala_24h(), ev("E6", "gfs", rows=120), ev("E7", "sounding", launch_utc="2026-01-05 00:00Z"),
             ev("E5", "episode", n_known=3)]
    assert data_gap_flags(items) == []


def test_data_gap_flags_one_flag_per_problem_across_items():
    items = [pahala_24h(eid="E1", n_hours=5, dq=["analyzer_upper_range"]), ev("E6", "gfs", rows=0), ev("E7", "sounding", n=0)]
    flags = data_gap_flags(items)
    assert [(f.code, f.evidence_ids[0]) for f in flags] == [("data_gap", "E1"), ("dq_failed", "E1"), ("data_gap", "E6"), ("data_gap", "E7")]


# ---------------------------------------------------------------- outcome_of / verification_time

@pytest.mark.parametrize("p,event,expected", [
    (0.30, True, "hit"), (0.30, False, "false_alarm"), (0.20, True, "miss"), (0.20, False, "correct_negative"),
])
def test_outcome_of_the_four_contingency_cells(p, event, expected):
    assert outcome_of({"kind": "issue", "p": p}, event, 0.25) == expected


def test_outcome_of_warns_exactly_at_t_star():
    assert outcome_of({"kind": "issue", "p": 0.25}, True, 0.25) == "hit"
    assert outcome_of({"kind": "issue", "p": 0.25 - 1e-9}, True, 0.25) == "miss"


def test_outcome_of_caveat_decisions_are_scored_like_issued_ones():
    assert outcome_of({"kind": "issue_with_caveat", "p": 0.4}, False, 0.25) == "false_alarm"


def test_outcome_of_unknown_event_is_excluded():
    assert outcome_of({"kind": "issue", "p": 0.9}, None, 0.25) == "excluded"


@pytest.mark.parametrize("event", [True, False])
def test_outcome_of_handover_is_counted_separately_whatever_happened(event):
    assert outcome_of({"kind": "handover", "p": 0.9}, event, 0.25) == "handover"
    assert outcome_of({"kind": "handover", "p": 0.01}, event, 0.25) == "handover"


def test_verification_time_is_noon_hst_next_day_plus_the_publication_lag():
    got = verification_time("2026-01-05")
    assert got == pd.Timestamp("2026-01-07 00:00", tz="UTC")
    assert got.tzinfo is not None


def test_verification_time_ignores_the_time_of_day_of_the_issue_date():
    assert verification_time(pd.Timestamp("2026-01-05 15:30")) == verification_time("2026-01-05")


# ---------------------------------------------------------------- Trace

def test_trace_two_writes_make_two_jsonl_lines_with_increasing_seq(tmp_path):
    path = tmp_path / "case.jsonl"
    tr = Trace("case-1", path)
    tr.write("EvidenceAgent", "recent_so2", output={"n": 1})
    tr.write("DutyLead", "decide", output={"n": 2})
    raw = path.read_text().splitlines()
    assert len(raw) == 2
    rows = [json.loads(x) for x in raw]
    assert [r["seq"] for r in rows] == [0, 1]
    assert [(r["agent"], r["step"]) for r in rows] == [("EvidenceAgent", "recent_so2"), ("DutyLead", "decide")]
    assert all(r["case_id"] == "case-1" for r in rows)
    assert tr.lines() == rows


def test_trace_hashes_inputs_stably_and_leaves_missing_inputs_null(tmp_path):
    tr = Trace("case-2", tmp_path / "sub" / "case.jsonl")
    a = tr.write("A", "s", inputs={"x": 1, "y": 2})
    b = tr.write("A", "s", inputs={"y": 2, "x": 1})
    c = tr.write("A", "s")
    assert a["inputs_hash"] == b["inputs_hash"]
    assert c["inputs_hash"] is None


def test_trace_lines_is_empty_before_anything_is_written(tmp_path):
    assert Trace("case-3", tmp_path / "none.jsonl").lines() == []


def test_trace_step_records_duration_and_the_output_set_inside_the_block(tmp_path):
    tr = Trace("case-4", tmp_path / "case.jsonl")
    with tr.step("EvidenceAgent", "gfs", inputs={"d": "2026-01-05"}) as rec:
        time.sleep(0.03)
        rec["output"] = {"rows": 42}
        rec["sql"] = ["select 1"]
    [line] = tr.lines()
    assert line["agent"] == "EvidenceAgent" and line["step"] == "gfs"
    assert line["output"] == {"rows": 42}
    assert line["sql"] == ["select 1"]
    assert line["duration_ms"] >= 25
    assert line["inputs_hash"] is not None


def test_trace_step_still_writes_its_line_when_the_block_raises(tmp_path):
    tr = Trace("case-5", tmp_path / "case.jsonl")
    with pytest.raises(RuntimeError):
        with tr.step("DutyLead", "decide") as rec:
            rec["output"] = {"partial": True}
            raise RuntimeError("boom")
    [line] = tr.lines()
    assert line["step"] == "decide"
    assert line["output"] == {"partial": True}



def test_data_gap_flags_mark_a_hole_right_before_issue_as_stale_data():
    from vogdesk.desk.forecaster import data_gap_flags
    fresh = ev("E1", "pahala_24h", summary="Pahala 24 h", n_hours=24, latest_hour_hst="16:00")
    stale = ev("E1", "pahala_24h", summary="Pahala 24 h", n_hours=20, latest_hour_hst="12:00")
    assert [f.code for f in data_gap_flags([fresh])] == []
    codes = [f.code for f in data_gap_flags([stale])]
    assert codes == ["stale_data"]



def test_gate_adjustment_only_honours_adjustments_when_a_flag_was_raised():
    from vogdesk.desk.duty_lead import MAX_ADJUSTMENT, gate_adjustment
    assert gate_adjustment(-0.1, []) == 0.0
    flag = Flag(code="stale_data", detail="hole before issue")
    assert gate_adjustment(-0.1, [flag]) == pytest.approx(-0.1)
    assert gate_adjustment(-0.5, [flag]) == pytest.approx(-MAX_ADJUSTMENT)
    assert gate_adjustment(0.4, [flag]) == pytest.approx(MAX_ADJUSTMENT)



def test_review_flags_reports_the_same_gap_once_after_a_requery():
    from vogdesk.analogs import AnalogResult
    from vogdesk.desk.forecaster import review_flags
    from datetime import datetime, timezone
    a = AnalogResult(p=0.3, lo=0.1, hi=0.5, n=10, n_events=3, analog_ids=[], analog_nights=[], max_ppb_seen=None, tags_used=[],
                     query_text="", as_of_utc=datetime(2026, 1, 1, tzinfo=timezone.utc), trace=[], split=False, fallback_level=0)
    e1 = ev("E7", "sounding", summary="no sounding", n=0)
    e2 = ev("E9", "sounding", summary="no sounding", n=0)  # the re-query copy
    flags = review_flags([e1, e2], 0.2, a, [], p_memory=0.25)
    assert [f.code for f in flags].count("data_gap") == 1
