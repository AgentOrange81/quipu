"""#109 — the promotion gate asks whether evidence exists; `evidence_kind` records what it IS.

The gate stays as it was, because a 9-byte "see log" is a weak memory, not a false one: refusing it
would cost the claim and still leave the corpus unable to say how much of it is re-checkable. The
classification is deterministic — no model call, no drift — and the property is recorded, indexed and
filterable, so "present" and "re-checkable" stop being the same word.
"""
import json

import pytest

from quipu import evidence as evidence_mod
from quipu import export as export_mod
from quipu import promote as promote_mod
from quipu.store import Entry, iter_entries


@pytest.mark.parametrize("text, kind", [
    # commands: something a person could re-run
    ("`quipu reindex` dropped it", "command"),
    ("$ systemctl --user restart quipu-capture", "command"),
    ("ps -eo comm,args | grep nuq-worker", "command"),
    ("pytest tests/test_hook.py", "command"),
    ("tail ~/admin/logs/quipu-capture.log", "command"),
    ("sqlite3 index.db 'select count(*) from entries'", "command"),
    # paths: something a person could open
    ("docs/USING.md:316-318", "path"),
    ("quipu/hook.py:76", "path"),
    ("MEDIA:/home/dirk/reports/strata-3card-2026-11-05.html", "path"),
    ("`docs/moe-cache.md` lives on a PR branch", "path"),
    ("moe-bench.py did not exist on wraith", "path"),
    # urls
    ("see https://example.com/runs/42715230290", "url"),
    # claims: prose that points at nothing re-checkable
    ("see log", "claim"),
    ("9 bytes", "claim"),
    ("378 passed, 1 xfailed", "claim"),
    ("acceptance: 0.697 (XXS) vs 0.871 (IQ3_S)", "claim"),
    ("", "claim"),
])
def test_the_evidence_kind_is_classified_from_the_text(text, kind):
    assert evidence_mod.classify(text) == kind


def test_a_proposal_that_cites_a_command_is_promoted_and_labelled(cfg):
    proposal = {"kind": "lesson", "summary": "reindex drops an edge whose carrier is gone",
                "evidence": "`quipu reindex` printed 'skipped 3 edge specs'",
                "importance": 0.9, "ask": "what happens to an edge with no carrier?",
                "entities": [], "edges": [], "confidence": 0.8}
    written = promote_mod.apply(promote_mod.parse_response(json.dumps({"entries": [proposal]})), cfg)
    assert written[0].evidence_kind == "command"

    # markdown is the truth: the kind survives a round trip and a rebuild
    path = next(cfg.root.glob(f"notes/{written[0].id}*.md"))
    assert "evidence_kind: command" in path.read_text(encoding="utf-8")
    assert Entry.from_markdown(path.read_text(encoding="utf-8")).evidence_kind == "command"


def test_thin_evidence_still_promotes_and_says_so(cfg):
    """The decision that closed #109: a weak memory is a memory. The gate does not change."""
    proposal = {"kind": "fact", "summary": "it worked", "evidence": "see log", "importance": 0.9,
                "ask": "did the fix actually make it work?", "entities": [], "edges": [], "confidence": 0.9}
    written = promote_mod.apply(promote_mod.parse_response(json.dumps({"entries": [proposal]})), cfg)
    assert written[0].state == "promoted"
    assert written[0].evidence_kind == "claim"


def test_the_sft_export_can_filter_on_evidence_without_touching_the_vault(cfg):
    good = {"kind": "fact", "summary": "the box has three cards", "evidence": "`nvidia-smi` shows 3",
            "importance": 0.9, "ask": "how many GPUs does it have?", "entities": [], "edges": [],
            "confidence": 0.9}
    thin = {"kind": "fact", "summary": "it works now", "evidence": "see log", "importance": 0.9,
            "ask": "did the change actually take effect?", "entities": [], "edges": [], "confidence": 0.9}
    promote_mod.apply(promote_mod.parse_response(json.dumps({"entries": [good, thin]})), cfg)

    out = cfg.root.parent / "sft-all.jsonl"
    assert export_mod.export_sft(cfg, out) == 2
    filtered = cfg.root.parent / "sft-filtered.jsonl"
    assert export_mod.export_sft(cfg, filtered, evidence_kinds={"command", "path", "url"}) == 1

    # and the vault still holds both: filtering is a view, not a delete
    assert len([e for _p, e in iter_entries(cfg.root)]) == 2


def test_stats_reports_the_evidence_mix(cfg):
    entries = [
        {"kind": "fact", "summary": "a", "evidence": "`quipu stats`", "importance": 0.9,
         "ask": "what do the stats command report?", "entities": [], "edges": [], "confidence": 0.9},
        {"kind": "fact", "summary": "b", "evidence": "see log", "importance": 0.9,
         "ask": "what does the evidence in the log say?", "entities": [], "edges": [], "confidence": 0.9},
    ]
    promote_mod.apply(promote_mod.parse_response(json.dumps({"entries": entries})), cfg)
    report = export_mod.dataset_report(cfg)
    assert report["evidence_by_kind"] == {"command": 1, "claim": 1}
    assert report["evidence_bytes"]["min"] > 0
