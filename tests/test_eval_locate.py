"""Turns-to-locate on tool output shaped like the pilot's: a run locates a function when a result shows its
definition, not when a call, a docstring or a same-named token mentions it."""

import json
import os

from bench.eval import locate, stream
from bench.eval.locate import Target

RUNS = os.path.join(os.path.dirname(__file__), "eval_runs")


def run(*calls):
    """A transcript of (tool, input, result) calls, one round each."""
    return stream.Transcript(calls=[stream.Call(str(i), n, i, inp, res) for i, (n, inp, res) in enumerate(calls, 1)])


def recorded(name):
    with open(os.path.join(RUNS, f"{name}.jsonl")) as f:
        return stream.read(f)


NEEDLE = Target("src/a.py", "needle_fn", ((4, 4),))  # tests/eval_runs: needle_fn is defined on line 4


def test_recorded_runs():
    assert locate.turns_to_locate(recorded("plain"), [NEEDLE]) == 1  # Grep shows src/a.py:4:def needle_fn()
    assert locate.turns_to_locate(recorded("mcp"), [NEEDLE]) == 3  # a file list first, then the read
    assert locate.turns_to_locate(recorded("mcp"), [Target("src/a.py")]) == 2  # a file key needs the path alone
    assert locate.turns_to_locate(recorded("plain"), [Target("src/a.py", "Other.needle")]) is None
    assert locate.turns_to_locate(recorded("auth_failure"), [Target("src/a.py")]) is None


def test_a_call_to_the_function_does_not_locate_it():
    process = Target("src/walk.rs", "ReceiverBuffer.process", ((174, 174),))
    tr = run(
        ("Grep", {"pattern": "ReceiverBuffer", "output_mode": "content"},
         "src/walk.rs:151:impl<'a, W: Write> ReceiverBuffer<'a, W> {\n"
         "src/walk.rs:443:            ReceiverBuffer::new(self, rx, stdout).process()"),
        ("Read", {"file_path": "/wt/src/walk.rs", "offset": 172, "limit": 3},
         "172\t\n173\t    /// Process results until finished.\n174\t    fn process(&mut self) -> ExitCode {"),
    )  # fmt: skip
    assert locate.turns_to_locate(tr, [process]) == 2


def test_a_docstring_or_a_same_named_token_does_not_locate_it():
    prepare = Target("src/requests/models.py", "Request.prepare", ((295, 295),))
    send = Target("requests/adapters.py", "HTTPAdapter.send", ((324, 324),))
    docstring = ("Grep", {"pattern": "\\.prepare\\(", "output_mode": "content"},
                 "src/requests/models.py:254:      >>> req.prepare()")  # fmt: skip
    body = ("Read", {"file_path": "/wt/requests/adapters.py", "offset": 395, "limit": 2},
            "395\t                        low_conn.send(b'\\r\\n')\n396\t                    r = low_conn.getresponse()")  # fmt: skip
    assert locate.turns_to_locate(run(docstring, body), [prepare, send]) is None


def test_a_read_that_shows_the_definition_line_locates_it():
    send = Target("requests/adapters.py", "HTTPAdapter.send", ((324, 324),))
    read = ("Read", {"file_path": "/wt/requests/adapters.py", "offset": 320, "limit": 5},
            "323\t\n324\t    def send(self, request, stream=False, timeout=None):")  # fmt: skip
    assert locate.turns_to_locate(run(read), [send]) == 1


def test_a_decorator_or_attribute_line_alone_does_not_locate_it():
    cached = Target("pkg/m.py", "A.f", ((11, 11),))  # @property on line 10, def f on line 11
    assert locate.turns_to_locate(run(("Grep", {"pattern": "property"}, "pkg/m.py:10:    @property")), [cached]) is None
    assert locate.turns_to_locate(run(("Grep", {"pattern": "def f"}, "pkg/m.py:11:    def f(self):")), [cached]) == 1


def test_duckgrep_rows_locate_by_qualified_name_despite_escaped_tabs():
    colorized = Target("src/output.rs", "print_entry_colorized", ((83, 83),))
    tsv = "src_path\tline\tcaller\tref_kind\treceiver\tresolution\ttargets\nsrc/output.rs\t106\tprint_entry_colorized\tcall\t\tlocal\t1"
    call = (
        "mcp__duckgrep__query",
        {"sql": "SELECT * FROM callers('replace_path_separator')"},
        json.dumps({"result": tsv}),
    )
    assert locate.turns_to_locate(run(call), [colorized]) == 1
    other = Target("src/output.rs", "print_entry", ((16, 16),))  # a prefix of the caller's name is not a match
    assert locate.turns_to_locate(run(call), [other]) is None


