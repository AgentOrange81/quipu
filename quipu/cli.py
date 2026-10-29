"""quipu command line."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from . import askfill as askfill_mod
from . import brief as brief_mod
from . import config as config_mod
from . import embed as embed_mod
from . import export as export_mod
from . import graph as graph_mod
from . import index as index_mod
from . import promote as promote_mod
from . import repair as repair_mod
from .importers import fabric as fabric_mod
from .store import (
    Entry,
    SummaryTooLong,
    check_summary,
    find_entry,
    format_edge_spec,
    iter_entries,
    looks_like_transcript,
    parse_edge_spec,
    write_entry,
)

#: The on-box tier this box happens to serve. Overridable per call; nothing here assumes it exists.
DEFAULT_CHAT_URL = "http://127.0.0.1:8081/v1/chat/completions"
DEFAULT_CHAT_MODEL = "qwen3.8-flash-next-iq3_xxs"


def _read_stdin() -> str:
    """Read piped input without exploding when stdin is a closed/captured handle."""
    try:
        if sys.stdin is None or sys.stdin.isatty():
            return ""
        return sys.stdin.read()
    except (OSError, ValueError):
        return ""


def _cfg(args):
    return config_mod.load(args.vault, create=False)


def _provider(args, cfg):
    if args.fake:
        return promote_mod.FakeProvider(json.loads(Path(args.fake).read_text()))
    return promote_mod.HttpProvider(cfg.llm_base_url, args.model or cfg.llm_model, cfg.llm_api_key)


def cmd_init(args) -> int:
    cfg = config_mod.load(args.vault, create=True)
    index_mod.connect(cfg.db_path).close()
    print(f"initialised vault at {cfg.root}")
    return 0


def cmd_capture(args) -> int:
    cfg = _cfg(args)
    text = _read_stdin().strip() if not args.text else args.text
    if not text:
        print("nothing to capture (pass text or pipe stdin)", file=sys.stderr)
        return 2
    e = promote_mod.record_capture(cfg, text, agent=args.agent, project=args.project,
                                   session=args.session, ask=args.ask, source="capture")
    index_mod.reindex(cfg.root, cfg.db_path)
    print(e.id)
    return 0


def cmd_write(args) -> int:
    cfg = _cfg(args)
    body = _read_stdin().strip() if args.body else args.body
    specs = []
    order = []
    broken = 0
    for spec in args.edge or []:
        try:
            src, predicate, dst = parse_edge_spec(spec)
        except ValueError:
            # a malformed spec is a broken claim, not a reason to lose the memory: the entry is
            # written, the claim is skipped, and the reindex reports it.
            print(f"--edge wants src:predicate:dst, got {spec!r}", file=sys.stderr)
            broken += 1
            order.append(spec)
            continue
        specs.append((spec, src, predicate, dst))
        order.append(spec)
    try:
        check_summary(args.summary, allow_long=getattr(args, "allow_long_summary", False))
    except SummaryTooLong as exc:
        print(str(exc), file=sys.stderr)
        return 2
    e = Entry(kind=args.kind, state=args.state, agent=args.agent, project=args.project,
              session=args.session, summary=args.summary, evidence=args.evidence or "",
              ask=args.ask or "", importance=args.importance, body=body, source="cli",
              supersedes=list(args.supersedes or []), edges=order)
    # The entry is written BEFORE the edges are asserted (docs/USING.md): a bad predicate must
    # not cost the memory. The write then fails and the entry is left on disk but unindexed;
    # `quipu reindex` puts it back, skipping the broken claim and saying so.
    p = write_entry(cfg.root, e)
    failed = 0
    for _spec, src, predicate, dst in specs:
        # the entry's `created` is the window's start, so a rebuild from the markdown reproduces
        # the same window instead of stamping it `now`
        try:
            graph_mod.add_edge(cfg, src, predicate, dst, note_id=e.id, at=e.created,
                               allow_new_predicate=args.allow_new)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            failed += 1
    if failed or broken:
        # the write failed: the index is not refreshed, so the entry stays unindexed until a
        # reindex runs. The markdown is the truth; the index is disposable.
        return 2
    # the graph follows --supersedes (#20): the old belief's window closes at the instant the
    # correction was written, and the closure is recorded in the markdown so a rebuild replays it.
    closed = graph_mod.close_superseded(cfg, e.supersedes, e) if e.supersedes else []
    for row in closed:
        print(f"closed edge {row['edge']} {row['spec']} (superseded {row['superseded']})",
              file=sys.stderr)
    index_mod.reindex(cfg.root, cfg.db_path)
    graph_mod.materialize(cfg)
    print(p)
    return 0


def cmd_reflect(args) -> int:
    cfg = _cfg(args)
    transcript = Path(args.transcript).read_text(encoding="utf-8") if args.transcript else _read_stdin()
    if not transcript.strip():
        print("empty transcript", file=sys.stderr)
        return 2
    existing = [e.summary for _p, e in iter_entries(cfg.root) if e.state == "promoted"]
    proposals = promote_mod.reflect(transcript, cfg, _provider(args, cfg), existing)
    if args.dry_run:
        print(json.dumps([p.__dict__ for p in proposals], indent=2, ensure_ascii=False))
        return 0
    written = promote_mod.apply(proposals, cfg, agent=args.agent, project=args.project,
                                session=args.session, source=args.transcript or "stdin")
    graph_mod.materialize(cfg)
    index_mod.reindex(cfg.root, cfg.db_path)
    promoted = sum(1 for e in written if e.state == "promoted")
    print(f"{len(written)} proposals, {promoted} promoted, {len(written) - promoted} left raw")
    return 0


def cmd_import(args) -> int:
    """Bring an external corpus into the vault (currently: Icarus/fabric markdown)."""
    src = Path(args.src_dir)
    if not src.is_dir():
        print(f"not a directory: {src}", file=sys.stderr)
        return 2
    # `--vault` may come before or after the subcommand; the subcommand's copy wins.
    vault = getattr(args, "vault_override", None) or args.vault
    if args.dry_run:
        cfg = config_mod.load(vault, create=False)
        result = fabric_mod.import_fabric(src, cfg.root, dry_run=True,
                                         include_noise=args.include_noise,
                                         promote_threshold=cfg.promote_threshold)
        print(json.dumps(_import_summary(result, src, vault, True), indent=2))
        return 0
    cfg = config_mod.load(vault, create=True)
    result = fabric_mod.import_fabric(src, cfg.root, dry_run=False, include_noise=args.include_noise,
                                      promote_threshold=cfg.promote_threshold)
    index_mod.reindex(cfg.root, cfg.db_path)
    print(json.dumps(_import_summary(result, src, vault, False), indent=2))
    return 0


def _import_summary(result, src, vault, dry_run) -> dict:
    return {
        "source": str(src),
        "vault": str(vault),
        "dry_run": dry_run,
        "written": len(result.written),
        "skipped": len(result.skipped),
        "by_kind": result.by_kind,
        "by_state": result.by_state,
    }


def cmd_search(args) -> int:
    cfg = _cfg(args)
    note = ""
    if getattr(args, "hybrid", False):
        try:
            rows, note = embed_mod.hybrid_search(
                cfg.db_path, args.query, args.embed_url or cfg.embed_url,
                args.embed_model or cfg.embed_model, limit=args.limit, kind=args.kind,
                state=args.state, agent=args.agent, project=args.project,
                since=args.since, until=args.until)
        except ValueError as exc:
            print(f"--since/--until: {exc}", file=sys.stderr)
            return 2
        if note:
            print(note, file=sys.stderr)
    else:
        try:
            rows = index_mod.search(cfg.db_path, args.query, kind=args.kind, state=args.state,
                                    agent=args.agent, project=args.project, limit=args.limit,
                                    since=args.since, until=args.until)
        except ValueError as exc:
            print(f"--since/--until: {exc}", file=sys.stderr)
            return 2
    if args.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return 0 if rows else 1
    if not rows:
        print("no matches")
        return 1
    for i, r in enumerate(rows, 1):
        print(f"{i}. [{r['id']}] {r['state']:<8} {r['kind']:<10} {r['summary'][:90]}")
        snip = " ".join((r["snip"] or "").split())
        if snip:
            print(f"   {snip[:200]}")
    return 0


def cmd_show(args) -> int:
    cfg = _cfg(args)
    p = find_entry(cfg.root, args.id)
    if not p:
        print(f"no entry {args.id}", file=sys.stderr)
        return 1
    print(p.read_text(encoding="utf-8"))
    return 0


def cmd_edge(args) -> int:
    cfg = _cfg(args)
    if not args.note:
        # an edge in no entry is an edge the markdown does not know about, and `rm index.db`
        # would take it with the db. Say so, loudly, on stderr.
        print("warning: no --note: this edge is in no entry, so a reindex will drop it",
              file=sys.stderr)
    try:
        eid = graph_mod.add_edge(cfg, args.src, args.predicate, args.dst, note_id=args.note or "",
                                 confidence=args.confidence, allow_new_predicate=args.allow_new)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    graph_mod.materialize(cfg)
    print(eid)
    return 0


def cmd_invalidate(args) -> int:
    cfg = _cfg(args)
    graph_mod.invalidate(cfg, args.edge)
    graph_mod.materialize(cfg)
    print(f"closed edge {args.edge}")
    return 0


def cmd_contradict(args) -> int:
    cfg = _cfg(args)
    eid = graph_mod.contradict(cfg, args.edge, args.src, args.predicate, args.dst, note_id=args.note or "")
    graph_mod.materialize(cfg)
    print(eid)
    return 0


def cmd_timeline(args) -> int:
    cfg = _cfg(args)
    rows = graph_mod.timeline(cfg, args.entity, at=args.at)
    for r in rows:
        end = r["valid_to"] or "now"
        print(f"{r['id']:>4} {r['valid_from'][:19]} → {end[:19]}  {r['src']} -{r['predicate']}-> {r['dst']}")
    return 0 if rows else 1


def cmd_neighbors(args) -> int:
    cfg = _cfg(args)
    out = graph_mod.neighbors(cfg, args.entity, depth=args.depth)
    if args.json:
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0
    for n in out["nodes"]:
        print(f"node  {n['name']}")
    for e in out["edges"]:
        print(f"edge  {e['src']} -{e['predicate']}-> {e['dst']}")
    return 0


def cmd_export(args) -> int:
    cfg = _cfg(args)
    out = Path(args.out)
    if args.which == "sft":
        n = export_mod.export_sft(cfg, out, system=args.system)
        print(f"{n} sft examples -> {out}")
    elif args.which == "dpo":
        n = export_mod.export_preferences(cfg, out)
        print(f"{n} preference pairs -> {out}")
    elif args.which == "graph":
        r = export_mod.export_graph(cfg, out)
        print(f"{r['nodes']} nodes, {r['edges']} edges -> {out}")
    return 0


def cmd_reindex(args) -> int:
    """Rebuild the index AND the graph. The db is derived from the markdown, so this is the
    honest recovery for a stale schema, a lost db, or a vault whose graph moved into notes."""
    cfg = _cfg(args)
    n = index_mod.reindex(cfg.root, cfg.db_path)
    report = graph_mod.rebuild(cfg)
    graph_mod.materialize(cfg)
    summary = f"indexed {n} entries, rebuilt {report['edges']} edges ({report['closed']} closed)"
    skipped = report.get("skipped") or []
    if skipped:
        # a broken edge spec is reported, not fatal: the entry is indexed and the memory is kept
        reasons = "; ".join(dict.fromkeys(skipped))
        summary += f", {len(skipped)} edge{'' if len(skipped) == 1 else 's'} skipped: {reasons}"
    print(summary)
    if report["folded"]:
        print(f"folded {report['folded']} db-only edges into the markdown")
    if report["orphans"]:
        print(f"warning: {report['orphans']} edges were in no entry and were dropped",
              file=sys.stderr)
    return 0


def cmd_stats(args) -> int:
    cfg = _cfg(args)
    report = export_mod.dataset_report(cfg)
    print(json.dumps(report, indent=2))
    return 0


def cmd_audit(args) -> int:
    """Cheap health check: unreachable links, missing evidence, dangling supersedes."""
    cfg = _cfg(args)
    entries = {e.id: e for _p, e in iter_entries(cfg.root)}
    problems = []
    for e in entries.values():
        if e.state == "promoted" and not e.evidence:
            problems.append(f"{e.id}: promoted without evidence")
        if e.state == "promoted" and looks_like_transcript(e.summary):
            # a trusted entry whose summary is a pasted tool transcript (#30): an agent reads it
            # as a statement. Filed as a class so a vault can be triaged without reading it.
            problems.append(f"{e.id}: suspicious_summary")
        for old in e.supersedes:
            if old not in entries:
                problems.append(f"{e.id}: supersedes missing entry {old}")
        if not e.summary:
            problems.append(f"{e.id}: empty summary")
        if e.summary[:1] in ("'", '"'):
            # an imported-and-quoted summary (#54): the importer keeps the markdown verbatim, so a
            # summary written as a YAML quoted scalar arrived with its quotes and often truncated at the
            # wrap. The parser is fixed, but the existing notes are not repaired by that - the audit is
            # how the damage stays visible instead of silently ranking 88 entries on a mangled handle.
            problems.append(f"{e.id}: summary starts with a quote character (re-import or edit)")
    # the graph must live in the markdown (#24): an edge no entry records is data that
    # `rm .quipu/index.db` would take with it.
    c = index_mod.connect(cfg.db_path)
    rows = c.execute("SELECT id, src, predicate, dst, note_id, valid_to FROM edges").fetchall()
    version = c.execute("PRAGMA user_version").fetchone()[0]
    c.close()
    # a superseded entry whose belief is still live is the exact failure #20 produced: two live
    # windows for one fact, and the stale claim looks as current as the correction. The write path
    # closes it now; this line is what says so out loud when a vault was written by an older one.
    live_notes = {r["note_id"] for r in rows if r["valid_to"] is None and r["note_id"]}
    for e in entries.values():
        for old in e.supersedes:
            if old in live_notes:
                problems.append(f"{e.id}: supersedes {old} but its edge is still live")
    unrecorded = []
    for r in rows:
        entry = entries.get(r["note_id"] or "")
        spec = format_edge_spec(r["src"], r["predicate"], r["dst"])
        if entry is None or not graph_mod.entry_records_edge(entry, spec):
            unrecorded.append({"edge": r["id"], "spec": spec, "note_id": r["note_id"]})
    print(json.dumps({"entries": len(entries), "problems": problems,
                      "schema_version": version,
                      "edges_not_in_markdown": unrecorded}, indent=2))
    return 0 if not problems else 1


def cmd_ask_backfill(args) -> int:
    """Write the question each entry answers (#27). Dry run unless --apply, sample first."""
    cfg = _cfg(args)
    # default to the free on-box tier; any OpenAI-compatible endpoint works (--url/--model/--api-key)
    url = args.url or DEFAULT_CHAT_URL
    model = args.model or DEFAULT_CHAT_MODEL
    todo = askfill_mod.candidates(cfg.root, state=args.state, limit=args.limit)
    if not todo:
        print(json.dumps({"candidates": 0, "note": "every entry already has an ask"}, indent=2))
        return 0
    if args.count:
        print(json.dumps({"candidates": len(todo)}, indent=2))
        return 0

    def progress(index, prop):
        if args.verbose:
            print(f"  [{index}/{len(todo)}] {prop.id} -> {prop.ask or prop.reason}", file=sys.stderr)

    # a sample must not cost a whole-corpus run: bound it to the sample size
    effective = args.limit or (args.sample if args.sample else 0)
    result = askfill_mod.run(cfg.root, url, model, api_key=args.api_key or "",
                             limit=effective, state=args.state, progress=progress)
    for prop in result.proposals[: max(args.sample, 0) or None]:
        print(f"{prop.id}  {prop.summary[:70]!r}\n  -> {prop.ask or '[' + prop.reason + ']'}")
    if args.sample:
        print(f"\n{len(result.proposals)} proposed, {result.skipped} skipped, {result.failed} failed "
              f"— sample only, nothing written.", file=sys.stderr)
        return 0
    if not args.apply:
        print(json.dumps({"candidates": len(result.proposals), "proposed": len(result.proposals)
                          - result.skipped - result.failed, "skipped": result.skipped,
                          "failed": result.failed, "written": 0,
                          "note": "dry run — re-run with --apply"}, indent=2))
        return 0
    written = askfill_mod.apply(cfg.root, result)
    askfill_mod.reindex(cfg.root)
    print(json.dumps({"candidates": len(result.proposals), "written": written,
                      "skipped": result.skipped, "failed": result.failed}, indent=2))
    return 0 if result.failed == 0 else 1


def cmd_repair(args) -> int:
    """Repair imported notes whose summary still carries its YAML quotes (#58). Dry run by default."""
    cfg = _cfg(args)
    result = repair_mod.find(cfg.root, args.source)
    if not result.repairs:
        print(json.dumps({"quoted_summaries": 0, "note": "nothing to repair"}, indent=2))
        return 0
    if not args.apply:
        for rep in result.repairs:
            state = rep.reason or f"from {rep.source}"
            print(f"{rep.id}  [{state}]\n  old: {rep.old[:110]!r}\n  new: {rep.new[:110]!r}")
        print(f"\n{len(result.repairs)} quoted summaries, {result.unresolved} with no recoverable source. "
              f"Nothing written — re-run with --apply.", file=sys.stderr)
        return 0
    written = repair_mod.apply(cfg.root, result)
    repair_mod.reindex(cfg.root)
    print(json.dumps({"quoted_summaries": len(result.repairs), "rewritten": written,
                      "unresolved": result.unresolved}, indent=2))
    return 0 if result.unresolved == 0 else 1


def cmd_brief(args) -> int:
    """Render the standing answer: promoted entries, one line each (#51). Derived, never an entry."""
    cfg = _cfg(args)
    scope = args.project or args.agent or ""
    text = brief_mod.build((e for _p, e in iter_entries(cfg.root)), project=args.project,
                           agent=args.agent, scope=scope, max_lines=args.limit)
    if args.stdout:
        sys.stdout.write(text)
        return 0
    rel = args.out or brief_mod.default_relpath(scope)
    path = Path(cfg.root) / ".quipu" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    lines = text.count("\n- ") + text.count("\n  - ")
    print(f"{path} ({len(text.encode('utf-8'))} bytes, {lines} lines) · {brief_mod.stamp()}",
          file=sys.stderr)
    print(str(path))
    return 0


def cmd_embed(args) -> int:
    """Build or refresh the vector index for this vault (#5). Derived data: re-run after a reindex."""
    cfg = _cfg(args)
    url = args.embed_url or cfg.embed_url
    if not url:
        print("no embedding endpoint: pass --url or set [embed] url in .quipu/config.toml",
              file=sys.stderr)
        return 2
    model = args.embed_model or cfg.embed_model
    entries = [e for _p, e in iter_entries(cfg.root)]
    wanted = {}
    for e in entries:
        text = embed_mod.entry_text(e.summary, e.ask, e.body)
        if text:
            wanted[e.id] = (embed_mod.text_digest(text), text)
    c = index_mod.connect(cfg.db_path)
    try:
        have = {} if args.force else embed_mod.stored_digests(c, model)
        todo = {eid: text for eid, (digest, text) in wanted.items() if have.get(eid) != digest}
        vectors: dict[str, tuple[str, list[float]]] = {}
        ids = list(todo)
        for start in range(0, len(ids), 64):
            chunk = ids[start:start + 64]
            got = embed_mod.embed_texts(url, model, [todo[i] for i in chunk], batch=args.batch)
            for eid, vec in zip(chunk, got, strict=False):
                vectors[eid] = (wanted[eid][0], vec)
        written = embed_mod.store(c, model, vectors) if vectors else 0
    finally:
        c.close()
    print(f"{written} embedded, {len(wanted) - written} unchanged, {len(entries)} entries, "
          f"model {model}")
    return 0


def cmd_mcp(args) -> int:
    """Serve the vault over MCP on stdio — tools instead of a subprocess per query."""
    from .mcp import serve
    return serve(_cfg(args).root)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="quipu", description="local-first agent memory")
    p.add_argument("--vault", default=".", help="vault root (default: cwd)")
    p.add_argument("--version", action="version", version=f"quipu {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name: str, text: str, fn) -> argparse.ArgumentParser:
        sp = sub.add_parser(name, help=text)
        sp.set_defaults(fn=fn)
        return sp

    add("init", "create a vault", cmd_init)

    s = add("capture", "store a raw capture (text or stdin)", cmd_capture)
    s.add_argument("text", nargs="?")
    for flag in ("agent", "project", "session", "ask"):
        s.add_argument(f"--{flag}", default="")

    s = add("write", "write a curated entry directly", cmd_write)
    s.add_argument("--kind", default="conclusion")
    s.add_argument("--state", default="promoted")
    s.add_argument("--summary", required=True)
    s.add_argument("--body", default="")
    s.add_argument("--evidence", default="")
    s.add_argument("--ask", default="")
    s.add_argument("--agent", default="")
    s.add_argument("--project", default="")
    s.add_argument("--session", default="")
    s.add_argument("--importance", type=float, default=0.8)
    s.add_argument("--supersedes", action="append", help="entry id this one corrects (repeatable)")
    s.add_argument("--edge", action="append", metavar="SRC:PRED:DST",
                   help="assert a graph edge from this entry (repeatable)")
    s.add_argument("--allow-new", action="store_true")
    s.add_argument("--allow-long-summary", action="store_true",
                   help=f"permit a summary over {280} bytes (you almost certainly want --body)")

    add("mcp", "serve the vault over MCP (stdio JSON-RPC)", cmd_mcp)

    s = add("import", "import a corpus into the vault", cmd_import)
    s.add_argument("--vault", dest="vault_override", default=None,
                   help="vault root (same as the global --vault)")
    fmt = s.add_subparsers(dest="format", required=True)

    fs = fmt.add_parser("fabric", help="import an Icarus/fabric corpus")
    fs.add_argument("src_dir")
    fs.add_argument("--vault", dest="vault_override", default=argparse.SUPPRESS,
                    help="vault root (same as the global --vault)")
    fs.add_argument("--dry-run", action="store_true", help="report counts, write nothing")
    fs.add_argument("--include-noise", action="store_true",
                    help="also import low-value session entries (as pruned)")

    s = add("reflect", "run the promotion pass over a transcript", cmd_reflect)
    s.add_argument("transcript", nargs="?")
    s.add_argument("--agent", default="")
    s.add_argument("--project", default="")
    s.add_argument("--session", default="")
    s.add_argument("--model", default="")
    s.add_argument("--fake", help="JSON responses file (no LLM call)")
    s.add_argument("--dry-run", action="store_true")

    s = add("search", "full-text search", cmd_search)
    s.add_argument("query")
    for flag in ("kind", "state", "agent", "project"):
        s.add_argument(f"--{flag}")
    s.add_argument("--limit", type=int, default=10)
    s.add_argument("--json", action="store_true")
    s.add_argument("--since", default=None,
                   help="only entries created on/after this (2026-10-01, or an ISO instant)")
    s.add_argument("--until", default=None,
                   help="only entries created through this DAY, or before an exact instant")
    s.add_argument("--hybrid", action="store_true",
                   help="fuse the lexical ranking with stored embeddings (#5)")
    s.add_argument("--embed-url", default="", help="override [embed] url")
    s.add_argument("--embed-model", default="", help="override [embed] model")

    s = add("ask-backfill", "write the question each entry answers", cmd_ask_backfill)
    s.add_argument("--url", default="", help="OpenAI-compatible /chat/completions endpoint")
    s.add_argument("--model", default="", help="model id at that endpoint")
    s.add_argument("--api-key", default="", help="bearer token (usually not needed for a local tier)")
    s.add_argument("--state", default="promoted", help="only entries in this state ('' for all)")
    s.add_argument("--limit", type=int, default=0, help="stop after N entries (0 = all)")
    s.add_argument("--sample", type=int, default=0, help="print N proposals and write nothing")
    s.add_argument("--count", action="store_true", help="just report how many entries are missing an ask")
    s.add_argument("--apply", action="store_true", help="write the ask lines (default: dry run)")
    s.add_argument("--verbose", action="store_true")

    s = add("repair", "fix imported notes whose summary still carries its YAML quotes", cmd_repair)
    s.add_argument("--source", default="~/fabric", help="where the fabric originals live")
    s.add_argument("--apply", action="store_true", help="write the notes (default: dry run)")

    s = add("brief", "write the standing answer: promoted entries, one line each", cmd_brief)
    s.add_argument("--project", default="")
    s.add_argument("--agent", default="")
    s.add_argument("--limit", type=int, default=brief_mod.DEFAULT_LIMIT,
                   help=f"max entries (default {brief_mod.DEFAULT_LIMIT}, 0 = all)")
    s.add_argument("--out", default="", help="path relative to .quipu/ (default .quipu/brief/<scope>.md)")
    s.add_argument("--stdout", action="store_true", help="print instead of writing")

    s = add("embed", "build or refresh the vector index (needs an embeddings endpoint)", cmd_embed)
    s.add_argument("--url", dest="embed_url", default="", help="OpenAI-compatible /v1/embeddings")
    s.add_argument("--model", dest="embed_model", default="", help="model name the endpoint expects")
    s.add_argument("--batch", type=int, default=32)
    s.add_argument("--force", action="store_true", help="re-embed unchanged entries too")

    s = add("show", "print an entry", cmd_show)
    s.add_argument("id")

    s = add("edge", "assert a graph edge", cmd_edge)
    s.add_argument("src")
    s.add_argument("predicate")
    s.add_argument("dst")
    s.add_argument("--note", default="")
    s.add_argument("--confidence", type=float, default=1.0)
    s.add_argument("--allow-new", action="store_true")

    s = add("invalidate", "close an edge's validity window", cmd_invalidate)
    s.add_argument("edge", type=int)

    s = add("contradict", "close an edge and assert its replacement", cmd_contradict)
    s.add_argument("edge", type=int)
    s.add_argument("src")
    s.add_argument("predicate")
    s.add_argument("dst")
    s.add_argument("--note", default="")

    s = add("timeline", "an entity's edges, optionally as of a date", cmd_timeline)
    s.add_argument("entity")
    s.add_argument("--at", default=None)

    s = add("neighbors", "graph neighbourhood", cmd_neighbors)
    s.add_argument("entity")
    s.add_argument("--depth", type=int, default=1)
    s.add_argument("--json", action="store_true")

    s = add("export", "write a dataset", cmd_export)
    s.add_argument("which", choices=("sft", "dpo", "graph"))
    s.add_argument("--out", required=True)
    s.add_argument("--system", default=None)

    add("reindex", "rebuild the sqlite index", cmd_reindex)
    add("stats", "dataset + graph report", cmd_stats)
    add("audit", "find corpus problems", cmd_audit)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
