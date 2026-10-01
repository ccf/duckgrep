import difflib
import re

import pytest

from bench.eval import gold

BASE_PY = """import os
from .util import (
    a,
    b)

X = 1


class Session:
    retries = 3

    def request(self, url):
        def inner():
            return url
        return inner()

    @property
    def closed(self):
        return False


def helper(x):
    y = x + 1
    return y
"""

BASE_RS = """use std::fmt;

pub struct Walker<T> {
    items: Vec<T>,
}

impl<T> Walker<T> {
    pub fn new() -> Self {
        Walker { items: Vec::new() }
    }
}

impl<T> fmt::Display for Walker<T> {
    fn fmt(&self, f: &mut fmt::Formatter) -> fmt::Result {
        write!(f, "walker")
    }
}

pub fn run() -> u32 {
    1
}

#[cfg(test)]
mod tests {
    #[test]
    fn it_works() {
        assert_eq!(super::run(), 1);
    }
}
"""


def patch(path, before, after, shift=0):
    """A unified diff like SWE-bench's; `shift` moves every hunk header, as stale line numbers do."""
    lines = difflib.unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True), f"a/{path}", f"b/{path}"
    )
    text = f"diff --git a/{path} b/{path}\n" + "".join(lines)
    if shift:
        text = re.sub(r"@@ -(\d+)", lambda m: f"@@ -{int(m.group(1)) + shift}", text)
    return text


def key(path, before, after, lang="python", shift=0):
    return gold.derive(patch(path, before, after, shift), {path: before}.get, lang).entries


def test_nested_function_rolls_up():
    assert key("pkg/s.py", BASE_PY, BASE_PY.replace("return url", "return url.strip()")) == (
        "pkg/s.py:Session.request",
    )


def test_class_level_edit_is_a_file_only_key():
    assert key("pkg/s.py", BASE_PY, BASE_PY.replace("retries = 3", "retries = 4")) == ("pkg/s.py",)


def test_a_function_key_makes_the_file_only_key_redundant():
    after = BASE_PY.replace("retries = 3", "retries = 4").replace("return False", "return True")
    assert key("pkg/s.py", BASE_PY, after) == ("pkg/s.py:Session.closed",)


def test_imports_even_multiline_and_comments_are_ignored():
    after = BASE_PY.replace("    b)", "    b, c)").replace("import os", "import os  # os\nimport re")
    assert key("pkg/s.py", BASE_PY, after) == ()


def test_insertion_at_the_end_of_a_body_belongs_to_the_function():
    assert key("pkg/s.py", BASE_PY, BASE_PY.replace("    return y\n", "    return y\n    print(y)\n")) == (
        "pkg/s.py:helper",
    )


def test_new_top_level_function_is_a_file_only_key():
    after = BASE_PY + "\n\ndef added():\n    return 1\n"
    assert key("pkg/s.py", BASE_PY, after) == ("pkg/s.py",)


def test_decorator_edit_belongs_to_the_function():
    after = BASE_PY.replace("@property", "@functools.cached_property")
    assert key("pkg/s.py", BASE_PY, after) == ("pkg/s.py:Session.closed",)


def test_stale_hunk_line_numbers_are_located_by_context():
    after = BASE_PY.replace("y = x + 1", "y = x + 2")
    assert key("pkg/s.py", BASE_PY, after, shift=4) == key("pkg/s.py", BASE_PY, after) == ("pkg/s.py:helper",)


def test_a_hunk_that_does_not_apply_is_an_error():
    with pytest.raises(ValueError):
        gold.derive(
            patch("pkg/s.py", BASE_PY, BASE_PY.replace("X = 1", "X = 2")), {"pkg/s.py": "other\n"}.get, "python"
        )


def test_test_files_are_ignored_and_new_files_are_file_only_keys():
    diff = patch("tests/test_s.py", "def test_a():\n    pass\n", "def test_a():\n    assert 1\n")
    diff += "diff --git a/pkg/new.py b/pkg/new.py\nnew file mode 100644\n--- /dev/null\n+++ b/pkg/new.py\n@@ -0,0 +1 @@\n+x = 1\n"
    k = gold.derive(diff, {}.get, "python")
    assert k.entries == ("pkg/new.py",) and k.files == ("pkg/new.py",)


