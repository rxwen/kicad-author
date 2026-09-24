"""Extract board facts from a **saved** board file. Requires KiCad's interpreter (pcbnew).

Order matters and is the whole point of this module:

    finalize(path)      load → refill zones → save        (authoring; changes the file)
    extract(path)       load the saved file → facts       (read-only; hashes that file)

The pipeline used to import the router's session file and save without re-filling, so the
board on disk carried pre-route zone fills while DRC and the isolation check reasoned over
it. Filling must happen before the hash is taken, and extraction must never write.
"""
import pathlib

import pcbnew

from . import facts as F


def _mm(v):
    return round(pcbnew.ToMM(v), 4)


def _pt(v):
    return [_mm(v.x), _mm(v.y)]


def layer_names(board):
    return [board.GetLayerName(i) for i in _copper_ids(board)]


def _layer_set(board, item):
    """Copper layers an item sits on, by containment: LSET.CuStack() is not wrapped
    consistently across KiCad versions, LSET.Contains() is."""
    ls = item.GetLayerSet()
    return [board.GetLayerName(i) for i in _copper_ids(board) if ls.Contains(i)]


def _copper_ids(board):
    return [i for i in range(pcbnew.PCB_LAYER_ID_COUNT)
            if board.IsLayerEnabled(i) and pcbnew.IsCopperLayer(i)]


def _regions(poly_set):
    """SHAPE_POLY_SET → [{"outline": [...], "holes": [[...]]}] in mm."""
    out = []
    for i in range(poly_set.OutlineCount()):
        outline = poly_set.Outline(i)
        pts = [[_mm(outline.CPoint(k).x), _mm(outline.CPoint(k).y)]
               for k in range(outline.PointCount())]
        holes = []
        for h in range(poly_set.HoleCount(i)):
            hv = poly_set.Hole(i, h)
            holes.append([[_mm(hv.CPoint(k).x), _mm(hv.CPoint(k).y)]
                          for k in range(hv.PointCount())])
        out.append({"outline": pts, "holes": holes})
    return out


def finalize(board_path):
    """Refill every zone and save. Run this after importing routing, before extraction."""
    b = pcbnew.LoadBoard(str(board_path))
    pcbnew.ZONE_FILLER(b).Fill(b.Zones())
    pcbnew.SaveBoard(str(board_path), b)
    return len(b.Zones())


