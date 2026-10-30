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
from .graph import add_edge, close_superseded, ensure_entity
from .index import connect
from .store import Entry, check_summary, first_prose_line, format_edge_spec, write_entry

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "promote.md"

#: The roles a Hermes session row can carry that belong in a transcript. A `tool` row is included
#: because a tool result usually carries the evidence the reflection pass needs.
TURN_ROLES = ("user", "assistant", "system", "tool", "human")

#: A line that opens a turn: `role: text`. The loader emits exactly this shape, and the chunker
#: splits on it, so a chunk boundary is always a turn boundary.
_TURN_LINE = re.compile(r"^(user|assistant|system|tool|human):\s", re.I)


@dataclass
class LoadStats:
    """What the last load did with the file it was given.

    A corrupt line is skipped, never fatal — but it is counted, because a session that quietly lost
    half its lines is a session that lies.
    """
    messages: int = 0
    malformed: int = 0
    skipped: int = 0

    def reset(self) -> None:
        self.messages = 0
        self.malformed = 0
        self.skipped = 0


#: The counts from the most recent `load_transcript` call.
LAST_LOAD_STATS = LoadStats()


def load_transcript(source: str | Path, stats: LoadStats | None = None) -> str:
    """Read a session file — or a transcript already in hand — into plain `role: text` lines (#2).

    Two shapes arrive in practice and they want different treatment:

    * **Hermes session JSONL** (`.jsonl`, or content that sniffs as one): one JSON object per line
      with `role` and `content`, where `content` is a string or a list of `{"type": "text",
      "text": ...}` parts. Rendered as `role: text`, parts of one message joined with a single space.
    * **Anything else** (markdown, a pasted transcript): passed through untouched, because a human
      already chose its shape.

    A row that is not a message (`type: meta`), a malformed line, and a message with no text are
    skipped rather than fatal: a session file with one corrupt line is still a session. An empty or
    missing file IS fatal — a pass over nothing must not look like a pass.

    What was skipped is counted: in `stats` when the caller passes one, otherwise in the module's
    `LAST_LOAD_STATS` — exactly one of the two, never silently neither.
    """
    counts = stats if stats is not None else LAST_LOAD_STATS
    counts.reset()
    if isinstance(source, Path):
        path: Path | None = source
        text = ""
    else:
        raw = str(source)
        looks_like_a_path = "\n" not in raw and len(raw) < 4096
        path = Path(raw) if looks_like_a_path else None
        text = raw if path is None else ""
    if path is not None:
        if not path.exists():
            raise ValueError(f"transcript not found: {path}")
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise ValueError(f"cannot read transcript {path}: {exc}") from exc
    if not text.strip():
        raise ValueError(f"empty transcript: {path or '<text>'}")

    if not _sniffs_jsonl(text, path):
        return text
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            counts.malformed += 1
            continue  # one corrupt line is not a broken session
        if not isinstance(row, dict):
            counts.skipped += 1
            continue
        role = str(row.get("role") or "").lower()
        if role not in TURN_ROLES:
            counts.skipped += 1
            continue  # meta/control rows are not conversation
        content = row.get("content")
        if isinstance(content, str):
            parts = [content]
        elif isinstance(content, list):
            parts = [p.get("text", "") for p in content
                     if isinstance(p, dict)
                     and p.get("type", "text") in ("text", "input_text", "output_text")]
        else:
            counts.skipped += 1
            continue
        message = re.sub(r"\s+", " ", " ".join(str(p) for p in parts if p)).strip()
        if not message:
            counts.skipped += 1
            continue
        lines.append(f"{role}: {message}")
        counts.messages += 1
    if not lines:
        raise ValueError(f"no messages in transcript: {path or '<text>'}")
    return "\n".join(lines)


def _sniffs_jsonl(text: str, path: Path | None) -> bool:
    """A `.jsonl` name is a promise; without one, the first non-empty line has to be a message row."""
    if path is not None and path.suffix.lower() in (".jsonl", ".ndjson"):
        return True
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if not line.startswith("{"):
            return False
        try:
            row = json.loads(line)
        except ValueError:
            return False
        return isinstance(row, dict) and "role" in row and "content" in row
    return False


