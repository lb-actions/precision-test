"""sgl-kernel-npu csrc 算子 ↔ 测试用例静态映射。

覆盖 sglang-kernel 的 csrc 原生代码变更（C++/CANN 算子）：这类文件不在 Python 覆盖率数据里，
无法用行/函数级精准匹配召回，因此用静态映射补足选择面：

  csrc 文件 → 归属算子（TORCH_LIBRARY 注册名）
            → python 调用图闭包（torch.ops.npu.*）
            → 测试导入归因（tests/python/**）

三条数据来源：
  1. csrc 各 CMakeLists.txt 的 sgl_register_operator 声明（新 CANN 体系目录归属）
  2. torch_adapter.cpp / pytorch_extensions.cpp 的 m.def("name(...)") 注册（原生算子名全集）
  3. csrc 头文件 #include "..." 依赖图（共享头文件的影响面：被 include 的头继承所有引用方算子）


仅依赖标准库，可独立调用：
    python -m test_selector.operator_mapper --repo-root /path/to/sgl-kernel-npu --output operator_map.json
"""

import argparse
import ast
import json
import re
from pathlib import Path, PurePosixPath

#: 与注册点绑定的独立适配器文件（多算子聚合注册点）
SPECIAL_ADAPTER_FILES = {
    "csrc/pytorch_extensions.cpp",
    "csrc/attentions/csrc/plugin/register_ops.cpp",
}

#: attn 独立插件树：与 csrc/attention/<op> 并行的一套 CANN 插件构建（自带 build 脚本）
ATTN_PLUGIN_OPS = ["ada_block_sparse_attention", "la", "layernorm", "rainfusionattention", "sparse_block_estimate"]

#: csrc 中 deepep 树（独立构建，Python 侧通过 deep_ep 包调用，无法按 torch.ops.npu 归因）
DEEPEP_OP = "deep_ep"

_PKG = "sgl_kernel_npu"
_PY_OPS_RE = re.compile(r"torch\.ops\.npu\.([A-Za-z0-9_]+)")
_INCLUDE_RE = re.compile(r'^\s*#\s*include\s+"([^"]+)"', re.M)
_MDEF_RE = re.compile(r'm\.def\(\s*"([A-Za-z0-9_]+)\(')


# ======================================================================
# 1. csrc 侧解析
# ======================================================================
def parse_adapter_ops(adapter_path: Path) -> list[str]:
    """从 torch_adapter / extensions cpp 中提取 m.def("name(...)") 的算子名（去重保序）。"""
    try:
        text = adapter_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    seen: dict[str, None] = {}
    for m in _MDEF_RE.finditer(text):
        seen.setdefault(m.group(1))
    return list(seen)


def parse_legacy_dir_ops(csrc_root: Path) -> dict[str, list[str]]:
    """旧 Torch 体系：m.impl 算子 -> 实现符号 -> OP_SRCS 定义文件 -> 目录。"""
    adapter_path = csrc_root / "pytorch_extensions.cpp"
    if not adapter_path.exists():
        return {}
    adapter = adapter_path.read_text(encoding="utf-8", errors="ignore")
    cmake_path = csrc_root.parent / "cmake" / "legacy_kernels.cmake"
    cmake = cmake_path.read_text(encoding="utf-8", errors="ignore")

    impls = list(re.finditer(r'm\.impl\(\s*"([A-Za-z0-9_]+)"', adapter))
    op_symbols: dict[str, list[str]] = {}
    for index, match in enumerate(impls):
        end = impls[index + 1].start() if index + 1 < len(impls) else len(adapter)
        op_symbols[match.group(1)] = re.findall(
            r"sglang::npu_kernel::([A-Za-z0-9_]+)", adapter[match.end():end]
        )

    blocks = re.findall(
        r"(?:set\s*\(\s*OP_SRCS\b|list\s*\(\s*APPEND\s+OP_SRCS\b)(.*?)\)", cmake, re.S
    )
    sources = sorted({
        rel
        for block in blocks
        for rel in re.findall(r"\$\{PROJECT_OP_SRC_BASE\}/([^\s)]+\.cpp)", block)
        if "${" not in rel and rel != "pytorch_extensions.cpp" and (csrc_root / rel).is_file()
    })
    source_texts = {
        rel: (csrc_root / rel).read_text(encoding="utf-8", errors="ignore")
        for rel in sources
    }

    dir_ops: dict[str, set[str]] = {}
    for op, symbols in op_symbols.items():
        for symbol in symbols:
            definition = re.compile(r"\b" + re.escape(symbol) + r"\s*\([^;{}]*\)\s*\{", re.S)
            for rel, source in source_texts.items():
                if symbol not in source or not definition.search(source):
                    continue
                directory = rel.split("/op_host/", 1)[0] if "/op_host/" in rel else PurePosixPath(rel).parent.as_posix()
                dir_ops.setdefault(directory, set()).add(op)
    return {directory: sorted(ops) for directory, ops in sorted(dir_ops.items())}


