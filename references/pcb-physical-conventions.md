# KiCad PCB physical conventions

> Companion to [schematic-layout-conventions.md](schematic-layout-conventions.md). That
> document covers the drawing; this one covers the copper.
>
> **Documentation expresses intent; code and validation gates enforce correctness.** The
> schematic side learned this the hard way — seven connectivity incidents that knowing the
> rules did not prevent (§9 there). Every rule below therefore names the mechanism that
> implements it and the check that proves it, and §8 records the PCB-side incidents.

## 0. What is checked, and what is checked against

A generator cannot be wrong about its own variables. The check target is therefore the
**board file that was saved, reloaded and re-filled** — never the in-memory board and never
the generator's inputs:

```
gen_pcb.py → route → kicad-author facts --finalize → kicad-author check --stage final
             │                          │                            │
             │                          │                            └ pure-stdlib checks
             │                          └ refill pours, save, then hash the saved file
             └ tracks imported from the router's session file
```

`facts` records the sha256 of the file it read, and `check` refuses to report at all when
that hash no longer matches the board on disk. This is what stops a DRC result from being
quoted after the next edit.

| Recorded in the snapshot | Why it must come from the file |
|---|---|
| Pad and footprint positions, layers, nets | Placement is only real once saved |
| Tracks, vias, widths, layers | Routing arrives from an external tool |
| **Filled** zone polygons, with holes | A pour's outline says nothing about what filled |
| Rule areas and their layer sets | A keepout that names no layers keeps nothing out |
| Net classes in force, enabled copper layers | A rule in a file is not a rule on a board |

## 1. Four states, never two

Every check reports **pass / fail / unknown / n/a**.

| State | Meaning |
|---|---|
| `pass` | The check ran and the board satisfies it |
| `fail` | The check ran and the board violates it |
| `unknown` | **The premise is missing.** The check could not run |
| `n/a` | The board has no such feature |

`unknown` is the state that removes the most false precision. Without it the only options are
to claim a pass or invent a failure, and the claim wins: "decoupling is adequate" because a
capacitor happens to be nearby, "the RF feed is 50 Ω" with no dielectric stack on record.

> ⚠️ **`unknown` must not become an escape hatch.** An `unknown` on an item the project
> declared critical gates the build exactly like a failure. The only way past it is a
> `WAIVERS` entry naming the external evidence, and every report prints it.

`t2.impedance.<net>` is deliberately incapable of passing: a track width over a plane is not
an impedance without a dielectric stack and a solver, and neither lives in this repository.
Record the calculation as a waiver or leave the build gated.

## 2. Hard constraints and optimisation targets are different things

Candidates are judged in this order, and the tiers are **gates, not weights**:

| Tier | Contents | Gates the build |
|---|---|---|
| 1 | Connectivity, mechanical, isolation, device hard constraints | ✅ |
| 2 | Critical loops, return paths, power and signal requirements | ✅ |
| 3 | Detours, layer changes, congestion | ❌ reported |
| 4 | Alignment, silkscreen, appearance | ❌ reported |

> ⚠️ **There is no total score, by construction.** A sum would let "shorter tracks, fewer
> vias" offset copper inside an antenna keepout or a broken reference plane. Sometimes one
> more via is the correct answer, because it keeps a reference plane intact — an aggregate
> would rank that worse. `Report` exposes per-state counts and a gating list, and no
> weighted total exists to be traded against.

Tier 3 and 4 findings are still printed in full. Warnings are not dropped: the pipeline runs
ERC and DRC with `--severity-error --severity-warning`, gates on the errors, and reports the
warnings at tier 3.

## 3. Relations, not proximity

Record **what a supporting part is for**, in `design.py`, as `GROUPS`. A free-text
description ("U1 decoupling") that no code reads cannot distinguish a capacitor serving U1
pin 12 from one that merely sits near U1.

| Kind | Declares | Checks it unlocks |
|---|---|---|
| `decouple` | cap, ic, **pin**, net, return net, limits | distance to *that* pin, orientation, return distance |
| `protect` | device, net, exposed pin, protected pin | the device is on the exposed side of the net |
| `crystal` | oscillator, load caps, ic, return point | caps at the crystal, returns at the IC's ground pin |
| `regulator` | ref, in/out pins, in/out caps, nets | each capacitor at the pin it serves, with orientation |

Each kind expands into primitives — `near`, `facing`, `between` — so the constraint that
gets checked is geometric and exact:

- **`facing`** is the rotation check. A two-pad part must present its *net* pad to the pin it
  serves. Centre-to-centre distance passes a capacitor mounted backwards; this does not.
  Orientation follows the pad relationship, not the tidy row.
- **`between`** requires two conditions, because either alone is trivial to satisfy: the
  device must be closer to the exposed pin than to the protected pin, **and** within the
  exposed half of the net. A TVS 30 mm from the connector and 2 mm from the transceiver is
  "nearer the connector than the transceiver is" and clamps nothing.
- **`near`** with a return-net target measures where the return current has to go, not
  merely whether a ground pad exists somewhere on the board.

Declare nothing and the report says `unknown`, listing what is unverified. It never says the
placement is fine.

> Distances are pad centre to pad centre. That is exact about *which pin is served* and a
> proxy for loop area. The report states the measurement it made; it does not claim a loop
> analysis it did not perform.

