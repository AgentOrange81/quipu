"""quipu — local-first agent memory.

Verbatim capture, a temporal knowledge graph, and lifecycle-labeled training data.
The vault is plain markdown (Obsidian-compatible); the index and graph live in SQLite.
"""

__version__ = "0.5.1"

KINDS = ("capture", "conclusion", "decision", "lesson", "fact", "handoff", "question")
STATES = ("raw", "promoted", "pruned")

DEFAULT_PREDICATES = (
    "causes", "depends_on", "implements", "supersedes", "decided_against",
    "measured_as", "located_in", "used_by", "blocks", "owned_by", "part_of",
    "contradicts", "learned_from",
)


def register(ctx):
    """Hermes memory-provider entry point.

    Deliberately a re-export rather than the implementation: Hermes resolves a provider's package
    directory from the entry point WITHOUT importing it, and only a package ``__init__`` yields
    one. Pointing the entry point at ``quipu.hermes_provider`` (a bare module) made a correctly
    installed provider read as "configured but not installed".
    """
    from quipu.hermes_provider import register as _register

    return _register(ctx)