def parse_cmake_registrations(csrc_root: Path) -> dict[str, list[str]]:
    """新 CANN 体系：每个算子目录的 torch_adapter.cpp 即注册点。

    扫描含 sgl_register_operator 的 CMakeLists 目录，目录内 torch_adapter.cpp 的 m.def 名即该目录算子集。
    """
    dir_ops: dict[str, list[str]] = {}
    for cmk in csrc_root.rglob("CMakeLists.txt"):
        try:
            text = cmk.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "sgl_register_operator" not in text:
            continue
        op_dir = cmk.parent
        adapter = op_dir / "torch_adapter.cpp"
        if not adapter.exists():
            continue
        rel_dir = op_dir.relative_to(csrc_root).as_posix()
        dir_ops[rel_dir] = parse_adapter_ops(adapter)
    return dir_ops


def build_dir_ops(csrc_root: Path) -> dict[str, list[str]]:
    """汇总 csrc 目录 -> 原生算子集：新体系注册目录 + 动态 legacy 归因 + 插件树。"""
    dir_ops = parse_cmake_registrations(csrc_root)
    dir_ops.update(parse_legacy_dir_ops(csrc_root))
    dir_ops["attentions/csrc/ops/ada_block_sparse_attention"] = ["ada_block_sparse_attention"]
    dir_ops["attentions/csrc/ops/laser_attention"] = ["la"]
    dir_ops["attentions/csrc/ops/sparse_block_estimate"] = ["sparse_block_estimate"]
    dir_ops["attentions/csrc/plugin"] = list(ATTN_PLUGIN_OPS)
    if (csrc_root / "deepep").is_dir():
        dir_ops["deepep"] = [DEEPEP_OP]
    dm_adapter = csrc_root / "data_move" / "torch_adapter.cpp"
    if dm_adapter.exists():
        dir_ops["data_move"] = parse_adapter_ops(dm_adapter)
    return dir_ops


def _build_file_ops(rel: str, dir_ops: dict[str, list[str]], csrc_root: Path | None) -> set[str]:
    """按最近源码作用域及构建脚本实际引用路径，计算嵌套构建文件的算子影响面。"""
    scope_ops: dict[str, set[str]] = {}
    for key, ops in dir_ops.items():
        parts = key.split("/")
        for end in range(1, len(parts) + 1):
            scope_ops.setdefault("/".join(parts[:end]), set()).update(ops)
    scope_ops[""] = {op for ops in dir_ops.values() for op in ops}

    bare = rel[len("csrc/"):]
    parent = PurePosixPath(bare).parent.as_posix()
    base_scope = "" if parent == "." else parent
    while base_scope and base_scope not in scope_ops:
        base_scope = PurePosixPath(base_scope).parent.as_posix()
        if base_scope == ".":
            base_scope = ""
    base_ops = scope_ops.get(base_scope, set())
    if bare == "CMakeLists.txt":
        return set(base_ops)

    content = rel
    if csrc_root is not None:
        try:
            content += "\n" + (csrc_root / bare).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            pass
    referenced: set[str] = set()
    for scope, ops in scope_ops.items():
        if not scope or (base_scope and not scope.startswith(base_scope + "/")):
            continue
        parts = scope.split("/")
        for index in range(len(parts)):
            hint = "/".join(parts[index:])
            if hint == "csrc" or len(hint) < 3:
                continue
            if re.search(r"(?<![A-Za-z0-9_])" + re.escape(hint) + r"(?![A-Za-z0-9_])", content):
                referenced |= ops
                break
    return referenced or set(base_ops)


