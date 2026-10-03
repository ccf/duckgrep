"""Per-file extraction: tree-sitter parse -> symbols, refs, imports, module keys.

Everything here is a pure function of (path, bytes, small repo context) so it can
run in worker processes and be cached by content hash.

The walker is table-driven: each language supplies a `Spec` describing which
node types are definitions, calls, member accesses, and which syntactic
positions put an identifier into a context (type, import, write, ...).
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass, field

import tree_sitter as ts

from .bindings import extract_bindings, normalize_type

EXT_LANG = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".rs": "rust",
}
FAMILY = {"python": "py", "javascript": "js", "typescript": "js", "tsx": "js", "go": "go", "rust": "rs"}

STICKY = frozenset({"inherit", "decorator", "import", "type", "param"})
CTX_KIND = {
    "callee": "call",
    "attr_call": "call",
    "attr": "attr",
    "attr_write": "write",
    "write": "write",
    "inherit": "inherit",
    "decorator": "decorator",
    "import": "import",
    "type": "type",
    "kwarg": "kwarg",
    "normal": "name",
}
CLASS_KINDS = frozenset({"class", "interface", "struct", "trait", "enum", "impl"})

_LANGS: dict[str, ts.Language] = {}
_PARSERS: dict[str, ts.Parser] = {}


def get_parser(lang: str) -> ts.Parser:
    p = _PARSERS.get(lang)
    if p is None:
        if lang == "python":
            import tree_sitter_python as m

            raw = m.language()
        elif lang == "javascript":
            import tree_sitter_javascript as m

            raw = m.language()
        elif lang == "typescript":
            import tree_sitter_typescript as m

            raw = m.language_typescript()
        elif lang == "tsx":
            import tree_sitter_typescript as m

            raw = m.language_tsx()
        elif lang == "go":
            import tree_sitter_go as m

            raw = m.language()
        elif lang == "rust":
            import tree_sitter_rust as m

            raw = m.language()
        else:
            raise ValueError(lang)
        _LANGS[lang] = ts.Language(raw)
        p = _PARSERS[lang] = ts.Parser(_LANGS[lang])
    return p


@dataclass
class Spec:
    defs: dict  # node type -> symbol kind
    idents: frozenset  # identifier-like leaf node types
    type_idents: frozenset  # identifier types that always denote a type
    calls: dict  # node type -> field holding the callee
    attrs: dict  # node type -> (object field, member field)
    sticky: dict  # node type -> context for all descendants
    field_ctx: dict  # (parent type | '*', field) -> context
    resets: frozenset  # (parent type, field) -> back to 'normal'
    write_through: frozenset  # node types that pass a 'write' context to children
    comments: frozenset
    import_nodes: frozenset
    scope_only: dict = field(default_factory=dict)  # node type -> field naming the scope (Rust impl)


PY = Spec(
    defs={"function_definition": "function", "class_definition": "class"},
    idents=frozenset({"identifier"}),
    type_idents=frozenset(),
    calls={"call": "function"},
    attrs={"attribute": ("object", "attribute")},
    sticky={
        "decorator": "decorator",
        "import_statement": "import",
        "import_from_statement": "import",
        "future_import_statement": "import",
        "parameters": "param",
        "lambda_parameters": "param",
        "type": "type",
    },
    field_ctx={
        ("class_definition", "superclasses"): "inherit",
        ("keyword_argument", "name"): "kwarg",
        ("assignment", "left"): "write",
        ("augmented_assignment", "left"): "write",
        ("for_statement", "left"): "write",
        ("for_in_clause", "left"): "write",
        ("*", "type"): "type",
        ("*", "return_type"): "type",
    },
    resets=frozenset({("call", "arguments"), ("default_parameter", "value"), ("typed_default_parameter", "value")}),
    write_through=frozenset(
        {"pattern_list", "tuple_pattern", "list_pattern", "tuple", "list", "parenthesized_expression"}
    ),
    comments=frozenset({"comment"}),
    import_nodes=frozenset({"import_statement", "import_from_statement"}),
)

JS = Spec(
    defs={
        "function_declaration": "function",
        "generator_function_declaration": "function",
        "class_declaration": "class",
        "abstract_class_declaration": "class",
        "method_definition": "method",
        "interface_declaration": "interface",
        "type_alias_declaration": "type",
        "enum_declaration": "enum",
        "function_signature": "function",
        "method_signature": "method",
        "abstract_method_signature": "method",
        "internal_module": "module",
    },
    idents=frozenset(
        {
            "identifier",
            "property_identifier",
            "type_identifier",
            "shorthand_property_identifier",
            "shorthand_property_identifier_pattern",
            "private_property_identifier",
        }
    ),
    type_idents=frozenset({"type_identifier"}),
    calls={
        "call_expression": "function",
        "new_expression": "constructor",
        "jsx_opening_element": "name",
        "jsx_self_closing_element": "name",
    },
    attrs={"member_expression": ("object", "property")},
    sticky={
        "import_statement": "import",
        "decorator": "decorator",
        "class_heritage": "inherit",
        "extends_type_clause": "inherit",
        "formal_parameters": "param",
        "type_annotation": "type",
        "type_arguments": "type",
        "type_parameters": "param",
    },
    field_ctx={
        ("assignment_expression", "left"): "write",
        ("augmented_assignment_expression", "left"): "write",
        ("variable_declarator", "name"): "write",
        ("pair", "key"): "kwarg",
        ("*", "type"): "type",
        ("*", "return_type"): "type",
    },
    resets=frozenset(
        {
            ("call_expression", "arguments"),
            ("new_expression", "arguments"),
            ("required_parameter", "value"),
            ("optional_parameter", "value"),
            ("assignment_pattern", "right"),
        }
    ),
    write_through=frozenset({"array_pattern", "object_pattern", "pair_pattern"}),
    comments=frozenset({"comment"}),
    import_nodes=frozenset({"import_statement", "export_statement"}),
)

GO = Spec(
    defs={
        "function_declaration": "function",
        "method_declaration": "method",
        "type_spec": "type",
        "type_alias": "type",
        "method_elem": "method",
        "method_spec": "method",
    },
    idents=frozenset({"identifier", "field_identifier", "type_identifier", "package_identifier"}),
    type_idents=frozenset({"type_identifier"}),
    calls={"call_expression": "function"},
    attrs={"selector_expression": ("operand", "field"), "qualified_type": ("package", "name")},
    sticky={"import_declaration": "import", "parameter_list": "param", "type_arguments": "type"},
    field_ctx={
        ("assignment_statement", "left"): "write",
        ("short_var_declaration", "left"): "write",
        ("var_spec", "name"): "write",
        ("const_spec", "name"): "write",
        ("range_clause", "left"): "write",
        ("keyed_element", "key"): "kwarg",
        ("*", "type"): "type",
        ("*", "result"): "type",
    },
    resets=frozenset({("call_expression", "arguments")}),
    write_through=frozenset({"expression_list"}),
    comments=frozenset({"comment"}),
    import_nodes=frozenset({"import_declaration"}),
)

RS = Spec(
    defs={
        "function_item": "function",
        "function_signature_item": "function",
        "struct_item": "struct",
        "enum_item": "enum",
        "union_item": "struct",
        "trait_item": "trait",
        "type_item": "type",
        "const_item": "constant",
        "static_item": "variable",
        "mod_item": "module",
        "macro_definition": "macro",
    },
    idents=frozenset({"identifier", "field_identifier", "type_identifier"}),
    type_idents=frozenset({"type_identifier"}),
    calls={"call_expression": "function", "macro_invocation": "macro"},
    attrs={
        "field_expression": ("value", "field"),
        "scoped_identifier": ("path", "name"),
        "scoped_type_identifier": ("path", "name"),
    },
    sticky={
        "use_declaration": "import",
        "parameters": "param",
        "attribute_item": "decorator",
        "type_arguments": "type",
        "type_parameters": "param",
    },
    field_ctx={
        ("let_declaration", "pattern"): "write",
        ("assignment_expression", "left"): "write",
        ("compound_assignment_expr", "left"): "write",
        ("field_initializer", "field"): "kwarg",
        ("impl_item", "trait"): "inherit",
        ("*", "type"): "type",
        ("*", "return_type"): "type",
    },
    resets=frozenset({("call_expression", "arguments")}),
    write_through=frozenset({"tuple_pattern"}),
    comments=frozenset({"line_comment", "block_comment"}),
    import_nodes=frozenset({"use_declaration"}),
    scope_only={"impl_item": "type"},
)

SPECS = {"python": PY, "javascript": JS, "typescript": JS, "tsx": JS, "go": GO, "rust": RS}

JS_FUNC_VALUES = frozenset({"arrow_function", "function_expression", "function", "generator_function"})


def _text(src: bytes, node) -> str:
    return src[node.start_byte : node.end_byte].decode("utf-8", "replace")


def _one_line(s: str, limit: int = 240) -> str:
    s = " ".join(s.split())
    return s if len(s) <= limit else s[: limit - 3] + "..."


# ---------------------------------------------------------------- definitions


def _def_targets(node, t: str, spec: Spec, lang: str, src: bytes, top_level: bool):
    """Return [(name_node, kind, push_scope, parent_override, body_node)] for a definition node."""
    if t in spec.defs:
        kind = spec.defs[t]
        if lang == "go":
            if t == "type_spec":
                ty = node.child_by_field_name("type")
                kind = {"struct_type": "struct", "interface_type": "interface"}.get(ty.type if ty else "", "type")
            if t == "method_declaration":
                recv = node.child_by_field_name("receiver")
                rname = _first_of_type(recv, "type_identifier", src) if recv else None
                return [(node.child_by_field_name("name"), "method", True, rname, node.child_by_field_name("body"))]
        name = node.child_by_field_name("name")
        if name is None:
            return []
        return [(name, kind, kind not in ("constant", "variable"), None, node.child_by_field_name("body"))]

    if lang == "python" and t == "assignment" and top_level:
        p = node.parent
        if p is not None and p.type == "expression_statement" and p.parent is not None and p.parent.type == "module":
            left = node.child_by_field_name("left")
            if left is not None and left.type == "identifier":
                nm = _text(src, left)
                return [(left, "constant" if nm.isupper() else "variable", False, None, None)]
        return []

    if lang in ("javascript", "typescript", "tsx"):
        if t == "variable_declarator":
            name = node.child_by_field_name("name")
            val = node.child_by_field_name("value")
            if name is None or name.type != "identifier":
                return []
            if val is not None and val.type in JS_FUNC_VALUES:
                return [(name, "function", True, None, val.child_by_field_name("body"))]
            if val is not None and val.type == "class":
                return [(name, "class", True, None, val.child_by_field_name("body"))]
            if top_level:
                decl = node.parent
                kw = decl.child_by_field_name("kind") if decl is not None else None
                is_const = kw is not None and _text(src, kw) == "const"
                return [(name, "constant" if is_const else "variable", False, None, None)]
            return []
        if t in ("public_field_definition", "field_definition"):
            name = node.child_by_field_name("name") or node.child_by_field_name("property")
            val = node.child_by_field_name("value")
            if name is not None and val is not None and val.type in JS_FUNC_VALUES:
                return [(name, "method", True, None, val.child_by_field_name("body"))]
        return []

    if lang == "go" and t in ("const_spec", "var_spec") and top_level:
        kind = "constant" if t == "const_spec" else "variable"
        return [(n, kind, False, None, None) for n in node.children_by_field_name("name")]

    return []


def _first_of_type(node, typ: str, src: bytes):
    stack = [node]
    while stack:
        n = stack.pop()
        if n.type == typ:
            return _text(src, n)
        stack.extend(reversed(n.children))
    return None


def _signature(node, body, src: bytes, lang: str) -> str:
    if body is not None and body.start_byte > node.start_byte:
        s = src[node.start_byte : body.start_byte].decode("utf-8", "replace")
    else:
        s = src[node.start_byte : node.end_byte].decode("utf-8", "replace").split("\n", 1)[0]
    s = _one_line(s)
    return s.rstrip(" :{=").rstrip() if lang == "python" or s.endswith(("{", "=")) else s


def _docstring(node, body, spec: Spec, lang: str, src: bytes) -> str | None:
    if lang == "python":
        if body is None:
            return None
        first = body.named_children[0] if body.named_child_count else None
        if first is not None and first.type == "expression_statement" and first.named_child_count:
            s = first.named_children[0]
            if s.type == "string":
                raw = _text(src, s).strip()
                raw = raw.lstrip("rRbBuUfF")
                for q in ('"""', "'''", '"', "'"):
                    if raw.startswith(q) and raw.endswith(q) and len(raw) >= 2 * len(q):
                        raw = raw[len(q) : -len(q)]
                        break
                for ln in raw.splitlines():
                    if ln.strip():
                        return _one_line(ln, 200)
        return None
    # leading comment block (skipping attributes / decorators / export wrappers)
    anchor = node
    while anchor.parent is not None and anchor.parent.type in (
        "export_statement",
        "decorated_definition",
        "lexical_declaration",
        "variable_declaration",
        "type_declaration",
        "const_declaration",
        "var_declaration",
    ):
        anchor = anchor.parent
    lines = []
    prev = anchor.prev_named_sibling
    expect_row = anchor.start_point[0]
    while prev is not None and (prev.type in spec.comments or prev.type == "attribute_item"):
        if prev.end_point[0] < expect_row - 1:
            break
        if prev.type in spec.comments:
            lines.insert(0, _text(src, prev))
        expect_row = prev.start_point[0]
        prev = prev.prev_named_sibling
    for blk in lines:
        for ln in blk.splitlines():
            ln = ln.strip().lstrip("/*!").rstrip("*/").strip()
            if ln:
                return _one_line(ln, 200)
    return None


