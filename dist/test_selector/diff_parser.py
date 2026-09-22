"""diff 解析与变更场景排除（独立模块）。

将统一 diff 文本解析为受影响的 base（变更前）行号集合，并排除不构成
代码变更的场景：
- 纯注释/docstring 变更
- 纯类型注解变更（插入/替换/删除三个方向，可证明惰性时豁免）
- 函数/类定义之间的空行插入
- 新增函数/类整体

实现要点：
- 纯插入分支带 base_no<1 兜底（新文件不分类）
- '+++ /dev/null' 新文件跳过行级解析
- pending 候选对元组为 8 元素（含 add_texts，供注解豁免增强使用）
"""

import ast
import textwrap

import regex as re

_HUNK_RE = re.compile(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_DEF_RE = re.compile(r"(async\s+def|def|class)\s")


def _parse_diff_base_lines(
    diff_output: str,
) -> tuple[dict[str, set[int]], dict[str, list[tuple]], dict[str, list[tuple]]]:
    """Parse unified diff text into affected base (pre-change) line numbers.

    Returns (files, pending, del_groups):
      files[path]     : set of base line numbers recorded directly
                        (blank lines inside contiguous deletion blocks)
      pending[path]   : candidate pairs needing base-content classification;
                        tuple = (a, b, kind, add_indent, introduces_def,
                                 adds_all_comment, hunk_base_end, add_texts)
                        kind='insert' -> pure insertion between a and b
                        kind='blank'  -> isolated blank deletion at a+1 (b = a+2)
      del_groups[path]: deletion groups needing comment/docstring filtering;
                        tuple = ([(base_line, deleted_text), ...], [added_text, ...])
    """
    files, pending, del_groups = {}, {}, {}
    current = None
    base_no = None
    hunk_base_end = 0
    old_path = None  # path from the last '--- a/...' line (used for deleted files)
    group_del = []  # (base_line, text) of '-' lines in the current change group
    group_add = []  # texts of '+' lines in the current change group

    def flush_group():
        if not group_del and not group_add:
            return
        if group_del:
            del_set = {n for n, _ in group_del}
            del_lines = []
            for n, text in group_del:
                if text.strip():
                    del_lines.append((n, text))
                elif (n - 1) in del_set or (n + 1) in del_set:
                    # blank inside a contiguous deletion block: classify with
                    # the group (dropped too if the block is pure comment/docstring)
                    del_lines.append((n, text))
                else:
                    pending[current].append((n - 1, n + 1, "blank", None, False, False, hunk_base_end, None))
            if del_lines:
                del_groups[current].append((del_lines, list(group_add)))
        else:
            # base_no is the next unprocessed base line = the line below the insertion
            if base_no is None or base_no < 1:
                # New file (hunk '@@ -0,0 ...'): there is no base version at all,
                # so there is nothing to classify the insertion against. Skip the
                # pair so callers never attempt to fetch a base file.
                return
            a = base_no - 1
            indent = min(((len(t) - len(t.lstrip())) for t in group_add if t.strip()), default=0)
            introduces_def = any(t.strip().startswith("@") or _DEF_RE.match(t.strip()) for t in group_add if t.strip())
            adds_all_comment = all(t.strip().startswith("#") for t in group_add if t.strip())
            pending[current].append(
                (
                    a,
                    a + 1,
                    "insert",
                    indent,
                    introduces_def,
                    adds_all_comment,
                    hunk_base_end,
                    tuple(group_add),
                )
            )

    for raw_line in diff_output.split("\n"):
        line = raw_line.rstrip("\r")
        if line.startswith("diff --git"):
            flush_group()
            group_del, group_add = [], []
            current, base_no = None, None
            continue
        if line.startswith("--- "):
            old_path = line[4:]
            if old_path.startswith("a/"):
                old_path = old_path[2:]
            continue
        if line.startswith("+++ "):
            flush_group()
            group_del, group_add = [], []
            path = line[4:]
            if path == "/dev/null":
                # deleted file: keep the '--- a/...' path so deletions are recorded
                path = old_path
                old_path = None
                if path is None or path == "/dev/null":
                    current = None
                    continue
            if old_path == "/dev/null":
                # new file: no base version exists, base line numbers are
                # meaningless - skip line-level parsing entirely (also avoids
                # a doomed base-content fetch for the file)
                old_path = None
                current = None
                continue
            if path.startswith("b/"):
                path = path[2:]
            current = path
            files.setdefault(path, set())
            pending.setdefault(path, [])
            del_groups.setdefault(path, [])
            continue
        if line.startswith("@@"):
            flush_group()
            group_del, group_add = [], []
            if current is None:
                continue
            m = _HUNK_RE.search(line)
            base_no = int(m.group(1))
            hunk_base_end = base_no + int(m.group(2) or "1") - 1
            continue
        if current is None or base_no is None:
            continue
        if line.startswith("-"):
            group_del.append((base_no, line[1:]))
            base_no += 1
        elif line.startswith("+"):
            group_add.append(line[1:])
        elif line.startswith("\\"):
            continue
        else:
            flush_group()
            group_del, group_add = [], []
            base_no += 1
    flush_group()
    return files, pending, del_groups


def _collect_defs(source: str) -> tuple[list, set, set, set]:
    """Parse Python source, return (ranges, end_lines, start_lines, blanks).

    ranges      : [(lineno, end_lineno, col_offset)] of every function/method
    end_lines   : line numbers where a function/class definition ends
    start_lines : def/class lines and their decorator lines
    blanks      : blank line numbers
    """
    tree = ast.parse(source)
    ranges, end_lines, start_lines = [], set(), set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            end_lines.add(node.end_lineno)
            start_lines.add(node.lineno)
            for deco in node.decorator_list:
                start_lines.add(deco.lineno)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                ranges.append((node.lineno, node.end_lineno, node.col_offset))
    blanks = {i for i, text in enumerate(source.splitlines(), 1) if not text.strip()}
    return ranges, end_lines, start_lines, blanks


def _get_docstring_lines(source: str) -> set[int]:
    """Line numbers covered by docstrings (module/class/function docstring nodes)."""
    tree = ast.parse(source)
    lines = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if (
            isinstance(body, list)
            and body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            lines.update(range(body[0].lineno, body[0].end_lineno + 1))
    return lines


def _looks_like_code(texts: list) -> bool:
    """True if the added lines are real code: parseable as Python and not
    solely string-literal expressions (docstring prose)."""
    block = textwrap.dedent("\n".join(t for t in texts if t.strip()))
    if not block.strip():
        return False
    try:
        tree = ast.parse(block)
    except (SyntaxError, ValueError):
        return False
    return any(
        not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str))
        for stmt in tree.body
    )


