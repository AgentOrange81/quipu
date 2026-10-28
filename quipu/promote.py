"""The promotion ladder: raw capture -> reflection -> promoted memory.

The reflection pass runs as its own isolated step (its own process / its own context), reads a
session transcript or a batch of raw captures, and proposes distilled entries. Proposals carry an
importance score and must cite evidence; below the threshold, or with no evidence, they stay raw.
That gate decision is the training label — see export.py.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from . import KINDS
from .config import Config
from .graph import add_edge, ensure_entity
from .index import connect
from .store import Entry, write_entry

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "promote.md"


@dataclass
class Proposal:
    kind: str
    summary: str
    body: str = ""
    evidence: str = ""
    importance: float = 0.0
    confidence: float = 0.5
    ask: str = ""
    entities: list[tuple[str, str]] = field(default_factory=list)   # [(name, kind)]
    edges: list[tuple[str, str, str]] = field(default_factory=list)  # [(src, predicate, dst)]
    supersedes: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    @classmethod
    def from_json(cls, d: dict) -> Proposal:
        kind = str(d.get("kind", "conclusion"))
        if kind not in KINDS:
            kind = "conclusion"
        ents = [(str(e.get("name")), str(e.get("kind", "thing")))
                for e in d.get("entities", []) if isinstance(e, dict) and e.get("name")]
        edges = [(str(e.get("src")), str(e.get("predicate")), str(e.get("dst")))
                 for e in d.get("edges", []) if isinstance(e, dict) and e.get("src") and e.get("dst")]
        return cls(
            kind=kind,
            summary=str(d.get("summary") or "").strip(),
            body=str(d.get("body") or "").strip(),
            evidence=str(d.get("evidence") or "").strip(),
            importance=float(d.get("importance") or 0.0),
            confidence=float(d.get("confidence") or 0.5),
            ask=str(d.get("ask") or "").strip(),
            entities=ents, edges=edges,
            supersedes=[str(s) for s in d.get("supersedes", [])],
            tags=[str(t) for t in d.get("tags", [])],
        )


class FakeProvider:
    """Deterministic provider for tests and dry runs."""

    def __init__(self, payload: list[dict] | str):
        self.payload = payload

    def complete(self, prompt: str) -> str:  # noqa: ARG002
        return self.payload if isinstance(self.payload, str) else json.dumps({"entries": self.payload})


class HttpProvider:
    """Any OpenAI-compatible /chat/completions endpoint (Ollama Cloud, llama.cpp, vLLM, Ollama)."""

    def __init__(self, base_url: str, model: str, api_key: str = "", timeout: int = 120):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout

    def complete(self, prompt: str) -> str:
        body = json.dumps({
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "stream": False,
        }).encode()
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions", data=body,
            headers={"Content-Type": "application/json",
                     **({"Authorization": f"Bearer {self.api_key}"} if self.api_key else {})},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = json.loads(r.read())
        except urllib.error.URLError as exc:
            raise RuntimeError(f"LLM request failed: {exc}") from exc
        return data["choices"][0]["message"]["content"]


def build_prompt(transcript: str, cfg: Config, existing: list[str] | None = None) -> str:
    base = PROMPT_PATH.read_text(encoding="utf-8") if PROMPT_PATH.exists() else _DEFAULT_PROMPT
    ents = ", ".join(cfg.predicates)
    known = "\n".join(f"- {e}" for e in (existing or [])[:40]) or "- (none)"
    return (base.replace("{{PREDICATES}}", ents)
                .replace("{{KNOWN_ENTRIES}}", known)
                .replace("{{TRANSCRIPT}}", transcript.strip()))


_DEFAULT_PROMPT = """You are the reflection pass of an agent memory system.
Read the transcript and extract only what a future session would need: decisions with their reason,
lessons, verified facts, handoffs. Not chatter, not narration, not a summary of the conversation.
Return JSON: {"entries":[{"kind":"decision|lesson|fact|handoff|conclusion","summary":"one statement",
"body":"detail","evidence":"the concrete proof: number, path, command, quote","importance":0.0-1.0,
"ask":"the user request that led here","entities":[{"name":"x","kind":"project|file|tool|person|concept"}],
"edges":[{"src":"x","predicate":"one of {{PREDICATES}}","dst":"y"}],"supersedes":["entry-id"]}]}
Known entries: {{KNOWN_ENTRIES}}
TRANSCRIPT:
{{TRANSCRIPT}}"""


def parse_response(raw: str) -> list[Proposal]:
    """Tolerate prose around the JSON object — models leak fences no matter what you ask."""
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        raise ValueError("no JSON object in model response")
    data = json.loads(m.group(0))
    items = data.get("entries") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ValueError("response has no entries list")
    return [Proposal.from_json(d) for d in items if isinstance(d, dict) and d.get("summary")]


def reflect(transcript: str, cfg: Config, provider, existing: list[str] | None = None) -> list[Proposal]:
    return parse_response(provider.complete(build_prompt(transcript, cfg, existing)))


def apply(proposals: list[Proposal], cfg: Config, agent: str = "", project: str = "",
          session: str = "", source: str = "") -> list[Entry]:
    """Write proposals, gated by the promotion threshold.

    promoted    importance >= cfg.promote_threshold AND evidence present
    raw         everything else (kept verbatim for a later pass; it costs nothing to keep)
    """
    written: list[Entry] = []
    for p in proposals:
        promoted = p.importance >= cfg.promote_threshold and bool(p.evidence)
        e = Entry(
            kind=p.kind, state="promoted" if promoted else "raw", agent=agent, project=project,
            session=session, confidence=p.confidence, importance=p.importance,
            source=source or "reflect", ask=p.ask, summary=p.summary, evidence=p.evidence,
            supersedes=p.supersedes, tags=p.tags, body=p.body,
        )
        write_entry(cfg.root, e)
        written.append(e)
        # graph only learns from what survived the gate: a raw capture is allowed to be wrong
        if promoted:
            for name, kind in p.entities:
                c = connect(cfg.db_path)
                with c:
                    ensure_entity(c, name, kind)
                c.close()
            for src, predicate, dst in p.edges:
                try:
                    add_edge(cfg, src, predicate, dst, note_id=e.id)
                except ValueError:
                    continue  # predicate outside the closed vocabulary: skip, don't poison the graph
    return written


def record_capture(cfg: Config, text: str, agent: str = "", project: str = "", session: str = "",
                   ask: str = "", source: str = "") -> Entry:
    """Store a raw capture. Cheap, unfiltered, one fact per call."""
    e = Entry(kind="capture", state="raw", agent=agent, project=project, session=session,
              ask=ask, summary=text.splitlines()[0][:200], body=text, source=source or "capture")
    write_entry(cfg.root, e)
    return e