def chunk_transcript(text: str, max_chars: int = 24000) -> list[str]:
    """Split a transcript on TURN boundaries so no message is ever cut in half (#2).

    A chunk is a whole number of turns, packed greedily up to `max_chars`. One turn longer than the
    cap is returned **whole** as its own chunk: truncating a message silently is exactly the failure
    this project exists to stop, so bounding an oversized turn is the caller's explicit decision, not
    the chunker's quiet one. `max_chars <= 0` disables chunking.

    Markdown without `role:` lines is split on blank lines instead — still a boundary a human drew.
    Joining the returned chunks with the text's own separator reproduces the input exactly.
    """
    if max_chars <= 0:
        return [text]
    if len(text) <= max_chars:
        return [text]

    lines = text.split("\n")
    blocks: list[list[str]] = []
    if sum(1 for line in lines if _TURN_LINE.match(line)) >= 1:
        for line in lines:
            if not blocks or _TURN_LINE.match(line):
                blocks.append([line])
            else:
                blocks[-1].append(line)   # blank lines and continuations stay with their turn
    else:
        for line in lines:                # paragraphs; a blank run closes the block before it
            if blocks and line.strip() and blocks[-1][-1].strip() == "":
                blocks.append([line])
            elif blocks:
                blocks[-1].append(line)
            else:
                blocks.append([line])

    chunks: list[str] = []
    current: list[list[str]] = []
    size = 0
    for block in blocks:
        block_size = len("\n".join(block))
        extra = block_size + (1 if current else 0)
        if current and size + extra > max_chars:
            chunks.append("\n".join("\n".join(b) for b in current))
            current, size = [block], block_size
        else:
            current.append(block)
            size += extra
    if current:
        chunks.append("\n".join("\n".join(b) for b in current))
    return [c for c in chunks] or [text]


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
        url = f"{self.base_url}/chat/completions"
        req = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": "application/json",
                     **({"Authorization": f"Bearer {self.api_key}"} if self.api_key else {})},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw = r.read()
        except urllib.error.HTTPError as exc:  # a status code, not a transport failure
            raise RuntimeError(f"LLM request failed: {url}: HTTP {exc.code} {exc.reason}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"LLM request failed: {url}: {exc}") from exc
        except OSError as exc:  # a socket timeout surfaces here, not as a URLError
            raise RuntimeError(f"LLM request failed: {url}: {type(exc).__name__}: {exc}") from exc
        try:
            data = json.loads(raw)
            return data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"LLM request failed: {url}: bad response: {exc}") from exc


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


def dedupe_proposals(proposals: list) -> list:
    """Union per-chunk proposals, dropping a summary an earlier chunk already produced.

    Chunking sends neighbouring context to the same model, so one claim can come back twice — and a
    chunked pass that writes it twice turns a clean corpus into a pile of near-duplicates. The key is
    the summary with whitespace collapsed and case folded: the reflection pass writes that summary
    itself, so two spellings of one claim are one claim.
    """
    seen: set[str] = set()
    kept = []
    for proposal in proposals:
        key = " ".join(str(proposal.summary).split()).lower()
        if key in seen:
            continue
        seen.add(key)
        kept.append(proposal)
    return kept


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
        # the entry that asserted an edge records it, so the graph survives a lost db (#24).
        # A raw proposal never enters the graph, so it carries no edges either.
        specs = [format_edge_spec(src, predicate, dst) for src, predicate, dst in p.edges] if promoted else []
        e = Entry(
            kind=p.kind, state="promoted" if promoted else "raw", agent=agent, project=project,
            session=session, confidence=p.confidence, importance=p.importance,
            source=source or "reflect", ask=p.ask, summary=p.summary, evidence=p.evidence,
            supersedes=p.supersedes, tags=p.tags, edges=specs, body=p.body,
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
                    # the entry's `created` is the window's start, so a rebuild reproduces it
                    add_edge(cfg, src, predicate, dst, note_id=e.id, at=e.created)
                except ValueError:
                    continue  # predicate outside the closed vocabulary: skip, don't poison the graph
        # a correction proposed by the reflection pass closes the belief it corrects (#20), the same
        # way `quipu write --supersedes` does; an edge the new entry restates is left live
        if e.supersedes:
            close_superseded(cfg, e.supersedes, e)
    return written


def record_capture(cfg: Config, text: str, agent: str = "", project: str = "", session: str = "",
                   ask: str = "", source: str = "", allow_blob: bool = False) -> Entry:
    """Store a raw capture. Cheap, unfiltered, one fact per call.

    The summary is the first prose line rather than blindly the first line: a capture whose body
    opens with a JSON blob used to hand that blob to the retrieval handle, which then ranked on
    text no agent can use (#77). A body with no prose at all is refused by `check_summary` unless
    the caller opted out — the entry is not worth keeping if nothing in it can be stated.
    """
    first = text.splitlines()[0][:200]
    summary = first if allow_blob else (first_prose_line(text) or first)
    check_summary(summary, allow_blob=allow_blob)
    e = Entry(kind="capture", state="raw", agent=agent, project=project, session=session,
              ask=ask, summary=summary, body=text, source=source or "capture")
    write_entry(cfg.root, e)
    return e
