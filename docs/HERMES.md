# quipu as a Hermes memory provider

Hermes can drive an agent from one memory backend. quipu can be that backend: `memory.provider:
quipu` in the profile's `config.yaml` makes recall arrive in the prompt on its own and turns
land in the vault without the agent being asked to use a CLI.

The failure this prevents is the one quipu was built around. Today quipu is a CLI an agent has to
be told to use, and the vault is only as good as the agent's discipline: one session writes
nothing, another dumps a transcript, and nothing tells you which. As a provider the recall
discipline is the default, and the per-turn append — the thing that turns a corpus into noise —
never happens at all.

## Install

quipu ships as an ordinary package that publishes a Hermes entry point, so nothing is copied
into the profile. The entry point lives in the distribution's `pyproject.toml`:

```toml
[project.entry-points."hermes_agent.memory_providers"]
quipu = "quipu:register"
```

Install the released wheel — there is no PyPI publish step; the release workflow attaches the
wheel and sdist to the GitHub Release:

```bash
pip install https://github.com/AgentOrange81/quipu/releases/download/v0.3.0/quipu-0.3.0-py3-none-any.whl
```

or, from a checkout, `pip install -e .`.

Then select it in the profile's `config.yaml` — two lines, nothing else:

```yaml
memory:
  provider: quipu
```

That is the whole selection: the entry-point name (`quipu`) is the name Hermes matches against
`memory.provider`.

## Where the vault comes from

The provider binds its vault from `initialize()`, taking the first of these that is an existing
directory:

1. `$QUIPU_VAULT` — an explicit path wins over everything else.
2. `<hermes_home>/quipu-vault` — the profile-scoped vault, e.g.
   `~/.hermes/profiles/glitch/quipu-vault`.
3. `~/quipu-vault` — the shared vault.

So if your vault is not `~/quipu-vault` and you do not want it under the profile, set
`QUIPU_VAULT` in the profile's environment:

```bash
export QUIPU_VAULT=/path/to/vault
```

Two knobs, both optional, both read when the provider is constructed: `memory.quipu.limit`
(default 5, entries per recall) and `memory.quipu.max_tokens` (default 400, the recall budget).
The env vars `QUIPU_PREFETCH_LIMIT` and `QUIPU_PREFETCH_MAX_TOKENS` override the same two values.

## What the agent sees

Two things, both small.

**A static system block** (`system_prompt_block()`): the vault path, the entry count and how many
are promoted, and the discipline — recall before answering, write after a real outcome. It
carries no recalled entries, only the counts, and it is small: 27 words on an empty vault in a
real run, against a 150-word ceiling the contract test holds it to.

**A recall block per turn** (`prefetch()`), built from the SQLite FTS index and capped at the
token budget: a heading and one line per hit, `- [id] summary (state, score)`. The cap is
enforced on characters — budget × 4 — and the cut is made at the last line break inside the
budget, so a recall list is not left ending in half a line. A query shorter than 4 characters, or
one that matches nothing, returns nothing at all.

The budget is the point. A memory provider that floods the prompt is worse than no provider: the
agent reads the noise instead of the question. Recall is also **not** an LLM call and makes **no**
network request — it is BM25 over the local index, so it costs milliseconds and works with the
network unplugged.

## What gets written, and what deliberately does not

`prefetch()` reads. `sync_turn()` writes to exactly one place:

```
<vault>/.quipu/session-<session-id>.jsonl
```

One JSON line per turn, with the timestamp, the session id and the user and assistant text (each
side truncated to 4000 characters). That file is a transcript, not a memory. **No vault entry is
created per turn** — the entry count does not move when a session runs, and the vault does not
fill with `session: "fixed a bug"` rows. This is the append-only failure quipu exists to fix,
and the provider refuses to commit it.

Entries come from a promotion pass instead: `quipu reflect` over a transcript, or `quipu hook` at
a session end. The gate is the same one the CLI applies — importance over the threshold **and**
evidence present — and a summary that is a topic rather than a statement does not clear it. See
[USING.md](USING.md) for the gate and the reflection pass.

The provider adds two tools for an agent that wants to reach further than the recall budget:

| tool | what it takes | what it does |
|---|---|---|
| `quipu_search` | `query`, optional `limit` (default 5) | BM25 search over the vault index; returns id, summary, state, score |
| `quipu_write` | `summary` and `evidence` (both required), optional `ask`, `kind` (default `fact`), `project`, `agent` | writes one entry into the vault and reindexes it |

Call `quipu_write` after a real outcome — a decision with its reason, a measurement, a root cause
— and not per turn. The summary is the retrieval handle, so write it as a statement; the evidence
is what a skeptic would re-run. One caveat, because it is in the code and nowhere in the prose:
the write tool exposes no state, so what it writes lands `state=raw`. Promoted entries still come
from the reflection pass or the CLI's `--state promoted`.

## Measuring it

Turn the provider on, ask the agent something the vault already knows, and look at two places.

The vault side, from any shell:

```bash
quipu --vault ~/quipu-vault stats
```

That prints the corpus report as JSON — `entries`, `promoted`, `promotion_rate` and the export
counts. A vault the provider can search has a non-zero `entries`.

The session side, in the vault:

```bash
ls ~/quipu-vault/.quipu/session-*.jsonl
```

One file per session id, one JSON line per turn. It proves the provider is capturing turns. It
does **not** prove recall worked — for that the agent has to answer a question from the vault,
and the recall block has to appear in the turn's context.

## When it goes wrong

**The provider does not load.** Check that the entry point is installed:

```bash
python -c "import importlib.metadata as m; print([e.name for e in m.entry_points(group='hermes_agent.memory_providers')])"
```

`quipu` in that list means the distribution is installed where Hermes can see it; an empty list
means it is not — Hermes discovers a pip-installed provider by entry point, so a provider that
imports but was installed elsewhere is invisible.

**No vault found.** The provider says so instead of starting blind: `quipu: no vault directory
found (looked for: ...)`, naming the paths it tried, in order. Create the vault with
`quipu --vault <path> init`, or point `QUIPU_VAULT` at an existing one.

**An empty vault.** Not an error: recall returns nothing and the agent has no prior context.
`quipu stats` reports `entries: 0`.

**`QUIPU_VAULT` pointing somewhere else.** The provider takes the first candidate that is a
directory, and `QUIPU_VAULT` is first — so an exported path silently wins over the profile vault.
The vault path is in the system block, so it is visible in the agent's own context.

**A broken vault degrades, it does not crash.** The provider never raises into the agent loop:
recall returns an empty string, the write tools return a JSON `error`, and turn capture is
dropped with a debug log. A session with a broken vault degrades to no recall; it does not die.

**A stale index.** The index is derived from the markdown. If entries were written outside the
provider (`quipu write`, a file edit), reindex before blaming the provider:
`quipu --vault ~/quipu-vault reindex`.

## See also

[USING.md](USING.md) — the CLI path: the recall-first habit, the promotion gate, corrections.
[DESIGN.md](DESIGN.md) — the layers and why `ask` is required.