def test_rust_impl_and_trait_methods_are_type_dot_method():
    after = BASE_RS.replace("Vec::new()", "Vec::with_capacity(4)").replace('"walker"', '"w"')
    assert key("src/walk.rs", BASE_RS, after, "rust") == ("src/walk.rs:Walker.fmt", "src/walk.rs:Walker.new")


def test_rust_test_code_and_use_lines_are_ignored():
    after = BASE_RS.replace("super::run(), 1", "super::run(), 2").replace("use std::fmt;", "use std::fmt::{self};")
    assert key("src/walk.rs", BASE_RS, after, "rust") == ()


def test_rust_insertion_after_a_closing_brace_is_outside_the_function():
    after = BASE_RS.replace("    1\n}\n", "    1\n}\n\npub fn added() {}\n")
    assert key("src/walk.rs", BASE_RS, after, "rust") == ("src/walk.rs",)
    assert key("src/walk.rs", BASE_RS, BASE_RS.replace("    1\n}", "    let x = 1;\n    x\n}"), "rust") == (
        "src/walk.rs:run",
    )


def test_a_visibility_only_edit_is_not_the_fix():
    # pixi-6335: `pub(crate) fn invalid()` -> `pub fn invalid()` came with an unrelated feature, not the fix
    widened = BASE_RS.replace("pub fn new()", "pub(crate) fn new()")
    assert key("src/walk.rs", widened, BASE_RS, "rust") == ()
    assert key("src/walk.rs", BASE_RS, BASE_RS.replace("pub fn run()", "fn run()"), "rust") == ()
    both = BASE_RS.replace("pub fn run()", "pub(super) fn run()").replace("    1\n}", "    2\n}")
    assert key("src/walk.rs", BASE_RS, both, "rust") == ("src/walk.rs:run",)  # its body changed too
    typed = BASE_RS.replace("pub fn run() -> u32", "pub(crate) fn run() -> u64")
    assert key("src/walk.rs", BASE_RS, typed, "rust") == ("src/walk.rs:run",)  # more than visibility


def test_only_a_real_visibility_modifier_is_exempt():
    fmt = ("src/walk.rs:Walker.fmt",)
    worded = BASE_RS.replace('"walker"', '"pub walker"')  # `pub` in a string is text the function writes
    assert key("src/walk.rs", worded, BASE_RS, "rust") == fmt
    spaced = BASE_RS.replace('"walker"', '"a  walker"')  # so is its spacing
    assert key("src/walk.rs", BASE_RS.replace('"walker"', '"a walker"'), spaced, "rust") == fmt
    quoted = "pub fn made() -> TokenStream {\n    quote! {\n        pub fn f() {}\n    }\n}\n"  # a macro's tokens
    assert key("src/gen.rs", quoted, quoted.replace("pub fn f", "pub(crate) fn f"), "rust") == ("src/gen.rs:made",)


def test_a_visibility_change_does_not_hide_the_rest_of_its_block():
    before, after = "fn helper() -> u32 {\n\n    1\n}\n", "pub fn helper() -> u32 {\n    do_work();\n    1\n}\n"
    assert key("src/lib.rs", before, after, "rust") == ("src/lib.rs:helper",)


def test_rust_units_mark_test_code():
    kinds = {u.qualname: u.kind for u in gold.rust_units(BASE_RS)}
    assert kinds["Walker.new"] == "function" and kinds["run"] == "function"
    assert kinds["it_works"] == "test-fn" and kinds["tests"] == "test-mod"


def test_listed_key_normalisation():
    read = {"pkg/s.py": BASE_PY}.get
    assert gold.normalize_listed(["pkg/s.py:Session", "pkg/s.py:Session.request.inner"], read) == (
        "pkg/s.py:Session.request",
    )
    assert gold.normalize_listed(["pkg/s.py:Session"], read) == ("pkg/s.py",)
    with pytest.raises(ValueError):
        gold.normalize_listed(["pkg/s.py:missing"], read)


def test_same_key_treats_a_class_as_its_init():
    assert gold.same_key(("pkg/s.py:Session.__init__",), ("pkg/s.py",))
    assert gold.same_key(
        ("pkg/s.py:Session.__init__", "pkg/s.py:Session.request"), ("pkg/s.py", "pkg/s.py:Session.request")
    )
    assert not gold.same_key(("pkg/s.py:Session.request",), ("pkg/s.py:Session.closed",))
