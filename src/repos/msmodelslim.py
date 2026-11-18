"""msmodelslim 仓库适配器（https://gitcode.com/Ascend/msmodelslim）。

- 产品代码分布在多个顶层目录（modelslim/msmodelslim/config/lab_calib/lab_practice/
  ascend_utils/precision_tool/tools），无单一 product_code_prefix，通过
  is_product_code_path 多根判定。
- 覆盖率文件为裸 `.coverage`（pytest-cov 默认名），默认 glob "coverage*" 无法匹配。
- 覆盖率库中文件路径为绝对路径，需按 PRODUCT_ROOTS 边界剥离前缀得到相对路径。
- 源码位于 covstub 顶层（无 msmodelslim/ 子目录），resolve_source_file 需覆写。
- 测试名规范化与 msmodeling 一致：-- -> ::，__ -> /，文件级补 .py。
- 测试目录为单数 `test/`（与 msmodeling 的 `tests/` 不同）。
"""

from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath

from .base import RepoAdapter


class MsmodelslimAdapter(RepoAdapter):
    """msmodelslim 仓库适配器。"""

    repo_name = "msmodelslim"
    product_code_prefix = ""  # 产品代码分散在多个顶层目录，无单一前缀
    test_case_dir_prefix = None
    coverage_file_glob = ".coverage"  # 关键：默认 "coverage*" 匹配不到 .coverage

    #: 产品代码顶层目录（多根，无法用单一 product_code_prefix 表达）
    #: 依据 msmodelslim 仓库 setup.py 的 package_dir + find_packages 推导；security/example 非产品代码
    PRODUCT_ROOTS = (
        "modelslim",
        "msmodelslim",
        "config",
        "lab_calib",
        "lab_practice",
        "ascend_utils",
        "precision_tool",
        "tools",
    )
    #: 可纳入 PR 检测的测试根目录（单数 test/）
    TEST_ROOTS = ("test/",)

    # ------------------------------------------------------------------
    # 覆盖率数据处理
    # ------------------------------------------------------------------
    def is_test_case_dir(self, name: str) -> bool:
        return name.startswith("tests__")

    def normalize_test_name(self, test_name: str) -> str:
        # 先转换 -- 为 ::（函数级测试标记）
        result = test_name.replace("--", "::")
        # 再转换 __ 为 /
        result = result.replace("__", "/")
        # 无 ::（文件级测试）且非 .py 后缀则补 .py
        if "::" not in result and not result.endswith(".py"):
            result += ".py"
        return result

    def covered_path_to_rel(self, path: str) -> str | None:
        """覆盖率库绝对路径按 PRODUCT_ROOTS 边界剥离，得到相对路径；非产品代码返回 None。"""
        for root in self.PRODUCT_ROOTS:
            marker = f"/{root}/"
            idx = path.find(marker)
            if idx != -1:
                return path[idx + 1:]
        return None

    # ------------------------------------------------------------------
    # 测试文件检测（PR 内容检测）
    # ------------------------------------------------------------------
    def is_test_file(self, path: str) -> bool:
        return path.startswith(self.TEST_ROOTS) and fnmatchcase(PurePosixPath(path).name, "test_*.py")

    def is_product_code_path(self, path: str) -> bool:
        """多根产品代码判定：path 处于任一 PRODUCT_ROOTS 顶层目录内。"""
        return any(path == root or path.startswith(f"{root}/") for root in self.PRODUCT_ROOTS)

    # ------------------------------------------------------------------
    # 路径解析
    # ------------------------------------------------------------------
    def resolve_source_file(self, source_dir: Path, rel_path: str) -> Path | None:
        """源码位于 covstub 顶层（无 msmodelslim/ 子目录），直接 source_dir/rel_path。"""
        source_path = source_dir / rel_path
        return source_path if source_path.exists() else None
