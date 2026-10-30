import json

import pytest

from quipu import graph as graph_mod
from quipu import promote as promote_mod
from quipu.store import iter_entries

GOOD = [{
    "kind": "decision",
    "summary": "DOCP 3200 is the stable memory config; 3600 threw 649 CCE events in one boot",
    "body": "Dropped FCLK to 1600 to keep 1:1 with 3200.",
    "evidence": "journalctl -b -1 | grep -c MC25_STATUS -> 649",
    "importance": 0.9,
    "ask": "why did the box reboot?",
    "entities": [{"name": "trx40", "kind": "machine"}, {"name": "docp-3200", "kind": "config"}],
    "edges": [{"src": "trx40", "predicate": "measured_as", "dst": "docp-3200"}],
}]

WEAK = [{
    "kind": "fact",
    "summary": "The user seems to like concise answers",
    "importance": 0.2,
    "evidence": "",
}]


def test_promoted_requires_threshold_and_evidence(cfg):
    proposals = promote_mod.parse_response(json.dumps({"entries": GOOD + WEAK}))
    written = promote_mod.apply(proposals, cfg, agent="glitch", project="wraith")
    assert [e.state for e in written] == ["promoted", "raw"]


def test_graph_only_learns_from_promoted(cfg):
    proposals = promote_mod.parse_response(json.dumps({"entries": GOOD + WEAK}))
    promote_mod.apply(proposals, cfg, agent="glitch")
    names = {n["name"] for n in graph_mod.neighbors(cfg, "trx40")["nodes"]}
    assert names == {"trx40", "docp-3200"}


def test_parse_response_tolerates_fences_and_prose():
    raw = 'Sure! Here you go:\n```json\n{"entries": ' + json.dumps(GOOD) + '}\n```\nHope that helps'
    assert promote_mod.parse_response(raw)[0].kind == "decision"


def test_parse_response_rejects_garbage():
    with pytest.raises(ValueError):
        promote_mod.parse_response("no json here")


def test_reflect_uses_provider(cfg):
    provider = promote_mod.FakeProvider(GOOD)
    proposals = promote_mod.reflect("a transcript about ram", cfg, provider)
    assert proposals[0].entities[0] == ("trx40", "machine")


def test_unknown_predicate_is_skipped_not_fatal(cfg):
    bad = dict(GOOD[0])
    bad["edges"] = [{"src": "trx40", "predicate": "vibes_with", "dst": "docp-3200"}]
    written = promote_mod.apply(promote_mod.parse_response(json.dumps({"entries": [bad]})), cfg)
    assert written[0].state == "promoted"
    assert graph_mod.timeline(cfg, "trx40") == []


def test_record_capture_is_raw_and_searchable(cfg):
    from quipu import index as index_mod
    e = promote_mod.record_capture(cfg, "raw thought about exporters", agent="glitch")
    assert e.state == "raw" and e.kind == "capture"
    index_mod.reindex(cfg.root, cfg.db_path)
    assert index_mod.search(cfg.db_path, "exporters")


def test_capture_with_empty_summary_still_writes(cfg):
    e = promote_mod.record_capture(cfg, "x")
    assert any(x.id == e.id for _p, x in iter_entries(cfg.root))


def test_a_proposal_with_a_quoted_summary_is_written_as_a_handle(cfg):
    """A measured reflect run emitted exactly this shape and it went straight to disk (#85)."""
    quoted = dict(GOOD[0])
    quoted["summary"] = '"' + GOOD[0]["summary"] + '"'
    written = promote_mod.apply(promote_mod.parse_response(json.dumps({"entries": [quoted]})), cfg)
    assert written[0].summary == GOOD[0]["summary"]


def test_a_proposal_with_a_blob_summary_takes_the_body_statement(cfg):
    blob = dict(GOOD[0])
    blob["summary"] = '{"findings": [{"claim": "the lightbox lives in HistoryPage"}]}'
    blob["body"] = "The lightbox is rendered in HistoryPage.tsx around line 106, with an onClick handler."
    written = promote_mod.apply(promote_mod.parse_response(json.dumps({"entries": [blob]})), cfg)
    assert written[0].summary == "The lightbox is rendered in HistoryPage.tsx around line 106, with an onClick handler."


def test_a_blob_proposal_with_no_prose_body_keeps_the_entry(cfg):
    """The body has no statement either, so the entry survives with the text it arrived with: a
    mis-shaped handle costs the handle, never the memory."""
    blob = dict(GOOD[0])
    blob["summary"] = '{"findings": []}'
    blob["body"] = '{"other": 1}'
    written = promote_mod.apply(promote_mod.parse_response(json.dumps({"entries": [blob]})), cfg)
    assert written[0].summary == '{"findings": []}'