def _exported(node, name: str, kind: str, lang: str) -> bool:
    if lang == "python":
        return not name.startswith("_") or (name.startswith("__") and name.endswith("__"))
    if lang == "go":
        return name[:1].isupper()
    if lang == "rust":
        return any(c.type == "visibility_modifier" for c in node.children)
    # js / ts
    if kind == "method":
        return not name.startswith(("#", "_"))
    p = node.parent
    for _ in range(3):
        if p is None:
            break
        if p.type == "export_statement":
            return True
        p = p.parent
    return False


# ---------------------------------------------------------------- imports


def _py_package(path: str) -> list[str]:
    parts = path[:-3].split("/") if path.endswith(".py") else path.rsplit(".", 1)[0].split("/")
    return parts[:-1]


def _py_abs(modtxt: str, path: str) -> str:
    if not modtxt.startswith("."):
        return modtxt
    level = len(modtxt) - len(modtxt.lstrip("."))
    rest = modtxt[level:]
    pkg = _py_package(path)
    base = pkg[: len(pkg) - (level - 1)] if level - 1 <= len(pkg) else []
    return ".".join(base + ([rest] if rest else []))


def _imports_python(node, src, path, ctx):
    line = node.start_point[0] + 1
    out = []
    if node.type == "import_statement":
        for c in node.children_by_field_name("name"):
            if c.type == "aliased_import":
                mod = _text(src, c.child_by_field_name("name"))
                alias = _text(src, c.child_by_field_name("alias"))
                out.append((mod, None, alias, alias, line, mod, None))
            else:
                mod = _text(src, c)
                out.append((mod, None, None, mod.split(".")[0], line, mod, None))
    else:
        mn = node.child_by_field_name("module_name")
        modtxt = _text(src, mn) if mn is not None else ""
        absmod = _py_abs(modtxt, path)
        if any(c.type == "wildcard_import" for c in node.children):
            out.append((modtxt, "*", None, None, line, absmod, None))
        for c in node.children_by_field_name("name"):
            if c.type == "aliased_import":
                nm = _text(src, c.child_by_field_name("name"))
                alias = _text(src, c.child_by_field_name("alias"))
            else:
                nm, alias = _text(src, c), None
            sub = f"{absmod}.{nm}" if absmod else nm
            out.append((modtxt, nm, alias, alias or nm, line, absmod, sub))
    return out


