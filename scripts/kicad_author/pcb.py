"""PCB helpers. All require KiCad's interpreter (import pcbnew).

Reusable steps derived from the current board:
  · Load nets from an exported netlist already checked against the design source
  · Vendor locating-peg holes with zero annular ring are non-plated mounting holes; correct to NPTH
  · Set the auxiliary origin at the bottom left for positive placement coordinates with Y upward
"""
import os
import pathlib
import re
import sys

import pcbnew

from .gates import resolve_net


def mm(v):
    return pcbnew.FromMM(v)


def to_mm(v):
    return pcbnew.ToMM(v)


def load_netlist_pin_map(netlist_path):
    """Read (ref, pin) -> net name from a kicad-cli netlist export."""
    txt = netlist_path.read_text()
    pin2net = {}
    for m in re.finditer(r'\(net\s*\(code "\d+"\)\s*\(name "([^"]*)"\)(.*?)\n\t\t\)',
                         txt, re.S):
        for ref, pin in re.findall(r'\(ref "([^"]+)"\)\s*\(pin "([^"]+)"\)', m.group(2)):
            pin2net[(ref, pin)] = m.group(1)
    return pin2net


def fix_mounting_pegs(fp):
    """Correct zero-annular-ring PTH locating-peg holes to NPTH. Return the correction count."""
    n = 0
    for pad in fp.Pads():
        if pad.GetAttribute() != pcbnew.PAD_ATTRIB_PTH:
            continue
        ds, sz = pad.GetDrillSize(), pad.GetSize()
        if not pad.GetNumber().strip() or ds.x >= sz.x or ds.y >= sz.y:
            pad.SetAttribute(pcbnew.PAD_ATTRIB_NPTH)
            pad.SetSize(ds)
            n += 1
    return n


def copper_layer_ids(board):
    """Copper layers this board actually has. The keepout default used to be a hardcoded
    four-layer guess, which silently names layers a two-layer board does not enable."""
    return [i for i in range(pcbnew.PCB_LAYER_ID_COUNT)
            if board.IsLayerEnabled(i) and pcbnew.IsCopperLayer(i)]


def add_keepout(board, name, points, layers=None):
    """Copper keepout, for isolation barriers and antenna clearances.

    `layers` defaults to **every enabled copper layer**: an antenna clearance that exists
    only on F.Cu is a blank rectangle, not a clearance.
    """
    z = pcbnew.ZONE(board)
    z.SetIsRuleArea(True)
    z.SetDoNotAllowZoneFills(True)
    z.SetDoNotAllowTracks(True)
    z.SetDoNotAllowVias(True)
    ls = pcbnew.LSET()
    for ly in (layers if layers is not None else copper_layer_ids(board)):
        ls.addLayer(ly)
    z.SetLayerSet(ls)
    op = z.Outline()
    op.NewOutline()
    for (px, py) in points:
        op.Append(mm(px), mm(py))
    z.SetZoneName(name)
    board.Add(z)
    return z


def add_pour(board, name, net, layer, points, clearance=0.25, min_width=0.2):
    nets = board.GetNetsByName()
    z = pcbnew.ZONE(board)
    z.SetLayer(layer)
    z.SetNet(nets[resolve_net(nets, net)])
    z.SetZoneName(name)
    z.SetLocalClearance(mm(clearance))
    z.SetMinThickness(mm(min_width))
    z.SetPadConnection(pcbnew.ZONE_CONNECTION_THERMAL)
    op = z.Outline()
    op.NewOutline()
    for (px, py) in points:
        op.Append(mm(px), mm(py))
    board.Add(z)
    return z


def board_outline(board, w, h, width=0.1):
    pts = [(0, 0), (w, 0), (w, h), (0, h)]
    for i in range(4):
        seg = pcbnew.PCB_SHAPE(board)
        seg.SetShape(pcbnew.SHAPE_T_SEGMENT)
        seg.SetStart(pcbnew.VECTOR2I(mm(pts[i][0]), mm(pts[i][1])))
        j = (i + 1) % 4
        seg.SetEnd(pcbnew.VECTOR2I(mm(pts[j][0]), mm(pts[j][1])))
        seg.SetLayer(pcbnew.Edge_Cuts)
        seg.SetWidth(mm(width))
        board.Add(seg)


