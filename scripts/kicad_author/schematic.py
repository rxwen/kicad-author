"""Generate .kicad_sch from the design source.

Choose one of three representations (see references/schematic-layout-conventions.md §2):
  Power nets → power port symbols / local nets → wires + junctions / others → text labels
"""
import json
import sys
import uuid

from .geom import OUTWARD, pin_abs, snap, stub_end
from .router import route
from .sexp import embed, load_symbols

STUB_WIRE = 2.54       # Wired pins: short stubs keep routing compact
STUB_TEXT = 5.08       # Labels/power symbols: longer stubs avoid pin-number overlap
BODY_CLEAR = 1.6       # Wire-to-component-body clearance
WIRE_MAX_PINS = 5      # Use labels above this pin count to avoid tangled wiring

DEFAULT_POWER_SYM = {
    "GND": "power:GND", "GNDA": "power:GNDA",
    "+3V3": "power:+3V3", "+5V": "power:+5V", "VBUS": "power:VBUS",
    "VCC": "power:VCC", "VDD": "power:VDD",
}


def _u():
    return str(uuid.uuid4())


def _prop(name, val, x, y, hide=False):
    h = "\n\t\t\t\t(hide yes)" if hide else ""
    return (f'\t\t(property "{name}" "{val}"\n\t\t\t(at {x} {y} 0)\n\t\t\t(effects'
            f'\n\t\t\t\t(font\n\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t){h}\n\t\t\t)\n\t\t)')


def _wire(x1, y1, x2, y2):
    return ['\t(wire', f'\t\t(pts\n\t\t\t(xy {x1} {y1}) (xy {x2} {y2})\n\t\t)',
            '\t\t(stroke\n\t\t\t(width 0)\n\t\t\t(type default)\n\t\t)',
            f'\t\t(uuid "{_u()}")', '\t)']


def _label(net, x, y, ang):
    return [f'\t(label "{net}"', f'\t\t(at {x} {y} {ang})',
            '\t\t(effects\n\t\t\t(font\n\t\t\t\t(size 1.27 1.27)\n\t\t\t)'
            '\n\t\t\t(justify left bottom)\n\t\t)', f'\t\t(uuid "{_u()}")', '\t)']


def _junction(x, y):
    return ['\t(junction', f'\t\t(at {x} {y})', '\t\t(diameter 0)',
            '\t\t(color 0 0 0 0)', f'\t\t(uuid "{_u()}")', '\t)']