_JS_EXTS = (".d.ts", ".tsx", ".ts", ".mts", ".cts", ".jsx", ".js", ".mjs", ".cjs")


def _strip_js_ext(p: str) -> str:
    for e in _JS_EXTS:
        if p.endswith(e):
            return p[: -len(e)]
    return p


def _js_key(source: str, path: str) -> str:
    if source.startswith("."):
        return _strip_js_ext(posixpath.normpath(posixpath.join(posixpath.dirname(path), source)))
    return source


def _imports_js(node, src, path, ctx):
    line = node.start_point[0] + 1
    s = node.child_by_field_name("source")
    if s is None:
        return []
    source = _text(src, s).strip("'\"`")
    key = _js_key(source, path)
    out = []
    if node.type == "export_statement":  # re-export: export {a as b} from './x' / export * from './x'
        for c in node.children:
            if c.type == "*":
                out.append((source, "*", None, None, line, key, None))
            elif c.type == "namespace_export":
                ident = next((x for x in c.children if x.type == "identifier"), None)
                out.append((source, "*", None, _text(src, ident) if ident else None, line, key, None))
            elif c.type == "export_clause":
                for spec in c.named_children:
                    if spec.type != "export_specifier":
                        continue
                    nm = _text(src, spec.child_by_field_name("name"))
                    al = spec.child_by_field_name("alias")
                    alias = _text(src, al) if al is not None else None
                    out.append((source, nm, alias, alias or nm, line, key, None))
        return out
    clause = next((c for c in node.children if c.type == "import_clause"), None)
    if clause is None:
        return [(source, None, None, None, line, key, None)]
    for c in clause.children:
        if c.type == "identifier":
            out.append((source, "default", None, _text(src, c), line, key, None))
        elif c.type == "namespace_import":
            ident = next((x for x in c.children if x.type == "identifier"), None)
            out.append((source, "*", None, _text(src, ident) if ident else None, line, key, None))
        elif c.type == "named_imports":
            for spec in c.named_children:
                if spec.type != "import_specifier":
                    continue
                nm = _text(src, spec.child_by_field_name("name"))
                al = spec.child_by_field_name("alias")
                alias = _text(src, al) if al is not None else None
                out.append((source, nm, alias, alias or nm, line, key, None))
    return out


