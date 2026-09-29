"""Names that belong to a language or its standard library, not to the repo.

Without types, `d.get()` would link to the repo's only `get` method and `Date.now()` to some class's
`now`. Calls on these names resolve through scope and imports, or not at all.
"""

# methods of builtin types (a receiver of unknown type calling one of these is probably a builtin)
_PY_METHODS = """
add append as_integer_ratio bit_count bit_length capitalize casefold center clear conjugate copy count critical
debug decode difference difference_update discard encode endswith error exception expandtabs extend find format
format_map from_bytes fromhex fromkeys get hex index info insert intersection intersection_update is_integer
isalnum isalpha isascii isdecimal isdigit isdisjoint isidentifier islower isnumeric isprintable isspace issubset
issuperset istitle isupper items join keys ljust lower lstrip maketrans partition pop popitem remove removeprefix
removesuffix replace reverse rfind rindex rjust rpartition rsplit rstrip setdefault sort split splitlines
startswith strip swapcase symmetric_difference symmetric_difference_update title to_bytes translate union
update upper values warn warning zfill
"""
_JS_METHODS = """
add apply at bind call catch charAt charCodeAt clear codePointAt concat debug delete endsWith entries error every
exec fill filter finally find findIndex findLast findLastIndex flat flatMap forEach get getTime has
hasOwnProperty includes indexOf info join json keys lastIndexOf localeCompare log map match matchAll normalize
padEnd padStart pop push reduce reduceRight repeat replace replaceAll reverse search set shift slice some sort
splice split startsWith substring test text then toFixed toISOString toLowerCase toString toUpperCase trim
trimEnd trimStart unshift valueOf values warn
"""
_RS_METHODS = """
all and_then any as_bytes as_deref as_mut as_ptr as_ref as_slice as_str binary_search borrow borrow_mut bytes
capacity chain chars chunks clone cloned cmp collect contains contains_key copied count dedup drain ends_with
entry enumerate eq expect extend extend_from_slice filter filter_map find first flat_map flatten fmt fold
for_each from get get_mut get_or_insert_with hash insert into into_iter is_empty is_err is_none is_ok is_some
iter iter_mut join keys last len lines lock map map_err max min next ok ok_or ok_or_else or_default or_insert
or_insert_with parse partial_cmp pop position push push_str read recv remove replace retain rev send skip sort
sort_by sort_by_key sort_unstable split split_off starts_with step_by sum take to_lowercase to_owned to_string
to_uppercase to_vec trim truncate try_from try_into unwrap unwrap_or unwrap_or_default unwrap_or_else values
windows with_capacity write zip
"""

# receivers that are builtins or globals when the file doesn't define or import the name itself
_PY_GLOBALS = "bool bytearray bytes complex dict float frozenset int list object set str tuple type"
_JS_GLOBALS = """
Array ArrayBuffer Atomics BigInt Boolean Buffer DataView Date Error Float32Array Float64Array Int16Array
Int32Array Int8Array Intl JSON Map Math Number Object Promise Proxy RangeError Reflect RegExp Set String Symbol
TypeError URL URLSearchParams Uint16Array Uint32Array Uint8Array WeakMap WeakRef WeakSet console crypto document
globalThis localStorage location navigator performance process sessionStorage window
"""
_RS_GLOBALS = """
Arc BTreeMap BTreeSet BinaryHeap Box Cell Cow Duration Err HashMap HashSet Instant Mutex None Ok Option Path
PathBuf PhantomData Rc RefCell Result RwLock Some String SystemTime Vec VecDeque alloc bool char core f32 f64
i128 i16 i32 i64 i8 isize std str u128 u16 u32 u64 u8 usize
"""


def _pairs(family: str, words: str) -> frozenset[tuple[str, str]]:
    return frozenset((family, w) for w in words.split())


METHODS = _pairs("py", _PY_METHODS) | _pairs("js", _JS_METHODS) | _pairs("rs", _RS_METHODS)
GLOBALS = _pairs("py", _PY_GLOBALS) | _pairs("js", _JS_GLOBALS) | _pairs("rs", _RS_GLOBALS)
