"""Score retrieval variants on the ten realistic questions against a COPY of the live vault.

The synthetic harness cannot show this failure: its clusters share a rare marker term with the right
note, so BM25 always has a clean signal. The live corpus is 850 notes where the words are everywhere,
several notes are pasted transcripts, and the answer is a note whose SUMMARY is the claim — not a note
whose body happens to contain the words.

Scoring, stated plainly because it is a human judgement made mechanical:
  rank1_ok     rank 1 mentions the subject of the question (one required stem per query, listed below).
               Loose on purpose — it asks "is this about the right thing", not "is this the exact note".
  promoted_r1  rank 1 is a promoted entry (survived the gate) rather than raw/pruned.
  gold_in_5    one of the notes listed as a human-verified answer is in the top five.

    python benchmarks/live_queries.py /tmp/livevault
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from quipu.index import _fts_query, _fts_query_or, connect  # noqa: E402

#: (question, the stems a correct rank-1 summary contains, notes verified as answers)
QUERIES = [
    ("why did glitch memory run at 3200 fclk", ("fclk", "3200", "mce"), ()),
    ("which model is the gateway default for this team", ("minimax", "model", "gateway"), ()),
    ("where is the cloudflare tunnel token", ("cloudflared", "tunnel", "token", "cert.pem"), ()),
    ("what broke with transformers 5.8 and florence", ("florence", "transformers"), ()),
    ("how do we back up secrets to usb", ("usb", "secret", "backup", "key"), ()),
    ("which gpu should i buy for local ai", ("gpu", "vram", "6700", "3090", "4080"), ()),
    ("how do we deploy a pump.fun token", ("pump", "deploy", "token"), ()),
    ("obs overlay transparency keying", ("obs", "transparen", "chroma", "alpha"), ()),
    ("hiveos rig ip addresses for the mining fleet", ("hiveos", "rig", "fleet"), ()),
    ("did we already try embeddings for search", ("embed", "vector", "hybrid"), ()),
]

FLAT = (1.0, 1.0, 1.0, 1.0)
MILD = (4.0, 2.0, 2.0, 1.0)
SHIPPED = (10.0, 6.0, 3.0, 1.0)


def scan(c, match, weights, limit):
    sql = ("SELECT e.id, e.state, e.kind, e.summary, bm25(entries_fts, ?, ?, ?, ?) AS score "
           "FROM entries_fts JOIN entries e ON e.rowid = entries_fts.rowid "
           "WHERE entries_fts MATCH ? ORDER BY score LIMIT ?")
    return [dict(r) for r in c.execute(sql, list(weights) + [match, limit]).fetchall()]


def search(db, query, weights=FLAT, mode="and_only", demote=None, limit=5, window=4):
    """`demote` multiplies the score of a state. bm25 scores are NEGATIVE distances, so a factor
    BELOW 1 makes an entry rank worse and a factor ABOVE 1 makes it rank better."""
    demote = demote or {}
    c = connect(db)
    match, loose = _fts_query(query), _fts_query_or(query)
    win = max(limit * window, limit)
    try:
        rows = scan(c, match, weights, win)
        if mode == "and_only" and not rows and loose != match:
            rows = scan(c, loose, weights, win)
        elif mode == "and_thin" and loose != match and len(rows) < limit:
            seen = {r["id"] for r in rows}
            rows += [r for r in scan(c, loose, weights, win) if r["id"] not in seen]
    finally:
        c.close()
    for r in rows:
        r["score"] *= demote.get(r["state"], 1.0)
    rows.sort(key=lambda r: r["score"])
    return rows[:limit]


def evaluate(label, db, **kw):
    ok = promoted = in5 = 0
    misses = []
    for question, stems, gold in QUERIES:
        hits = search(db, question, **kw)
        top = hits[0] if hits else None
        text = ((top["summary"] if top else "") or "").lower()
        if top and any(s in text for s in stems):
            ok += 1
        else:
            misses.append((question, (top["summary"][:70] if top else "<no hits>")))
        if top and top["state"] == "promoted":
            promoted += 1
        if gold and any(h["id"] in gold for h in hits):
            in5 += 1
    n = len(QUERIES)
    print(f"{label:<52} rank1_ok={ok}/{n}  rank1_promoted={promoted}/{n}  gold_in_5={in5}/{n}")
    return ok, promoted, misses


def main():
    db = Path(sys.argv[1]) / ".quipu" / "index.db"
    print(f"corpus: {db}\n")
    variants = [
        ("A baseline: and_only, flat", dict(weights=FLAT, mode="and_only")),
        ("B and_thin, flat", dict(weights=FLAT, mode="and_thin")),
        ("C and_thin, mild weights", dict(weights=MILD, mode="and_thin")),
        ("D and_thin, shipped weights", dict(weights=SHIPPED, mode="and_thin")),
        ("E and_thin, flat, raw x0.5", dict(weights=FLAT, mode="and_thin", demote={"raw": 0.5})),
        ("F and_thin, flat, raw x0.5 pruned x0.1",
         dict(weights=FLAT, mode="and_thin", demote={"raw": 0.5, "pruned": 0.1})),
        ("G and_thin, mild, raw x0.5 pruned x0.1",
         dict(weights=MILD, mode="and_thin", demote={"raw": 0.5, "pruned": 0.1})),
        ("H and_thin, mild, raw x0.7 pruned x0.2",
         dict(weights=MILD, mode="and_thin", demote={"raw": 0.7, "pruned": 0.2})),
        ("I and_thin, shipped, raw x0.5 pruned x0.1",
         dict(weights=SHIPPED, mode="and_thin", demote={"raw": 0.5, "pruned": 0.1})),
        ("J and_thin, shipped, raw x0.7 pruned x0.2",
         dict(weights=SHIPPED, mode="and_thin", demote={"raw": 0.7, "pruned": 0.2})),
        ("K and_thin, shipped, raw x0.4 pruned x0.1",
         dict(weights=SHIPPED, mode="and_thin", demote={"raw": 0.4, "pruned": 0.1})),
        ("L and_thin, sharp (20/10/4/1)", dict(weights=(20.0, 10.0, 4.0, 1.0), mode="and_thin")),
        ("M and_thin, ask-heavy (8/8/2/1)", dict(weights=(8.0, 8.0, 2.0, 1.0), mode="and_thin")),
        ("N and_thin, summary-only (10/1/1/0.2)", dict(weights=(10.0, 1.0, 1.0, 0.2), mode="and_thin")),
        ("O and_thin, shipped + raw x0.8 pruned x0.2",
         dict(weights=SHIPPED, mode="and_thin", demote={"raw": 0.8, "pruned": 0.2})),
        ("P and_thin, mid (10/4/2/0.5)", dict(weights=(10.0, 4.0, 2.0, 0.5), mode="and_thin")),
        ("Q and_thin, mid2 (10/3/1/0.5)", dict(weights=(10.0, 3.0, 1.0, 0.5), mode="and_thin")),
    ]
    results = []
    for label, kw in variants:
        ok, promoted, misses = evaluate(label, db, **kw)
        results.append((ok, promoted, label, misses))
    best = max(results, key=lambda r: (r[0], r[1]))
    print(f"\nbest: {best[2]}\n")
    for question, top in best[3]:
        print(f"  MISS {question!r}\n       rank1: {top}")


if __name__ == "__main__":
    main()