def _go_key(ipath: str, ctx) -> str:
    for mdir, mod in ctx.get("gomods", ()):
        if ipath == mod or ipath.startswith(mod + "/"):
            rest = ipath[len(mod) :].lstrip("/")
            return posixpath.join(mdir, rest).strip("/") if (mdir or rest) else ""
    return ipath


def _imports_go(node, src, path, ctx):
    out = []
    stack = [node]
    while stack:
        n = stack.pop()
        if n.type == "import_spec":
            p = n.child_by_field_name("path")
            ipath = _text(src, p).strip('"`')
            nm = n.child_by_field_name("name")
            alias = _text(src, nm) if nm is not None else None
            local = alias if alias not in (None, ".", "_") else ipath.rsplit("/", 1)[-1]
            out.append((ipath, None, alias, local, n.start_point[0] + 1, _go_key(ipath, ctx), None))
        else:
            stack.extend(n.children)
    return out


RS_EXTERNAL = frozenset({"std", "core", "alloc", "proc_macro", "test"})


def _rs_crate(path: str, ctx) -> tuple[str, str] | None:
    """(package dir, crate import name) of the Cargo package that owns `path`; None outside any package."""
    for d, name in (ctx or {}).get("rscrates", ()):
        if not d or path.startswith(d + "/"):
            return d, name
    return None


