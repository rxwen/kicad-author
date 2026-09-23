"""Validation gates: golden netlist and isolation barrier checks.

The golden netlist is the foundation: changes to drawing, layout, or stub lengths
must preserve every connection. Any connectivity change is a bug.
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


ISO_CHECK_CODE = r'''
import pcbnew, sys
X0, X1 = {x0}, {x1}
ISO = set({iso!r}) | set("/" + n for n in {iso!r})
b = pcbnew.LoadBoard({board!r})
cross = vin = zbad = mixed = 0
for t in b.GetTracks():
    if t.Type() == pcbnew.PCB_VIA_T:
        if X0 - 0.3 <= pcbnew.ToMM(t.GetPosition().x) <= X1 + 0.3:
            vin += 1
    else:
        a, c = pcbnew.ToMM(t.GetStart().x), pcbnew.ToMM(t.GetEnd().x)
        if min(a, c) < X1 and max(a, c) > X0:
            cross += 1
for z in b.Zones():
    if z.GetIsRuleArea():
        continue
    bb = z.GetBoundingBox()
    L, R = pcbnew.ToMM(bb.GetLeft()), pcbnew.ToMM(bb.GetRight())
    if z.GetNetname() in ISO and L < X1 - 0.05:
        zbad += 1
    if z.GetNetname() not in ISO and R > X0 + 0.05:
        zbad += 1
for fp in b.Footprints():
    for p in fp.Pads():
        n = p.GetNetname()
        if not n or "unconnected" in n:
            continue
        if (n in ISO) != (pcbnew.ToMM(p.GetPosition().x) > X1):
            mixed += 1
print("   Isolation barrier: crossing tracks %d / vias inside %d / out-of-bounds pours %d / wrong-domain pads %d"
      % (cross, vin, zbad, mixed))
sys.exit(1 if (cross or vin or zbad or mixed) else 0)
'''


def isolation_barrier(kicad_py, board_path, x0, x1, isolated_nets, cwd=None):
    """No copper may cross the isolation barrier. Requires KiCad's interpreter (pcbnew)."""
    import subprocess
    code = ISO_CHECK_CODE.format(x0=x0, x1=x1, iso=sorted(isolated_nets),
                                 board=str(board_path))
    r = subprocess.run([str(kicad_py), "-c", code], capture_output=True,
                       text=True, cwd=cwd)
    out = [l for l in (r.stdout or "").splitlines() if l.strip()]
    print("\n".join(out) or (r.stderr or "")[:300])
    return r.returncode == 0


def resolve_net(board_nets, name):
    """Power nets are global (no / prefix); signal nets have a sheet-path prefix. Try both."""
    for cand in (name, "/" + name):
        if cand in board_nets:
            return cand
    sys.exit("Net not found: %s (also tried /%s)" % (name, name))