def _innermost_func(ranges: list, n: int):
    """The innermost function whose body contains line n (None if module level)."""
    best = None
    for start, end, col in ranges:
        if start <= n <= end and (best is None or start >= best[0]):
            best = (start, end, col)
    return best


def _between_definitions(a: int, b: int, end_lines: set, start_lines: set, blanks: set) -> bool:
    """True if the pair (a, b) sits between two definitions: the upper line is
    the end of a function/class (if a itself is blank, walk up past consecutive
    blank lines and check the nearest non-blank line instead) and the lower
    line is the start of a function/class (def/class line or decorator)."""
    if b not in start_lines:
        return False
    upper = a
    while upper in blanks:
        upper -= 1
    return upper in end_lines


# ======================================================================
# 纯类型注解变更豁免（vllm 完整实现，并入公共 diff_parser）
# ======================================================================

# Base classes whose machinery reads class __annotations__ at runtime
_ANNOTATION_CONSUMING_BASES = frozenset({"TypedDict", "Protocol", "BaseModel", "Enum", "NamedTuple"})


def _parse_pure_annotations(texts) -> list[ast.AnnAssign] | None:
    """Parse diff lines as a block of pure type annotations.

    Returns the annotation statements when every meaningful (non-blank,
    non-comment) line forms simple-name annotations without a value
    (``x: int``); returns None when the block is unparsable or contains
    anything else. An empty list means the block has no meaningful lines.
    """
    lines = [t for t in texts if t.strip() and not t.strip().startswith("#")]
    if not lines:
        return []
    block = textwrap.dedent("\n".join(lines))
    try:
        tree = ast.parse(block)
    except (SyntaxError, ValueError):
        return None
    anns = []
    for stmt in tree.body:
        if not (isinstance(stmt, ast.AnnAssign) and stmt.value is None and isinstance(stmt.target, ast.Name)):
            return None
        anns.append(stmt)
    return anns


