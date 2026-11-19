"""torch_npu 本体仓库适配器（检测 Ascend/pytorch：https://gitcode.com/Ascend/pytorch）。

- REPO_NAME = "torch_npu"，产品代码前缀 "torch_npu/"，PR 源为 GitCode（--gitcode-pr Ascend/pytorch#N）
- Ascend/pytorch 是 pytorch/pytorch 的 fork + NPU 补丁，仅 torch_npu/ 下的
  Python 产品代码纳入精准匹配（fork 的 torch/ 上游代码与原生 C++/CUDA 不解析）
- 测试用例目录、covdata/ 布局、测试文件规则与上游版（pytorch）一致
- 源文件解析：source_dir/torch_npu/<rel>（运行需 -s 指向 covstub，
  covstub/torch_npu/ 对应 Ascend/pytorch 仓库 torch_npu/ 目录内容）
"""

import re

from .pytorch import PytorchAdapter


class AscendPytorchAdapter(PytorchAdapter):
    """Ascend/pytorch（torch_npu 本体）仓库适配器。规则与上游版一致，仅前缀不同。"""

    repo_name = "torch_npu"
    product_code_prefix = "torch_npu/"

    #: 精确匹配路径中独立的 torch_npu/ 段（前为路径起点或 /），
    #: 避免与其他含 torch_npu 子串的路径混淆
    _TORCH_PATH_RE = re.compile(r"(?:^|/)torch_npu/(.+)")
