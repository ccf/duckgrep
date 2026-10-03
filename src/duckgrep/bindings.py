"""Binding facts for type inference: what each name is bound to, per scope (Python; pure, per file).

extract.py records these rows; EDGES_COMPUTE's `typed` tier resolves them. Every binding of a name is
recorded, typed or not, so the resolver can tell one binding from many: an untyped binding (an unannotated
parameter, a loop variable) has a NULL type and only blocks inference.
"""

from __future__ import annotations

import re

BINDING_COLS = ["path", "scope", "name", "kind", "type_text", "line", "pos"]

DOTTED = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*\Z")
_TYPING = ("typing.", "typing_extensions.")
_UNWRAP = {"Optional", "Final", "ClassVar", "Annotated", "type", "Type", "Required", "NotRequired", "ReadOnly"}
_NO_METHODS = {"object", "Generic", "Protocol", "ABC", "abc.ABC"}  # bases that add no methods worth walking
_LITERALS = {
    "string": "str",
    "concatenated_string": "str",
    "integer": "int",
    "float": "float",
    "true": "bool",
    "false": "bool",
    "list": "list",
    "list_comprehension": "list",
    "dictionary": "dict",
    "dictionary_comprehension": "dict",
    "set": "set",
    "set_comprehension": "set",
    "tuple": "tuple",
}
_TARGETS = {
    "pattern_list",
    "tuple_pattern",
    "list_pattern",
    "parenthesized_expression",
    "tuple",
    "list",
    "list_splat_pattern",
    "as_pattern_target",
    "expression_list",
}


def _split_top(s: str, sep: str) -> list[str]:
    parts, depth, cur = [], 0, []
    for ch in s:
        if ch in "[(":
            depth += 1
        elif ch in "])":
            depth -= 1
        if ch == sep and depth == 0:
            parts.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur).strip())
    return parts


def _strip_typing(t: str) -> str:
    for p in _TYPING:
        if t.startswith(p):
            return t[len(p) :]
    return t


def normalize_type(text: str, cls: str | None = None, generic_head: bool = False) -> str | None:
    """An annotation as one dotted class name, or None: unwraps Optional/X | None/quotes/Annotated/type[...],
    maps Self to `cls` (the enclosing class qualname). With generic_head, Base[T] names Base (for base classes); otherwise a subscript
    (a container or generic) is None."""
    t = " ".join(text.split())
    if len(t) >= 2 and t[0] == t[-1] and t[0] in "'\"":
        return normalize_type(t[1:-1], cls, generic_head)
    t = _strip_typing(t)
    union = [p for p in _split_top(t, "|") if p != "None"]
    if len(union) != 1:
        return None
    t = union[0]
    if t.endswith("]") and "[" in t:
        head = _strip_typing(t[: t.index("[")].strip())
        args = _split_top(t[t.index("[") + 1 : -1], ",")
        if head in _UNWRAP:
            return normalize_type(args[0], cls, generic_head)
        if head == "Union":
            rest = [a for a in args if a != "None"]
            return normalize_type(rest[0], cls, generic_head) if len(rest) == 1 else None
        return normalize_type(head, cls) if generic_head else None
    if t == "Self":
        return cls
    if t == "None":
        return None
    return t if DOTTED.match(t) else None


def _subtree(n):
    stack = [n]
    while stack:
        c = stack.pop()
        yield c
        stack.extend(c.named_children)