def _rs_layout(path: str, ctx) -> tuple[list[str], list[str], list[str]]:
    """(crate root, module key, scope) of a Rust file, all as segment lists.

    Files under <package>/src belong to crate <name>. tests/, examples/ and benches/ files are keyed
    <name>::tests and so on, but each of tests/<x>.rs, src/bin/<x>.rs and <dir>/<x>/main.rs is the root of its
    own crate: `crate::` names <name>::tests (tests/<x>.rs, tests/common/**), <name>::bin (src/bin/<x>.rs),
    <name>::bin::<x> (src/bin/<x>/**) or <name>::tests::<x> (tests/<x>/**, assumed to have a main.rs).
    `scope` is what `self::`, `super::` and uniform paths start from: the module key, except for a
    single-file crate root, whose scope is its directory. Other files outside src/ (build.rs) get a key no
    import can name. Without a Cargo.toml the old scheme applies: 'crate' + the path after the last src/.
    Known gap: items defined in a single-file crate root (src/bin/<x>.rs, tests/<x>.rs) are keyed by its file
    name, so `crate::Item` or `super::Item` from its submodules stays unresolved rather than resolving.
    """
    crate = _rs_crate(path, ctx)
    if crate is None:
        rel = path.split("/")
        if "src" in rel:
            rel = rel[len(rel) - rel[::-1].index("src") :]
        base, croot, single = ["crate"], ["crate"], False
    else:
        d, name = crate
        rel = (path[len(d) + 1 :] if d else path).split("/")
        if rel[0] == "src":
            rel = rel[1:]
            base, croot, single = [name], [name], False
            if rel[0] == "bin" and len(rel) > 1:
                if len(rel) == 2:
                    croot, single = [name, "bin"], True
                else:
                    croot = [name, "bin", rel[1]]
        elif rel[0] in ("tests", "examples", "benches") and len(rel) > 1:
            base = [name, rel[0]]
            rel = rel[1:]
            croot, single = base, len(rel) == 1
            if len(rel) > 1 and rel[0] != "common":
                croot = base + [rel[0]]
        else:
            key = [name, "!" + "/".join(rel)]
            return key, key, key
    stem = rel[-1].rsplit(".", 1)[0]
    key = base + rel[:-1] + ([] if stem in ("lib", "main", "mod") else [stem])
    return croot, key, base + rel[:-1] if single else key


