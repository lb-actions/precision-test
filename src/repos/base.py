"""RepoAdapter - 仓库差异化抽象接口。

合并架构的核心：所有与具体仓库相关的差异点都通过该接口暴露，
公共代码（diff 解析、噪音过滤、覆盖率选择、CLI 等）只依赖此接口，
不直接引用任何仓库常量，从而保证架构统一、差异按仓库归类。

各仓库只需实现本基类的抽象方法，并把差异化常量配置在子类属性上。
"""

from abc import ABC, abstractmethod
from pathlib import Path


class RepoAdapter(ABC):
    """仓库适配器抽象基类，每个仓库实现一个子类。"""

    #: 仓库名（用于路径规范化与输出展示，如 vllm_ascend / sglang）
    repo_name: str = ""
    #: 产品代码路径前缀（diff / 覆盖率库中的产品代码路径前缀，剥离后得到相对路径）
    product_code_prefix: str = ""
    #: 覆盖率数据目录下的测试用例文件夹命名前缀；None 表示无固定前缀（vllm 用 tests__ 前缀 + cpu-ut）
    test_case_dir_prefix: str | None = None
    #: 覆盖率文件名 glob 模式（测试目录或其 covdata/ 子目录下）。
    #: 默认 "coverage*" 兼容 pytest-cov 裸文件（torch_npu: coverage）与原始格式
    #: 带后缀文件（vllm/sglang: coverage.linux-...-workflow.xxx）。
    coverage_file_glob: str = "coverage*"

    # ------------------------------------------------------------------
    # 覆盖率数据处理
    # ------------------------------------------------------------------
    @abstractmethod
    def is_test_case_dir(self, name: str) -> bool:
        """判断覆盖率数据目录下的子目录名是否是一个测试用例目录。"""
        raise NotImplementedError

    @abstractmethod
    def normalize_test_name(self, test_name: str) -> str:
        """将测试用例目录名转换为标准脚本名（如 tests/e2e/.../test_foo.py 或 test/.../test_foo.py）。"""
        raise NotImplementedError

    @abstractmethod
    def covered_path_to_rel(self, path: str) -> str | None:
        """将覆盖率库中的文件路径转换为仓库相对路径；非产品代码路径返回 None。"""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # 测试文件检测（PR 内容检测）
    # ------------------------------------------------------------------
    @abstractmethod
    def is_test_file(self, path: str) -> bool:
        """判断一个仓库内路径是否是应纳入 PR 检测的测试文件。"""
        raise NotImplementedError

    def has_full_suite_changes(self, diff_content: str) -> bool:
        """diff 中是否存在触发全量测试套件的变更（如 vllm 的 csrc 目录）。

        默认返回 False（不触发全量测试）；需要全量触发的仓库覆写此方法。
        """
        return False

    # ------------------------------------------------------------------
    # 路径规范化 / 解析
    # ------------------------------------------------------------------
    def strip_product_prefix(self, path: str) -> str:
        """剥离产品代码路径前缀，得到仓库相对路径（无前缀时原样返回）。"""
        prefix = self.product_code_prefix
        return path[len(prefix):] if prefix and path.startswith(prefix) else path

    def normalize_changed_path(self, path: str) -> str:
        """规范化变更文件路径：优先剥离 PRODUCT_PREFIX，其次剥离 '{repo_name}/' 前缀。

        兼容两种覆盖率库路径风格（sglang: python/sglang/...；vllm: vllm_ascend/...），
        统一到与 test_case_map.json 的 key 一致的相对路径。
        """
        if self.product_code_prefix and path.startswith(self.product_code_prefix):
            return path[len(self.product_code_prefix):]
        prefix = f"{self.repo_name}/"
        if path.startswith(prefix):
            return path[len(prefix):]
        return path

    def resolve_source_file(self, source_dir: Path, rel_path: str) -> Path | None:
        """解析仓库相对路径到磁盘上的源文件（source_dir/<repo_name>/<rel_path>）。"""
        source_path = source_dir / self.repo_name / rel_path
        return source_path if source_path.exists() else None

    # ------------------------------------------------------------------
    # 哈希比对扫描根
    # ------------------------------------------------------------------
    def hash_scan_root(self, source_dir: Path) -> Path:
        """哈希比对扫描根目录。默认扫描整个 source_dir。"""
        return source_dir

    # ------------------------------------------------------------------
    # CLI 默认值
    # ------------------------------------------------------------------
    def cli_defaults(self) -> dict:
        """CLI 参数默认值（统一参数集，按仓库提供默认值）。"""
        return {
            "source_dir": "covstub",
            "map_file": "test_case_map.json",
            "coverage_dir": "coverage",
            "min_affected": 1,
            "enable_line_match": True,
            "enable_function_match": True,
            "skip_imports": False,
            "dedup": False,
        }