def _annotation_eval_safe(anns: list[ast.AnnAssign], has_future: bool) -> bool:
    """False when an annotation expression may have runtime side effects.

    With ``from __future__ import annotations`` expressions are stored as
    strings and never evaluated. Otherwise annotation expressions are
    evaluated at definition time, so calls, walrus assignments and
    ``Annotated[...]`` metadata are treated as unsafe.
    """
    if has_future:
        return True
    for ann in anns:
        for node in ast.walk(ann.annotation):
            if isinstance(node, (ast.Call, ast.NamedExpr)):
                return False
            if isinstance(node, ast.Subscript):
                value = node.value
                name = None
                if isinstance(value, ast.Name):
                    name = value.id
                elif isinstance(value, ast.Attribute):
                    name = value.attr
                if name == "Annotated":
                    return False
    return True


def _base_simple_name(base: ast.expr) -> str | None:
    """Simple name of a base class entry: 'Base' for a plain name or the
    attribute tail of 'pkg.Base'; None for anything unresolvable."""
    if isinstance(base, ast.Name):
        return base.id
    if isinstance(base, ast.Attribute):
        return base.attr
    return None


def _class_consumes_annotations(cls_node: ast.ClassDef, class_map: dict) -> bool:
    """True when the class (or a same-file ancestor) may consume
    ``__annotations__`` at runtime: any decorator, metaclass/other class
    keywords, an unresolvable base, or an annotation-consuming base such as
    TypedDict / Protocol / BaseModel / Enum / NamedTuple."""
    stack, seen = [cls_node], set()
    while stack:
        node = stack.pop()
        if id(node) in seen:
            continue
        seen.add(id(node))
        if node.decorator_list or node.keywords:
            return True
        for base in node.bases:
            name = _base_simple_name(base)
            if name is None or name in _ANNOTATION_CONSUMING_BASES:
                return True
            parent = class_map.get(name)
            if parent is not None and id(parent) not in seen:
                stack.append(parent)
    return False


def _collect_class_info(source: str) -> tuple[list[tuple], dict[str, ast.ClassDef], bool]:
    """Parse source and return (scopes, class_map, has_future):

    scopes    : [(lineno, end_lineno, col_offset, node)] of every
                function/class definition, for annotation scope resolution;
    class_map : {class name -> ClassDef} for same-file base resolution
                (a later definition shadows an earlier one);
    has_future: the file has ``from __future__ import annotations``.
    """
    tree = ast.parse(source)
    scopes = [
        (n.lineno, n.end_lineno, n.col_offset, n)
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]
    classes = sorted(
        (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)),
        key=lambda n: n.lineno,
    )
    class_map = {n.name: n for n in classes}
    has_future = any(
        isinstance(n, ast.ImportFrom)
        and n.module == "__future__"
        and any(alias.name == "annotations" for alias in n.names)
        for n in tree.body
    )
    return scopes, class_map, has_future


