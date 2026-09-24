"""仓库适配器注册表：按仓库名获取对应适配器实例。"""

from .base import RepoAdapter
from .sglang import SglangAdapter
from .torch_npu import TorchNpuAdapter
from .vllm_ascend import VllmAscendAdapter

__all__ = ["RepoAdapter", "VllmAscendAdapter", "SglangAdapter", "TorchNpuAdapter", "get_adapter", "AVAILABLE_REPOS"]

#: 已注册的适配器
_ADAPTERS = {
    "vllm_ascend": VllmAscendAdapter,
    "sglang": SglangAdapter,
    "torch_npu": TorchNpuAdapter,
}

AVAILABLE_REPOS = tuple(_ADAPTERS.keys())


def get_adapter(repo_name: str) -> RepoAdapter:
    """按仓库名获取适配器实例。

    Args:
        repo_name: 仓库名（vllm_ascend / sglang / torch_npu）

    Returns:
        RepoAdapter 实例

    Raises:
        ValueError: 仓库名未注册时
    """
    cls = _ADAPTERS.get(repo_name)
    if cls is None:
        raise ValueError(f"Unknown repo '{repo_name}', available: {', '.join(AVAILABLE_REPOS)}")
    return cls()