def seed_file_ops(
    rel: str,
    dir_ops: dict[str, list[str]],
    adapter_file_ops: dict[str, list[str]],
    csrc_root: Path | None = None,
) -> set[str]:
    """目录归属：给 csrc 相对路径一个初始算子集（include 图传播的种子）。rel 带 csrc/ 前缀。"""
    if rel in SPECIAL_ADAPTER_FILES:
        return set(adapter_file_ops.get(rel, []))
    if rel.startswith("csrc/attentions/csrc/plugin/"):
        name = PurePosixPath(rel).stem
        if name in ATTN_PLUGIN_OPS:
            return {name}
        # find_op_path.cpp / pytorch_npu_helper.h 等插件公共文件：影响插件内全部算子
        return set(ATTN_PLUGIN_OPS)
    if rel.startswith("csrc/deepep/"):
        return {DEEPEP_OP}
    bare = rel[len("csrc/"):]
    best = ""
    for key in dir_ops:
        if bare.startswith(key + "/") and len(key) > len(best):
            best = key
    if best:
        return set(dir_ops[best])
    if bare.endswith((".txt", ".cmake", ".sh")):
        return _build_file_ops(rel, dir_ops, csrc_root)
    return set()


def collect_all_native_ops(csrc_root: Path) -> dict[str, list[str]]:
    """csrc 下所有带 TORCH_LIBRARY 注册的 cpp 文件 -> 算子名（键带 csrc/ 前缀）。"""
    out: dict[str, list[str]] = {}
    for cpp in csrc_root.rglob("*.cpp"):
        try:
            text = cpp.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "TORCH_LIBRARY" not in text:
            continue
        ops = parse_adapter_ops(cpp)
        if ops:
            out["csrc/" + cpp.relative_to(csrc_root).as_posix()] = ops
    return out


# ======================================================================
# 2. csrc include 依赖图（共享头文件影响面）
# ======================================================================
class IncludeGraph:
    """解析 csrc 内 #include "..."，传播算子归属：被 include 的头文件继承所有引用方的算子。

    解析顺序：相对引用方目录 → 唯一路径后缀匹配 → 同顶层目录最浅候选 → utils/ 最浅候选。
    """

    SRC_EXTS = {".h", ".cpp", ".cc", ".cxx", ".hpp", ".inc"}

    def __init__(self, csrc_root: Path):
        self.csrc_root = csrc_root
        self.files = sorted(
            p for p in csrc_root.rglob("*")
            if p.is_file() and "cmake" not in p.relative_to(csrc_root).parts
        )
        self.rel_set = {"csrc/" + p.relative_to(csrc_root).as_posix() for p in self.files}
        self.suffix_idx: dict[str, list[str]] = {}
        for rel in self.rel_set:
            segs = rel.split("/")
            for i in range(len(segs)):
                tok = "/".join(segs[i:])
                self.suffix_idx.setdefault(tok, []).append(rel)
        self.edge_count = 0

    def _resolve(self, rel: str, tok: str) -> str | None:
        if not re.search(r"\.(h|hpp|inc|cpp|cc|txt)$", tok):
            return None
        parts: list[str] = []
        for seg in (PurePosixPath(rel).parent / tok).parts:
            if seg == "..":
                if parts:
                    parts.pop()
            elif seg != ".":
                parts.append(seg)
        cand = "/".join(parts)
        if cand in self.rel_set:
            return cand
        cands = self.suffix_idx.get(tok, [])
        if len(cands) == 1:
            return cands[0]
        if cands:
            top = rel.split("/")[1] if rel.startswith("csrc/") else rel.split("/")[0]
            same = [c for c in cands if c.split("/")[1] == top]
            if same:
                return sorted(same, key=lambda c: c.count("/"))[0]
            utils = [c for c in cands if c.startswith("csrc/utils/")]
            if utils:
                return sorted(utils, key=lambda c: c.count("/"))[0]
        return None

    def propagate(self, dir_ops: dict[str, list[str]], adapter_file_ops: dict[str, list[str]]) -> dict[str, set[str]]:
        edges: dict[str, set[str]] = {}
        for p in self.files:
            if p.suffix not in self.SRC_EXTS:
                continue
            rel = "csrc/" + p.relative_to(self.csrc_root).as_posix()
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            targets = set()
            for m in _INCLUDE_RE.finditer(text):
                tgt = self._resolve(rel, m.group(1))
                if tgt and tgt != rel:
                    targets.add(tgt)
            edges[rel] = targets

        file_ops: dict[str, set[str]] = {}
        for p in self.files:
            rel = "csrc/" + p.relative_to(self.csrc_root).as_posix()
            file_ops[rel] = seed_file_ops(rel, dir_ops, adapter_file_ops, self.csrc_root)

        for _ in range(20):
            changed = False
            for rel, targets in edges.items():
                includer_ops = file_ops.get(rel)
                if not includer_ops:
                    continue
                for tgt in targets:
                    s = file_ops.get(tgt)
                    if s is None:
                        continue
                    before = len(s)
                    s |= includer_ops
                    if len(s) != before:
                        changed = True
            if not changed:
                break
        self.edge_count = sum(len(v) for v in edges.values())
        return file_ops


