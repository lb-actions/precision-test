"""vllm_ascend 仓库适配器。

- REPO_NAME = "vllm_ascend"，产品代码前缀 "vllm_ascend/"
- 测试用例目录：tests__ 前缀目录 + cpu-ut，覆盖率文件位于 covdata/ 子目录
- 测试文件规则：tests/e2e/pull_request/ 或 tests/ut/ 下 test_*.py
- csrc 目录变更（非 .md）触发全量测试套件
- 测试名规范化：-- -> ::，__ -> /，文件级补 .py
"""

from fnmatch import fnmatchcase
from pathlib import PurePosixPath

from .base import RepoAdapter


class VllmAscendAdapter(RepoAdapter):
    """vllm_ascend 仓库适配器。"""

    repo_name = "vllm_ascend"
    product_code_prefix = "vllm_ascend/"
    test_case_dir_prefix = None

    #: 可纳入 PR 检测的测试根目录
    TEST_ROOTS = ("tests/e2e/pull_request/", "tests/ut/")

    # ------------------------------------------------------------------
    # 覆盖率数据处理
    # ------------------------------------------------------------------
    def is_test_case_dir(self, name: str) -> bool:
        return name.startswith("tests__") or name == "cpu-ut"

    def normalize_test_name(self, test_name: str) -> str:
        """
        将测试用例目录名转换为标准脚本名：
        - tests__e2e__... -> tests/e2e/... (文件级) -> tests/e2e/....py (补 .py 后缀)
        - tests__e2e__...--test_foo -> tests/e2e/...::test_foo (函数级，不补 .py)
        - cpu-ut -> cpu-ut (保持不变)
        """
        if test_name == "cpu-ut":
            return test_name
        # 先转换 -- 为 ::（函数级测试标记）
        result = test_name.replace("--", "::")
        # 再转换 __ 为 /
        result = result.replace("__", "/")
        # 无 ::（文件级测试）则补 .py 后缀
        if "::" not in result:
            result = result + ".py"
        return result

    def covered_path_to_rel(self, path: str) -> str | None:
        """覆盖率库路径包含 vllm_ascend/ 时剥离前缀得到相对路径，否则返回 None。"""
        if self.repo_name not in path:
            return None
        marker = f"{self.repo_name}/"
        return path.split(marker)[-1] if marker in path else path

    # ------------------------------------------------------------------
    # 测试文件检测（PR 内容检测）
    # ------------------------------------------------------------------
    def is_test_file(self, path: str) -> bool:
        return path.startswith(self.TEST_ROOTS) and fnmatchcase(PurePosixPath(path).name, "test_*.py")

    def has_full_suite_changes(self, diff_content: str) -> bool:
        """vllm: csrc 目录（非 .md）变更触发全量测试套件。"""
        import regex as re

        # 匹配 +++ b/csrc/xxx.cpp 或 --- a/csrc/xxx.cpp，排除 .md 变更
        csrc_pattern = re.compile(
            r"^(?:\+{3} [ab]/|-{3} a/)csrc/(?!.*\.md$)",
            re.MULTILINE | re.IGNORECASE,
        )
        if csrc_pattern.search(diff_content):
            print("  CSRC directory changes detected in PR diff")
            return True
        return False
