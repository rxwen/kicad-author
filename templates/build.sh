#!/usr/bin/env bash
# Full build: from design.py to manufacturing files, with validation at every step.
# Usage: bash src/build.sh [--no-route]
set -euo pipefail
cd "$(dirname "$0")/.."

KA="${KICAD_AUTHOR_HOME:-$(cd .. && pwd)/.claude/skills/kicad-author}/scripts/kicad-author"
[ -x "$KA" ] || { echo "Cannot find kicad-author: $KA"; exit 1; }
eval "$("$KA" env --export)"          # Export KICAD_CLI / KICAD_PY

mkdir -p build fab
NAME=$(python3 -c "import sys;sys.path.insert(0,'src');import design;print(design.BOARD_NAME)")

echo "── 1/6 Generate schematic ──"
"$KA" sch --project .

echo "── 2/6 Gate: ERC ──"
"$KICAD_CLI" sch erc --format json --severity-error --exit-code-violations \
    -o build/erc.json "$NAME.kicad_sch"

echo "── 3/6 Gate: golden netlist ──"
"$KICAD_CLI" sch export netlist --format kicadsexpr -o "build/$NAME.net" "$NAME.kicad_sch" >/dev/null
"$KA" gate --project .

echo "── 4/6 Generate PCB ──"
"$KICAD_PY" src/gen_pcb.py            # Thin wrapper around kicad_author.pcb

if [ "${1:-}" != "--no-route" ]; then
  echo "── 5/6 Autorouting (Freerouting) ──"
  "$KICAD_PY" -c "import pcbnew;b=pcbnew.LoadBoard('$NAME.kicad_pcb');pcbnew.ExportSpecctraDSN(b,'build/$NAME.dsn')"
  "${JAVA_BIN:-java}" -jar "$FREEROUTING_JAR" -de "build/$NAME.dsn" -do "build/$NAME.ses" -mt 1 -mp 30
  "$KICAD_PY" -c "
import pcbnew
b=pcbnew.LoadBoard('$NAME.kicad_pcb')
assert pcbnew.ImportSpecctraSES(b,'build/$NAME.ses')
pcbnew.SaveBoard('$NAME.kicad_pcb',b)"
fi

echo "── 6/6 Gate: DRC + schematic parity ──"
"$KICAD_CLI" pcb drc --format json --schematic-parity --severity-error \
    --exit-code-violations -o build/drc.json "$NAME.kicad_pcb"

echo "✅ Build passed"
