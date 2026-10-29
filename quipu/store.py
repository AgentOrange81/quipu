"""Markdown entry store — the source of truth.

Every memory is one file with YAML-ish frontmatter. No YAML dependency: scalars are written as
`key: value`, quoted only when needed, and lists as JSON arrays (which are valid YAML inline lists
for any other tool, Obsidian included).
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import KINDS, STATES

FM_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.S)
KV_RE = re.compile(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_id() -> str:
    return os.urandom(4).hex()


#: ONE definition of "this looks like a number", shared by the writer and the reader. The writer
#: quotes every string that matches it (so it comes back as the string it was); the reader
#: coerces only UNQUOTED values that match it (so a quoted value is always a string).
#: Exponent forms are in: "1.5e3" reads back as 1500.0 unless the writer quotes it. Underscores
#: and a leading "+" are out: Python's int()/float() would take "1_0" for 10 and "+7" for 7, and
#: a string that only looks numeric must stay a string.
NUMBER_RE = re.compile(r"^-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?$")

#: Markers of a pasted tool transcript. A summary that carries one is the output of a tool, not
#: a claim anybody made (#30).
TRANSCRIPT_MARKERS = ("FILES:", "GATE:", "MAP:")

#: A path on its own - "quipu/graph.py", "/home/dirk/x.md" - is a file listing, not a sentence.
_BARE_PATH_RE = re.compile(r"^[\w.~@+-]+(?:/[\w.~@+-]+)*\.[A-Za-z0-9]{1,8}$")

#: A lowercase word of three letters or more: the mark of prose.
_PROSE_RE = re.compile(r"[a-z]{3,}")

#: The end of a sentence.
_SENTENCE_END_RE = re.compile(r"[.!?…]")


def looks_like_transcript(summary: str) -> bool:
    """Is this summary a pasted tool transcript rather than a claim? (#30)

    The summary is the retrieval signal: an agent reads it as a statement, so a pasted
    transcript reads as a statement too - and is worthless. It is caught at import and filed as
    `pruned` with a tag: visible, reversible, and never promoted.

    A transcript starts with a code fence, carries a tool marker (FILES:, GATE:, MAP:), spans
    more than one paragraph, is a bare path, or has no sentence structure at all.
    """
    s = (summary or "").strip()
    if not s:
        return False
    if s.startswith("```"):
        return True
    if any(marker in s for marker in TRANSCRIPT_MARKERS):
        return True
    if "\n\n" in s:
        return True
    if _BARE_PATH_RE.match(s):
        return True
    if not _SENTENCE_END_RE.search(s) and not _PROSE_RE.findall(s):
        return True
    return False


def _scalar(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    # A digit-only STRING must be quoted or it comes back as an int: an id of "04159069" round-tripped
    # to "4159069" (CI, 3.13) — leading zero gone, which silently breaks every supersedes reference
    # that names it. Same for a project called "2026" and anything else that looks numeric. The
    # quote guard and the parser share NUMBER_RE, so they cannot drift apart again.
    if s == "" or NUMBER_RE.match(s) or s in ("true", "false") or any(c in s for c in ':#\n"\''):
        return json.dumps(s, ensure_ascii=False)
    return s


def _parse_scalar(raw: str):
    raw = raw.strip()
    if not raw:
        return ""
    if raw.startswith("["):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw
    if raw[0] == raw[-1] and raw[0] in "\"'" and len(raw) >= 2:
        # a quoted value is ALWAYS a string, whatever it looks like
        try:
            return json.loads(raw) if raw[0] == '"' else raw[1:-1]
        except json.JSONDecodeError:
            return raw[1:-1]
    if raw in ("true", "false"):
        return raw == "true"
    if NUMBER_RE.match(raw):
        return float(raw) if ("." in raw or "e" in raw or "E" in raw) else int(raw)
    return raw


#: The sections this writer generates from the frontmatter. They are stripped from the body
#: before it is written back, so re-writing an entry - which happens every time a closure is
#: recorded - does not duplicate them.
GENERATED_SECTIONS = ("## Supersedes", "## Edges", "## Edge closures")


def strip_generated(body: str) -> str:
    """Drop the generated sections (and the bullets under them) from a body."""
    out: list[str] = []
    skipping = False
    for line in (body or "").splitlines():
        if line.strip() in GENERATED_SECTIONS:
            skipping = True
            continue
        if skipping:
            if line.startswith("#"):
                skipping = False
            else:
                continue
        out.append(line)
    return "\n".join(out).strip()


@dataclass
class Entry:
    id: str = ""
    kind: str = "conclusion"
    state: str = "raw"
    agent: str = ""
    project: str = ""
    session: str = ""
    created: str = ""
    updated: str = ""
    confidence: float = 0.5
    importance: float = 0.0
    source: str = ""
    ask: str = ""
    summary: str = ""
    evidence: str = ""
    supersedes: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    #: The graph edges this entry asserted, as "SRC:PRED:DST" specs. The graph lives here, in the
    #: markdown: a db that is deleted can be rebuilt from these (#24).
    edges: list[str] = field(default_factory=list)
    #: The windows this entry's edges closed: [{"spec": "SRC:PRED:DST", "at": "<instant>"}]. A
    #: rebuild has to restore closed windows, not live ones - a resurrected belief is worse than a
    #: lost one.
    edge_closures: list[dict] = field(default_factory=list)
    body: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = new_id()
        if self.kind not in KINDS:
            raise ValueError(f"kind must be one of {KINDS}, got {self.kind!r}")
        if self.state not in STATES:
            raise ValueError(f"state must be one of {STATES}, got {self.state!r}")
        if not self.created:
            self.created = now_iso()
        if not self.updated:
            self.updated = self.created

    # --- serialisation -------------------------------------------------
    def to_markdown(self) -> str:
        head = [
            f"id: {_scalar(self.id)}",
            f"kind: {self.kind}",
            f"state: {self.state}",
            f"agent: {_scalar(self.agent)}",
            f"project: {_scalar(self.project)}",
            f"session: {_scalar(self.session)}",
            f"created: {self.created}",
            f"updated: {self.updated}",
            f"confidence: {self.confidence}",
            f"importance: {self.importance}",
            f"source: {_scalar(self.source)}",
        ]
        # summary and evidence belong in the frontmatter, not only in the body: the body copy is for
        # humans, while from_markdown/exports read the fields. Writing them body-only silently lost
        # evidence on every round-trip (which disarmed the promotion gate and the DPO reason).
        head.append(f"summary: {_scalar(self.summary)}")
        if self.evidence:
            head.append(f"evidence: {_scalar(self.evidence)}")
        if self.ask:
            head.append(f"ask: {_scalar(self.ask)}")
        if self.supersedes:
            head.append("supersedes: " + json.dumps(self.supersedes, ensure_ascii=False))
        if self.tags:
            head.append("tags: " + json.dumps(self.tags, ensure_ascii=False))
        if self.edges:
            head.append("edges: " + json.dumps(self.edges, ensure_ascii=False))
        if self.edge_closures:
            head.append("edge_closures: " + json.dumps(self.edge_closures, ensure_ascii=False))
        title = self.summary or self.body.splitlines()[0] if (self.summary or self.body) else self.id
        parts = ["---"] + head + ["---", "", f"# {title}", ""]
        if self.summary:
            parts += [self.summary, ""]
        if self.evidence:
            parts += ["**Evidence:** " + self.evidence, ""]
        # the H1/summary/evidence preamble is written from the fields above, so the body keeps
        # only what the writer wrote. Re-writing an entry happens every time an edge or a closure
        # is recorded into it, and a body that grew a copy per rewrite was unreadable within a
        # dozen writes.
        body = strip_generated(self.stripped_body())
        if body:
            parts += [body, ""]
        if self.supersedes:
            parts += ["## Supersedes", ""] + [f"- [[{s}]]" for s in self.supersedes] + [""]
        if self.edges:
            parts += ["## Edges", ""] + [f"- {spec}" for spec in self.edges] + [""]
        if self.edge_closures:
            parts += ["## Edge closures", ""] + [
                f"- {c.get('spec', '')} closed at {c.get('at', '')}" for c in self.edge_closures] + [""]
        return "\n".join(parts)

    @classmethod
    def from_markdown(cls, text: str) -> Entry:
        fm: dict = {}
        body = text
        m = FM_RE.match(text)
        if m:
            body = text[m.end():]
            for line in m.group(1).splitlines():
                mm = KV_RE.match(line)
                if mm:
                    fm[mm.group(1)] = _parse_scalar(mm.group(2))
        title = ""
        for line in body.splitlines():
            if line.startswith("# "):
                title = line[2:].strip()
                break
        tags = fm.get("tags") or []
        sup = fm.get("supersedes") or []
        edges = fm.get("edges") or []
        closures = fm.get("edge_closures") or []
        if not isinstance(edges, list):
            edges = [edges]
        if not isinstance(closures, list):
            closures = [closures]
        return cls(
            id=str(fm.get("id") or new_id()),
            kind=str(fm.get("kind") or "conclusion"),
            state=str(fm.get("state") or "raw"),
            agent=str(fm.get("agent") or ""),
            project=str(fm.get("project") or ""),
            session=str(fm.get("session") or ""),
            created=str(fm.get("created") or ""),
            updated=str(fm.get("updated") or fm.get("created") or ""),
            confidence=float(fm.get("confidence") or 0.5),
            importance=float(fm.get("importance") or 0.0),
            source=str(fm.get("source") or ""),
            ask=str(fm.get("ask") or ""),
            summary=str(fm.get("summary") or title),
            evidence=str(fm.get("evidence") or ""),
            supersedes=list(sup) if isinstance(sup, list) else [str(sup)],
            tags=list(tags) if isinstance(tags, list) else [str(tags)],
            edges=[str(s) for s in edges],
            edge_closures=[c for c in closures if isinstance(c, dict)],
            body=body.strip(),
        )

    def stripped_body(self) -> str:
        """Body without the duplicated H1/summary preamble (what a reader wants as content)."""
        lines = self.body.splitlines()
        out: list[str] = []
        for line in lines:
            if line.startswith("# ") and not out:
                continue
            if line.strip() == self.summary.strip():
                continue
            if line.startswith("**Evidence:**"):
                continue
            out.append(line)
        return "\n".join(out).strip()


def entry_path(root: Path, eid: str, summary: str = "") -> Path:
    slug = re.sub(r"[^a-z0-9]+", "-", (summary or "").lower()).strip("-")[:48].rstrip("-")
    return Path(root) / "notes" / f"{eid}-{slug}.md" if slug else Path(root) / "notes" / f"{eid}.md"


def write_entry(root: str | Path, entry: Entry) -> Path:
    path = entry_path(Path(root), entry.id, entry.summary)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(entry.to_markdown(), encoding="utf-8")
    return path


def read_entry(path: str | Path) -> Entry:
    return Entry.from_markdown(Path(path).read_text(encoding="utf-8", errors="replace"))


def iter_entries(root: str | Path):
    notes = Path(root) / "notes"
    if not notes.exists():
        return
    for p in sorted(notes.rglob("*.md")):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
            if not text.lstrip().startswith("---"):
                continue  # not a quipu entry (hand-written note, README, ...)
            yield p, Entry.from_markdown(text)
        except (ValueError, OSError):
            continue


def find_entry(root: str | Path, eid: str) -> Path | None:
    if not eid:
        return None
    for p in (Path(root) / "notes").rglob(f"{eid}*.md"):
        return p
    return None


#: The edge spec format the markdown carries: "SRC:PRED:DST". One definition, so the writer,
#: the reader, the CLI and the graph cannot drift apart on how a spec is written or split.
def format_edge_spec(src: str, predicate: str, dst: str) -> str:
    return f"{src}:{predicate}:{dst}"


def parse_edge_spec(spec: str) -> tuple[str, str, str]:
    """Split "SRC:PRED:DST" into its three parts. Raises ValueError on a malformed spec."""
    parts = (spec or "").split(":", 2)
    if len(parts) != 3:
        raise ValueError(f"edge spec must be src:predicate:dst, got {spec!r}")
    src, predicate, dst = (p.strip() for p in parts)
    if not src or not predicate or not dst:
        raise ValueError(f"edge spec must be src:predicate:dst, got {spec!r}")
    return src, predicate, dst
