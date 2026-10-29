"""`quipu hook` — the session-end entry point a framework calls, without embedding quipu (#3).

A memory system that nobody calls gathers nothing, and a hook that fails quietly is worse than no hook:
the session ends, the framework says "memory: ok", and nothing was written. This module is the one place
that trade is decided, so every failure path raises or exits non-zero, and a hung endpoint cannot hold
the session open:

- `run()` does capture -> reflect -> apply in that order, writing only after the model returned, so a
  transport error leaves the vault untouched rather than half-written;
- the reflection call is bounded by `timeout` (default 300 s) on a worker thread, because a blocking
  `urlopen` has no deadline of its own once it is inside a framework's shutdown path;
- `dry_run` (or `QUIPU_HOOK_DRY_RUN=1`) returns the exact prompt and writes nothing, which is how a
  human checks the hook without spending tokens.

The transcript is a path to the session file (Hermes JSONL or plain markdown) or `-` for stdin.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import graph as graph_mod
from . import index as index_mod
from . import promote as promote_mod
from .store import Entry, iter_entries

DEFAULT_TIMEOUT = 300.0


class HookError(RuntimeError):
    """Anything that means the pass did not complete. The caller exits non-zero and prints it."""


@dataclass
class HookResult:
    written: list[Entry] = field(default_factory=list)
    prompt: str = ""
    proposed: int = 0
    chunks: int = 0
    seconds: float = 0.0

    @property
    def promoted(self) -> int:
        return sum(1 for e in self.written if e.state == "promoted")

    def line(self) -> str:
        chunks = f" in {self.chunks} chunks" if self.chunks > 1 else ""
        return (f"{len(self.written)} entries, {self.promoted} promoted, "
                f"{len(self.written) - self.promoted} raw{chunks}, {self.seconds:.1f}s")


def read_transcript(source: str | Path, stdin=None) -> str:
    """A path, or `-` for stdin, read through the shared loader (#2).

    The loader turns a Hermes session JSONL into `role: text` turns and passes markdown through
    untouched, so the hook and `reflect` read the same transcript the same way — and a session file
    reaches the model as turns, not as raw JSON lines. The loader raises `ValueError`; `run` speaks
    it as a `HookError`, so the hook still has exactly one exception type.
    """
    if str(source) == "-":
        return promote_mod.load_transcript((stdin or sys.stdin).read())
    return promote_mod.load_transcript(Path(source))


def _existing(cfg) -> list[str]:
    return [e.summary for _p, e in iter_entries(cfg.root) if e.state == "promoted"]


def _reflect_bounded(chunks: list[str], cfg, provider, timeout: float):
    """Run every chunk on a worker thread so a hung endpoint cannot block session end.

    `urlopen(timeout=...)` covers a dead socket, not a server that accepted the request and then
    stopped talking — and this call sits in a framework's shutdown path, where hanging is the worst
    outcome available. The budget is for the WHOLE pass, not per chunk: three chunks that each take
    100 s are not a 300 s pass when the framework is waiting on it.
    """
    box: dict = {}
    existing = _existing(cfg)

    def work():
        try:
            proposals = []
            for chunk in chunks:
                proposals.extend(promote_mod.reflect(chunk, cfg, provider, existing))
            box["proposals"] = proposals
        except BaseException as exc:  # noqa: BLE001 - reported, not swallowed
            box["error"] = exc

    thread = threading.Thread(target=work, name="quipu-hook-reflect", daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise HookError(f"reflection timed out after {timeout:g}s (endpoint {cfg.llm_base_url}, "
                        f"{len(chunks)} chunk{'s' if len(chunks) != 1 else ''})")
    if "error" in box:
        exc = box["error"]
        raise HookError(f"reflection failed: {type(exc).__name__}: {exc}") from exc
    return box.get("proposals") or []


def run(cfg, transcript: str | Path, provider, agent: str = "", project: str = "",
        session: str = "", timeout: float = DEFAULT_TIMEOUT, dry_run: bool = False,
        stdin=None, max_chars: int = 24000) -> HookResult:
    started = time.monotonic()
    try:
        text = read_transcript(transcript, stdin=stdin)
    except ValueError as exc:  # the loader speaks ValueError; the hook speaks HookError
        raise HookError(str(exc)) from exc
    chunks = promote_mod.chunk_transcript(text, max_chars)

    if dry_run:
        # the first chunk is the first request the endpoint would receive
        return HookResult(prompt=promote_mod.build_prompt(chunks[0] if chunks else text, cfg,
                                                           _existing(cfg)),
                          seconds=time.monotonic() - started)
    proposals = promote_mod.dedupe_proposals(_reflect_bounded(chunks, cfg, provider, timeout))
    # nothing is written until the model has answered in full: a transport error above leaves the
    # vault exactly as it was, which is what "no partial entry" means
    written = promote_mod.apply(proposals, cfg, agent=agent, project=project,
                                session=session, source=str(transcript))
    graph_mod.materialize(cfg)
    index_mod.reindex(cfg.root, cfg.db_path)
    return HookResult(written=written, proposed=len(proposals), chunks=len(chunks),
                      seconds=time.monotonic() - started)


def dry_run_requested(env: dict | None = None) -> bool:
    value = (env or os.environ).get("QUIPU_HOOK_DRY_RUN", "")
    return value.strip().lower() in {"1", "true", "yes", "on"}
