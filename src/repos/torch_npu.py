"""torch_npu 仓库适配器（检测上游 PyTorch：https://github.com/pytorch/pytorch）。

- REPO_NAME = "torch"，产品代码前缀 "torch/"，PR 源为 GitHub（--github-pr pytorch/pytorch#N）
- 测试用例目录：目录名含 "__" 或以 "test_" 开头（如 _inductor__test_add），
  覆盖率文件位于 covdata/ 子目录
- 测试文件规则：test/ 下 test_*.py
- 测试名规范化：-- -> ::，__ -> /，文件级补 .py
- 粒度默认值跟随合并版：line=True, function=True
- 源文件解析使用基类默认候选（source_dir/torch/<rel>，运行需 -s 指向 covstub，
  covstub/torch/ 对应 pytorch/pytorch 仓库根目录）
"""

import re
from fnmatch import fnmatchcase
from pathlib import PurePosixPath

from .base import RepoAdapter


class TorchNpuAdapter(RepoAdapter):
    """torch_npu 仓库适配器。"""

    repo_name = "torch"
    product_code_prefix = "torch/"
    test_case_dir_prefix = None

    #: 精确匹配路径中独立的 torch/ 段（前为路径起点或 /），
    #: 避免 pytorch/、torchvision/、test_torch.py 等含 torch 子串的路径被误剥
    _TORCH_PATH_RE = re.compile(r"(?:^|/)torch/(.+)")

    #: 可纳入 PR 检测的测试根目录
    TEST_ROOTS = ("test/",)

    # ------------------------------------------------------------------
    # 覆盖率数据处理
    # ------------------------------------------------------------------
    def is_test_case_dir(self, name: str) -> bool:
        # PyTorch: "__" in name or name.startswith("test_")
        return name.endswith(".py") or ("__" in name) or name.startswith("test_")

    def normalize_test_name(self, test_name: str) -> str:
        """
        将测试用例目录名转换为标准脚本名：
        - test__xxx__... -> test/xxx/... (文件级) -> test/xxx/....py (补 .py 后缀)
        - test__xxx__...--test_foo -> test/xxx/...::test_foo (函数级，不补 .py)
        """
        # 先转换 -- 为 ::（函数级测试标记）
        result = test_name.replace("--", "::")
        if result.endswith(".py") or (".py::" in result):
            if not result.startswith("test/"):
                result = f"test/{result}"
            return result
        return result

    def covered_path_to_rel(self, path: str) -> str | None:
        """覆盖率库路径含独立的 torch/ 段时剥离前缀得到相对路径，否则返回 None。"""
        match = self._TORCH_PATH_RE.search(path)
        return match.group(1) if match else None

    # ------------------------------------------------------------------
    # 测试文件检测（PR 内容检测）
    # ------------------------------------------------------------------
    def is_test_file(self, path: str) -> bool:
        return path.startswith(self.TEST_ROOTS) and fnmatchcase(PurePosixPath(path).name, "test_*.py")