def extract_bindings(root, src: bytes, path: str) -> list[tuple]:
    out: list[tuple] = []

    def text(n) -> str:
        return src[n.start_byte : n.end_byte].decode("utf-8", "replace")

    def add(scope, name, kind, type_text, node, pos=0):
        out.append((path, scope, name, kind, type_text, node.start_point[0] + 1, pos))

    def idents(n):
        """identifier nodes a target binds (attributes and subscripts bind no name here)"""
        if n.type == "identifier":
            yield n
        elif n.type in _TARGETS:
            for c in n.named_children:
                yield from idents(c)

    def self_attrs(n):
        """self.x / cls.x targets inside a (possibly tuple) target"""
        if n.type == "attribute":
            obj = n.child_by_field_name("object")
            if obj is not None and obj.type == "identifier" and text(obj) in ("self", "cls"):
                yield n
        elif n.type in _TARGETS:
            for c in n.named_children:
                yield from self_attrs(c)

    def value_type(v, params) -> str | None:
        if v is None:
            return None
        if v.type == "assignment":  # a = b = Foo()
            return value_type(v.child_by_field_name("right"), params)
        if v.type == "call":
            fn = v.child_by_field_name("function")
            ft = " ".join(text(fn).split()) if fn is not None else ""
            return "call:" + ft if DOTTED.match(ft) else None
        if v.type == "identifier":
            return params.get(text(v))
        return _LITERALS.get(v.type)

    def rebound(body) -> set[str]:
        """names (re)assigned anywhere in a function body, not counting nested defs, classes and lambdas"""
        out, stack = set(), [body]
        while stack:
            n = stack.pop()
            if n.type in ("function_definition", "class_definition", "lambda"):
                # a nested def can still rebind it: `nonlocal p; p = ...`
                for c in _subtree(n):
                    if c.type == "nonlocal_statement":
                        out.update(text(i) for i in c.named_children if i.type == "identifier")
                continue
            target = None
            if n.type in ("assignment", "augmented_assignment", "for_statement", "for_in_clause"):
                target = n.child_by_field_name("left")
            elif n.type == "as_pattern":
                target = n.child_by_field_name("alias")
            elif n.type == "named_expression":
                target = n.child_by_field_name("name")
            elif n.type == "delete_statement":
                for c in n.named_children:
                    out.update(text(i) for i in idents(c))
            elif n.type in ("import_statement", "import_from_statement"):
                for c in n.children_by_field_name("name"):
                    a = c.child_by_field_name("alias") if c.type == "aliased_import" else None
                    out.add(text(a) if a is not None else text(c).split(".")[0])
            elif n.type in ("global_statement", "nonlocal_statement"):
                out.update(text(c) for c in n.named_children if c.type == "identifier")
            elif n.type == "case_pattern":
                out.update(text(c) for c in _subtree(n) if c.type == "identifier")
            if target is not None:
                out.update(text(i) for i in idents(target))
            stack.extend(n.named_children)
        return out

    def param(p):
        """(name, annotation node) for one parameter node"""
        t = p.type
        if t == "identifier":
            return text(p), None
        if t in ("list_splat_pattern", "dictionary_splat_pattern"):
            i = next((c for c in p.named_children if c.type == "identifier"), None)
            return (text(i), None) if i is not None else (None, None)
        if t == "typed_parameter":
            first = p.named_children[0] if p.named_child_count else None
            if first is None:
                return None, None
            if first.type != "identifier":  # *args: T / **kw: T bind a tuple / dict, not T
                return param(first)[0], None
            return text(first), p.child_by_field_name("type")
        if t in ("default_parameter", "typed_default_parameter"):
            n = p.child_by_field_name("name")
            return (text(n) if n is not None else None), p.child_by_field_name("type")
        return None, None

    def walk_class(c, qual, short):
        if c.type == "expression_statement" and c.named_child_count and c.named_children[0].type == "assignment":
            a = c.named_children[0]
            left, right, ann = (a.child_by_field_name(f) for f in ("left", "right", "type"))
            if left is not None and left.type == "identifier":
                tt = normalize_type(text(ann), qual) if ann is not None else value_type(right, {})
                add(qual, "self." + text(left), "attr", tt, left)
            return
        walk(c, qual, (qual, short), {})

    def walk(n, scope, cls, params):
        t = n.type
        if t == "class_definition":
            nm = n.child_by_field_name("name")
            if nm is None:
                return
            short = text(nm)
            qual = f"{scope}.{short}" if scope else short
            sup = n.child_by_field_name("superclasses")
            pos = 0
            for b in sup.named_children if sup is not None else []:
                if b.type == "keyword_argument":
                    continue
                bt = normalize_type(text(b), qual, generic_head=True)
                if bt not in _NO_METHODS:
                    add(qual, "", "base", bt, b, pos)
                pos += 1
            body = n.child_by_field_name("body")
            for c in body.named_children if body is not None else []:
                walk_class(c, qual, short)
            return
        if t == "function_definition":
            nm = n.child_by_field_name("name")
            if nm is None:
                return
            qual = f"{scope}.{text(nm)}" if scope else text(nm)
            ps: dict[str, str] = {}
            plist = n.child_by_field_name("parameters")
            for p in plist.named_children if plist is not None else []:
                pname, ann = param(p)
                if pname is None:
                    continue
                tt = normalize_type(text(ann), cls[0] if cls else None) if ann is not None else None
                add(qual, pname, "param", tt, p)
                if tt:
                    ps[pname] = tt
            body = n.child_by_field_name("body")
            if body is not None:
                again = rebound(body)  # an annotation no longer types a parameter that is reassigned
                walk(body, qual, cls, {k: v for k, v in ps.items() if k not in again})
            return
        if t == "assignment":
            left, right, ann = (n.child_by_field_name(f) for f in ("left", "right", "type"))
            tt = normalize_type(text(ann), cls[0] if cls else None) if ann is not None else value_type(right, params)
            if left is not None and left.type == "identifier":
                add(scope, text(left), "annot" if ann is not None else "assign", tt, left)
            elif left is not None and left.type == "attribute":
                for a in self_attrs(left):
                    if cls:
                        add(cls[0], "self." + text(a.child_by_field_name("attribute")), "attr", tt, a)
            elif left is not None:
                for i in idents(left):
                    add(scope, text(i), "assign", None, i)
                for a in self_attrs(left):
                    if cls:
                        add(cls[0], "self." + text(a.child_by_field_name("attribute")), "attr", None, a)
            if right is not None:
                walk(right, scope, cls, params)
            return
        if t == "augmented_assignment":
            left = n.child_by_field_name("left")
            if left is not None:
                for i in idents(left):
                    add(scope, text(i), "assign", None, i)
                for a in self_attrs(left):
                    if cls:
                        add(cls[0], "self." + text(a.child_by_field_name("attribute")), "attr", None, a)
        elif t in ("for_statement", "for_in_clause"):
            left = n.child_by_field_name("left")
            if left is not None:
                for i in idents(left):
                    add(scope, text(i), "assign", None, i)
        elif t == "as_pattern":
            alias = n.child_by_field_name("alias")
            if alias is not None:
                for i in idents(alias):
                    add(scope, text(i), "assign", None, i)
        elif t == "named_expression":
            nm = n.child_by_field_name("name")
            if nm is not None:
                add(scope, text(nm), "assign", None, nm)
        elif t == "delete_statement":
            for c in n.named_children:
                for i in idents(c):
                    add(scope, text(i), "assign", None, i)
        elif t in ("global_statement", "nonlocal_statement"):
            for c in n.named_children:
                if c.type == "identifier":
                    add(scope, text(c), "global", None, c)
        elif t in ("import_statement", "import_from_statement"):
            # at module level an import is its own kind: alone it lets the resolver follow the import, beside another
            # binding of the name it blocks inference (try: from fast import x / except: x = Slow())
            kind = "assign" if scope else "import"
            for c in n.children_by_field_name("name"):
                if c.type == "aliased_import":
                    a = c.child_by_field_name("alias")
                    add(scope, text(a), kind, None, a)
                else:
                    add(scope, text(c).split(".")[0], kind, None, c)
        elif t == "lambda":  # its parameters shadow the name inside the lambda, whose refs carry this scope
            plist = n.child_by_field_name("parameters")
            for p in plist.named_children if plist is not None else []:
                pname = param(p)[0]
                if pname is not None:
                    add(scope, pname, "assign", None, p)
        elif t == "case_clause":  # match captures bind names (a class name in a pattern only blocks, harmlessly)
            stack = [c for c in n.named_children if c.type == "case_pattern"]
            while stack:
                c = stack.pop()
                if c.type == "identifier":
                    add(scope, text(c), "assign", None, c)
                stack.extend(c.named_children)
        for c in n.named_children:
            walk(c, scope, cls, params)

    walk(root, "", None, {})
    return out
