# Changelog

Notable changes, newest first. Versions follow `0.x` while the format and the CLI are still moving;
anything that changes a vault's on-disk shape gets an explicit note, because markdown is the source of
truth and a migration is the user's data.

## Unreleased

### Added

- **`quipu ask-backfill`** — asks a chat endpoint one question per entry that has no `ask`, validates the
  answer (one line, ends in `?`, 20–200 bytes, not a restatement of the summary) and writes it in place
  after `summary:`, never renaming the note. Dry run by default, `--sample N` to review proposals,
  `--count` to size the job. This is the verb that makes an imported corpus into training data: the
  validation gate, not the model, is the guard rail, so a truncated or prose answer costs that entry only
  and is reported as `skipped`. Live vault: 241 asks written, 36 rejected, `export sft` 18 → 265 (#27).

- **`quipu repair`** — rewrites notes whose summary still carries the YAML quotes from an older import,
  preferring the fabric original (by frontmatter id), then the note's own body continuation, then a
  quotes-only fix when the text is whole. Dry run by default; a note with no recoverable source is
  reported and left untouched; nothing is renamed. Live vault: 84 of 88 repaired, 4 reported (#58).

### Fixed

- **The live-vault benchmark now scores the answer, not the subject** (#47). `rank1_ok` counted rank 1 as
  correct when its summary merely contained a stem from the question, so a hardware audit that said
  "gateway" scored for "which model is the gateway default" — and the 9/10 it published was quoted as
  precision. It is now `rank1_subject` (trend only, never an accuracy), beside `rank1_promoted`,
  `gold_in_5`, and **`rank1_gold`**: rank 1 is one of the notes hand-verified as an answer to that
  question. Honest headline on the 870-note vault: **6/10, not 9/10**, and two of the four misses have no
  entry in the corpus that answers the question at all (`NO VERIFIED ANSWER IN CORPUS`) — a curation gap,
  not a weight. The row labelled "shipped weights" was also a copy of `10/6/3/1` while the code shipped
  `10/4/2/0.5`: the top row now calls `quipu.index.search` itself, the weights are read from the package
  and pinned by a test, and the copy's baseline row must reproduce the shipped row on every column before
  its variants mean anything. `pytest benchmarks/tests` joins CI, since `testpaths` never reached those
  tests. Result file: `benchmarks/results/live-2026-10-29.json`.

- **Two of the ten live questions had no answer in the vault; writing them took `rank1_gold` from 6/10 to
  8/10 with no code change** (#61). The rank-1 hits for "glitch memory at 3200 FCLK" were the *other* box's
  3900X DOCP note and a stale claim that glitch is a 3900X, and nothing answered "which GPU should I buy".
  Both are now single-purpose `promoted` entries: the Threadripper 3960X at 4x16 GB / 3200 MT/s / FCLK 1600
  with its `lscpu` + `dmidecode` evidence re-checked over ssh, and the standing no-purchase-while-mining
  stance with its layer-split and VRAM reasoning. The remaining two misses are `gold_in_5` hits — right note
  in the list, wrong note first. Curation moved the number; no weight, no ranking change, and the summary
  column went 9/10 -> 10/10 only as a side effect.

- **`reindex` names the entry and the spec for every skipped edge, one line each** (#46). The summary used
  to carry `1 edge skipped: served_by is not in the vocabulary` — the predicate and nothing else, so a vault
  with twenty skips printed one repeated sentence and even a single skip left no file to open. It now prints
  `1 edge skipped` and then `skipped: e1da99d9: 'quipu-vault:served_by:quipu-mcp' (predicate 'served_by' is
  not in the vocabulary; add it to <vault>/.quipu/config.toml)`. The entry is still indexed either way: a
  broken edge is a broken claim, not a reason to lose the memory. The strict xfail that encoded this
  (`test_out_of_vocabulary_spec_is_skipped_and_reported`) went red on the fix as `XPASS(strict)` and the
  marker came off with it, and a second test covers the unparseable-spec shape.

- **Fabric import mangled single-quoted, wrapped summaries** (#54). Fabric writes summaries as YAML
  single-quoted scalars and wraps long ones; the importer was line-based and only understood `"…"` and
  `[…]`, so those summaries arrived with their opening quote attached *and* truncated at the wrap — 88
  entries on the live vault, 50 of them promoted, and one 20-entry family of truncated identical prefixes
  supplied 190 of 206 high-similarity pairs in a semantic sweep. The parser now unquotes single-quoted
  scalars (including the `''` escape) and folds continuation lines, and `quipu audit` reports any summary
  that still starts with a quote character, because fixing the parser does not repair an imported vault.
  Importers are still unblocked by the 280-byte capture limit — a 900-byte historical summary imports.

### Added

- **Time-restricted search** — `quipu search --since/--until` (and the MCP `search` tool's `since`/`until`)
  filter on `created`. A bare date means a day and `--until` runs *through* it; an explicit instant is
  exclusive, offsets are normalised to UTC, and a bad value is a usage error naming the value rather than
  a dropped filter (#52).
- **`quipu brief`** — the standing answer an agent boots with: promoted entries, one line each, grouped
  by kind, importance-first, deterministic and model-free, written to `.quipu/brief/` (derived, never
  beside the entries). Default 40 entries ≈ 2.5k tokens; `--limit 0` for all of them (#51).
- **One sentence per summary** — `quipu write` and MCP `write` now refuse a summary over 280 bytes and
  point at `--body`, because a blob is the one entry class no ranking lever could fix. `write_entry`
  itself is unchanged, so importers still migrate historical notes (#51).
- **Optional semantic search** (`quipu embed`, `quipu search --hybrid`, #5). Vectors come from any
  OpenAI-compatible `/v1/embeddings` endpoint over plain `urllib` — the package still has zero runtime
  dependencies — and are stored in the disposable index. Search fuses the lexical ranking with the
  vector one by weighted reciprocal rank fusion (vector 0.8 / lexical 0.2, k=60, both measured). Off by
  default, degrades to lexical with a reason on stderr when there are no vectors or no endpoint.
  Measured on the synthetic harness (nomic-embed-text-v1.5 f16, CPU, 858 entries in 37 s): R@5 0.640 ->
  0.760, MRR 0.318 -> 0.383, question-shaped R@5 0.52 -> 0.76, exact-phrase R@5 held at 1.00. On the
  curated live vault hybrid scored 8/10 against bm25's 10/10 on ten hand-judged questions — semantic
  neighbours cannot tell a stale claim from a current one, so it is an opt-in tool for questions that
  share no words with the corpus, not a new default.

### Fixed

- `write --supersedes` now closes the live graph edges of the entry it corrects, dated at the moment the
  correction was written, and records the closure in the markdown so a rebuild reproduces the same
  history (#20). An edge the correction restates is left live — restating a fact changes its carrier, not
  the belief. `quipu audit` reports any superseded entry whose edge is still live, which is what a vault
  written by an older version looks like.
- Retrieval ranks by WHERE a match landed, not just by whether there was one: bm25 now takes per-column
  weights (`summary` 10, `ask` 4, `evidence` 2, `body` 0.5), so a note whose body merely contains the
  query words no longer outranks the note whose summary is the claim (#31). On the live 850-note vault
  rank 1 is a correct answer for 9 of 10 hand-judged questions instead of 5; on the synthetic harness
  R@5 0.64 / R@10 0.90 / MRR 0.3182 (0.640 / 0.870 / 0.320 before).
- A thin AND result is topped up from the OR ranking instead of being returned as if complete: one rare
  token in one unrelated note used to satisfy the AND and suppress the OR fallback entirely, giving a
  one-row answer that looked finished (#43). The AND ranking still comes first, and the caller's `limit`
  and filters still apply. Question-shaped recall on the harness: R@5 0.52, R@10 1.00 (0.00 / 0.00 at the
  alpha tag).

## 0.2.0-alpha — 2026-10-29

Tagged `v0.2.0-alpha`; package version `0.2.0a1`. This is the first version meant to be *used*: a live
vault, a binary on PATH, an MCP server an agent can call, CI gating every change, and a benchmark that
reports failure honestly.

The first version that is meant to be *used*, not just tested: a live vault, a binary on PATH, CI that
gates every change, and a benchmark that reports failure honestly.

### Added

- `quipu import fabric <dir>` — reads an Icarus/fabric markdown corpus into a vault (#15). Measured on the
  live corpus: 848 files -> 829 entries in 355 ms, `--dry-run` writes nothing, a re-run is idempotent.
- `benchmarks/` — a retrieval harness with its own corpus, ground truth known by construction, and
  per-kind reporting (#21). First numbers: R@5 0.51, R@10 0.62, MRR 0.24; `question`-shaped recall 0.00.
- `docs/USING.md` (#19), `docs/MEASUREMENTS.md` and the source-level survey in `docs/DESIGN.md` (#12).
- `quipu mcp` — a dependency-free stdio MCP server exposing `search`, `timeline`, `neighbors`, `write` and
  `stats` as tools (#9), so an agent recalls as part of its normal work instead of shelling out.
- A QA suite that encodes defects as strict xfails (#16), and documentation tests that run the documented
  commands instead of trusting them (#33), plus a graph-replay suite that fails if `rm index.db` + `reindex`
  ever stops reproducing the graph (#39).
- CI on 3.10 / 3.12 / 3.13, required on `main` with `enforce_admins: true` (#13).

### Fixed

- Entity identity: punctuation variants are one node, and names in any script no longer collapse to a
  single `unknown` key (#11, #18). Display names and hub filenames get digests where a slug would collide.
- `add_edge` is idempotent across seconds, not just within one (#13).
- `contradict()` validates before it closes anything, so a rejected replacement no longer leaves an entity
  with no live belief (#18).
- The frontmatter writer and reader now share one definition of "this looks like a number", so `04159069`
  keeps its leading zero and `1.5e3` stays a string (#13, #18).
- `importlib` on 3.10: `tomllib` is 3.11+, so a minimal parser covers the TOML subset `Config.save()`
  writes (#13).

### Fixed after the first alpha draft

- Question-shaped searches returned nothing (#25); the importer promoted entries with no evidence (#26) and
  pasted tool transcripts as trusted statements (#30); the whole graph was lost by `rm index.db` (#24); and a
  vault written before the identity key crashed on every command (#23). All fixed and verified against the
  live corpus: `audit` went from 516 problems to `[]`, and question-shaped recall from 0.00 to 0.52.

### Known at this version

- Rank-1 precision ignores *where* a match lands, so the top hit for a topic question is often a demoted
  transcript (#31).
- A migrated pre-`key` database backfills the identity key but not the display name, so `timeline` reports an
  empty graph instead of crashing (#37).
- `write --supersedes` did not close the graph edge it replaced (#20) — fixed in Unreleased, so a vault
  written by this version may still carry a superseded claim's edge as live; `quipu audit` now names them.
- The graph is only as populated as the `--edge` assertions written — the imported corpus carried none.
- Imported history contributes recall, not training data, until the `ask` backfill lands (#27).

## 0.1.0

The lifecycle (capture -> reflect -> promote -> correct), the temporal graph, and the three exports
(SFT JSONL, DPO preference pairs, graph nodes/edges), with a CLI and 34 tests.