def _rs_modpath(path: str, ctx=None) -> list[str]:
    return _rs_layout(path, ctx)[1]


def _rs_abs(mod: str, path: str, ctx=None, inline=()) -> str:
    """Module key of a `use` path written in `path`, inside the inline `mod` blocks `inline`."""
    segs = mod.split("::") if mod else []
    if segs and segs[0] == "":  # ::name
        segs = segs[1:]
    root, _, scope = _rs_layout(path, ctx)
    here = scope + list(inline)
    crates = {name for _, name in (ctx or {}).get("rscrates", ())}
    if not segs:
        return "::".join(here)
    head = segs[0]
    if head == "crate":
        segs = root + segs[1:]
    elif head == "self":
        segs = here + segs[1:]
    elif head == "super":
        n = 0
        while n < len(segs) and segs[n] == "super":
            n += 1
        segs = here[: max(len(root), len(here) - n)] + segs[n:]
    elif head in crates or head in RS_EXTERNAL:
        pass
    else:  # 2018 uniform paths: an item of the current module (`mod auth; use auth::Signer;`)
        segs = here + segs
    return "::".join(segs)


def _imports_rust(node, src, path, ctx):
    line = node.start_point[0] + 1
    out = []

    def flatten(n, prefix: str):
        t = n.type
        if t == "scoped_identifier":
            p = n.child_by_field_name("path")
            full_prefix = "::".join(x for x in (prefix, _text(src, p) if p is not None else "") if x)
            nm = _text(src, n.child_by_field_name("name"))
            out.append((full_prefix, nm, None))
        elif t in ("identifier", "self", "crate", "super"):
            out.append((prefix, _text(src, n), None))
        elif t == "use_as_clause":
            p = n.child_by_field_name("path")
            alias = _text(src, n.child_by_field_name("alias"))
            before = len(out)
            flatten(p, prefix)
            if len(out) > before:
                m, nm, _ = out[-1]
                out[-1] = (m, nm, alias)
        elif t == "scoped_use_list":
            p = n.child_by_field_name("path")
            new_prefix = "::".join(x for x in (prefix, _text(src, p) if p is not None else "") if x)
            lst = n.child_by_field_name("list")
            if lst is not None:
                for c in lst.named_children:
                    flatten(c, new_prefix)
        elif t == "use_list":
            for c in n.named_children:
                flatten(c, prefix)
        elif t == "use_wildcard":
            inner = n.named_children[0] if n.named_child_count else None
            out.append(("::".join(x for x in (prefix, _text(src, inner) if inner is not None else "") if x), "*", None))

    inline = []
    p = node.parent
    while p is not None:
        if p.type == "mod_item" and p.child_by_field_name("body") is not None:
            nm = p.child_by_field_name("name")
            if nm is not None:
                inline.append(_text(src, nm))
        p = p.parent
    inline.reverse()
    arg = node.child_by_field_name("argument")
    if arg is not None:
        flatten(arg, "")
    rows = []
    for mod, nm, alias in out:
        if nm == "self":
            key = _rs_abs(mod, path, ctx, inline)
            rows.append((mod, None, alias, alias or mod.rsplit("::", 1)[-1], line, key, None))
            continue
        key = _rs_abs(mod, path, ctx, inline)
        sub = f"{key}::{nm}" if nm != "*" else None
        rows.append((mod, nm, alias, (alias or nm) if nm != "*" else None, line, key, sub))
    return rows


