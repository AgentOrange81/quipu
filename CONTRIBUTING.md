# Contributing

Small project, strong opinions, short list of rules.

## Setup

```bash
uv sync --extra dev     # or: python -m venv .venv && .venv/bin/pip install -e ".[dev]"
make test
make lint
```

## Rules

1. **Markdown is the source of truth.** Anything that can only be reconstructed from SQLite is a bug.
2. **A test or it did not happen.** Behaviour changes need a test that fails before the change. The
   pipeline is testable without a model — use `FakeProvider`.
3. **No new runtime dependencies** without an argument in the PR that names what breaks without it.
   `pyproject.toml` currently has an empty `dependencies` list and that is a feature.
4. **No silent failure.** Hooks, migrations and background passes must exit non-zero and say what
   broke. A memory system that fails quietly is worse than one that fails loudly.
5. **Never invent a benchmark number.** If a claim has not been measured on a named dataset with a
   committed result file, it does not go in the README.

## Commit messages

`type: what changed` — `feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `perf:`, `chore:`.
The body explains *why*, and names the failure it prevents when that is the reason.

## Adding a predicate

Predicates are a closed vocabulary on purpose. Add one in `.quipu/config.toml` for your vault, and to
`DEFAULT_PREDICATES` in `quipu/__init__.py` only if it is generally useful — with a line in the PR
saying which existing predicate it cannot be expressed as.