def test_duckgrep_definitions_and_the_target_side_of_edges():
    main = Target("src/requests/help.py", "main", ((128, 128),))
    defs = "path\tstart_line\tend_line\tkind\tqualname\tsignature\tdoc\nsrc/requests/help.py\t128\t133\tfunction\tmain\t()\t"
    assert locate.turns_to_locate(run(("mcp__duckgrep__query", {"sql": "SELECT * FROM defs('main')"},
                                       json.dumps({"result": defs}))), [main]) == 1  # fmt: skip
    edges = "src_path\tsrc_scope\tline\tdst_path\tdst_qualname\tdst_line\nsrc/x.py\tcaller\t9\tsrc/requests/help.py\tmain\t128"
    assert locate.turns_to_locate(run(("mcp__duckgrep__query", {"sql": "SELECT * FROM edges"},
                                       json.dumps({"result": edges}))), [main]) == 1  # fmt: skip
    # the source side of an edge is another file: its line 128 is not main's
    swapped = "src_path\tsrc_scope\tline\tdst_path\tdst_qualname\nsrc/requests/help.py\tinfo\t128\tsrc/x.py\tf"
    assert locate.turns_to_locate(run(("mcp__duckgrep__query", {"sql": "SELECT * FROM edges"},
                                       json.dumps({"result": swapped}))), [Target("src/x.py", "g", ((128, 128),))]) is None  # fmt: skip


def test_duckgrep_source_of_a_named_method_locates_it():
    send = Target("requests/adapters.py", "HTTPAdapter.send", ((324, 324),))
    tsv = "file\tline\ttext\n\t324\t    def send(self, request, stream=False):⏎        conn = self.get_connection()"
    call = ("mcp__duckgrep__query", {"sql": "SELECT * FROM source('HTTPAdapter.send')"}, json.dumps({"result": tsv}))
    assert locate.turns_to_locate(run(call), [send]) == 1


def test_serena_name_paths_locate_impl_and_trait_methods():
    names = Target("compiler-core/src/erlang.rs", "FunctionGenerator.function_arguments_names", ((710, 710),))
    fmt = Target("crates/ignore/src/walk.rs", "WalkBuilder.fmt", ((506, 506),))
    refs = {
        "compiler-core/src/erlang.rs": {"Method": [{
            "name_path": "impl<'a, 'generator> FunctionGenerator<'a, 'generator>/function_arguments_names",
            "body_location": {"start_line": 709, "end_line": 740},
            "content_around_reference": "...  712:        let x = 1;\n  > 713:        y(x)",
        }]},
        "crates/ignore/src/walk.rs": {"Method": [{"name_path": "impl std::fmt::Debug for WalkBuilder/fmt"}]},
    }  # fmt: skip
    call = ("mcp__serena__find_referencing_symbols", {"name_path": "y", "relative_path": "x.rs"}, json.dumps(refs))
    assert locate.turns_to_locate(run(call), [names]) == 1
    assert locate.turns_to_locate(run(call), [fmt]) == 1


def test_serena_symbol_locations_are_zero_based():
    before = Target("src/filter/time.rs", "TimeFilter.before", ((42, 42),))
    found = [{"name_path": "impl TimeFilter/other", "kind": "Function", "relative_path": "src/filter/time.rs",
              "body_location": {"start_line": 41, "end_line": 43}}]  # fmt: skip
    assert locate.turns_to_locate(run(("mcp__serena__find_symbol", {}, json.dumps(found))), [before]) == 1


def test_bash_without_line_numbers_locates_by_the_definition_line():
    fmt_sig = Target("sphinx/ext/autodoc/__init__.py", "ClassDocumenter.format_signature", ((1469, 1469),))
    cmd = "cd sphinx/ext/autodoc; sed -n 1225,1275p __init__.py; sed -n 1465,1495p __init__.py"
    out = "    def format_args(self, **kwargs: Any) -> str:\n        pass\n    def format_signature(self, **kwargs: Any) -> str:"
    assert locate.turns_to_locate(run(("Bash", {"command": cmd}, out)), [fmt_sig]) == 1
    # the same definition in a file of the same name elsewhere does not count
    elsewhere = "cd sphinx/domains; cat __init__.py"
    assert locate.turns_to_locate(run(("Bash", {"command": elsewhere}, out)), [fmt_sig]) is None


