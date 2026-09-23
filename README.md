# kicad-author

**Generate readable KiCad schematics and PCBs from code.** A Claude Code / Claude Agent Skill.

The official KiCad `kicad-cli` provides `erc` / `drc` / `export` / `import` /
`render` / `upgrade`, but **no authoring commands**. The `pcbnew` Python API covers
PCBs only; schematics lack an API. AI agents therefore tend to write disposable
scripts with inconsistent drawing styles, and layout adjustments can silently
break connectivity.

This skill provides three reusable pieces:

| Resource | Purpose |
|---|---|
| **Drawing conventions** | [`references/schematic-layout-conventions.md`](references/schematic-layout-conventions.md) fills the gap left by KLC, which covers symbol/footprint libraries, and ERC, which checks electrical correctness |
| **Deterministic implementation** | `scripts/kicad_author/`: standard-library-only comb routing, tiered stub lengths, power symbols, and S-expression reading/writing |
| **Exit-code validation gates** | The **golden netlist** ensures that drawing, layout, and stub-length changes preserve every connection |

> During development, seven connectivity errors occurred despite knowing the rules:
> trunks crossed other nets' endpoints, longer stubs shorted nets, and unlabeled
> wires caused KiCad to rename nets. **Validation gates caught every one.**
> Section 9 of the conventions records each incident.

## Installation (project-local)

Clone and symlink into the project's `.claude/skills/` without changing the global environment:

```bash
git clone https://github.com/rxwen/kicad-author.git .claude/vendor/kicad-author
ln -s ../vendor/kicad-author .claude/skills/kicad-author
```

Dependencies: Python 3.8+ (standard library only) and KiCad 9/10 `kicad-cli`.
PCB operations also require KiCad's Python interpreter with `pcbnew`.
Run `scripts/kicad-author env` to inspect the environment.

## Usage

```bash
scripts/kicad-author env                    # Detect the toolchain
scripts/kicad-author sch  --project <dir>   # design.py + layout.py → .kicad_sch
scripts/kicad-author gate --project <dir>   # Check every pin against the golden netlist
```

Projects provide **data** (`src/design.py` for the design and `src/layout.py` for
presentation); the skill provides **mechanisms**. The golden netlist protects this
separation. See [`SKILL.md`](SKILL.md) for the contract and [`templates/`](templates/)
for starter files.

## Boundaries

The following tasks are delegated to other tools or manual input:

| Task | Handled by |
|---|---|
| Automatic placement | Manual positions in `layout.py`; automatic layouts are often harder to read |
| PCB autorouting | [Freerouting](https://github.com/freerouting/freerouting) |
| Component libraries | [`easyeda2kicad`](https://github.com/uPesy/easyeda2kicad.py), fetching symbols, footprints, and 3D models by LCSC part number |
| Design review | [`kicad-happy`](https://github.com/aklofas/kicad-happy), read-only and deliberately separate from authoring |

## License

MIT
