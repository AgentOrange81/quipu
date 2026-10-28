import pytest

from quipu import graph as graph_mod


def test_edge_requires_known_predicate(cfg):
    with pytest.raises(ValueError):
        graph_mod.add_edge(cfg, "trx40", "vibes_with", "docp")
    eid = graph_mod.add_edge(cfg, "trx40", "measured_as", "docp-3200")
    assert isinstance(eid, int)


def test_allow_new_predicate_escape_hatch(cfg):
    graph_mod.add_edge(cfg, "a", "custom_pred", "b", allow_new_predicate=True)
    assert graph_mod.timeline(cfg, "a")[0]["predicate"] == "custom_pred"


def test_invalidate_closes_window_but_keeps_history(cfg):
    eid = graph_mod.add_edge(cfg, "trx40", "measured_as", "docp-3600", at="2026-01-01T00:00:00Z")
    graph_mod.invalidate(cfg, eid, at="2026-06-01T00:00:00Z")
    rows = graph_mod.timeline(cfg, "trx40")
    assert len(rows) == 1, "history must survive invalidation"
    assert rows[0]["valid_to"] == "2026-06-01T00:00:00Z"
    assert rows[0]["valid_from"] == "2026-01-01T00:00:00Z"


def test_timeline_as_of_a_date(cfg):
    eid = graph_mod.add_edge(cfg, "trx40", "measured_as", "docp-3600", at="2026-01-01T00:00:00Z")
    graph_mod.invalidate(cfg, eid, at="2026-06-01T00:00:00Z")
    assert len(graph_mod.timeline(cfg, "trx40", at="2026-03-01T00:00:00Z")) == 1
    assert len(graph_mod.timeline(cfg, "trx40", at="2026-09-01T00:00:00Z")) == 0


def test_contradict_closes_old_and_opens_new(cfg):
    old = graph_mod.add_edge(cfg, "trx40", "measured_as", "docp-3600", at="2026-01-01T00:00:00Z")
    new = graph_mod.contradict(cfg, old, "trx40", "measured_as", "docp-3200", at="2026-06-01T00:00:00Z")
    assert new != old
    rows = graph_mod.timeline(cfg, "trx40")
    live = [r for r in rows if r["valid_to"] is None]
    assert [r["dst"] for r in live] == ["docp-3200"]
    assert len(rows) == 2


def test_invalidating_a_closed_edge_fails(cfg):
    eid = graph_mod.add_edge(cfg, "a", "blocks", "b")
    graph_mod.invalidate(cfg, eid)
    with pytest.raises(ValueError):
        graph_mod.invalidate(cfg, eid)


def test_neighbors_walks_two_hops(cfg):
    graph_mod.add_edge(cfg, "quipu", "depends_on", "sqlite")
    graph_mod.add_edge(cfg, "sqlite", "used_by", "fts5")
    one = graph_mod.neighbors(cfg, "quipu", depth=1)
    two = graph_mod.neighbors(cfg, "quipu", depth=2)
    assert {n["name"] for n in one["nodes"]} == {"quipu", "sqlite"}
    assert "fts5" in {n["name"] for n in two["nodes"]}


def test_materialize_writes_entity_hubs(cfg):
    graph_mod.add_edge(cfg, "quipu", "depends_on", "sqlite")
    assert graph_mod.materialize(cfg) == 2
    hub = (cfg.entities_dir / "quipu.md").read_text()
    assert "-->" in hub and "[[sqlite]]" in hub