# ======================================================================
# 3. Python 包侧：模块解析 + 跨模块符号级调用图
# ======================================================================
def _is_torch_ops(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "ops"
        and isinstance(node.value, ast.Name)
        and node.value.id == "torch"
    )


def _is_torch_npu(node: ast.AST) -> bool:
    return isinstance(node, ast.Attribute) and node.attr == "npu" and _is_torch_ops(node.value)


def _dynamic_binding_ops(scope: ast.AST) -> set[str]:
    """提取 namespace/binding 条件赋值中实际可能走 npu 命名空间的算子名。"""
    nodes = list(ast.walk(scope))
    has_dynamic_call = False
    for node in nodes:
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Name)
            and node.args[1].id == "binding"
        ):
            continue
        owner = node.args[0]
        if (
            isinstance(owner, ast.Call)
            and isinstance(owner.func, ast.Name)
            and owner.func.id == "getattr"
            and len(owner.args) >= 2
            and _is_torch_ops(owner.args[0])
            and isinstance(owner.args[1], ast.Name)
            and owner.args[1].id == "namespace"
        ):
            has_dynamic_call = True
            break
    if not has_dynamic_call:
        return set()

    ops: set[str] = set()
    for node in nodes:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not (
            isinstance(target, ast.Tuple)
            and [elt.id for elt in target.elts if isinstance(elt, ast.Name)] == ["namespace", "binding"]
            and len(target.elts) == 2
        ):
            continue
        for value in ast.walk(node.value):
            if (
                isinstance(value, ast.Tuple)
                and len(value.elts) == 2
                and all(isinstance(elt, ast.Constant) and isinstance(elt.value, str) for elt in value.elts)
                and value.elts[0].value == "npu"
            ):
                ops.add(value.elts[1].value)
    return ops


def _extract_npu_ops(text: str, tree: ast.AST) -> set[str]:
    ops = set(_PY_OPS_RE.findall(text))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and _is_torch_npu(node.args[0])
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        ):
            ops.add(node.args[1].value)
    return ops | _dynamic_binding_ops(tree)


class PyModule:
    def __init__(self, name: str, is_package: bool, relpath: str):
        self.name = name
        self.is_package = is_package
        self.relpath = relpath
        self.imports: dict[str, tuple[str, str]] = {}
        self.reexports: dict[str, tuple[str, str]] = {}
        self.ops: set[str] = set()
        self.funcs: dict[str, set[str]] = {}
        self.func_calls: dict[str, set[str]] = {}
        self.aliases: dict[str, str] = {}
        self.str_consts: dict[str, str] = {}         # 模块级字符串常量
        self.dispatch_regs: dict[str, str] = {}      # dispatcher func -> register_operator 注册名
        self.impl_regs: dict[str, set[str]] = {}     # register_impl 注册名 -> impl 函数集


def _module_name(pkg_prefix: str, rel: str) -> tuple[str, bool]:
    if rel.endswith("__init__.py"):
        mod = rel[: -len("__init__.py")].rstrip("/").replace("/", ".")
        return (f"{pkg_prefix}.{mod}" if mod else pkg_prefix), True
    return f"{pkg_prefix}." + rel[: -len(".py")].replace("/", "."), False