def place_parts(board, parts, place, fp_libs, pin2net, uuids):
    """Place parts, assign nets, and fix peg holes. Return (count, NPTH corrections, unassigned pads)."""
    nets = board.GetNetsByName()
    placed, npth, unassigned = 0, [], []
    for ref, part in parts.items():
        lib, fpname = part[2].split(":", 1)
        libpath = fp_libs.get(lib) or os.path.join(fp_libs["__kicad__"], lib + ".pretty")
        fp = pcbnew.FootprintLoad(str(libpath), fpname)
        if fp is None:
            sys.exit(f"Failed to load footprint: {part[2]} ({libpath})")
        fp.SetFPID(pcbnew.LIB_ID(lib, fpname))
        fp.SetReference(ref)
        fp.SetValue(part[1])
        x, y, rot = place[ref]
        fp.SetPosition(pcbnew.VECTOR2I(mm(x), mm(y)))
        if rot:
            fp.SetOrientationDegrees(rot)
        if ref in uuids:
            fp.SetPath(pcbnew.KIID_PATH("/" + uuids[ref]))
        npth += [ref] * fix_mounting_pegs(fp)
        for pad in fp.Pads():
            n = pin2net.get((ref, pad.GetNumber()))
            if n:
                pad.SetNet(nets[n])
            elif pad.GetNumber():
                unassigned.append(f"{ref}.{pad.GetNumber()}")
        board.Add(fp)
        placed += 1
    return placed, npth, unassigned


def declare_nets(board, pin2net):
    for nm in sorted(set(pin2net.values())):
        board.Add(pcbnew.NETINFO_ITEM(board, nm))


def set_aux_origin_bottom_left(board, height):
    board.GetDesignSettings().SetAuxOrigin(pcbnew.VECTOR2I(mm(0.0), mm(height)))


def refill(board):
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())


def load_uuids(path):
    """Schematic symbol UUIDs, so footprints keep their link across regenerations."""
    import json
    p = pathlib.Path(path) if not hasattr(path, "read_text") else path
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def apply_stackup(board, physical):
    """Enable the copper layers declared in src/physical.py. Returns the layer names."""
    names = list(getattr(physical, "COPPER_LAYERS", None) or ["F.Cu", "B.Cu"])
    board.SetCopperLayerCount(len(names))
    for want in names:
        lid = board.GetLayerID(want)
        if lid < 0:
            sys.exit("Layer %r is not a valid KiCad copper layer name" % want)
    return names


def apply_planes(board, physical, layout, margin=0.3):
    """Pour each declared plane layer with the net it carries.

    A layer is a reference plane because physical.py says so, not because a pour happened
    to be drawn there; this is the step that makes the declaration true on the board.
    """
    planes = getattr(physical, "PLANES", None) or {}
    rects = getattr(physical, "PLANE_RECT", None) or {}
    made = []
    for layer_name, net in planes.items():
        rect = rects.get(layer_name) or (margin, margin,
                                         layout.BOARD_W - margin, layout.BOARD_H - margin)
        x0, y0, x1, y1 = rect
        add_pour(board, "plane-%s" % layer_name, net, board.GetLayerID(layer_name),
                 [(x0, y0), (x1, y0), (x1, y1), (x0, y1)])
        made.append("%s:%s" % (layer_name, net))
    return made


def apply_keepouts(board, physical):
    """Rule areas from KEEPOUTS and BARRIERS, on the layers each one declares."""
    made = []
    for name, ko in (getattr(physical, "KEEPOUTS", None) or {}).items():
        want = ko.get("layers", "all")
        ids = (copper_layer_ids(board) if want == "all"
               else [board.GetLayerID(n) for n in want])
        pts = ko.get("outline") or _rect_pts(ko["rect"])
        add_keepout(board, name, pts, ids)
        made.append(name)
    for name, bar in (getattr(physical, "BARRIERS", None) or {}).items():
        add_keepout(board, name, _rect_pts(bar["rect"]), copper_layer_ids(board))
        made.append(name)
    return made


def _rect_pts(rect):
    x0, y0, x1, y1 = rect
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
