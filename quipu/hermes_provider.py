"""Quipu as a Hermes memory provider — ``memory.provider: quipu``.

The manager drives this class per agent instance (multiplexing contract, ``MemoryProvider``):
every store, path and thread is bound from ``initialize()`` and kept on the instance — no
module-level state. Recall goes straight to the quipu SQLite FTS index (no LLM, no network);
turn sync is an append-only JSONL per session, never a vault entry per turn — that per-turn
failure is exactly what quipu exists to prevent.
"""
from __future__ import annotations

import importlib
import json
import logging
import os
import queue
import re
import threading
import time
from pathlib import Path
from typing import Any

try:  # hermes runtime; quipu used standalone falls back so the package imports anywhere
    from agent.memory_provider import MemoryProvider, spawn_context_thread
except Exception:  # pragma: no cover - exercised only outside hermes

    class MemoryProvider:  # type: ignore[no-redef]
        """Fallback base so a standalone ``import quipu.hermes_provider`` never fails."""

    def spawn_context_thread(target, *, name: str, daemon: bool = True) -> threading.Thread:  # type: ignore[no-redef]
        return threading.Thread(target=target, name=name, daemon=daemon)


logger = logging.getLogger(__name__)

_CHAR_PER_TOKEN = 4      # rough budget for the ≤400-token recall cap
_DEFAULT_LIMIT = 5
_DEFAULT_MAX_TOKENS = 400
_MIN_QUERY_CHARS = 4
_TURN_CAPTURE_CHARS = 4000
_JOIN_TIMEOUT = 5.0

_SAFE_SESSION_RE = re.compile(r"[^A-Za-z0-9._-]")


def _load_plugin_config() -> dict[str, Any]:
    """Read ``memory.quipu`` from the hermes profile config; {} when unavailable."""
    try:
        from hermes_cli.config import load_config

        memory = load_config().get("memory", {})
        block = memory.get("quipu", {}) if isinstance(memory, dict) else {}
        return dict(block) if isinstance(block, dict) else {}
    except Exception:
        return {}


def _load_submodule(name: str):
    """Import ``quipu.<name>``, or None if it does not import (is_available() checks index)."""
    try:
        return importlib.import_module(f".{name}", __package__)
    except Exception as exc:
        logger.debug("quipu.%s failed to import: %s", name, exc)
        return None


def _candidates(hermes_home: str = "") -> list[Path]:
    """Vault lookup order: QUIPU_VAULT, <hermes_home>/quipu-vault, ~/quipu-vault."""
    out: list[Path] = []
    env = os.environ.get("QUIPU_VAULT")
    if env:
        out.append(Path(env).expanduser())
    if hermes_home:
        out.append(Path(hermes_home).expanduser() / "quipu-vault")
    out.append(Path.home() / "quipu-vault")
    return list(dict.fromkeys(out))


def _resolve_vault(hermes_home: str = "") -> Path | None:
    for cand in _candidates(hermes_home):
        if cand.is_dir():
            return cand
    return None


def _safe_session(session_id: str) -> str:
    return _SAFE_SESSION_RE.sub("_", session_id) or "anon"


def _db_path(vault: str | Path) -> Path:
    return Path(vault) / ".quipu" / "index.db"


