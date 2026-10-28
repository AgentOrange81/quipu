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
        title = self.summary or self.body.splitlines()[0] if (self.summary or self.body) else self.id
        parts = ["---"] + head + ["---", "", f"# {title}", ""]
        if self.summary:
            parts += [self.summary, ""]
        if self.evidence:
            parts += ["**Evidence:** " + self.evidence, ""]
        if self.body:
            parts += [self.body, ""]
        if self.supersedes:
            parts += ["## Supersedes", ""] + [f"- [[{s}]]" for s in self.supersedes] + [""]
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
    for p in (Path(root) / "notes").rglob(f"{eid}*.md"):
        return p
    return None
