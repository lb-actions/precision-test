"""sglang-kernel 仓库适配器（https://github.com/sgl-project/sglang-kernel）。

- 注册键 "sglang-kernel"；REPO_NAME = "sgl_kernel_npu"（Python 包名，covstub 布局 covstub/sgl_kernel_npu/...，
  对应 CI 中 cp -r sgl_kernel_npu covstub/），产品代码前缀 "sgl_kernel_npu/"
- 覆盖率库路径为 site-packages 安装形态：/usr/local/python3.x/.../sgl_kernel_npu/operators/xxx.py，
  diff 路径为仓库内 sgl_kernel_npu/operators/xxx.py
- 测试用例目录：嵌套于 tests/ 下、目录名以 .py 结尾（如 tests/python/operators/norm/test_layer_norm_fwd.py），
  覆盖率文件位于 covdata/ 子目录
- 测试文件规则：tests/ 下 test_*.py
- 测试名规范化：目录相对路径即仓库相对测试路径，保持不变（函数级 -- -> ::，参数化按 [ 合并）
"""

import re
from fnmatch import fnmatchcase
from pathlib import PurePosixPath

from .base import RepoAdapter


class SglangKernelAdapter(RepoAdapter):
    """sglang-kernel 仓库适配器。"""

    repo_name = "sgl_kernel_npu"
    product_code_prefix = "sgl_kernel_npu/"

    #: 精确匹配路径中独立的 sgl_kernel_npu/ 段（前为路径起点或 /），
    #: 避免含 sgl_kernel_npu 子串的其他路径被误剥
    _PKG_PATH_RE = re.compile(r"(?:^|/)sgl_kernel_npu/(.+)")

    #: 可纳入 PR 检测的测试根目录
    TEST_ROOTS = ("tests/",)

    # ------------------------------------------------------------------
    # 覆盖率数据处理
    # ------------------------------------------------------------------
    def is_test_case_dir(self, name: str) -> bool:
        # 用例目录名：tests/.../test_foo.py（文件级）或 tests/.../test_foo.py--TestX--test_bar[param]（函数级）
        return name.endswith(".py") or ".py--" in name

    def normalize_test_name(self, test_name: str) -> str:
        """
        将测试用例目录名转换为标准脚本名。
        - tests/python/operators/norm/test_layer_norm_fwd.py（目录相对路径即仓库相对路径，保持不变）
        - tests/.../test_foo.py--TestX--test_bar -> tests/.../test_foo.py::TestX::test_bar（函数级）
        """
        return test_name.replace("--", "::")

    def covered_path_to_rel(self, path: str) -> str | None:
        """覆盖率库路径含独立的 sgl_kernel_npu/ 段时剥离前缀得到相对路径，否则返回 None。"""
        match = self._PKG_PATH_RE.search(path)
        return match.group(1) if match else None

    # ------------------------------------------------------------------
    # 测试文件检测（PR 内容检测）
    # ------------------------------------------------------------------
    def is_test_file(self, path: str) -> bool:
        return path.startswith(self.TEST_ROOTS) and fnmatchcase(PurePosixPath(path).name, "test_*.py")
