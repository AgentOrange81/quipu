"""quipu — local-first agent memory.

Verbatim capture, a temporal knowledge graph, and lifecycle-labeled training data.
The vault is plain markdown (Obsidian-compatible); the index and graph live in SQLite.
"""

__version__ = "0.1.0"

KINDS = ("capture", "conclusion", "decision", "lesson", "fact", "handoff", "question")
STATES = ("raw", "promoted", "pruned")

DEFAULT_PREDICATES = (
    "causes", "depends_on", "implements", "supersedes", "decided_against",
    "measured_as", "located_in", "used_by", "blocks", "owned_by", "part_of",
    "contradicts", "learned_from",
)