def parse_py_modules(pkg_root: Path) -> dict[str, PyModule]:
    modules: dict[str, PyModule] = {}
    for py in pkg_root.rglob("*.py"):
        rel = py.relative_to(pkg_root).as_posix()
        name, is_pkg = _module_name(_PKG, rel)
        mod = PyModule(name, is_pkg, rel)
        try:
            text = py.read_text(encoding="utf-8", errors="ignore")
            tree = ast.parse(text)
        except (OSError, SyntaxError):
            modules[name] = mod
            continue

        mod.ops = _extract_npu_ops(text, tree)
        for node in tree.body:
            # 模块级字符串常量（注册名常用 _IMPL_NAME = "examples.elu" 形式）
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                t = node.targets[0]
                if isinstance(t, ast.Name) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                    mod.str_consts[t.id] = node.value.value
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                target = node.module or ""
                if node.level:
                    base = name if is_pkg else name.rsplit(".", 1)[0]
                    parts = base.split(".")
                    keep = len(parts) - (node.level - 1)
                    target = ".".join(parts[:keep]) + ("." + target if target else "")
                for alias in node.names:
                    local = alias.asname or alias.name
                    mod.imports[local] = (target, alias.name)
                    mod.reexports[local] = (target, alias.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    local = alias.asname or alias.name.split(".")[0]
                    mod.imports[local] = (alias.name, "")
            elif isinstance(node, ast.Assign) and len(node.targets) == 1:
                t = node.targets[0]
                if isinstance(t, ast.Name) and isinstance(node.value, ast.Name):
                    mod.aliases[t.id] = node.value.id

        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                seg = ast.get_source_segment(text, node) or ""
                calls = set()
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Call):
                        f = sub.func
                        if isinstance(f, ast.Name):
                            calls.add(f.id)
                        elif isinstance(f, ast.Attribute):
                            calls.add(f.attr)
                mod.funcs[node.name] = _extract_npu_ops(seg, node)
                mod.func_calls[node.name] = calls
                # @register_operator("domain.op") / @register_impl("domain.op", ...)
                # 注册名参数支持字符串字面量或模块级字符串常量（_IMPL_NAME）
                for dec in node.decorator_list:
                    if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Name) and dec.args:
                        arg0 = dec.args[0]
                        reg = None
                        if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str):
                            reg = arg0.value
                        elif isinstance(arg0, ast.Name):
                            reg = mod.str_consts.get(arg0.id)
                        if reg is None:
                            continue
                        if dec.func.id == "register_operator":
                            mod.dispatch_regs[node.name] = reg
                        elif dec.func.id == "register_impl":
                            mod.impl_regs.setdefault(reg, set()).add(node.name)
        modules[name] = mod

    # dispatcher 合并同注册名 impl 的 ops（dispatcher 本身不含 torch.ops.npu 调用）
    for mod in modules.values():
        for fname, reg in mod.dispatch_regs.items():
            for impl_name in mod.impl_regs.get(reg, ()):
                if impl_name != fname:
                    impl_ops = resolve_symbol(modules, mod.name, impl_name)
                    mod.funcs[fname] = mod.funcs.get(fname, set()) | impl_ops
    return modules


def resolve_symbol(
    modules: dict[str, PyModule],
    module: str,
    symbol: str,
    depth: int = 0,
    _seen: set[tuple[str, str]] | None = None,
) -> set[str]:
    """跨模块解析符号，并递归闭包本地及导入 helper 的 torch.ops.npu.* 调用。"""
    if depth > 12 or module not in modules:
        return set()
    mod = modules[module]
    if symbol in mod.aliases and symbol not in mod.funcs:
        symbol = mod.aliases[symbol]
    seen = set() if _seen is None else _seen
    key = (module, symbol)
    if key in seen:
        return set()
    seen.add(key)

    ops: set[str] = set()
    if symbol in mod.funcs:
        ops |= mod.funcs[symbol]
        for callee in mod.func_calls.get(symbol, ()):
            if callee == symbol:
                continue
            if callee in mod.funcs:
                ops |= resolve_symbol(modules, module, callee, depth + 1, seen)
            elif callee in mod.reexports:
                tmod, tsym = mod.reexports[callee]
                ops |= resolve_symbol(modules, tmod, tsym, depth + 1, seen)
        return ops
    if symbol in mod.reexports:
        tmod, tsym = mod.reexports[symbol]
        return resolve_symbol(modules, tmod, tsym, depth + 1, seen)
    return ops


