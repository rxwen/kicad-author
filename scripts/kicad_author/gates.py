"""Validation gates: the golden netlist.

The golden netlist is the foundation: changes to drawing, layout, or stub lengths
must preserve every connection. Any connectivity change is a bug.

The isolation check that used to live here compared zone **bounding boxes** on a single
vertical band, which both misjudges non-rectangular pours and only fits one board shape.
It now runs over exact filled polygons in `checks._domain_checks`, driven by `BARRIERS`
in the project's physical.py. See references/pcb-physical-conventions.md §5 and §8.
"""
import sys


def _parse_netlist(path, sexp_parser):
    doc = sexp_parser.parse(path.read_text())
    root = doc[0] if isinstance(doc[0], list) else doc
    nets_node = sexp_parser.find_first(root, "nets")
    out = {}
    for n in sexp_parser.find_all(nets_node, "net"):
        name = sexp_parser.get_value(n, "name").lstrip("/")
        out[name] = {(sexp_parser.get_value(d, "ref"), sexp_parser.get_value(d, "pin"))
                     for d in sexp_parser.find_all(n, "node")}
    return out


def golden_table(netlist_path, expected_nets, sexp_parser, verbose=True):
    """Require an exact per-pin match to the design. Return (passed, matches, total, report)."""
    actual = _parse_netlist(netlist_path, sexp_parser)
    exp = {k: set(v) for k, v in expected_nets.items()}
    deviate = [n for n, p in exp.items() if actual.get(n) != p]
    extra = [e for e in set(actual) - set(exp)
             if not e.startswith(("Net-", "unconnected-"))]
    hit = sum(len(actual.get(n, set()) & p) for n, p in exp.items())
    total = sum(len(v) for v in exp.values())
    ok = not deviate and not extra and hit == total
    line = (f"Golden netlist {hit}/{total}  differing nets {deviate or 'none'}  "
            f"extra named nets {extra or 'none'}")
    if verbose:
        print("   " + line)
    return ok, hit, total, line


def resolve_net(board_nets, name):
    """Power nets are global (no / prefix); signal nets have a sheet-path prefix. Try both."""
    for cand in (name, "/" + name):
        if cand in board_nets:
            return cand
    sys.exit("Net not found: %s (also tried /%s)" % (name, name))
