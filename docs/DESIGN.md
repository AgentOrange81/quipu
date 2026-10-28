# Design

## 1. The problem, stated precisely

An agent that runs for months accumulates three kinds of knowledge, and each needs a different
mechanism:

| kind | example | right mechanism |
|---|---|---|
| **episodic** | "we tried fp16 VAE on gfx1031 and the ring hung" | cheap capture, kept verbatim |
| **semantic** | "gfx1031 needs `--cpu-vae` for heavy decodes" | promotion: distilled, always-loadable |
| **relational** | "quipu depends_on sqlite; sqlite used_by fts5" | graph edges, with validity windows |

Most systems store all three the same way — one vector table, one chunk size — and then wonder why
retrieval feels like guessing. quipu separates the storage per kind while keeping one query surface.

## 2. Three layers

```
notes/<id>-<slug>.md     the entries: episodic + semantic, frontmatter + body      <- source of truth
.quipu/index.db          FTS5 index over entries; entities; temporal edges         <- derived, disposable
entities/<slug>.md       graph hubs, written from the db                          <- browsable mirror
```

The db can be deleted at any time; `quipu reindex` recreates the FTS index and `quipu edge` replays
rebuild the graph from the entries' own frontmatter if you keep edge assertions in them. Nothing in
the design requires the db to be the truth.

## 3. The lifecycle

States are explicit because they are training labels, not bookkeeping:

- **raw** — captured. Zero cost, zero trust. Never enters the graph, never exports.
- **promoted** — survived the gate. Enters the graph, exports as SFT, is eligible to be superseded.
- **pruned** — was promoted, later judged wrong or superseded. With `--supersedes` on the replacement,
  the pair becomes a preference example.

The gate is deliberately dumb and inspectable:

```python
promoted = importance >= cfg.promote_threshold and bool(evidence)
```

`importance` comes from the reflection pass; `evidence` is a required field the model must fill with
something a skeptic could re-check (a number, a command, a path, a log line). A model that cannot
produce evidence has not produced a memory.

## 4. Why the ask matters

A distilled fact is only half a training example. Without the question, `quipu` would be exporting
answers to questions nobody asked — which teaches a model to volunteer facts. So every promoted entry
carries `ask`: the request it answers, self-contained. Export filters asks shorter than 20 characters
or under three words, because "did that fix it" is a conversation turn, not a prompt.

## 5. Temporal edges, not deletion

```sql
edges(id, src, predicate, dst, note_id, confidence, valid_from, valid_to)
```

- `assert` opens an edge (`valid_to IS NULL`).
- `invalidate` closes it at an instant. The row stays.
- `contradict` closes the old and opens the new in one transaction.
- `timeline(entity, at=...)` answers "what did we believe in June".

Deleting an edge destroys the most valuable record in the system: that we used to think otherwise.
MemPalace reached the same conclusion with its `invalidate + add at one instant` normalisation; we
take the mechanism and add the export that falls out of it.

## 6. What was taken from where

Only what has been verified against source (this table grows as the survey continues):

| system | what we took | what we deliberately did not |
|---|---|---|
| **Icarus / fabric** (the system this replaces) | the entry taxonomy (decision/lesson/resolution/handoff), the idea that an entry needs a summary that is a statement | auto-append at session end; an open predicate space; a flat corpus with no lifecycle |
| **MemPalace** | temporal entity-relationship graph with validity windows; closed predicate vocabulary normalised by invalidate+add; a cheap `audit` pass that scores the corpus and prints a repair list; hub files browsable as markdown | verbatim-only storage (we need distillation for training), ChromaDB as a required dependency |
| **Honcho** | the "deriver" pattern: an asynchronous background pass that reads messages and maintains a representation, so recall is not a per-request LLM call; peer/session separation (our `agent` / `session` fields) | a Postgres service and AGPL coupling; theory-of-mind representations are out of scope for v1 |
| **self-evolving memory (Cole Medin's KB)** | promotion instead of appending; a reflection pass run in an isolated context so it does not pollute the working one; the dual posture of a hand-curated rules file next to autonomous memory | treating the promoted file as the only artifact — we keep the raw layer and the graph |

## 7. Failure modes this design is a reaction to

Each of these cost real time in the corpus quipu was extracted from:

1. **84% noise.** Auto-writing a `session` entry at every session end put 4,572 session rows in front
   of 855 real ones. Fixed by the gate — and by making raw captures free but promotion expensive.
2. **A silent hook failure.** A `KeyError` in the scoring function killed every session-end write for
   months; the only symptom was a one-line warning. Hence `quipu audit`, and hence tests that assert
   *an entry was written*, not that a call returned.
3. **Evidence-free entries.** A plausible summary with no proof is worse than nothing: it gets
   retrieved and trusted. Hence `evidence` is required for promotion and audited.
4. **Entity twins.** "DOCP 3200", "docp-3200", "do cp-3200" in three nodes. Hence canonicalisation at
   write time with the observed spelling kept as an alias.
5. **Encoding damage.** JSON `\uXXXX` escapes and lone surrogates have already cost this project one
   migration. The store writes UTF-8 strictly and tests unicode round-trips.
