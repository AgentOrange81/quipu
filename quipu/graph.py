"""Temporal entity/relation graph.

Edges are never deleted. Asserting a new edge that contradicts an old one closes the old edge
(`valid_to`) and opens a new one, so "what did we believe in June, and why did it change" stays
answerable — and the pair becomes preference data on export.
"""
from __future__ import annotations

import json
import re
import sqlite3

from .config import Config
from .index import connect
from .store import now_iso


def canonical(name: str) -> str:
    """One spelling per entity: lowercase, whitespace/punctuation collapsed to single hyphens.

    Without this, "DOCP 3200", "docp-3200" and "DOCP3200" become three nodes with one fact between
    them — the failure mode that makes a hand-built graph useless after a few months.
    """
    return slug(name)


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "entity"


def ensure_entity(c: sqlite3.Connection, name: str, kind: str = "thing", aliases=()) -> str:
    canon = canonical(name)
    cur = c.execute("SELECT name, aliases FROM entities WHERE name = ?", (canon,))
    row = cur.fetchone()
    if row:
        if name != canon:  # keep the spelling we saw as an alias, never as a second node
            seen = set(json.loads(row[1] or "[]"))
            if name not in seen:
                seen.add(name)
                c.execute("UPDATE entities SET aliases = ? WHERE name = ?",
                          (json.dumps(sorted(seen), ensure_ascii=False), canon))
        return canon
    extra = sorted({*aliases, *([name] if name != canon else [])})
    c.execute("INSERT INTO entities(name, kind, aliases, created) VALUES(?,?,?,?)",
              (canon, kind, json.dumps(extra, ensure_ascii=False), now_iso()))
    return canon


def add_edge(cfg: Config, src: str, predicate: str, dst: str, note_id: str = "",
             confidence: float = 1.0, at: str | None = None,
             allow_new_predicate: bool = False) -> int:
    """Assert src -predicate-> dst. Returns the new edge id.

    Raises ValueError for a predicate outside the closed vocabulary unless allow_new_predicate —
    an open predicate space is how a graph rots into 400 spellings of "uses".
    """
    if predicate not in cfg.predicates and not allow_new_predicate:
        raise ValueError(
            f"predicate {predicate!r} is not in the vocabulary; add it to {cfg.config_path} "
            f"or pass allow_new_predicate=True"
        )
    at = at or now_iso()
    src, dst = canonical(src), canonical(dst)
    c = connect(cfg.db_path)
    with c:
        ensure_entity(c, src)
        ensure_entity(c, dst)
        c.execute(
            "INSERT INTO edges(src, predicate, dst, note_id, confidence, valid_from, valid_to) "
            "VALUES(?,?,?,?,?,?,NULL)",
            (src, predicate, dst, note_id, confidence, at),
        )
        eid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
        c.execute("INSERT INTO events(at, kind, detail) VALUES(?,?,?)",
                  (at, "assert", json.dumps({"edge": eid, "src": src, "pred": predicate, "dst": dst,
                                            "note": note_id}, ensure_ascii=False)))
    c.close()
    return eid


def contradict(cfg: Config, old_edge_id: int, src: str, predicate: str, dst: str,
               note_id: str = "", confidence: float = 1.0, at: str | None = None) -> int:
    """Close `old_edge_id` and assert its replacement in one instant.

    The replacement inherits the old edge's valid_from for query purposes only if `at` is omitted —
    no: it starts now. History is preserved because the old row keeps its window.
    """
    at = at or now_iso()
    c = connect(cfg.db_path)
    with c:
        row = c.execute("SELECT id FROM edges WHERE id = ?", (old_edge_id,)).fetchone()
        if row is None:
            raise ValueError(f"no edge {old_edge_id}")
        c.execute("UPDATE edges SET valid_to = ? WHERE id = ? AND valid_to IS NULL", (at, old_edge_id))
        c.execute("INSERT INTO events(at, kind, detail) VALUES(?,?,?)",
                  (at, "invalidate", json.dumps({"edge": old_edge_id}, ensure_ascii=False)))
    c.close()
    return add_edge(cfg, src, predicate, dst, note_id=note_id, confidence=confidence, at=at)


def invalidate(cfg: Config, edge_id: int, at: str | None = None) -> None:
    at = at or now_iso()
    c = connect(cfg.db_path)
    try:
        with c:
            cur = c.execute("UPDATE edges SET valid_to = ? WHERE id = ? AND valid_to IS NULL",
                            (at, edge_id))
            if cur.rowcount == 0:
                # raising inside `with c` rolls back; never close the handle before the block exits
                raise ValueError(f"no live edge {edge_id}")
            c.execute("INSERT INTO events(at, kind, detail) VALUES(?,?,?)",
                      (at, "invalidate", json.dumps({"edge": edge_id}, ensure_ascii=False)))
    finally:
        c.close()


def timeline(cfg: Config, entity: str, at: str | None = None) -> list[dict]:
    """Edges touching `entity`, optionally as known at a point in time."""
    entity = canonical(entity)
    c = connect(cfg.db_path)
    if at:
        rows = c.execute(
            "SELECT * FROM edges WHERE (src = ? OR dst = ?) AND valid_from <= ? "
            "AND (valid_to IS NULL OR valid_to > ?) ORDER BY valid_from",
            (entity, entity, at, at),
        ).fetchall()
    else:
        rows = c.execute(
            "SELECT * FROM edges WHERE src = ? OR dst = ? ORDER BY valid_from", (entity, entity)
        ).fetchall()
    c.close()
    return [dict(r) for r in rows]


def neighbors(cfg: Config, entity: str, depth: int = 1) -> dict:
    """BFS over live edges. Returns {"nodes": [...], "edges": [...]}."""
    entity = canonical(entity)
    seen = {entity}
    edges: list[dict] = []
    frontier = [entity]
    c = connect(cfg.db_path)
    for _ in range(max(1, depth)):
        nxt: list[str] = []
        for node in frontier:
            for r in c.execute(
                "SELECT * FROM edges WHERE valid_to IS NULL AND (src = ? OR dst = ?)", (node, node)
            ).fetchall():
                d = dict(r)
                if d["id"] in {e["id"] for e in edges}:
                    continue
                edges.append(d)
                for n in (d["src"], d["dst"]):
                    if n not in seen:
                        seen.add(n)
                        nxt.append(n)
        frontier = nxt
    ents = {r["name"]: dict(r) for r in c.execute("SELECT * FROM entities").fetchall()}
    c.close()
    return {"nodes": [ents.get(n, {"name": n}) for n in seen], "edges": edges}


def materialize(cfg: Config) -> int:
    """Write entities/<slug>.md hubs so the graph is browsable in Obsidian too."""
    c = connect(cfg.db_path)
    ents = c.execute("SELECT * FROM entities ORDER BY name").fetchall()
    cfg.entities_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for e in ents:
        lines = ["---", f"entity: {e['name']}", f"kind: {e['kind']}", "tags: [entity]", "---", "",
                 f"# {e['name']}", ""]
        for r in c.execute(
            "SELECT * FROM edges WHERE src = ? OR dst = ? ORDER BY valid_from", (e["name"], e["name"])
        ).fetchall():
            arrow = "-->" if r["src"] == e["name"] else "<--"
            other = r["dst"] if r["src"] == e["name"] else r["src"]
            window = f"{r['valid_from'][:10]} → {r['valid_to'][:10] if r['valid_to'] else 'now'}"
            lines.append(f"- {arrow} `{r['predicate']}` [[{other}]] ({window})")
        (cfg.entities_dir / f"{slug(e['name'])}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        count += 1
    c.close()
    return count
