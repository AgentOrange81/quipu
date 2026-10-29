"""SQLite index: full-text search over entries plus the temporal graph tables."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from .store import Entry, iter_entries

#: The schema version of this db, carried in `PRAGMA user_version`. A db written by an older
#: quipu has a lower number. The index is derived from the markdown, so an old db is a rebuild
#: away from being current - never a vault that cannot be opened (#23).
SCHEMA_VERSION = 2

#: TABLES FIRST, and no index in here: an index on a column that the migration has not added yet
#: is the crash that made every command die on a vault written before the identity key.
TABLES_SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS entries(
  id TEXT PRIMARY KEY, path TEXT NOT NULL, kind TEXT, state TEXT, agent TEXT, project TEXT,
  session TEXT, created TEXT, confidence REAL, importance REAL, ask TEXT, summary TEXT,
  evidence TEXT, body TEXT);

CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5(
  summary, ask, evidence, body, content='entries', content_rowid='rowid',
  tokenize='porter unicode61');

CREATE TABLE IF NOT EXISTS entities(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, kind TEXT DEFAULT 'thing',
  aliases TEXT DEFAULT '[]', created TEXT, key TEXT);

CREATE TABLE IF NOT EXISTS edges(
  id INTEGER PRIMARY KEY, src TEXT NOT NULL, predicate TEXT NOT NULL, dst TEXT NOT NULL,
  note_id TEXT, confidence REAL DEFAULT 1.0, valid_from TEXT, valid_to TEXT,
  UNIQUE(src, predicate, dst, valid_from));
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY, at TEXT NOT NULL, kind TEXT NOT NULL, detail TEXT);
"""

#: INDEXES LAST, once every column they name exists.
INDEXES_SCHEMA = """
CREATE INDEX IF NOT EXISTS entities_key ON entities(key);
CREATE INDEX IF NOT EXISTS edges_src ON edges(src);
CREATE INDEX IF NOT EXISTS edges_dst ON edges(dst);
"""

#: The old name, kept so anything that reached for it still finds the tables.
SCHEMA = TABLES_SCHEMA


def _migrate(c: sqlite3.Connection) -> None:
    """Add the columns an older db is missing, before any index needs them.

    A db written before the identity key existed has no `key` column, and `CREATE INDEX
    entities_key` on it raises `no such column: key`. The old `connect()` ran the whole schema
    script - indexes included - and only then asked whether the column existed, so the migration
    was unreachable exactly when it was needed and every command died on an old vault (#23).
    The order is now: tables, then columns, then indexes. The keys are backfilled here, so the
    db is usable the moment `connect()` returns.
    """
    cols = {r[1] for r in c.execute("PRAGMA table_info(entities)")}
    if "key" not in cols:
        c.execute("ALTER TABLE entities ADD COLUMN key TEXT")
    from .graph import identity  # local import: graph imports index, so a top-level one cycles
    for row in c.execute("SELECT id, name FROM entities WHERE key IS NULL OR key = ''").fetchall():
        c.execute("UPDATE entities SET key = ? WHERE id = ?", (identity(row["name"]), row["id"]))


def connect(db: str | Path) -> sqlite3.Connection:
    db = Path(db)
    db.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    c.executescript(TABLES_SCHEMA)
    _migrate(c)
    c.executescript(INDEXES_SCHEMA)
    c.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    c.commit()
    return c


def row_to_entry(row: sqlite3.Row) -> Entry:
    return Entry(
        id=row["id"], kind=row["kind"], state=row["state"], agent=row["agent"],
        project=row["project"], session=row["session"], created=row["created"],
        confidence=row["confidence"] or 0.0, importance=row["importance"] or 0.0,
        ask=row["ask"] or "", summary=row["summary"] or "", evidence=row["evidence"] or "",
    )


def reindex(root: str | Path, db: str | Path) -> int:
    rows = list(iter_entries(root))
    c = connect(db)
    with c:
        c.execute("DELETE FROM entries")
        c.execute("INSERT INTO entries_fts(entries_fts) VALUES('delete-all')")
        for path, e in rows:
            c.execute(
                "INSERT OR REPLACE INTO entries(id,path,kind,state,agent,project,session,created,"
                "confidence,importance,ask,summary,evidence,body) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (e.id, str(Path(path).relative_to(Path(root))), e.kind, e.state, e.agent, e.project,
                 e.session, e.created, e.confidence, e.importance, e.ask, e.summary, e.evidence, e.body),
            )
        c.execute("INSERT INTO entries_fts(rowid, summary, ask, evidence, body) "
                  "SELECT rowid, summary, ask, evidence, body FROM entries")
    c.close()
    return len(rows)


def _fts_query(q: str) -> str:
    toks = [t for t in "".join(ch if ch.isalnum() else " " for ch in q).split() if t]
    if not toks:
        return ""
    # the * must be outside the quotes: '"markdo*"' is a literal phrase, '"markdo"*' is a prefix
    return " AND ".join([f'"{t}"' for t in toks[:-1]] + [f'"{toks[-1]}"*'])


def search(db: str | Path, query: str, kind: str | None = None, state: str | None = None,
           agent: str | None = None, project: str | None = None, limit: int = 10) -> list[dict]:
    c = connect(db)
    match = _fts_query(query)
    if not match:
        return []
    where = ["entries_fts MATCH ?"]
    params: list = [match]
    for col, val in (("kind", kind), ("state", state), ("agent", agent), ("project", project)):
        if val:
            where.append(f"e.{col} = ?")
            params.append(val)
    params.append(limit)
    sql = ("SELECT e.*, snippet(entries_fts, 0, '<<', '>>', '…', 14) AS snip, bm25(entries_fts) AS score "
           "FROM entries_fts JOIN entries e ON e.rowid = entries_fts.rowid "
           "WHERE " + " AND ".join(where) + " ORDER BY rank LIMIT ?")
    out = [dict(r) for r in c.execute(sql, params).fetchall()]
    c.close()
    return out


def stats(db: str | Path) -> dict:
    c = connect(db)
    total = c.execute("SELECT count(*) FROM entries").fetchone()[0]
    by_state = {r[0]: r[1] for r in c.execute("SELECT state, count(*) FROM entries GROUP BY state")}
    by_kind = {
        r[0]: r[1]
        for r in c.execute("SELECT kind, count(*) FROM entries GROUP BY kind ORDER BY 2 DESC")
    }
    ents = c.execute("SELECT count(*) FROM entities").fetchone()[0]
    edges = c.execute("SELECT count(*) FROM edges").fetchone()[0]
    live = c.execute("SELECT count(*) FROM edges WHERE valid_to IS NULL").fetchone()[0]
    c.close()
    return {"entries": total, "by_state": by_state, "by_kind": by_kind,
            "entities": ents, "edges": edges, "edges_live": live, "edges_closed": edges - live}