def test_bash_numbered_lines_of_one_file_and_sed_ranges():
    multi = Target("crates/printer/src/standard.rs", "StandardImpl.sink_slow_multi_line", ((1095, 1095),))
    grep_n = ("Bash", {"command": 'grep -n "fn " /wt/crates/printer/src/standard.rs'},
              "1060:    fn sink_slow(&self) -> io::Result<()> {\n1095:    fn sink_slow_multi_line(&self) -> io::Result<()> {")  # fmt: skip
    assert locate.turns_to_locate(run(grep_n), [multi]) == 1
    sed = ("Bash", {"command": "sed -n 1090,1100p crates/printer/src/standard.rs"}, "    }\n\n    fn x() {}")
    assert locate.turns_to_locate(run(sed), [multi]) == 1  # the range covers line 1095, whatever it printed


def test_a_definition_in_another_file_does_not_locate():
    needle = Target("src/a.py", "needle_fn", ((4, 4),))
    other = ("Bash", {"command": "cat src/data.py"}, "def needle_fn():\n    return 2")
    assert locate.turns_to_locate(run(other), [needle]) is None


def test_targets_find_each_definition_at_the_commit():
    py = "class A:\n    @property\n    def f(self):\n        return 1\n\n\ndef g():\n    pass\n"
    rs = "struct Foo;\nimpl Foo {\n    #[inline]\n    pub fn bar(&self) {}\n}\n"
    sources = {"m.py": py, "lib.rs": rs}
    got = locate.targets(["m.py:A.f", "m.py:g", "m.py:A", "m.py", "m.py:missing", "lib.rs:Foo.bar", "gone.py:x"],
                         sources.get)  # fmt: skip
    assert got == [  # the def/fn/class line; decorators and attributes above it do not count
        Target("m.py", "A.f", ((3, 3),)),
        Target("m.py", "g", ((7, 7),)),
        Target("m.py", "A", ((1, 1),), "class"),
        Target("m.py"),
        Target("m.py", "missing"),
        Target("lib.rs", "Foo.bar", ((4, 4),)),
        Target("gone.py", "x"),
    ]


def test_a_class_key_is_located_by_its_class_line():
    cls = Target("pkg/m.py", "Config", ((12, 12),), "class")
    out = "class Config(Base):\n    x = 1"
    assert locate.turns_to_locate(run(("Bash", {"command": "cat pkg/m.py"}, out)), [cls]) == 1


def test_an_inner_line_number_is_the_line_awk_printed():
    # awk printing the line it was asked about, then the enclosing def's own number and text
    tuples = Target("tests/test_requests.py", "test_data_argument_accepts_tuples", ((2605, 2605),))
    cmd = 'cd tests; for l in 2610 2550; do awk -v l=$l \'NR<=l && /def /{s=NR": "$0} NR==l{print l": "s}\' test_requests.py; done'
    out = "2610: 2605: def test_data_argument_accepts_tuples(data):\n2550: 2546: def test_json_encodes_as_bytes():"
    assert locate.turns_to_locate(run(("Bash", {"command": cmd}, out)), [tuples]) == 1


def test_a_same_named_method_of_another_type_is_not_the_function():
    test_fn = Target("crates/ignore/src/walk.rs", "max_depth", ((1900, 1900),))  # a test fn inside mod tests
    rows = "path\tqualname\tstart_line\ncrates/ignore/src/walk.rs\tWalkBuilder.max_depth\t800"
    call = ("mcp__duckgrep__query", {"sql": "SELECT * FROM defs('max_depth')"}, json.dumps({"result": rows}))
    assert locate.turns_to_locate(run(call), [test_fn]) is None
    serena = [{"name_path": "tests/max_depth", "relative_path": "crates/ignore/src/walk.rs"}]  # a module prefix is fine
    assert locate.turns_to_locate(run(("mcp__serena__find_symbol", {}, json.dumps(serena))), [test_fn]) == 1


def test_the_working_directory_carries_across_bash_calls_and_paths_are_normalised():
    tuples = Target("tests/test_requests.py", "test_data_argument_accepts_tuples", ((2605, 2605),))
    first = ("Bash", {"command": "cd tests; ls"}, "test_requests.py")
    again = ("Bash", {"command": "cd ../tests && grep -n 'def test_data' ./test_requests.py"},
             "2605:def test_data_argument_accepts_tuples(data):")  # fmt: skip
    assert locate.turns_to_locate(run(first, again), [tuples]) == 2
    haystack = Target("crates/core/haystack.rs", "Haystack.is_explicit", ((131, 131),))
    up = ("Bash", {"command": "cd crates/ignore/src; grep -n 'fn is_explicit' ../../core/haystack.rs"},
          "131:    pub(crate) fn is_explicit(&self) -> bool {")  # fmt: skip
    assert locate.turns_to_locate(run(up), [haystack]) == 1


