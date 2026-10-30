# Changelog

Notable changes, newest first. Versions follow `0.x` while the format and the CLI are still moving;
anything that changes a vault's on-disk shape gets an explicit note, because markdown is the source of
truth and a migration is the user's data.

## 0.5.2 — 2026-10-31

### Fixed

- **a real transcript now fits the default endpoint budget.** `[llm] timeout` defaulted to 120 s and one
  request is one chunk: a 12-line slice of a Hermes session is 68 KB and a 185-line session is 509 KB, so
  the reflection pass died on exactly the sessions it exists for — `LLM request failed: … TimeoutError:
  timed out` — while a short `transcript.md` hid the problem completely. The default is 600 s, sized from
  that measurement, and `HttpProvider`'s own default now reads the config constant instead of carrying a
  second copy of the number.
- **a timeout says which knob to turn.** The message now names how long it waited, that one chunk is one
  request, and the three things a user can change (`--endpoint-timeout` / `[llm] timeout`, `--max-chars`,
  a faster endpoint). A connection error keeps the plain message: chunk-size advice belongs on a timeout
  only, and a test pins that so it cannot spread.

## 0.5.1 — 2026-10-31

A patch on the 0.5.0 guards, and one measurement worth having.

### Fixed

- **a reflection proposal now faces the same handle rule as every other write** (#85). `apply()` was the
  last surface that wrote a summary without checking its shape: a model's JSON can carry a
  quoted-scalar or a serialised blob, and both went to disk unchecked while the CLI, `capture` and the
  importer all refused or repaired them (#77). A blob is replaced by the body's first prose line; a blob
  with no prose anywhere keeps its text, because a mis-shaped handle costs the handle, never the memory.

### Measured

- **the reflection pass produces edges.** A real 68 KB transcript chunk (12 JSONL lines) through the
  on-box Flash-Next endpoint: 4 proposals, 4 promoted, 0 raw, and **every promoted entry carried an edge**
  (`npm:used_by:solana-volume-bot`, `walls.csv:part_of:solana-volume-bot` x3) — 3 graph nodes from one
  chunk. So the live vault's 3-edges-for-892-entries is not the pass failing at edges; it is the pass not
  being run. That changes what #85 option 3 is worth: the machinery is already there.

Live corpus at the tag: 893 entries, 321 promoted, `audit` clean.

## 0.5.0 — 2026-10-31

The graph release. 0.4.0 put the vault inside the agent; this one makes the relations between what is
in it reachable — without inventing an edge the corpus does not support.

The through-line: **the graph was empty because it was unreachable, not because it was useless.** The
live vault held 892 entries and 3 edges. Two of those three problems were plumbing, one was a
temptation.

### Added

- **the provider can assert edges, not just nodes** (#84). `quipu_write` gained `edges`
  (`src:predicate:dst`) and `supersedes`, and its schema names the closed predicate vocabulary — a model
  that can see the boundary is less likely to invent past it, and an invented predicate is reported in
  `skipped_edges` instead of written. This was the whole reason the graph stood still: `quipu write
  --edge` and MCP `_write` both carried an edge, but the surface the team actually writes through
  (`memory.provider: quipu`) did not, so a night of three agents capturing knowledge produced 0 relations.
- **project hubs** (#85, first slice). `quipu neighbors <project>` and `quipu timeline <project>` now
  carry the entries tagged with that project. Derived at read time from the tag, not persisted as one
  `part_of` edge per entry — the tag is already the assertion and already in the markdown, and
  `graph.rebuild` re-folds db edges back into the markdown on every reindex, so persisting would rewrite
  466 files forever to restate them. Live: `neighbors shitter` returns 69 promoted entries.
- **citations** (#85, second slice). `quipu neighbors <entry-id>` reports what that entry cites and what
  cites it, resolved against ids the corpus actually has. Also derived, for the same reason: a citation
  is reproducible from the body that makes it.
- **`audit` reports an off-vocabulary predicate** — one line per edge, live and closed. The vocabulary is
  closed per vault (`.quipu/config.toml` extends the defaults) and until now nothing said so when an edge
  arrived outside it.
- **`stats` reports graph connectivity** — `edges_by_predicate`, `graph_nodes`, `entries_with_an_edge`,
  and the top project hubs, so the gap between "has knowledge" and "has relations" is visible instead of
  assumed.

### Fixed

- **a summary must be prose** (#77). `write`, `capture` and the fabric importer all accepted a summary
  that opened with `{`, `[` or a code fence; a blob's tokens rank exactly as well as prose's, so 21
  entries on the live vault were competing for a 400-token recall budget with text no agent could read.
  The write boundary now refuses the shape (exit 2, `--allow-summary-blob` to override), `capture` takes
  the first *prose* line and keeps the blob in the body, the importer derives one from the body and
  refuses only when there is no prose at all, and `audit` reports anything that got in before the rule.
  Live: 19 flagged handles repaired, `audit` clean.

### Notes

- Two rules keep the derived views honest, both learned from the live corpus: `unknown` is never a hub
  (425 entries carry it — it is a bucket, not a claim), and `pruned` entries are excluded from a hub
  unless `--all` (a browse surface that leads with rejected material teaches an agent to cite it; the
  first live `neighbors shitter` led with transcript-summary junk until this rule existed).
- Still open on #85: `mentions` edges and reflection-derived semantics. The measurement that decides
  them is whether a real reflection pass over a real transcript emits edges at all — the pass already
  persists `edges` from promoted proposals and already refuses an unknown predicate without losing the
  entry (`tests/test_promote.py`).

Live corpus at the tag: 892 entries, 320 promoted, `audit` clean.

## 0.4.0 — 2026-10-30

The provider release. 0.3.0 made the labels trustworthy; this one puts the vault inside the agent instead
of beside it. `memory.provider: quipu` in a Hermes profile now means recall and capture happen without
anyone remembering to run a command.

The through-line: **a memory provider is a contract, and the contract is cheap.** Recall is a BM25 search
over the index the vault already keeps — 9 ms, no model call, works offline. Capture is one JSON line per
turn, never an entry per turn. The prompt block is 27 tokens and carries no recalled text. Everything an
agent writes still faces the same promotion gate as everything else, so the tool cannot mint a `promoted`
entry any more than the importer can.

Live corpus at the tag: 887 entries, 315 promoted, 3 graph edges, `audit` clean.

### Added

- **quipu is a packaged Hermes memory provider** (#10). A new `quipu/hermes_provider.py` implements the
  `MemoryProvider` contract and registers through the `hermes_agent.memory_providers` entry point, so
  `pip install` is the entire setup. Measured on the live vault: `prefetch()` 8.9 ms and 120 words
  against a 400-token budget, `system_prompt_block()` 27 tokens, `queue_prefetch()` on a daemon thread.
  `sync_turn()` appends `{ts, session, user, assistant}` to `<vault>/.quipu/session-<id>.jsonl` and
  writes **no entries** — a provider that mints one entry per turn is the append-only failure this
  project exists to fix. Tools `quipu_search` and `quipu_write` return JSON strings; an unknown tool, a
  missing vault or a broken index degrades to an error object and never raises into the agent loop.

- **The write tool exposes the promotion gate** (#79). `quipu_write` always wrote `state: raw`, so an
  agent's knowledge was present, searchable, unpromotable and never exported. It now takes `importance`
  and `state`, and promotion is decided by the same rule as everywhere else: evidence **and** importance
  at or above `promote_threshold`. `state: promoted` without the grounds writes `raw` and says why in a
  `note`; `state: pruned` is honoured, because pruning is a decision rather than a gate question. The
  reply carries `{id, path, state, importance}`.

- **`docs/HERMES.md`** — installing the provider, the two-line profile config, what each hook actually
  does, the vault-resolution order (`QUIPU_VAULT`, then `<hermes_home>/quipu-vault`, then
  `~/quipu-vault`), the session journal, and how to measure recall from both sides. `docs/USING.md` gains
  the same section from the CLI side.

### Fixed

- **The provider entry point names a package, not a module** (#80). Hermes resolves a provider's
  directory from its entry point *without importing it*, so `quipu.hermes_provider:register` resolved to a
  module origin, returned no directory, and the provider read as **not installed** while being installed
  correctly — every session logged `configured but not installed` and ran with no recall. The entry point
  is now `quipu:register`, with a thin re-export in `quipu/__init__.py`, and
  `tests/test_provider_entry_point.py` pins the packaging contract so it cannot regress silently.

- **Nineteen junk entries removed and twenty-seven summary repairs in the live vault.** Nine
  `PONG <your model>` probe captures and two raw task prompts were quarantined to
  `quarantine-20261030/`; six entries imported with `summary: ""` (invisible to every search) and
  twenty-one whose summary was a JSON blob got statements derived from their bodies. Filed as #77: the
  write boundary should refuse a summary that is not prose, rather than accept it and bury the entry.

## 0.3.0 — 2026-10-29

The gate release. 0.2.0-alpha proved quipu could be used; this one makes its labels and its edges
trustworthy enough to build a dataset from, and makes a release something a machine produces rather than
something a person remembers to do.

The through-line is the same argument at three sizes: **`state` is a claim with evidence behind it, not a
mood.** An import that inherits a dead system's `training_value` now demotes every entry that cannot show
evidence and says so in the report; a session-end hook that cannot answer leaves the vault untouched and
exits non-zero; an index written by an older version is rebuilt from the markdown rather than migrated.
Everything that writes also says what it refused to write.

Live corpus at the tag: 879 entries, 309 promoted, 3 graph edges, `audit` clean.

### Added

- **The import report says why** (`quipu import fabric`, #17). `skipped` was a bare integer that meant both
  "noise we meant to drop" and "we could not parse this file", and the subdirectories the importer ignores
  were not mentioned at all — 4,635 markdown files on the archived corpus. The report now carries
  `skipped_by_reason` (`ignored name`, `empty body`, `no type`, `unparsable frontmatter`, `duplicate id`,
  `noise (training_value: low)`) and `directories_ignored` (each subdirectory and how many markdown files it
  holds, counted at any depth). Policy stays top-level-only; it is now visible policy.

- **The endpoint read timeout is configurable** (`[llm] timeout`, `--endpoint-timeout`, #68). One
  request to the model endpoint is now bounded by a read timeout that defaults to 120 s; a vault
  whose `.quipu/config.toml` carries no `timeout` key keeps that default. `--endpoint-timeout
  SECONDS` on `quipu reflect` and `quipu hook` overrides it for that command. A value of `0`, a
  negative number or a non-integer is refused with an error naming `llm.timeout`. Why the knob
  exists, measured on this box's local `qwen3.6-35b-a3b`: ~3 KB of transcript takes 104 s, and a
  3.5 KB chunk fails on the old fixed 120 s cap.

- **Transcripts are loaded by shape and chunked on turn boundaries** (`quipu reflect --max-chars`, #2).
  Hermes session JSONL (`role`/`content`, a string or a list of text parts) is rendered as `role: text`;
  markdown passes through; meta rows and malformed lines are skipped, not fatal. The pass runs per chunk,
  splitting only at a turn boundary, keeping a single oversized turn **whole** rather than truncating it,
  and de-duplicating proposals across chunks so one claim stays one entry. `quipu hook` shares the loader
  and the cap, and its timeout still bounds the whole pass. Measured: 3 KB → 0 proposals in 104 s before
  this; 14 KB → 4 chunks at `--max-chars 3500` where the single-prompt version had hit the endpoint's own
  120 s read timeout.

- **`quipu hook`** — the session-end entry point (#3). `quipu hook --transcript <file|-> --agent <a>`
  runs reflect → apply → index in one call and speaks only in exit codes: 0 if the pass completed,
  non-zero with the reason on stderr for a missing or empty transcript, a transport error, an
  unparseable response or a timeout. Nothing is written until the model has answered in full, so a
  failed pass leaves the vault untouched; `--timeout` (default 300 s) cuts a hung endpoint off on a
  worker thread because the call sits in a framework's shutdown path; `QUIPU_HOOK_DRY_RUN=1` prints the
  prompt and writes nothing. A completed pass that proposes nothing exits 0 **and says so on stderr** —
  a hook that runs for a week and writes nothing must never be silent.

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

- **The hook reads through the transcript loader** (#70). `quipu hook` said it shared `reflect`'s loader and
  did not: it handed raw text to the chunker, so a session JSONL reached the model as raw JSON rows and the
  chunker fell into its markdown branch. A loader failure is still a `HookError`, the chunk breakdown goes
  to stderr when `--dry-run` keeps stdout for one JSON document, and the load now counts the malformed lines
  it dropped — a session that quietly lost half its lines looked exactly like a clean one.
- **The 3.10 config parser strips inline comments** (#72). A hand-commented `config.toml`
  (`promote_threshold = 0.5  # keep`) loaded on 3.11+ and raised on 3.10, because `_mini_toml` never stripped
  trailing comments; `[llm] timeout` survived only because it is pre-scanned. Scalars now pass through the
  quote-aware stripper and arrays are stripped once closed, so the same file loads to the same values on
  every supported interpreter.
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

- **A database from an older schema is rebuilt from the markdown instead of migrated** (#37). Migrating
  added the `key` column and backfilled it, but the entity rows kept their old display names, so the join
  found nothing and `timeline` reported an **empty graph** for a vault whose notes held the edges — better
  than the crash (#23) and worse than the truth. Any db whose `user_version` is behind now gets dropped and
  rebuilt (entries, graph and entities) on the way into a CLI command or the MCP server, with one line on
  stderr: `index schema v0 -> v2: rebuilt from markdown (873 entries, 2 edges)`. A current db is left alone,
  and an unreadable one counts as stale. The index is derived; the markdown is the truth.

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