def _insertion_scope(scopes: list[tuple], a: int, b: int, add_indent: int | None):
    """Innermost function/class whose body receives the insertion between
    base lines a and b (None when it lands at module level)."""
    inside = [s for s in scopes if s[0] <= a and b <= s[1]]
    if inside:
        return max(inside, key=lambda s: s[0])[3]
    if add_indent is None:
        return None
    containing = [s for s in scopes if s[0] <= a <= s[1] and s[2] < add_indent]
    if containing:
        return max(containing, key=lambda s: s[0])[3]
    return None


def _deletion_scope_kind(scopes: list[tuple], first: int, last: int) -> tuple[str, ast.AST | None]:
    """Scope kind of a contiguous deleted range: ('func'|'class', node),
    ('module', None), or ('mixed', None) when it crosses scope boundaries."""
    inside = [s for s in scopes if s[0] <= first and last <= s[1]]
    if inside:
        node = max(inside, key=lambda s: s[0])[3]
        return ("class" if isinstance(node, ast.ClassDef) else "func"), node
    for s in scopes:
        if s[0] <= first <= s[1] or s[0] <= last <= s[1]:
            return "mixed", None
    return "module", None


def _pure_annotation_insert_reason(
    add_texts,
    a: int,
    b: int,
    add_indent: int | None,
    ann_ctx: tuple | None,
) -> str | None:
    """Skip reason when a pure insertion consists of type annotations in a
    scope where they are provably inert; None to record as usual.

    Inert scopes: function bodies (local annotations are never evaluated),
    plain classes (no decorators and no annotation-consuming base) and
    module level, with annotation expressions free of runtime side effects
    unless the file uses ``from __future__ import annotations``.
    """
    if ann_ctx is None or not add_texts:
        return None
    scopes, class_map, has_future = ann_ctx
    anns = _parse_pure_annotations(add_texts)
    if not anns:
        return None
    scope = _insertion_scope(scopes, a, b, add_indent)
    if scope is None:
        if _annotation_eval_safe(anns, has_future):
            return "module-level pure type annotation"
        return None
    if isinstance(scope, ast.ClassDef):
        if not _class_consumes_annotations(scope, class_map) and _annotation_eval_safe(anns, has_future):
            return "pure type annotation in a plain class body"
        return None
    return "function-local pure type annotation"


def _pure_annotation_change_exempt(
    del_lines,
    add_texts,
    ann_ctx: tuple | None,
) -> bool:
    """True when a deletion group (with its replacement lines) is a pure type
    annotation change in a scope where annotations are provably inert.

    Direction-independent counterpart of _pure_annotation_insert_reason:
    deleted and added lines must both be pure annotations (additions may be
    absent or comments only), so ``x: int`` -> ``x: str`` and plain
    deletions are skipped, while ``x: int`` -> ``x = 5`` stays recorded.
    """
    if ann_ctx is None:
        return False
    scopes, class_map, has_future = ann_ctx
    del_anns = _parse_pure_annotations([t for _, t in del_lines])
    if not del_anns:
        return False
    adds = [t for t in add_texts if t.strip()]
    add_anns = []
    if adds:
        add_anns = _parse_pure_annotations(adds)
        if add_anns is None:  # replaced by real code: keep the anchor
            return False
    first, last = del_lines[0][0], del_lines[-1][0]
    kind, node = _deletion_scope_kind(scopes, first, last)
    if kind == "func":
        return True  # local annotations are never evaluated at runtime
    if kind == "mixed":
        return False
    safe = _annotation_eval_safe(del_anns, has_future) and (not add_anns or _annotation_eval_safe(add_anns, has_future))
    if not safe:
        return False
    if kind == "module":
        return True
    return not _class_consumes_annotations(node, class_map)


