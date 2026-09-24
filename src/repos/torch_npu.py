"""torch_npu 仓库适配器（PyTorch / GitCode 社区）。

- REPO_NAME = "torch_npu"，产品代码前缀 "torch_npu/"
- 测试用例目录：目录名含 "__" 或以 "test_" 开头（如 test__xxx 编码路径），
  覆盖率文件位于 covdata/ 子目录
- 测试文件规则：test/ 下 test_*.py
- 测试名规范化：-- -> ::，__ -> /，文件级补 .py
- 粒度默认值跟随合并版：line=True, function=True
- 源文件解析使用基类默认候选（source_dir/torch_npu/<rel>，运行需 -s 指向 covstub）
"""

from fnmatch import fnmatchcase
from pathlib import PurePosixPath

from .base import RepoAdapter


class TorchNpuAdapter(RepoAdapter):
    """torch_npu 仓库适配器。"""

    repo_name = "torch_npu"
    product_code_prefix = "torch_npu/"
    test_case_dir_prefix = None

    #: 可纳入 PR 检测的测试根目录
    TEST_ROOTS = ("test/",)

    # ------------------------------------------------------------------
    # 覆盖率数据处理
    # ------------------------------------------------------------------
    def is_test_case_dir(self, name: str) -> bool:
        # PyTorch: "__" in name or name.startswith("test_")
        return ("__" in name) or name.startswith("test_")

    def normalize_test_name(self, test_name: str) -> str:
        """
        将测试用例目录名转换为标准脚本名：
        - test__xxx__... -> test/xxx/... (文件级) -> test/xxx/....py (补 .py 后缀)
        - test__xxx__...--test_foo -> test/xxx/...::test_foo (函数级，不补 .py)
        """
        # 先转换 -- 为 ::（函数级测试标记）
        result = test_name.replace("--", "::")
        # 再转换 __ 为 /
        result = result.replace("__", "/")
        # 无 ::（文件级测试）则补 .py 后缀
        if "::" not in result:
            result = result + ".py"
        return result

    def covered_path_to_rel(self, path: str) -> str | None:
        """覆盖率库路径包含 torch_npu/ 时剥离前缀得到相对路径，否则返回 None。"""
        if self.repo_name not in path:
            return None
        marker = f"{self.repo_name}/"
        return path.split(marker)[-1] if marker in path else path

    # ------------------------------------------------------------------
    # 测试文件检测（PR 内容检测）
    # ------------------------------------------------------------------
    def is_test_file(self, path: str) -> bool:
        return path.startswith(self.TEST_ROOTS) and fnmatchcase(PurePosixPath(path).name, "test_*.py")
