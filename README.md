# quipu

**Local-first agent memory that knows what it changed its mind about.**

A quipu is an Inca record: knotted cords where the knots *are* the data. This one keeps your agent's
memory as plain markdown, adds a temporal knowledge graph on top, and — the part nothing else ships —
turns the memory's own lifecycle into labelled training data.

```bash
pip install -e ".[dev]"        # or: uv sync
quipu init --vault ~/memory
quipu capture "the 3600 DOCP config threw 649 CCEs in one boot" --project wraith
quipu reflect session.md --agent glitch --project wraith    # one LLM pass, gated
quipu search "memory stability" --project wraith
quipu export sft --out data/sft.jsonl                        # supervised examples
quipu export dpo --out data/dpo.jsonl                        # preference pairs
```

## Why another memory system

Three things go wrong in every "agent memory" I have run in anger:

1. **It appends instead of promoting.** Auto-writes at session end fill the corpus with
   `session: "fixed a bug"` rows. In the corpus this project was extracted from, 84% of 5,469 entries
   were `session` noise and 73% were tagged `low` — 4,049 rows that made retrieval worse, not better.
2. **It forgets that it forgot.** A note that says "DOCP 3600 is stable" is indistinguishable from one
   that says "DOCP 3200 is stable" unless the graph keeps *when* each was true and *why* it changed.
3. **It exports a pile, not a dataset.** A flat dump of entries is not fine-tuning data: no prompts,
   no negatives, no provenance, no way to rebuild the dataset from the corpus.

quipu is built around the inverse of each:

| Problem | quipu |
|---|---|
| append-only noise | **capture → reflect → promote** ladder; raw captures are cheap and stay raw until a gated reflection pass promotes them |
| memory that silently goes stale | **temporal edges** (`valid_from` / `valid_to`); contradiction is an operation, not an edit. History survives |
| unusable for training | **lifecycle labels**: promoted → positive example, pruned → negative, superseded → preference pair. Export with provenance |

## The thesis: the lifecycle *is* the label

Most systems throw away the most valuable signal they produce. When memory decides something mattered,
it has labelled that entry. When it later corrects itself, it has produced a preference pair.

```
capture ──(reflect)──► proposal ──(gate: importance ≥ 0.5 AND evidence)──► promoted
                          │                                                 │
                          └──────── below gate ──────────────► raw          └─(superseded by a later entry)
                                                                                └─► DPO pair: stale ≺ corrected
```

`quipu export dpo` emits exactly that:

```json
{"prompt": "can I run DOCP 3600 on the TRX40 after the BIOS flash?",
 "chosen": "DOCP 3600 with FCLK 1800 is stable after BIOS 2601 — 3 boots, zero MCEs. Evidence: ...",
 "rejected": "DOCP was dropped from 3600 to 3200 with FCLK 1600 to resolve instability. Evidence: 649 CCEs in boot -1",
 "meta": {"reason": "3 consecutive boots, journalctl -k per boot: 0 MCE lines each"}}
```

That record is a real one, produced by the tool from a real correction. Note what it carries: the
question that was actually asked, both answers, and the evidence that decided it.

## Design rules

- **Markdown is the source of truth.** The vault is an Obsidian folder. SQLite is a disposable index
  (`quipu reindex` rebuilds it in milliseconds); delete the db and you lose nothing.
- **Evidence or it did not happen.** A promoted entry without evidence is a bug, and `quipu audit` says so.
- **Closed predicate vocabulary.** An open predicate space becomes 400 spellings of "uses". New
  predicates go in the config, deliberately.
- **One entity, one spelling.** Names are canonicalised on write; the spelling seen becomes an alias.
- **The graph learns only from promoted entries.** A raw capture is allowed to be wrong.
- **Reflection runs isolated.** It is its own process with its own context — it never pollutes the
  session it is reviewing, and it runs while the context is still fresh.
- **No vendor, no daemon, no lock-in.** One Python package, stdlib only at runtime, any
  OpenAI-compatible endpoint for the reflection pass.

## Commands

| command | what it does |
|---|---|
| `quipu init` | create a vault |
| `quipu capture` | store a raw capture (cheap, unfiltered) |
| `quipu reflect <transcript>` | one reflection pass → proposals → gated writes |
| `quipu write` | write a curated entry directly (with `--supersedes`) |
| `quipu search` | BM25 over entries, filterable by kind/state/agent/project |
| `quipu show` | print an entry |
| `quipu edge` / `invalidate` / `contradict` | assert and retract graph edges |
| `quipu timeline <entity> [--at ISO]` | what was true, and when |
| `quipu neighbors <entity> --depth 2` | graph neighbourhood |
| `quipu export sft\|dpo\|graph` | write a dataset |
| `quipu audit` | find corpus problems (promoted-without-evidence, dangling supersedes) |
| `quipu stats` | promotion rate, graph size, dataset counts |

## What this is not

- Not a vector database. Retrieval is BM25 (SQLite FTS5) today; hybrid + embeddings is on the roadmap,
  not in `main`.
- Not a hosted service. Two processes maximum: you and the LLM you point it at.
- Not a finished product. It is a working v0.1 with tests, built to be developed in the open.

## Status

v0.1.0 — core lifecycle, temporal graph, and three exports are implemented and tested (34 tests).
See [PLAN.md](PLAN.md) for what lands next.

MIT licensed.
