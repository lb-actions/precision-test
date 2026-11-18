"""TransformerEngineNPU 仓库适配器（Ascend/TransformerEngineNPU）。

TransformerEngineNPU 是 NVIDIA/TransformerEngine 的 Ascend fork，产品代码位于
仓库根的 transformer_engine/ 包（common/ + pytorch/ 等子模块）。

覆盖率数据特征（coverage.py v7 格式）：
- 测试目录：covdata/ 下平铺，命名 tests__pytorch__attention__test_attention.py.test_xxx[param]
  （tests__ 前缀，__ 为路径分隔符，脚本名后跟 .函数名[param]）
- 每个测试目录下直接放置单个 .coverage SQLite 文件（coverage_file_glob 需匹配点开头文件）
- 行数据存储在 line_bits 位图表（arc 表为空），由 CoverageSelector 的 line_bits 回退逻辑解码

- repo_name = "transformer_engine"，product_code_prefix = "transformer_engine/"
- 覆盖率库路径为 /home/.../ci-ws/CODE/transformer_engine/xxx.py，按独立的
  transformer_engine/ 段剥离前缀
- 源文件解析 source_dir/transformer_engine/<rel>（-s 指向含 transformer_engine/
  目录的源码根，即仓库根目录本身），沿用基类默认实现
"""

import re
from fnmatch import fnmatchcase
from pathlib import PurePosixPath

from .base import RepoAdapter


class TransformerEngineAdapter(RepoAdapter):
    """TransformerEngineNPU 仓库适配器。"""

    repo_name = "transformer_engine"
    product_code_prefix = "transformer_engine/"
    test_case_dir_prefix = "tests__"
    #: .coverage 为点开头文件，默认 "coverage*" 匹配不到
    coverage_file_glob = ".coverage*"

    #: 精确匹配路径中独立的 transformer_engine/ 段（前为路径起点或 /），
    #: 避免其他含 transformer_engine 子串的路径被误剥
    _TE_PATH_RE = re.compile(r"(?:^|/)transformer_engine/(.+)")

    #: 测试目录名 = 脚本路径（tests__xxx.py）+ 可选的 ".函数名[param]" 后缀
    _TEST_NAME_RE = re.compile(r"^(?P<path>tests__(?:.+)\.py)(?:\.(?P<func>.+))?$")

    #: 可纳入 PR 检测的测试根目录
    TEST_ROOTS = ("tests/",)

    # ------------------------------------------------------------------
    # 覆盖率数据处理
    # ------------------------------------------------------------------
    def is_test_case_dir(self, name: str) -> bool:
        return name.startswith("tests__")

    def normalize_test_name(self, test_name: str) -> str:
        """
        将测试用例目录名转换为标准脚本名：
        - tests__pytorch__test_recipe.py -> tests/pytorch/test_recipe.py（文件级）
        - tests__pytorch__attention__test_attention.py.test_dpa_fp8_vs_f16[...]
          -> tests/pytorch/attention/test_attention.py::test_dpa_fp8_vs_f16[...]（函数级）
        """
        match = self._TEST_NAME_RE.match(test_name)
        if match is None:
            return test_name
        path = match.group("path").replace("__", "/")
        func = match.group("func")
        return f"{path}::{func}" if func else path

    def covered_path_to_rel(self, path: str) -> str | None:
        """覆盖率库路径含独立的 transformer_engine/ 段时剥离前缀得到相对路径，否则返回 None。"""
        match = self._TE_PATH_RE.search(path)
        return match.group(1) if match else None

    # ------------------------------------------------------------------
    # 测试文件检测（PR 内容检测）
    # ------------------------------------------------------------------
    def is_test_file(self, path: str) -> bool:
        return path.startswith(self.TEST_ROOTS) and fnmatchcase(PurePosixPath(path).name, "test_*.py")
