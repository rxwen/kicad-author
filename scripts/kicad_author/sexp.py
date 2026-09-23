"""KiCad S-expression reading: balanced-parenthesis scanning and symbol library parsing.

Do not split blocks with regex: the library's final closing parenthesis can end up
inside the last symbol, leaving unbalanced output that KiCad refuses to load.
"""
import re


def balanced_end(text, start):
    """Given '(' at text[start], return the index after its matching ')', respecting strings and escapes."""
    depth, i, n, in_str, esc = 0, start, len(text), False, False
    while i < n:
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return n


_PIN = re.compile(
    r'\(pin (\w+) \w+\s*\(at ([\-\d.]+) ([\-\d.]+) (\d+)\)'
    r'.*?\(name "([^"]*)".*?\(number "([^"]*)"', re.S)


def load_symbols(path):
    """Return {symbol_name: (source_text, [(pin_number, x, y, angle), ...])}.

    Support both official KiCad tab indentation and easyeda2kicad space indentation.
    """
    text = path.read_text() if hasattr(path, "read_text") else open(path).read()
    indent = "\t" if re.search(r'^\t\(symbol "', text, re.M) else "  "
    pat = re.compile(r'^%s\(symbol "([^"]+)"' % re.escape(indent), re.M)
    out = {}
    for m in pat.finditer(text):
        blk = text[m.start():balanced_end(text, m.start())]
        pins = [(p.group(6), float(p.group(2)), float(p.group(3)), int(p.group(4)))
                for p in _PIN.finditer(blk)]
        out[m.group(1)] = (blk.rstrip(), pins)
    return out


def embed(lib_id, src):
    """Rename a library symbol block and embed it in .kicad_sch lib_symbols."""
    body = "\n".join("\t" + ln for ln in src.split("\n", 1)[1].split("\n"))
    return [f'\t\t(symbol "{lib_id}"', body]