def build_op_api_closure(modules: dict[str, PyModule]) -> dict[str, set[str]]:
    """算子 -> 直接引用它的 API 文件集合（含函数级 ops）。"""
    op_apis: dict[str, set[str]] = {}
    for mod in modules.values():
        file_ops = set(mod.ops)
        for fops in mod.funcs.values():
            file_ops |= fops
        for op in file_ops:
            op_apis.setdefault(op, set()).add(mod.relpath)
    return op_apis


# ======================================================================
# 4. 测试归因
# ======================================================================
def resolve_tests(test_root: Path, modules: dict[str, PyModule]) -> tuple[dict[str, set[str]], dict[str, dict]]:
    """测试文件 -> 算子集：from sgl_kernel_npu... import X 解析到定义模块取 ops；含直接 torch.ops.npu 调用。"""
    test_ops: dict[str, set[str]] = {}
    test_info: dict[str, dict] = {}
    test_deps: dict[str, set[str]] = {}
    for py in sorted(test_root.rglob("*.py")):
        if py.name == "__init__.py":
            continue
        rel = "tests/python/" + py.relative_to(test_root).as_posix()
        try:
            text = py.read_text(encoding="utf-8", errors="ignore")
            tree = ast.parse(text)
        except (OSError, SyntaxError):
            continue
        ops: set[str] = _extract_npu_ops(text, tree)
        apis: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.level:
                continue
            module = node.module or ""
            if module.startswith("tests.python."):
                dep = "tests/python/" + module[len("tests.python."):].replace(".", "/") + ".py"
                test_deps.setdefault(rel, set()).add(dep)
            if not (module == _PKG or module.startswith(_PKG + ".")):
                continue
            if module.endswith(".tests") or ".tests." in module:
                continue
            for alias in node.names:
                ops |= resolve_symbol(modules, module, alias.name)
                apis.add(f"{module}:{alias.name}")
        # 与 PR 新增测试、覆盖率适配器及 pytest 输出统一使用仓库相对路径
        key = rel
        # deepep 测试：通过 import deep_ep 调用（非 sgl_kernel_npu），按目录名归因
        if key.startswith("tests/python/deepep/") and py.name.startswith("test_"):
            ops.add(DEEPEP_OP)
        test_ops[key] = ops
        test_info[key] = {"apis": sorted(apis), "ops": sorted(ops), "full_path": rel}

    while True:
        changed = False
        for key, deps in test_deps.items():
            for dep in deps:
                inherited = test_ops.get(dep, set()) - test_ops.get(key, set())
                if inherited:
                    test_ops[key] |= inherited
                    changed = True
        if not changed:
            break
    for key, ops in test_ops.items():
        test_info[key]["ops"] = sorted(ops)
    return test_ops, test_info


