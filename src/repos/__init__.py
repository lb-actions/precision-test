"""仓库适配器注册表：按仓库名获取对应适配器实例。"""

from .ascend_pytorch import AscendPytorchAdapter
from .base import RepoAdapter
from .msmodeling import MsmodelingAdapter
from .msmodelslim import MsmodelslimAdapter
from .sglang import SglangAdapter
from .sglang_kernel import SglangKernelAdapter
from .pytorch import PytorchAdapter
from .transformer_engine import TransformerEngineAdapter
from .vllm_ascend import VllmAscendAdapter

__all__ = [
    "RepoAdapter",
    "VllmAscendAdapter",
    "SglangAdapter",
    "SglangKernelAdapter",
    "PytorchAdapter",
    "AscendPytorchAdapter",
    "MsmodelingAdapter",
    "MsmodelslimAdapter",
    "TransformerEngineAdapter",
    "get_adapter",
    "AVAILABLE_REPOS",
]

#: 已注册的适配器
_ADAPTERS = {
    "vllm_ascend": VllmAscendAdapter,
    "sglang": SglangAdapter,
    "sglang-kernel": SglangKernelAdapter,
    "pytorch": PytorchAdapter,
    "torch_npu": AscendPytorchAdapter,
    "msmodeling": MsmodelingAdapter,
    "msmodelslim": MsmodelslimAdapter,
    "transformer_engine": TransformerEngineAdapter,
}

AVAILABLE_REPOS = tuple(_ADAPTERS.keys())


def get_adapter(repo_name: str) -> RepoAdapter:
    """按仓库名获取适配器实例。

    Args:
        repo_name: 仓库名（vllm_ascend / sglang / sglang-kernel / pytorch / torch_npu /
            msmodeling / msmodelslim / transformer_engine）

    Returns:
        RepoAdapter 实例

    Raises:
        ValueError: 仓库名未注册时
    """
    cls = _ADAPTERS.get(repo_name)
    if cls is None:
        raise ValueError(f"Unknown repo '{repo_name}', available: {', '.join(AVAILABLE_REPOS)}")
    return cls()