class SchematicWriter:
    """Take design/layout data and produce one .kicad_sch file.

    Required design data (source of truth): PARTS NETS NO_CONNECT ZONES POWER_NETS
    Required layout data (presentation): SCH_LAYOUT ZONE_RECT
    Optional: MPNS POWER_SYM PWR_FLAG_NETS PAPER TITLE REV
    """

    def __init__(self, design, layout, sym_paths, project="kicad-author"):
        self.d, self.l, self.project = design, layout, project
        self.libs = {name: load_symbols(p) for name, p in sym_paths.items()}
        self.power_sym = dict(DEFAULT_POWER_SYM)
        self.power_sym.update(getattr(design, "POWER_SYM", {}))
        self.ref2zone = {r: z for z, (_t, refs) in design.ZONES.items() for r in refs}
        self.out, self.sym_uuids, self.root = [], {}, _u()
        self._pwr_idx = 10

    # ── Preparation ────────────────────────────────────────────────────────────
    def _resolve_symbols(self):
        need = {}
        for ref, part in self.d.PARTS.items():
            lib, sym = part[0].split(":", 1)
            if lib not in self.libs or sym not in self.libs[lib]:
                sys.exit(f"Missing symbol: {part[0]}")
            need[part[0]] = self.libs[lib][sym]
        for lid in set(self.power_sym.values()) | {"power:PWR_FLAG"}:
            lib, sym = lid.split(":", 1)
            if sym in self.libs.get(lib, {}):
                need[lid] = self.libs[lib][sym]
        return need

    def _geometry(self, need):
        layout = {r: (snap(x), snap(y), rot)
                  for r, (x, y, rot) in self.l.SCH_LAYOUT.items()}
        missing = [r for r in self.d.PARTS if r not in layout]
        if missing:
            sys.exit(f"Missing layout coordinates: {missing}")
        pinpos, boxes = {}, {}
        for ref, part in self.d.PARTS.items():
            X, Y, RT = layout[ref]
            pts = []
            for num, px, py, ang in need[part[0]][1]:
                ax, ay, aa = pin_abs(X, Y, RT, px, py, ang)
                pinpos[(ref, num)] = (ax, ay, aa)
                pts.append((ax, ay))
            if pts:
                xs = [p[0] for p in pts]
                ys = [p[1] for p in pts]
                boxes[ref] = (min(xs) - BODY_CLEAR, min(ys) - BODY_CLEAR,
                              max(xs) + BODY_CLEAR, max(ys) + BODY_CLEAR)
        bad = [f"{n}: {r}.{p}" for n, ps in self.d.NETS.items()
               for r, p in ps if (r, p) not in pinpos]
        bad += [f"NC: {r}.{p}" for r, p in self.d.NO_CONNECT if (r, p) not in pinpos]
        if bad:
            sys.exit("Pins not found:\n  " + "\n  ".join(bad))
        return layout, pinpos, boxes

    def _plan(self):
        """Choose each net's representation before computing stub lengths to avoid shorts."""
        plan = {}
        for net, pins in self.d.NETS.items():
            zones = {self.ref2zone[r] for r, _p in pins}
            if net in self.power_sym:
                plan[net] = "power"
            elif len(zones) == 1 and 2 <= len(pins) <= WIRE_MAX_PINS:
                plan[net] = "wire"
            else:
                plan[net] = "label"
        return plan

    def _stubs(self, plan, pinpos):
        stub_of = {}
        for net, pins in self.d.NETS.items():
            L = STUB_WIRE if plan[net] == "wire" else STUB_TEXT
            stub_of[net] = [(*pinpos[(r, p)][:2],
                             *stub_end(*pinpos[(r, p)], L),
                             pinpos[(r, p)][2]) for r, p in pins]
        # Required check: stub endpoints from different nets must not coincide; this silently shorts them
        seen, clash = {}, []
        for net, ss in stub_of.items():
            for s in ss:
                k = (s[2], s[3])
                if seen.get(k, net) != net:
                    clash.append((k, seen[k], net))
                seen[k] = net
        if clash:
            sys.exit("Stub endpoint collision (short circuit):\n  " +
                     "\n  ".join(f"{k} : {a} × {b}" for k, a, b in clash))
        return stub_of

    # ── Output ────────────────────────────────────────────────────────────
    def _symbol(self, ref, part, layout, need):
        lib_id, value, fp, lcsc = part[0], part[1], part[2], part[3]
        note = part[4] if len(part) > 4 else ""
        X, Y, RT = layout[ref]
        ys = [pin_abs(X, Y, RT, p[1], p[2], p[3])[1] for p in need[lib_id][1]] or [Y]
        o = ['\t(symbol', f'\t\t(lib_id "{lib_id}")', f'\t\t(at {X} {Y} {RT})',
             '\t\t(unit 1)',
             '\t\t(exclude_from_sim no)\n\t\t(in_bom yes)\n\t\t(on_board yes)\n\t\t(dnp no)',
             f'\t\t(uuid "{self.sym_uuids.setdefault(ref, _u())}")',
             _prop("Reference", ref, X, snap(min(ys) - 3.0)),
             _prop("Value", value, X, snap(max(ys) + 3.0)),
             _prop("Footprint", fp, X, Y, hide=True),
             _prop("Datasheet", "", X, Y, hide=True),
             _prop("Description", note, X, Y, hide=True),
             _prop("LCSC", lcsc, X, Y, hide=True),
             _prop("MPN", getattr(self.d, "MPNS", {}).get(ref, ""), X, Y, hide=True)]
        for num, *_ in need[lib_id][1]:
            o.append(f'\t\t(pin "{num}"\n\t\t\t(uuid "{_u()}")\n\t\t)')
        o += ['\t\t(instances', f'\t\t\t(project "{self.project}"',
              f'\t\t\t\t(path "/{self.root}"',
              f'\t\t\t\t\t(reference "{ref}")\n\t\t\t\t\t(unit 1)',
              '\t\t\t\t)\n\t\t\t)\n\t\t)', '\t)']
        return o

    def _power_port(self, lib_id, net, x, y, need):
        """Power port: the origin is the pin; Value supplies the net name (verified in testing)."""
        self._pwr_idx += 1
        ref = f"#PWR{self._pwr_idx:03d}"
        below = lib_id.rsplit(":", 1)[1].startswith("GND")
        o = ['\t(symbol', f'\t\t(lib_id "{lib_id}")', f'\t\t(at {x} {y} 0)',
             '\t\t(unit 1)',
             '\t\t(exclude_from_sim no)\n\t\t(in_bom no)\n\t\t(on_board yes)\n\t\t(dnp no)',
             f'\t\t(uuid "{_u()}")',
             _prop("Reference", ref, x, y, hide=True),
             _prop("Value", net, x, snap(y + (3.6 if below else -3.6)))]
        for num, *_ in need[lib_id][1]:
            o.append(f'\t\t(pin "{num}"\n\t\t\t(uuid "{_u()}")\n\t\t)')
        o += ['\t\t(instances', f'\t\t\t(project "{self.project}"',
              f'\t\t\t\t(path "/{self.root}"',
              f'\t\t\t\t\t(reference "{ref}")\n\t\t\t\t\t(unit 1)',
              '\t\t\t\t)\n\t\t\t)\n\t\t)', '\t)']
        return o

    def _zones(self):
        o = []
        for z, (title, _refs) in self.d.ZONES.items():
            x0, y0, x1, y1 = self.l.ZONE_RECT[z]
            o += ['\t(rectangle', f'\t\t(start {x0} {y0})', f'\t\t(end {x1} {y1})',
                  '\t\t(stroke\n\t\t\t(width 0.2)\n\t\t\t(type dash)'
                  '\n\t\t\t(color 180 60 180 1)\n\t\t)',
                  '\t\t(fill\n\t\t\t(type none)\n\t\t)', f'\t\t(uuid "{_u()}")', '\t)',
                  f'\t(text "{title}"', '\t\t(exclude_from_sim no)',
                  f'\t\t(at {x0 + 2} {y0 + 5} 0)',
                  '\t\t(effects\n\t\t\t(font\n\t\t\t\t(size 2.2 2.2)'
                  '\n\t\t\t\t(color 180 60 180 1)\n\t\t\t)'
                  '\n\t\t\t(justify left bottom)\n\t\t)', f'\t\t(uuid "{_u()}")', '\t)']
        return o

    def build(self):
        need = self._resolve_symbols()
        layout, pinpos, boxes = self._geometry(need)
        plan = self._plan()
        stub_of = self._stubs(plan, pinpos)

        o = ['(kicad_sch', '\t(version 20250114)', '\t(generator "kicad-author")',
             '\t(generator_version "10.0")', f'\t(uuid "{self.root}")',
             f'\t(paper "{getattr(self.d, "PAPER", "A3")}")', '\t(title_block',
             f'\t\t(title "{getattr(self.d, "TITLE", self.project)}")',
             f'\t\t(rev "{getattr(self.d, "REV", "A")}")',
             '\t\t(comment 1 "Generated from design.py (kicad-author); do not edit manually")', '\t)',
             '\t(lib_symbols']
        for lib_id, (src, _p) in need.items():
            o += embed(lib_id, src)
        o.append('\t)')
        o += self._zones()
        for ref, part in self.d.PARTS.items():
            o += self._symbol(ref, part, layout, need)

        stats = {"wire": 0, "power": 0, "label": 0}
        wired, fallback, placed_segs = [], [], []
        for net, pins in self.d.NETS.items():
            stubs = stub_of[net]
            ends = [(s[2], s[3]) for s in stubs]
            routed = None
            if plan[net] == "wire":
                own = {r for r, _p in pins}
                foreign = [(s[0], s[1]) for m, ss in stub_of.items() if m != net for s in ss]
                foreign += [(s[2], s[3]) for m, ss in stub_of.items() if m != net for s in ss]
                routed = route(ends, [b for r, b in boxes.items() if r not in own],
                               foreign, placed_segs)
            for (px, py, ex, ey, _a) in stubs:
                o += _wire(px, py, ex, ey)
                placed_segs.append((px, py, ex, ey))
            if routed:
                segs, junc = routed
                placed_segs.extend(segs)
                for s in segs:
                    o += _wire(*s)
                for (jx, jy) in junc:
                    o += _junction(jx, jy)
                # Wired nets still need a naming label, or KiCad renames them to Net-(...)
                o += _label(net, stubs[0][2], stubs[0][3], (stubs[0][4] + 180) % 360)
                stats["wire"] += 1
                wired.append(net)
            elif plan[net] == "power":
                lid = self.power_sym[net]
                for (_px, _py, ex, ey, _a) in stubs:
                    o += self._power_port(lid, net, ex, ey, need)
                stats["power"] += 1
            else:
                for (_px, _py, ex, ey, ang) in stubs:
                    o += _label(net, ex, ey, (ang + 180) % 360)
                stats["label"] += 1
                if plan[net] == "wire":
                    fallback.append(net)

        for i, (net, fx, fy) in enumerate(getattr(self.d, "PWR_FLAG_NETS", []), 1):
            fx, fy = snap(fx), snap(fy)
            o += ['\t(symbol', '\t\t(lib_id "power:PWR_FLAG")', f'\t\t(at {fx} {fy} 0)',
                  '\t\t(unit 1)',
                  '\t\t(exclude_from_sim no)\n\t\t(in_bom no)\n\t\t(on_board yes)\n\t\t(dnp no)',
                  f'\t\t(uuid "{_u()}")',
                  _prop("Reference", f"#FLG{i:02d}", fx, fy, hide=True),
                  _prop("Value", "PWR_FLAG", fx, fy, hide=True)]
            tail = []
            for num, px, py, ang in need["power:PWR_FLAG"][1]:
                o.append(f'\t\t(pin "{num}"\n\t\t\t(uuid "{_u()}")\n\t\t)')
                tail += _label(net, snap(fx + px), snap(fy - py), (ang + 180) % 360)
            o += ['\t\t(instances', f'\t\t\t(project "{self.project}"',
                  f'\t\t\t\t(path "/{self.root}"',
                  f'\t\t\t\t\t(reference "#FLG{i:02d}")\n\t\t\t\t\t(unit 1)',
                  '\t\t\t\t)\n\t\t\t)\n\t\t)', '\t)'] + tail

        for ref, num in self.d.NO_CONNECT:
            x, y, _a = pinpos[(ref, num)]
            o += ['\t(no_connect', f'\t\t(at {x} {y})', f'\t\t(uuid "{_u()}")', '\t)']

        o += ['\t(sheet_instances\n\t\t(path "/"\n\t\t\t(page "1")\n\t\t)\n\t)',
              '\t(embedded_fonts no)', ')']
        self.out = o
        return stats, wired, fallback

    def write(self, sch_path, uuid_path=None):
        sch_path.write_text("\n".join(self.out) + "\n")
        if uuid_path:
            uuid_path.write_text(json.dumps(
                {"root": self.root, "symbols": self.sym_uuids}, indent=2))