def extract(board_path):
    """Read a saved board into a facts dict. Never writes."""
    board_path = pathlib.Path(board_path)
    b = pcbnew.LoadBoard(str(board_path))
    f = F.new(board_path)
    f["kicad_version"] = pcbnew.GetBuildVersion()
    f["copper_layers"] = layer_names(b)
    f["nets"] = sorted(n for n in b.GetNetsByName().keys() if n)

    # Net classes live in the project file; whether they reach a board loaded this way
    # depends on the KiCad version, so failure is recorded and reported as unknown
    # rather than silently becoming "no deviations".
    errors = []
    for getter in ("GetNetClasses", "GetAllNetClasses"):
        try:
            src = getattr(b.GetDesignSettings(), getter, None) or getattr(b, getter, None)
            if src is None:
                continue
            for name, nc in dict(src()).items():
                f["netclasses"][str(name)] = {"track": _mm(nc.GetTrackWidth()),
                                              "clearance": _mm(nc.GetClearance()),
                                              "via": _mm(nc.GetViaDiameter()),
                                              "via_hole": _mm(nc.GetViaDrill())}
            if f["netclasses"]:
                break
        except Exception as e:                  # API differs across KiCad versions
            errors.append("%s: %s" % (getter, e))
    if not f["netclasses"] and errors:
        f["netclasses_error"] = "; ".join(errors)
    for net in b.GetNetsByName().values():
        try:
            f["net_class_of"][net.GetNetname()] = net.GetNetClassName()
        except Exception:
            pass

    edges = [d for d in b.GetDrawings() if d.GetLayer() == pcbnew.Edge_Cuts]
    if edges:
        bb = b.GetBoardEdgesBoundingBox()
        f["outline"] = [_mm(bb.GetLeft()), _mm(bb.GetTop()),
                        _mm(bb.GetRight()), _mm(bb.GetBottom())]
        f["outline_segments"] = len(edges)

    for fp in b.Footprints():
        bb = fp.GetBoundingBox(False, False)
        f["footprints"].append({
            "ref": fp.GetReference(), "value": fp.GetValue(),
            "pos": _pt(fp.GetPosition()), "rot": fp.GetOrientationDegrees(),
            "side": "B.Cu" if fp.IsFlipped() else "F.Cu",
            "bbox": [_mm(bb.GetLeft()), _mm(bb.GetTop()),
                     _mm(bb.GetRight()), _mm(bb.GetBottom())],
            "footprint": str(fp.GetFPID().GetUniStringLibId()),
        })
        for p in fp.Pads():
            attr = p.GetAttribute()
            copper = {}
            for ly in _layer_set(b, p):
                shape = pcbnew.SHAPE_POLY_SET()
                # KiCad supplies board-coordinate geometry including rotation and
                # custom shapes. Approximate curved edges outward by at most 1 um.
                p.TransformShapeToPolygon(shape, b.GetLayerID(ly), 0,
                                          pcbnew.FromMM(0.001), pcbnew.ERROR_OUTSIDE)
                copper[ly] = _regions(shape)
            f["pads"].append({
                "ref": fp.GetReference(), "pad": p.GetNumber(),
                "net": p.GetNetname() or None, "pos": _pt(p.GetPosition()),
                "layers": _layer_set(b, p),
                "type": {pcbnew.PAD_ATTRIB_SMD: "smd", pcbnew.PAD_ATTRIB_PTH: "pth",
                         pcbnew.PAD_ATTRIB_NPTH: "npth",
                         pcbnew.PAD_ATTRIB_CONN: "conn"}.get(attr, str(attr)),
                "size": [_mm(p.GetSize().x), _mm(p.GetSize().y)],
                "copper": copper,
            })

    for t in b.GetTracks():
        if t.Type() == pcbnew.PCB_VIA_T:
            f["vias"].append({"net": t.GetNetname() or None, "pos": _pt(t.GetPosition()),
                              "diameter": _mm(t.GetWidth()), "drill": _mm(t.GetDrill()),
                              "layers": F.via_layers(f["copper_layers"],
                                         [b.GetLayerName(t.TopLayer()),
                                          b.GetLayerName(t.BottomLayer())])})
        else:
            f["tracks"].append({"net": t.GetNetname() or None,
                                "layer": b.GetLayerName(t.GetLayer()),
                                "start": _pt(t.GetStart()), "end": _pt(t.GetEnd()),
                                "width": _mm(t.GetWidth())})

    for z in b.Zones():
        layers = _layer_set(b, z)
        outline = _regions(z.Outline())
        rec = {"name": z.GetZoneName() or None, "layers": layers,
               "outline": outline[0]["outline"] if outline else []}
        if z.GetIsRuleArea():
            rec.update({"no_tracks": z.GetDoNotAllowTracks(),
                        "no_vias": z.GetDoNotAllowVias(),
                        "no_fill": z.GetDoNotAllowZoneFills()})
            f["rule_areas"].append(rec)
        else:
            filled = {}
            for ly in layers:
                lid = b.GetLayerID(ly)
                try:
                    ps = z.GetFilledPolysList(lid)
                except Exception:
                    continue
                regs = _regions(ps)
                if regs:
                    filled[ly] = regs
            try:
                prio = z.GetAssignedPriority()
            except AttributeError:              # Renamed from GetPriority in KiCad 7
                prio = z.GetPriority()
            rec.update({"net": z.GetNetname() or None, "filled": filled,
                        "priority": prio})
            f["zones"].append(rec)
    return f
