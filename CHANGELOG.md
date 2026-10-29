# Changelog

Notable changes, newest first. Versions follow `0.x` while the format and the CLI are still moving;
anything that changes a vault's on-disk shape gets an explicit note, because markdown is the source of
truth and a migration is the user's data.

## Unreleased — target `0.2.0-alpha`

The first version that is meant to be *used*, not just tested: a live vault, a binary on PATH, CI that
gates every change, and a benchmark that reports failure honestly.

### Added

- `quipu import fabric <dir>` — reads an Icarus/fabric markdown corpus into a vault (#15). Measured on the
  live corpus: 848 files -> 829 entries in 355 ms, `--dry-run` writes nothing, a re-run is idempotent.
- `benchmarks/` — a retrieval harness with its own corpus, ground truth known by construction, and
  per-kind reporting (#21). First numbers: R@5 0.51, R@10 0.62, MRR 0.24; `question`-shaped recall 0.00.
- `docs/USING.md` (#19), `docs/MEASUREMENTS.md` and the source-level survey in `docs/DESIGN.md` (#12).
- A QA suite that encodes defects as strict xfails (#16), and documentation tests that run the documented
  commands instead of trusting them (#33).
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

### Known at this version

- Question-shaped searches return nothing until #25.
- The importer promotes entries without evidence (#26) and pasted tool transcripts (#30) as trusted
  statements; `quipu audit` reports both, and a re-import after those fixes corrects the state.
- The graph is only as populated as the `--edge` assertions written, and it was not reproducible from the
  markdown before #24.
- Imported history contributes recall, not training data, until the `ask` backfill lands (#27).

## 0.1.0

The lifecycle (capture -> reflect -> promote -> correct), the temporal graph, and the three exports
(SFT JSONL, DPO preference pairs, graph nodes/edges), with a CLI and 34 tests.
