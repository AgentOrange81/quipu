"""`quipu hook` (#3) — the session-end adapter, and the failure paths that make it worth having.

A hook that exits 0 after writing nothing is the exact failure this project exists because of, so these
tests are mostly about the failure paths:

- a fake provider writes the entries and exits 0;
- a provider that raises exits 1, says why, and leaves the vault exactly as it was;
- a provider that never answers is cut off by --timeout instead of blocking session end;
- `QUIPU_HOOK_DRY_RUN=1` prints the prompt and writes nothing;
- a missing transcript is a failure, not an empty pass.
"""
import json
import time

import pytest

from quipu import hook as hook_mod
from quipu import index as index_mod
from quipu import promote as promote_mod
from quipu.config import load
from quipu.store import iter_entries

PROPOSAL = {
    "kind": "conclusion",
    "summary": "the hook wrote this entry at session end",
    "evidence": "pytest run of tests/test_hook.py",
    "importance": 0.9,
    "ask": "what does the session-end hook write?",
    "entities": [],
    "edges": [],
    "confidence": 0.8,
}


def _fake(tmp_path, proposals=None):
    path = tmp_path / "fake.json"
    path.write_text(json.dumps(proposals if proposals is not None else [PROPOSAL]), encoding="utf-8")
    return promote_mod.FakeProvider(json.loads(path.read_text(encoding="utf-8")))


def _transcript(tmp_path, text="user: we fixed the hook\nassistant: entries written\n"):
    path = tmp_path / "session.md"
    path.write_text(text, encoding="utf-8")
    return path


def _entries(cfg):
    return [e for _p, e in iter_entries(cfg.root)]


def test_a_successful_pass_writes_the_entry_and_indexes_it(tmp_path):
    cfg = load(tmp_path / "vault", create=True)
    result = hook_mod.run(cfg, _transcript(tmp_path), _fake(tmp_path), agent="glitch", project="quipu")

    assert len(result.written) == 1 and result.promoted == 1
    assert "1 entries, 1 promoted" in result.line()
    stored = _entries(cfg)
    assert [e.summary for e in stored] == ["the hook wrote this entry at session end"]
    assert stored[0].evidence, "a promoted entry without evidence is a bug"
    # indexed, not merely on disk: an MCP/framework caller cannot run reindex afterwards
    hits = index_mod.search(cfg.db_path, "session end hook")
    assert [h["id"] for h in hits] == [stored[0].id]


def test_a_provider_that_raises_exits_non_zero_and_writes_nothing(tmp_path):
    cfg = load(tmp_path / "vault", create=True)

    class Broken:
        def complete(self, prompt):
            raise ConnectionResetError("connection reset by peer")

    with pytest.raises(hook_mod.HookError) as caught:
        hook_mod.run(cfg, _transcript(tmp_path), Broken())
    assert "reflection failed" in str(caught.value)
    assert "ConnectionResetError" in str(caught.value)
    assert _entries(cfg) == [], "a failed pass must not leave a partial entry behind"


def test_a_hung_provider_is_cut_off_by_the_timeout(tmp_path):
    cfg = load(tmp_path / "vault", create=True)

    class Silent:
        def complete(self, prompt):
            time.sleep(30)
            return "[]"

    started = time.monotonic()
    with pytest.raises(hook_mod.HookError) as caught:
        hook_mod.run(cfg, _transcript(tmp_path), Silent(), timeout=0.2)
    assert "timed out after 0.2s" in str(caught.value)
    assert time.monotonic() - started < 5, "the timeout must return immediately, not after the call"
    assert _entries(cfg) == []


def test_dry_run_prints_the_prompt_and_writes_nothing(tmp_path):
    cfg = load(tmp_path / "vault", create=True)

    class Exploding:
        def complete(self, prompt):  # pragma: no cover - it must never be called
            raise AssertionError("dry run must not call the model")

    result = hook_mod.run(cfg, _transcript(tmp_path), Exploding(), dry_run=True)
    assert "transcript" in result.prompt.lower() or "session" in result.prompt.lower()
    assert result.written == []
    assert _entries(cfg) == []


def test_the_env_var_is_the_documented_switch():
    assert hook_mod.dry_run_requested({"QUIPU_HOOK_DRY_RUN": "1"})
    assert hook_mod.dry_run_requested({"QUIPU_HOOK_DRY_RUN": "true"})
    assert not hook_mod.dry_run_requested({"QUIPU_HOOK_DRY_RUN": "0"})
    assert not hook_mod.dry_run_requested({})


def test_a_missing_transcript_is_a_failure_not_an_empty_pass(tmp_path):
    cfg = load(tmp_path / "vault", create=True)
    with pytest.raises(hook_mod.HookError) as caught:
        hook_mod.run(cfg, tmp_path / "nope.md", _fake(tmp_path))
    assert "transcript not found" in str(caught.value)


def test_an_empty_transcript_is_a_failure(tmp_path):
    cfg = load(tmp_path / "vault", create=True)
    with pytest.raises(hook_mod.HookError) as caught:
        hook_mod.run(cfg, _transcript(tmp_path, "   \n\n"), _fake(tmp_path))
    assert "empty transcript" in str(caught.value)


# --- the exit codes are the contract: a framework reads nothing else -------------------


def test_the_cli_exits_zero_on_a_completed_pass(cfg, capsys, tmp_path):
    from quipu.cli import main

    fake = tmp_path / "fake.json"
    fake.write_text(json.dumps([PROPOSAL]), encoding="utf-8")
    code = main(["--vault", str(cfg.root), "hook", "--transcript", str(_transcript(tmp_path)),
                 "--fake", str(fake), "--agent", "glitch"])
    assert code == 0
    assert "1 entries, 1 promoted" in capsys.readouterr().out


def test_the_cli_exits_one_with_a_reason_when_the_pass_fails(cfg, capsys, tmp_path):
    from quipu.cli import main

    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    code = main(["--vault", str(cfg.root), "hook", "--transcript", str(_transcript(tmp_path)),
                 "--fake", str(broken)])
    assert code == 1
    err = capsys.readouterr().err
    assert "hook failed" in err
    assert _entries(cfg) == [], "a failed pass must not write"


def test_the_cli_honours_the_dry_run_env_var(cfg, capsys, monkeypatch, tmp_path):
    from quipu.cli import main

    monkeypatch.setenv("QUIPU_HOOK_DRY_RUN", "1")
    fake = tmp_path / "fake.json"
    fake.write_text(json.dumps([PROPOSAL]), encoding="utf-8")
    code = main(["--vault", str(cfg.root), "hook", "--transcript", str(_transcript(tmp_path)),
                 "--fake", str(fake)])
    assert code == 0
    out = capsys.readouterr().out
    assert "summary" in out.lower(), "dry run prints the prompt, which carries the vault's summaries"
    assert _entries(cfg) == []