def _schema(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"name": name, "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required}}


def _str_prop(description: str) -> dict[str, Any]:
    return {"type": "string", "description": description}


SEARCH_SCHEMA = _schema(
    "quipu_search",
    "Search the quipu memory vault for prior outcomes, decisions and measurements. "
    "Use before answering anything project-history shaped.",
    {"query": _str_prop("What to look for (keywords, not sentences)."),
     "limit": {"type": "integer", "description": "Maximum results (default 5)."}},
    ["query"],
)

WRITE_SCHEMA = _schema(
    "quipu_write",
    "Record one real outcome in the quipu vault: a decision with its reason, a measurement, or a "
    "root cause. summary is the retrieval handle; evidence is how a skeptic re-checks it.",
    {"summary": _str_prop("One-sentence statement of the fact."),
     "evidence": _str_prop("Numbers, paths or commands a skeptic could re-run."),
     "ask": _str_prop("Optional: the standalone question this entry answers."),
     "kind": _str_prop("Optional entry kind (default 'fact')."),
     "project": _str_prop("Optional project tag."),
     "agent": _str_prop("Optional agent name.")},
    ["summary", "evidence"],
)


class QuipuMemoryProvider(MemoryProvider):
    """MemoryProvider backed by a local quipu vault (index recall + JSONL turn capture)."""

    def __init__(self, config: dict[str, Any] | None = None):
        cfg = dict(config) if config is not None else _load_plugin_config()
        self._config = cfg
        self._limit = int(cfg.get("limit") or os.environ.get("QUIPU_PREFETCH_LIMIT") or _DEFAULT_LIMIT)
        self._max_tokens = int(cfg.get("max_tokens") or os.environ.get("QUIPU_PREFETCH_MAX_TOKENS")
                               or _DEFAULT_MAX_TOKENS)
        self._hermes_home = ""
        self._vault = ""
        self._session_id = ""
        self._stats_line = ""
        self._lock = threading.Lock()
        self._pending: dict[str, str] | None = None
        self._prefetch_thread: threading.Thread | None = None
        self._writer_thread: threading.Thread | None = None
        self._write_queue: queue.Queue = queue.Queue()

    # -- lifecycle -----------------------------------------------------------

    @property
    def name(self) -> str:
        return "quipu"

    def is_available(self) -> bool:
        """A vault directory exists and quipu/index.py imports; config/deps only, no network."""
        if _load_submodule("index") is None:
            return False
        return _resolve_vault(self._hermes_home) is not None

    def unavailable_reason(self) -> str:
        looked = ", ".join(str(c) for c in _candidates(self._hermes_home))
        if _load_submodule("index") is None:
            return f"quipu: quipu/index.py does not import (looked for a vault at: {looked})"
        return f"quipu: no vault directory found (looked for: {looked})"

    def initialize(self, session_id: str, **kwargs) -> None:
        """Bind vault + session on the instance; open nothing long-lived."""
        self._hermes_home = str(kwargs.get("hermes_home") or "")
        self._session_id = session_id
        vault = _resolve_vault(self._hermes_home)
        self._vault = str(vault) if vault else ""
        self._stats_line = ""
        if self._vault:
            self._writer_thread = threading.Thread(target=self._writer_loop, name="quipu-sync", daemon=True)
            self._writer_thread.start()

    def shutdown(self) -> None:
        """Drain the sync queue and join workers — nothing left running."""
        writer = self._writer_thread
        self._writer_thread = None
        if writer is not None and writer.is_alive():
            self._write_queue.put(None)
            writer.join(timeout=_JOIN_TIMEOUT)
        prefetcher = self._prefetch_thread
        self._prefetch_thread = None
        if prefetcher is not None and prefetcher.is_alive():
            prefetcher.join(timeout=_JOIN_TIMEOUT)

    # -- prompt + recall -----------------------------------------------------

    def system_prompt_block(self) -> str:
        if not self._vault:
            return ""
        if not self._stats_line:
            self._stats_line = self._compute_stats_line()
        return (f"# Quipu memory\nVault: {self._vault}{self._stats_line}\n"
                "Recall before answering; write after a real outcome, never per turn. "
                "Search with quipu_search, record outcomes with quipu_write.")

    def _compute_stats_line(self) -> str:
        index_mod = _load_submodule("index")
        if index_mod is None:
            return ""
        try:
            stats = index_mod.stats(_db_path(self._vault))
        except Exception as exc:
            logger.debug("quipu stats unavailable: %s", exc)
            return ""
        promoted = stats.get("by_state", {}).get("promoted", 0)
        return f" ({stats.get('entries', 0)} entries, {promoted} promoted)"

    def _recall(self, query: str, max_tokens: int | None = None) -> str:
        index_mod = _load_submodule("index")
        if index_mod is None or not self._vault:
            return ""
        if not query or len(query.strip()) < _MIN_QUERY_CHARS:
            return ""
        try:
            rows = index_mod.search(_db_path(self._vault), query, limit=self._limit)
        except Exception as exc:
            logger.warning("quipu prefetch search failed: %s", exc)
            return ""
        if not rows:
            return ""
        lines = ["## Quipu recall"]
        for row in rows:
            lines.append(f"- [{row.get('id')}] {row.get('summary')} "
                         f"({row.get('state')}, score {row.get('score', 0.0)})")
        body = "\n".join(lines)
        budget = self._max_tokens if max_tokens is None else max(1, int(max_tokens))
        cap = budget * _CHAR_PER_TOKEN
        if len(body) > cap:
            kept = body[:cap]
            cut = kept.rfind("\n")
            body = kept[:cut] if cut > 0 else kept
        return body

    def prefetch(self, query: str, *, session_id: str = "", max_tokens: int | None = None) -> str:
        """Formatted recall for this turn: the queued background result if fresh, else direct.

        `max_tokens` overrides the instance budget for this call only; the default comes from
        configuration (`max_tokens`, or `QUIPU_PREFETCH_MAX_TOKENS`, or 400).
        """
        try:
            with self._lock:
                pending, self._pending = self._pending, None
            if pending and pending["query"] == query and pending["result"]:
                return pending["result"]
            return self._recall(query, max_tokens)
        except Exception as exc:
            logger.warning("quipu prefetch failed: %s", exc)
            return ""

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        """Recall in the background; prefetch() consumes the cached result next turn."""

        def work() -> None:
            result = ""
            try:
                result = self._recall(query)
            except Exception as exc:
                logger.debug("quipu background prefetch failed: %s", exc)
            with self._lock:
                self._pending = {"query": query, "result": result}

        try:
            self._prefetch_thread = _spawn_thread(work, name="quipu-prefetch")
        except Exception as exc:
            logger.debug("quipu queue_prefetch dropped: %s", exc)

    # -- turn sync -----------------------------------------------------------

    def sync_turn(self, user_content: str, assistant_content: str, *,
                  session_id: str = "", messages: list[dict[str, Any]] | None = None,
                  turn_author: dict[str, Any] | None = None) -> None:
        """Append one JSON line per session transcript; never a vault entry per turn; never raises."""
        try:
            if not self._vault:
                return
            sid = _safe_session(session_id or self._session_id)
            self._write_queue.put(("turn", {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "session": sid,
                "user": (user_content or "")[:_TURN_CAPTURE_CHARS],
                "assistant": (assistant_content or "")[:_TURN_CAPTURE_CHARS],
            }))
        except Exception as exc:
            logger.debug("quipu sync_turn dropped: %s", exc)

    def _writer_loop(self) -> None:
        while True:
            item = self._write_queue.get()
            if item is None:
                return
            _, record = item
            try:
                path = Path(self._vault) / ".quipu" / f"session-{record['session']}.jsonl"
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            except Exception as exc:
                logger.debug("quipu sync append failed: %s", exc)

    # -- tools ---------------------------------------------------------------

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        return [SEARCH_SCHEMA, WRITE_SCHEMA]

    def handle_tool_call(self, tool_name: str, args: dict[str, Any], **kwargs) -> str:
        try:
            return self._handle_tool(tool_name, dict(args or {}))
        except Exception as exc:
            logger.warning("quipu tool %s failed: %s", tool_name, exc)
            return json.dumps({"error": f"quipu {tool_name} failed: {exc}"})

    def _handle_tool(self, tool_name: str, args: dict[str, Any]) -> str:
        if tool_name == "quipu_search":
            return self._tool_search(args)
        if tool_name == "quipu_write":
            return self._tool_write(args)
        return json.dumps({"error": f"quipu provider handles quipu_search/quipu_write, not {tool_name!r}"})

    def _tool_search(self, args: dict[str, Any]) -> str:
        index_mod = _load_submodule("index")
        if index_mod is None or not self._vault:
            return json.dumps({"results": [], "error": "quipu vault not available"})
        query = str(args.get("query") or "").strip()
        if not query:
            return json.dumps({"error": "query is required"})
        try:
            limit = int(args.get("limit") or self._limit)
        except (TypeError, ValueError):
            limit = self._limit
        rows = index_mod.search(_db_path(self._vault), query, limit=limit)
        results = [{"id": r.get("id"), "summary": r.get("summary"), "state": r.get("state"),
                    "score": r.get("score", 0.0)} for r in rows]
        return json.dumps({"results": results})

    def _tool_write(self, args: dict[str, Any]) -> str:
        config_mod = _load_submodule("config")
        mcp_mod = _load_submodule("mcp")
        if config_mod is None or mcp_mod is None or not self._vault:
            return json.dumps({"error": "quipu vault not available"})
        if not str(args.get("summary") or "").strip():
            return json.dumps({"error": "summary is required"})
        if not str(args.get("evidence") or "").strip():
            return json.dumps({"error": "evidence is required"})
        payload = dict(args)
        payload.setdefault("kind", "fact")
        payload.setdefault("session", self._session_id)
        result = mcp_mod._write(config_mod.load(self._vault), payload)
        return json.dumps({"id": result["id"], "path": result["path"]})


def _spawn_thread(target, *, name: str) -> threading.Thread:
    """Daemon thread under the caller's contextvars where hermes provides the helper."""
    try:
        thread = spawn_context_thread(target, name=name)  # type: ignore[unresolved-name]
    except Exception:
        thread = threading.Thread(target=target, name=name, daemon=True)
    thread.start()
    return thread


def register(ctx) -> None:
    """Entry point for the ``hermes_agent.memory_providers`` group."""
    ctx.register_memory_provider(QuipuMemoryProvider())