def _classify_candidate_pairs(
    affected: set[int],
    pairs: list[tuple],
    del_groups: list[tuple],
    base_text: str | None,
    path: str,
) -> None:
    """Classify deletion groups and candidate pairs of one file using its base
    content and update the affected line set in place.

    Deletion groups: a group is dropped entirely when every deleted line is a
    comment/docstring line in the base file AND the added lines are comments or
    doc prose (not parseable Python), i.e. a pure comment/docstring change.
    Pure type annotation changes are dropped in every direction (insertion,
    replacement, deletion) when provably inert: function-local annotations are
    never evaluated; class-level ones need a plain class (no decorators, no
    TypedDict/Protocol/Enum/BaseModel/NamedTuple base) and side-effect-free
    annotation expressions unless the file has future annotations.
    Candidate pairs: without base content (or non-parseable Python) both sides
    of each pair are counted, bounded by the hunk.
    """
    info = None
    docstr_lines = set()
    comment_lines = set()
    ann_ctx = None
    if base_text is not None:
        try:
            info = _collect_defs(base_text)
            docstr_lines = _get_docstring_lines(base_text)
            comment_lines = {i for i, t in enumerate(base_text.splitlines(), 1) if t.strip().startswith("#")}
            ann_ctx = _collect_class_info(base_text)
        except (SyntaxError, ValueError):
            info = None
    if base_text is None:
        print(f"  Warning: no base content for {path}, counting candidate pairs on both sides")

    # Deleted non-blank lines: comment/docstring lines are never changes by
    # themselves; a group made entirely of them is dropped unless its lines are
    # replaced by real code (then they are kept as the only base anchors).
    noise_lines = comment_lines | docstr_lines
    for del_lines, add_texts in del_groups:
        if info is None:
            affected.update(n for n, _ in del_lines)
            continue
        if _pure_annotation_change_exempt(del_lines, add_texts, ann_ctx):
            print(f"  Skipped {path}:{[n for n, _ in del_lines]} (pure type annotation change, not counted)")
            continue
        code_dels = [n for n, _ in del_lines if n not in noise_lines]
        if code_dels:
            affected.update(code_dels)
            dropped = [n for n, _ in del_lines if n in noise_lines]
            if dropped:
                print(f"  Skipped {path}:{dropped} (comment/docstring lines, not counted)")
            continue
        adds = [t for t in add_texts if t.strip()]
        pure = not adds or all(t.strip().startswith("#") for t in adds) or not _looks_like_code(adds)
        if pure:
            print(f"  Skipped {path}:{[n for n, _ in del_lines]} (pure comment/docstring change, not counted)")
        else:
            # comment/docstring lines replaced by real code: keep as change anchors
            affected.update(n for n, _ in del_lines)

    for a, b, kind, add_indent, introduces_def, adds_all_comment, hunk_base_end, add_texts in pairs:
        if info is None:
            if a >= 1:
                affected.add(a)
            if b <= hunk_base_end:
                affected.add(b)
            continue
        ranges, end_lines, start_lines, blanks = info
        reason = None
        if kind == "insert":
            if a in docstr_lines:
                reason = "inside a docstring"
            elif adds_all_comment:
                reason = "pure comment insertion"
            else:
                func = _innermost_func(ranges, a)
                modifies = func is not None and (b <= func[1] or (add_indent is not None and add_indent > func[2]))
                if not modifies:
                    if _between_definitions(a, b, end_lines, start_lines, blanks):
                        reason = "between function/class definitions"
                    elif introduces_def:
                        reason = "belongs to a newly added function/class"
            if reason is None:
                reason = _pure_annotation_insert_reason(add_texts, a, b, add_indent, ann_ctx)
        # kind == 'blank': isolated blank deletion == one-line insertion
        elif _between_definitions(a, b, end_lines, start_lines, blanks):
            reason = "between function/class definitions"
        if reason is None:  # kept: record the line above only
            if a >= 1:
                affected.add(a)
        else:
            print(f"  Skipped {path}:{a}-{b} ({reason}, not counted)")
