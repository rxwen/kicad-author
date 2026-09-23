"""Minimal S-expression parser for netlist validation when kicad-happy is unavailable."""


def parse(text):
    toks, i, n = [], 0, len(text)
    cur = []
    stack = [cur]
    while i < n:
        c = text[i]
        if c == "(":
            new = []
            stack[-1].append(new)
            stack.append(new)
            i += 1
        elif c == ")":
            stack.pop()
            i += 1
        elif c == '"':
            j = i + 1
            buf = []
            while j < n and text[j] != '"':
                if text[j] == "\\":
                    j += 1
                buf.append(text[j])
                j += 1
            stack[-1].append("".join(buf))
            i = j + 1
        elif c.isspace():
            i += 1
        else:
            j = i
            while j < n and not text[j].isspace() and text[j] not in '()"':
                j += 1
            stack[-1].append(text[i:j])
            i = j
    return toks or cur


def find_all(node, kw):
    if not isinstance(node, list):
        return []
    return [x for x in node if isinstance(x, list) and x and x[0] == kw]


def find_first(node, kw):
    r = find_all(node, kw)
    return r[0] if r else None


def get_value(node, kw):
    f = find_first(node, kw)
    return f[1] if f and len(f) > 1 else None