def test_a_deeper_file_with_the_same_ending_is_another_file():
    conftest = Target("conftest.py", "fixture", ((5, 5),))
    shown = ("Grep", {"pattern": "def fixture"}, "testing/conftest.py:5:def fixture():")
    assert locate.turns_to_locate(run(shown), [conftest]) is None
    root = ("Read", {"file_path": "/wt/conftest.py", "offset": 4, "limit": 2}, "5\tdef fixture():")
    assert locate.turns_to_locate(run(root), [conftest], roots=("/wt",)) == 1


def test_output_without_line_numbers_counts_only_for_a_file_the_call_names():
    helper = Target("pkg/a/utils.py", "helper", ((3, 3),))
    other = ("Bash", {"command": "cat pkg/b/utils.py"}, "def helper(x):\n    return x")
    assert locate.turns_to_locate(run(other), [helper]) is None
    this = ("Bash", {"command": "cd pkg/a; cat utils.py"}, "def helper(x):\n    return x")
    assert locate.turns_to_locate(run(this), [helper]) == 1


def test_a_glob_is_not_a_file():
    # a glob beside one named file: its numbered lines may come from any of them, so they are not pinned to it
    other = Target("src/a.py", "other", ((4, 4),))
    both = ("Bash", {"command": "awk '{print NR\": \"$0}' src/a.py src/*.py"}, "4: x = 1")
    ev, _ = locate.evidence(stream.Call("1", *both[:1], 1, both[1], both[2]))
    assert ev.files == ["src/a.py"] and ev.pairs == []
    assert locate.turns_to_locate(run(both), [other]) is None


def test_grep_output_follows_the_shells_directory():
    skip = Target("crates/ignore/src/walk.rs", "Walk.skip_entry", ((932, 932),))
    cd = ("Bash", {"command": "cd crates/ignore/src"}, "")
    grep = ("Grep", {"pattern": "fn skip_entry", "path": "/wt", "output_mode": "content"},
            "walk.rs:932:    fn skip_entry(&self, ent: &DirEntry) -> Result<bool, Error> {")  # fmt: skip
    assert locate.turns_to_locate(run(cd, grep), [skip], roots=("/wt",)) == 2
    dot = ("Bash", {"command": "cd crates/ignore/src; grep -rn 'fn skip_entry' ."}, "./walk.rs:932:    fn skip_entry(")
    assert locate.turns_to_locate(run(dot), [skip]) == 1


def test_duckgrep_rows_listing_only_paths_name_a_file():
    importer = Target("src/b.py")
    rows = json.dumps({"result": "path\nsrc/a.py\nsrc/b.py"})
    call = ("mcp__duckgrep__query", {"sql": "SELECT path FROM imports_resolved WHERE target_path = 'm.py'"}, rows)
    assert locate.turns_to_locate(run(call), [importer]) == 1
    assert locate.turns_to_locate(run(call), [Target("src/c.py")]) is None


def test_a_sed_range_counts_only_when_sed_reads_the_file():
    before = Target("src/filter/time.rs", "TimeFilter.before", ((30, 30),))
    piped = ("Bash", {"command": 'grep -n "^impl" src/filter/time.rs | sed -n 1,40p'}, "12:impl TimeFilter {")
    assert locate.turns_to_locate(run(piped), [before]) is None
    read = ("Bash", {"command": "sed -n 25,35p src/filter/time.rs"}, "    pub fn before(ref_time: &SystemTime) {")
    assert locate.turns_to_locate(run(read), [before]) == 1


def test_where_a_call_numbers_the_gold_file_its_numbers_decide():
    # two ranges of walk.rs that show another type's `fn next(`: the def line of Walk.next is not among them
    walk_next = Target("crates/ignore/src/walk.rs", "Walk.next", ((973, 973),))
    cmd = "sed -n 1060,1080p crates/ignore/src/walk.rs"
    out = "impl Iterator for WalkEventIter {\n    fn next(&mut self) -> Option<walkdir::Result<WalkEvent>> {"
    assert locate.turns_to_locate(run(("Bash", {"command": cmd}, out)), [walk_next]) is None