## 4. Layer purpose before routing

`src/physical.py` is the third file, answering the third question: `design.py` says what the
board is, `layout.py` where things go, `physical.py` **under what rules copper may exist**.

| Declaration | Purpose |
|---|---|
| `COPPER_LAYERS` | The stack, checked against the board's enabled copper layers |
| `LAYER_ROLE` | `signal` / `plane` / `mixed` — what each layer is for |
| `PLANES` | Plane layer → the net it carries |
| `CRITICAL_NETS` | Reference layer, return net, via budget, stitching radius, min width |
| `NETCLASSES` / `NET_CLASS` | Widths and clearances, emitted into `<board>.kicad_pro` |
| `DOMAINS` / `BARRIERS` | Groups no copper may bridge, and the gaps that separate them |
| `KEEPOUTS` | Regions no copper may enter, **on named layers** |

Run `kicad-author rules` **before** generating the PCB. The autorouter reads net class widths
through the DSN export, so declaring them afterwards changes nothing about how the board was
routed — "all nets connected" was the specification it was actually given.

### 4.1 Reference-plane continuity

For each critical net, every track segment must lie fully inside the **filled** polygon of its
reference net on its reference layer. Because the snapshot carries filled polygons with their
holes, a plane split or a track cutting the plane appears as a hole and fails the test with no
extra rule. A segment that spans the gap between two filled regions is backed by neither —
that gap is the broken reference.

### 4.2 Stitching is never blanket

> ⚠️ **Never "a ground via beside every signal via".** That rule bridges isolation domains,
> and on this skill's own boards the barrier and the antenna clearance are rule areas that
> such a via would sit inside.

A return via counts only when it carries the reference net **of the critical net's own
domain** and does not fall inside a rule area. Stitching radius is declared per critical net
(`stitch_mm`), never globally.

### 4.3 Keepouts name layers

A blank rectangle on `F.Cu` is not an antenna clearance on a four-layer board. `KEEPOUTS`
entries declare `layers` (or `"all"`, resolved against the layers the board actually enables),
`apply_keepouts` builds the rule area across exactly those layers, and the check fails when
the rule area on the board reaches fewer layers than were declared — before asking whether
anything intruded.

## 5. Isolation

A barrier is a region plus the side each domain belongs on. Four things are checked against
the saved board, using exact polygons:

1. No track crosses or enters the barrier, on any layer.
2. No via sits inside it.
3. No filled copper overlaps it — tested against filled polygons, not bounding boxes.
4. Every pad of a domain's nets is on that domain's declared side.

## 6. Where the authoring/review line now runs

This skill checks **its own declared intent against the artefact it produced**: did the pour
that `physical.py` declares exist, fill, and stay out of the keepout; does the capacitor sit
at the pin the design says it serves. That is self-consistency, and it belongs to authoring.

It does not judge whether the intent was any good. Whether 60 Ω is the right target, whether
this decoupling strategy suits this MCU, whether the stack is sensible — that is review, and
it stays with [`kicad-happy`](https://github.com/aklofas/kicad-happy), read-only and
independent. The skill does not become an EMC reviewer by acquiring a check engine.

## 7. Validation gates

| Gate | Command | Pass condition |
|---|---|---|
| Schematic stage | `kicad-author check --stage sch` | ERC errors 0, golden netlist exact |
| Rules in force | `kicad-author rules` then `t1.netclasses` | Declared classes present on the board |
| **Critical placement, pre-route** | `kicad-author check --stage preroute` | Tier 1-2 clear before a single track is routed |
| Final board | `kicad-author check --stage final` | No gating tier 1-2 result, no gating unknown |

The pre-route gate is the cheap one. A protection device on the wrong side of a net costs a
part move before routing and a re-route afterwards; the same argument as the schematic's
pre-write stub collision check (§3.1 there).

## 8. Incident log (PCB side)

Observed in this repository, on the first release, before these mechanisms existed:

| Symptom | Root cause | Now prevented by |
|---|---|---|
| Isolation and DRC results described pre-route copper | `build.sh` saved the board after `ImportSpecctraSES` without re-filling; `pcb.refill()` existed but the pipeline never called it | `facts --finalize`, and `t1.zones-filled` fails on any unfilled pour |
| Every net routed with KiCad defaults, supplies included | No net classes, stackup or custom rules were generated anywhere — the autorouter was told only "connect everything" | `physical.py` + `kicad-author rules`, verified on the board by `t1.netclasses` |
| Keepouts named layers a two-layer board does not have | `add_keepout` defaulted to a hardcoded `F/In1/In2/B` guess | Default is now the board's enabled copper layers; `t1.keepout.*` checks declared coverage |
| An L-shaped pour could pass or fail the isolation check wrongly | The check compared zone **bounding boxes** | Exact polygon predicates in `poly.py`, over filled polygons |
| DRC warnings were invisible | The pipeline ran `--severity-error` only | Both severities exported; errors gate at tier 1, warnings report at tier 3 |
| `build.sh` referenced `src/gen_pcb.py`, which no template provided | All PCB decisions lived in a file the skill did not ship or version | `templates/gen_pcb.py`, a thin wrapper with every decision in `src/*.py` |
| "C1 decouples U1" existed only as a description string no code read | The design source had no place to say which pin, which domain, where the return goes | `GROUPS` in `design.py`, expanded into checked primitives |
