"""verl 仓库适配器（https://github.com/volcengine/verl）。

- 注册键 "verl"；repo_name = "verl"（covstub 布局 covstub/verl/...，产品代码前缀 "verl/"）
- 覆盖率库路径为 CI workspace 安装形态：/__w/verl/verl/verl/xxx.py，
剥离包前缀后得到相对路径 xxx.py（与 product_code_prefix "verl/" 约定一致）
- 测试用例目录：workflows/<workflow>.yml（一个 workflow yml 即一个用例），
覆盖率文件位于其下 <job>/coverage/step-*/coverage.*，需要递归发现
- 测试文件规则：.github/workflows/ 下的 yml/yaml（workflow 本身即用例）
- 测试名规范化：workflow yml 文件名（如 e2e_ascend.yml），PR 检测路径经同一
normalize_test_name 归一，保证 map 用例名与 PR 新增用例名形态一致
"""

import re
from pathlib import Path, PurePosixPath

from .base import RepoAdapter


class VerlAdapter(RepoAdapter):
    """verl 仓库适配器。"""

    repo_name = "verl"
    product_code_prefix = "verl/"

    #: 覆盖率文件名形如 coverage.linux-aarch64-...-workflow.<pid>.<rand>；
    #: 用 "coverage.*" 精确匹配，避免误匹配 job 目录下的 coveragerc 配置
    coverage_file_glob = "coverage.*"

    #: 精确匹配 CI 安装形态中的 /verl/verl/verl/ 段（workspace/仓库/包），
    #: 剥离到包根目录，得到与 product_code_prefix 一致的相对路径（如 protocol.py）
    _PKG_PATH_RE = re.compile(r"(?:^|/)verl/verl/verl/(.+)")

    #: 可纳入 PR 检测的测试根目录（workflow yml 即测试用例）
    TEST_ROOTS = (".github/workflows/",)

    # ------------------------------------------------------------------
    # 覆盖率数据处理
    # ------------------------------------------------------------------
    def coverage_files_for(self, test_case_dir: Path) -> list[Path]:
        """递归发现 workflow 目录下所有 job/step 的覆盖率文件。"""
        return sorted(p for p in test_case_dir.rglob(self.coverage_file_glob) if p.is_file())

    def is_test_case_dir(self, name: str) -> bool:
        # 用例目录名即 workflow 文件名：e2e_ascend.yml / model_ascend.yml
        return name.endswith((".yml", ".yaml"))

    def normalize_test_name(self, test_name: str) -> str:
        """workflow yml 文件名即标准用例名（如 e2e_ascend.yml）。

        取 basename，兼容覆盖率根目录传入 workflows 的父目录时
        用例目录为 workflows/e2e_ascend.yml 的情况。
        """
        return PurePosixPath(test_name).name

    def covered_path_to_rel(self, path: str) -> str | None:
        """将 /__w/verl/verl/verl/xxx.py 转换为包相对路径 xxx.py。"""
        match = self._PKG_PATH_RE.search(path)
        if match:
            return match.group(1)
        # 已带产品代码前缀（verl/xxx.py）时剥离前缀
        if path.startswith(self.product_code_prefix):
            return path[len(self.product_code_prefix):]
        return None

    # ------------------------------------------------------------------
    # 测试文件检测（PR 内容检测）
    # ------------------------------------------------------------------
    def is_test_file(self, path: str) -> bool:
        return path.startswith(self.TEST_ROOTS) and path.endswith((".yml", ".yaml"))

    def normalize_pr_test_file(self, path: str) -> str:
        """PR 检测路径转换为 workflow 文件名，与 map 用例名形态一致。"""
        return PurePosixPath(path).name
