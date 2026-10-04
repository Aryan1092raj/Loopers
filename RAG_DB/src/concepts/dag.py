"""Keep the prerequisite graph acyclic (TRD §7.2)."""
import networkx as nx


def make_acyclic(edges: list[tuple[str, str, float]]) -> tuple[list[tuple[str, str, float]], list[tuple[str, str, float]]]:
    """Remove the lowest-weight edge of every cycle until the graph is a DAG.

    Returns (kept edges, removed edges). Self-loops are removed first. For each strongly connected
    component with more than one node the lowest-weight edge goes, then components are recomputed.
    """
    removed = [e for e in edges if e[0] == e[1]]
    g = nx.DiGraph()
    for src, dst, weight in edges:
        if src != dst:
            g.add_edge(src, dst, weight=weight)
    while True:
        cycles = [c for c in nx.strongly_connected_components(g) if len(c) > 1]
        if not cycles:
            break
        for comp in cycles:
            src, dst, data = min(g.subgraph(comp).edges(data=True), key=lambda e: e[2]["weight"])
            g.remove_edge(src, dst)
            removed.append((src, dst, data["weight"]))
    assert nx.is_directed_acyclic_graph(g)
    return [(s, d, data["weight"]) for s, d, data in g.edges(data=True)], removed