# ======================================================================
# 5. 顶层构建
# ======================================================================
def build_operator_map(repo_root: Path) -> dict:
    csrc_root = repo_root / "csrc"
    pkg_root = repo_root / "python" / _PKG / _PKG
    test_root = repo_root / "tests" / "python"
    for p, label in ((csrc_root, "csrc"), (pkg_root, "python pkg"), (test_root, "tests")):
        if not p.is_dir():
            raise FileNotFoundError(f"{label} 目录不存在: {p}")

    adapter_file_ops = collect_all_native_ops(csrc_root)
    dir_ops = build_dir_ops(csrc_root)
    graph = IncludeGraph(csrc_root)
    file_op_sets = graph.propagate(dir_ops, adapter_file_ops)

    modules = parse_py_modules(pkg_root)
    op_apis = build_op_api_closure(modules)
    test_ops, test_info = resolve_tests(test_root, modules)

    new_system_ops: set[str] = set()
    for d in parse_cmake_registrations(csrc_root).values():
        new_system_ops |= set(d)
    legacy_ops = set(parse_adapter_ops(csrc_root / "pytorch_extensions.cpp"))

    op_tests: dict[str, set[str]] = {}
    for t, ops in test_ops.items():
        if not PurePosixPath(t).name.startswith("test_"):
            continue
        for op in ops:
            op_tests.setdefault(op, set()).add(t)

    op_dirs: dict[str, set[str]] = {}
    for d, ops in dir_ops.items():
        for op in ops:
            op_dirs.setdefault(op, set()).add("csrc/" + d)
    # deepep 树整体为一个归属单元
    if any(rel.startswith("csrc/deepep/") for rel in file_op_sets):
        op_dirs.setdefault(DEEPEP_OP, set()).add("csrc/deepep")

    all_ops = set(op_apis) | set(op_dirs) | {o for v in adapter_file_ops.values() for o in v}
    ops_table: dict[str, dict] = {}
    for op in sorted(all_ops):
        system = "BOTH" if op in new_system_ops and op in legacy_ops else ("CANN-NEW" if op in new_system_ops else "LEGACY")
        if op == DEEPEP_OP:
            system = "DEEPEP"
        ops_table[op] = {
            "system": system,
            "dirs": sorted(op_dirs.get(op, [])),
            "apis": sorted(op_apis.get(op, [])),
            "tests": sorted(op_tests.get(op, [])),
        }

    files_table: dict[str, dict] = {}
    for rel, ops in sorted(file_op_sets.items()):
        tests: set[str] = set()
        for op in ops:
            tests |= op_tests.get(op, set())
        files_table[rel] = {"ops": sorted(ops), "tests": sorted(tests)}

    py_referenced = set(op_apis)
    registered = {o for v in adapter_file_ops.values() for o in v}
    diag = {
        "external_cann_ops": sorted(o for o in py_referenced if o not in registered and o != DEEPEP_OP),
        "registered_unused_by_python": sorted(o for o in registered if o not in py_referenced),
        "ops_without_tests": sorted(o for o in ops_table if not ops_table[o]["tests"]),
    }

    return {
        "meta": {
            "repo_root": str(repo_root),
            "csrc_files": len(files_table),
            "infra_files": sum(1 for v in files_table.values() if not v["ops"]),
            "ops": len(ops_table),
            "ops_with_tests": sum(1 for v in ops_table.values() if v["tests"]),
            "tests": len(test_info),
            "include_edges": graph.edge_count,
        },
        "files": files_table,
        "ops": ops_table,
        "tests": test_info,
        "diag": diag,
    }


def save_map(data: dict, output: Path) -> None:
    with open(output, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def load_map(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def select_tests_for_files(operator_map: dict, changed_paths: list[str]) -> dict[str, list[str]]:
    """给定 csrc 变更文件列表（仓库相对路径，csrc/ 前缀），返回 {文件: [测试...]}。

    输入可为文件或目录前缀；未命中映射的 csrc 文件返回空列表（INFRA）。
    """
    files = operator_map.get("files", {})
    result: dict[str, list[str]] = {}
    for path in changed_paths:
        norm = path.replace("\\", "/").lstrip("./")
        tests: set[str] = set()
        if norm in files:
            tests |= set(files[norm]["tests"])
        else:
            prefix = norm.rstrip("/") + "/"
            for f, info in files.items():
                if f.startswith(prefix):
                    tests |= set(info["tests"])
        result[norm] = sorted(tests)
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description="Build csrc operator -> test mapping for sgl-kernel-npu")
    ap.add_argument("--repo-root", required=True, help="sgl-kernel-npu 仓库根目录")
    ap.add_argument("--output", "-o", default="operator_map.json", help="输出 JSON 路径")
    args = ap.parse_args()
    data = build_operator_map(Path(args.repo_root))
    save_map(data, Path(args.output))
    m = data["meta"]
    print(f"csrc 文件: {m['csrc_files']}（INFRA {m['infra_files']}）")
    print(f"算子: {m['ops']}（有测试 {m['ops_with_tests']}）  测试文件: {m['tests']}  include 边: {m['include_edges']}")
    print(f"无测试算子: {data['diag']['ops_without_tests']}")
    print(f"输出: {args.output}")


if __name__ == "__main__":
    main()
