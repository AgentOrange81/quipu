# Retrieval benchmarks

What quipu's retrieval actually returns, measured with the shipped code, published with the
failures next to the score.

## The one rule

A number that is not in results/ does not exist.

Every number quoted below comes from a file in `results/`, and that file records the corpus
digest, the query-set digest, the git revision and the date it was produced. If a figure is not
in `results/`, it did not happen. That rule cuts both ways: it is not allowed to quote a number
that is not in a result file, and it is not allowed to leave a result file out of the record.

## What is measured

The shipped retrieval path, end to end. The harness re-implements nothing:

    quipu.store.write_entry   -> markdown notes
    quipu.index.reindex       -> SQLite FTS5 (unicode61 + porter)
    quipu.index.search        -> bm25 ranking, the same call the CLI makes

The corpus is written into a temporary vault, indexed by the real index, and queried with the real
`search`. A number is therefore the number an agent would get from `quipu search`, not a
re-implementation's idea of it.

## Datasets

### `synthetic` (default; no network, no download)

200 generated notes in 25 clusters of eight. A cluster is one subject area: eight notes about the
same subject, in the same domain, under one project, all mentioning the same rare marker term
(`kestrel`, `anvil`, `basalt`, ...). The clusters exist so that the corpus is not a list of
unrelated sentences: a query has to pick between eight similar notes, and a query built from the
subject alone has to compete with dozens.

The corpus is a pure function of `benchmarks/loaders.py`: same file in, same corpus out, every
run. The result file carries `corpus_sha256` and `query_set_sha256` so a run can be tied to the
exact corpus and questions it was scored on.

Four query kinds, one per cluster, 100 queries in total. Ground truth is defined by construction:
each query is generated from one chosen note, and that note's id is the gold.

| kind | what the query looks like | what it measures |
|---|---|---|
| `rare` | the marker term + the domain (`kestrel checkout service`) | can the retriever pick the right note out of eight near-identical ones? |
| `phrase` | the subject + a two-word phrase from the note (`postgres write path`) | does a phrase query find the note it came from, and does it rank first? |
| `paraphrase` | the same fact in different words (`purchase pipeline postgresql`) | can the retriever find a note that never used those words? |
| `question` | a natural question (`why did we choose postgres for atlas`) | what happens when the query is a sentence? |

The gold note rotates through the eight notes of each cluster, so no query kind is aligned with
the length of its gold note. BM25's bias toward short documents therefore hits every kind
equally, instead of being engineered into one class.

### `longmemeval` (local file only)

`--dataset longmemeval --path FILE` loads a local LongMemEval JSON file: each haystack session
becomes a note, and the notes containing the answer string are the gold. The dataset is never
downloaded. If the file is not there, the run prints `dataset not present, skipping` and exits 0
without writing a result file. There is no number for a dataset that was not run, and no
placeholder for one either.

## Metrics

- **R@k** - mean over queries of `|top-k hits ∩ gold| / |gold|`. With one gold note per query
  this is the hit rate.
- **MRR** - mean over queries of `1 / rank of the first gold hit` in the retrieved list. The list
  is capped at the largest requested k; a gold note outside it scores 0.

Both are reported per query kind, because one number over four different kinds of query says
nothing about which part of retrieval is weak.

## Results

From `results/synthetic-bm25-2026-10-28.json` (corpus `synthetic v1`, 200 notes, 100 queries,
git `77b5dd3`, run 2026-10-28):

| | R@5 | R@10 | MRR |
|---|---|---|---|
| **all queries** | **0.51** | **0.62** | **0.2422** |
| rare | 0.64 | 1.00 | 0.2987 |
| phrase | 1.00 | 1.00 | 0.4267 |
| paraphrase | 0.40 | 0.48 | 0.2433 |
| question | 0.00 | 0.00 | 0.0000 |

### What the misses have in common

38 of the 100 queries did not return their answer within the top 10. They fail in three distinct
ways, and all three are properties of the retrieval stack, not of the corpus:

1. **One absent word kills the whole query (25 queries, the `question` kind).** `_fts_query`
   joins every token with `AND`, and the last token is a prefix match. A question contains an
   interrogative ("why", "did", "choose") that appears in no note, so the query matches nothing
   at all - zero results, not bad results. This is the single biggest lever in the table, and it
   is why `docs/USING.md` tells an agent to search with a topic phrase (`quipu search "DOCP"`)
   rather than with the question. The matcher does not stopwords, does not OR, and does not fall
   back to a partial match when a token is unknown.
2. **The vocabulary is the note's, not the asker's (13 queries, the `paraphrase` kind).** The
   query returns three notes that carry both synonyms - the right area, the wrong note - and the
   gold note is missing because that note never used the synonym the asker used. All 13 of those
   misses never retrieve the gold at any rank up to 10. This is the failure an embedding or
   hybrid ranker would fix, and it is exactly the gap PLAN.md M2 names.
3. **Near-duplicate notes win on length (9 of the `rare` queries, all recovered by k=10).** Every
   `rare` gold is inside the top 10; 36% of them fall out of the top 5 because seven shorter
   notes in the same cluster mention the same terms more densely. BM25's length normalisation
   prefers them. The `phrase` kind shows the same effect at rank 1: R@5 is 1.00 but MRR is only
   0.43, so the right note is usually found and usually not first.

The two kinds that work are the two where the query's words are literally in the note. The
retriever currently reads the corpus; it does not yet understand the question.

## Honesty rules

- A number that is not in `results/` does not exist. No number appears in this file that is not
  in a result file.
- The corpus is synthetic and its difficulty is by construction. It measures the retrieval
  stack on a corpus with known ground truth; it is not a claim about anyone's real vault, and it
  is not comparable to a LongMemEval or LoCoMo score.
- Failures are published, not summarised away. Every result file lists the queries that missed,
  with the notes it returned instead.
- A dataset that was not run has no number. `--dataset longmemeval` without a local file prints
  `dataset not present, skipping` and exits 0; it does not guess.
- The corpus and the query set are deterministic and digested, so a rerun must reproduce the
  same numbers. If it does not, the harness is broken and the result file is wrong.
- BM25 only. No embeddings, no hybrid scoring, no graph expansion: PLAN.md M2 has not landed
  those yet, and this harness measures what exists today.

## Reproducing

    cd /home/dirk/Projects/quipu-work/bench-harness
    .venv/bin/python -m benchmarks.run --dataset synthetic --k 5 --k 10 \
        --json benchmarks/results/synthetic-bm25-$(date +%F).json
    .venv/bin/python -m pytest -q                 # the shipped suite stays green
    .venv/bin/python -m pytest benchmarks/tests -q   # the harness's own tests
    .venv/bin/python -m ruff check .

The harness's own tests live in `benchmarks/tests/` so the shipped suite (`tests/`) stays
untouched. They pin the three things a number depends on: the corpus is deterministic, the
metrics are what they claim, and a missing dataset produces no number.

LongMemEval, when a local copy exists:

    .venv/bin/python -m benchmarks.run --dataset longmemeval --path /path/to/longmemeval.json \
        --k 5 --k 10 --json benchmarks/results/longmemeval-bm25-$(date +%F).json

## What this harness does not tell you

- Whether quipu retrieves well on a real vault. The corpus here is generated, with near-
  duplicates on purpose, so the absolute numbers are pessimistic by design; only the ranking of
  the query kinds and the shape of the failures are meant to carry over.
- Whether a hybrid or embedding ranker would do better. That comparison needs the same harness
  with a second retriever plugged in, which is the next step of M2.
- Anything about LoCoMo, which is not wired up yet.