IMPORT_FNS = {
    "python": _imports_python,
    "javascript": _imports_js,
    "typescript": _imports_js,
    "tsx": _imports_js,
    "go": _imports_go,
    "rust": _imports_rust,
}


def module_keys(path: str, lang: str, ctx: dict | None = None) -> list[tuple[str, int]]:
    """Keys under which other files can import `path`, with number of dropped prefix components."""
    if lang == "python":
        parts = path.rsplit(".", 1)[0].split("/")
        if parts[-1] == "__init__":
            parts = parts[:-1]
        return [(".".join(parts[i:]), i) for i in range(len(parts))] if parts else []
    if lang in ("javascript", "typescript", "tsx"):
        base = _strip_js_ext(path)
        keys = [(base, 0)]
        if posixpath.basename(base) == "index":
            keys.append((posixpath.dirname(base), 0))
        return keys
    if lang == "go":
        return [(posixpath.dirname(path), 0)]
    if lang == "rust":
        return [("::".join(_rs_modpath(path, ctx)), 0)]
    return []


# ---------------------------------------------------------------- the walker


def extract(path: str, lang: str, src: bytes, ctx: dict | None = None) -> dict:
    """Parse one file. Returns dict of row lists: symbols, refs, imports, modules, parse_errors."""
    ctx = ctx or {}
    spec = SPECS[lang]
    family = FAMILY[lang]
    tree = get_parser(lang).parse(src)
    symbols, refs, imports = [], [], []
    skip: set[int] = set()
    scopes: list[str] = []  # qualnames
    classes: list[str | None] = []  # innermost class-like qualname at each scope level
    frames: list[tuple] = []  # (type, ctx, receiver, pops_scope)
    errors = 0
    import_fn = IMPORT_FNS[lang]
    idents, calls, attrs, sticky = spec.idents, spec.calls, spec.attrs, spec.sticky
    field_ctx, resets, write_through = spec.field_ctx, spec.resets, spec.write_through
    defs_or_hooks = set(spec.defs) | {
        "assignment",
        "variable_declarator",
        "public_field_definition",
        "field_definition",
        "const_spec",
        "var_spec",
    }

    cur = tree.walk()

    def enter(node, fld):
        nonlocal errors
        t = node.type
        if frames:
            ptype, pctx, precv, _ = frames[-1]
        else:
            ptype, pctx, precv = None, "normal", None
        # ---- context for this node
        key = (ptype, fld)
        if key in resets:
            c = "normal"
        elif key in field_ctx:
            c = field_ctx[key]
        elif fld is not None and ("*", fld) in field_ctx and pctx != "import":
            c = field_ctx[("*", fld)]
        elif ptype in sticky:
            c = sticky[ptype]
        elif ptype in attrs:
            obj_f, mem_f = attrs[ptype]
            if pctx in STICKY:
                c = pctx
            elif fld == mem_f:
                c = "attr_call" if pctx == "callee" else "attr_write" if pctx == "write" else "attr"
            else:
                c = "normal"
        elif ptype in calls and fld == calls[ptype]:
            c = pctx if pctx in STICKY else "callee"
        elif pctx in STICKY:
            c = pctx
        elif pctx == "write" and ptype in write_through:
            c = "write"
        else:
            c = "normal"

        recv = None
        if t in attrs:
            obj = node.child_by_field_name(attrs[t][0])
            if obj is not None:
                recv = _one_line(_text(src, obj), 80)

        pops = False
        if t == "ERROR" or node.is_missing:
            errors += 1
        elif t in idents:
            if node.start_byte not in skip and c != "param":
                kind = CTX_KIND[c]
                if kind == "name" and t in spec.type_idents:
                    kind = "type"
                r = precv if (ptype in attrs and fld == attrs[ptype][1]) else None
                refs.append(
                    (
                        path,
                        lang,
                        _text(src, node),
                        kind,
                        r,
                        node.start_point[0] + 1,
                        node.start_point[1],
                        scopes[-1] if scopes else "",
                        classes[-1] if classes else None,
                    )
                )
        elif t in defs_or_hooks:
            targets = _def_targets(node, t, spec, lang, src, not scopes)
            for name_node, kind, push, parent_override, body in targets:
                if name_node is None:
                    continue
                name = _text(src, name_node)
                skip.add(name_node.start_byte)
                parent = parent_override or (scopes[-1] if scopes else None)
                if kind == "function" and classes and classes[-1] is not None and scopes and classes[-1] == scopes[-1]:
                    kind = "method"
                qual = f"{parent}.{name}" if parent else name
                start = node
                if node.parent is not None and node.parent.type == "decorated_definition":
                    start = node.parent
                returns = None
                if lang == "python" and t == "function_definition":
                    rt = node.child_by_field_name("return_type")
                    if rt is not None:
                        cls_q = classes[-1] if classes else None
                        returns = normalize_type(_text(src, rt), cls_q.rsplit(".", 1)[-1] if cls_q else None)
                symbols.append(
                    (
                        path,
                        lang,
                        name,
                        qual,
                        kind,
                        parent,
                        start.start_point[0] + 1,
                        node.end_point[0] + 1,
                        _signature(node, body, src, lang),
                        _docstring(node, body, spec, lang, src),
                        _exported(node, name, kind, lang),
                        returns,
                    )
                )
                if push and not pops:
                    scopes.append(qual)
                    classes.append(qual if kind in CLASS_KINDS else (classes[-1] if classes else None))
                    pops = True
        elif t in spec.scope_only:
            tn = node.child_by_field_name(spec.scope_only[t])
            if tn is not None:
                if tn.type in ("generic_type", "scoped_type_identifier"):
                    inner = tn.child_by_field_name("type") or tn.child_by_field_name("name")
                    tn = inner or tn
                qual = _text(src, tn)
                scopes.append(qual)
                classes.append(qual)
                pops = True
        if t in spec.import_nodes:
            for m, nm, alias, local, line, k, sub in import_fn(node, src, path, ctx):
                imports.append((path, family, m, nm, alias, local, line, k, sub))
        frames.append((t, c, recv, pops))

    def leave():
        if frames.pop()[3]:
            scopes.pop()
            classes.pop()

    enter(cur.node, None)
    while True:
        if cur.goto_first_child():
            enter(cur.node, cur.field_name)
            continue
        leave()
        while not cur.goto_next_sibling():
            if not cur.goto_parent():
                break
            leave()
        else:
            enter(cur.node, cur.field_name)
            continue
        break

    if errors == 0 and tree.root_node.has_error:
        errors = 1
    mods = [(path, family, k, d) for k, d in module_keys(path, lang, ctx)]
    binds = extract_bindings(tree.root_node, src, path) if lang == "python" else []
    return {
        "symbols": symbols,
        "refs": refs,
        "imports": imports,
        "modules": mods,
        "bindings": binds,
        "parse_errors": errors,
    }
