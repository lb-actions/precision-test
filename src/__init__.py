"""test_selector - 覆盖率驱动的精准测试选择器（vllm_ascend / sglang / torch_npu 统一架构）。

公共逻辑集中在 test_selector/ 包（diff 解析、噪音过滤、覆盖率选择、CLI 等），
仓库差异通过 test_selector.repos.RepoAdapter 注入，保证架构统一、差异按仓库归类。

入口：
    python -m test_selector --repo <vllm_ascend|sglang|torch_npu> ...
"""

from .repos import AVAILABLE_REPOS, RepoAdapter, get_adapter
from .test_selector import TestSelector

__all__ = ["RepoAdapter", "TestSelector", "get_adapter", "AVAILABLE_REPOS"]
__version__ = "1.0.0"
