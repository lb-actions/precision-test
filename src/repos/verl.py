"""verl 仓库适配器（https://github.com/verl-project/verl）。

verl（Volcano Engine Reinforcement Learning）产品代码位于仓库根的 verl/ 包。

覆盖率数据特征（coverage.py v7 SQLite 格式，GitHub Actions 采集）：
- 覆盖率根目录下按 workflow yml 文件名建目录（e2e_ascend.yml/ 等），
  层级为 <yml>/<job>/<coverage/step-XX>/coverage.linux-...（多层嵌套，
  需要 recursive_cov_scan 递归扫描）
- 每个 yml 目录即一个"测试用例"：map 的 key 为 yml 文件名（如 e2e_ascend.yml），
  value 为该 yml 目录下所有覆盖率数据的并集
- 覆盖率库路径形如 /__w/verl/verl/verl/protocol.py：
  /__w/verl/verl 为 GitHub Actions 工作区+仓库目录，最后的 verl/ 为产品包，
  剥离后得到包内相对路径（如 trainer/main_ppo.py）
- job 目录下存在名为 coveragerc 的纯文本配置文件，coverage_file_glob
  必须用 "coverage.*"（带点）避免误匹配

- repo_name = "verl"，product_code_prefix = "verl/"，map 的 key 为剥离包前缀后的
  相对路径（如 trainer/main_ppo.py、protocol.py）
- 源文件解析 source_dir/verl/<rel>（-s 指向含 verl/ 目录的源码根），沿用基类默认实现
- PR 推荐：PR 改动的产品代码命中的 yml 即推荐结果（yml 列表）；
  PR 改动 .github/workflows/*.yml 本身时直接推荐对应 yml
"""

import re
from pathlib import PurePosixPath

from .base import RepoAdapter


class VerlAdapter(RepoAdapter):
    """verl 仓库适配器。"""

    repo_name = "verl"
    product_code_prefix = "verl/"
    test_case_dir_prefix = None
    #: 带点匹配，规避 job 目录下的 coveragerc 纯文本配置文件
    coverage_file_glob = "coverage.*"
    #: 覆盖率文件嵌套在 <yml>/<job>/coverage/step-XX/ 多层目录下，需递归扫描
    recursive_cov_scan = True

    #: 覆盖率库路径 /__w/verl/verl/verl/xxx.py -> 剥离工作区+仓库目录+产品包三层前缀
    _COV_PATH_RE = re.compile(r"(?:^|/)verl/verl/verl/(.+)$")

    #: workflow yml 在仓库内的目录（PR 改动 yml 本身时直接推荐对应 yml）
    WORKFLOW_DIR = ".github/workflows/"

    # ------------------------------------------------------------------
    # 覆盖率数据处理
    # ------------------------------------------------------------------
    def is_test_case_dir(self, name: str) -> bool:
        """覆盖率根目录下以 workflow yml/yaml 文件名命名的目录（如 e2e_ascend.yml）。"""
        return name.endswith((".yml", ".yaml"))

    def normalize_test_name(self, test_name: str) -> str:
        """测试用例目录名即 yml 文件名（e2e_ascend.yml），无需转换。"""
        return test_name

    def covered_path_to_rel(self, path: str) -> str | None:
        """剥离 /__w/verl/verl/ 工作区前缀与 verl/ 产品包前缀，得到包内相对路径；非产品代码返回 None。"""
        match = self._COV_PATH_RE.search(path)
        return match.group(1) if match else None

    # ------------------------------------------------------------------
    # 测试文件检测（PR 内容检测）
    # ------------------------------------------------------------------
    def is_test_file(self, path: str) -> bool:
        """PR 改动 workflow yml 本身视为"测试变更"：直接推荐对应 yml。

        verl 的推荐粒度是 workflow yml（而非测试脚本），tests/ 下新增测试脚本
        无法通过覆盖率映射到具体 yml，不做处理。
        """
        p = PurePosixPath(path)
        return (
            path.startswith(self.WORKFLOW_DIR)
            and p.suffix in (".yml", ".yaml")
        )

    def normalize_recommended_name(self, name: str) -> str:
        """.github/workflows/e2e_ascend.yml -> e2e_ascend.yml（与 map 的 key 对齐）。"""
        return name.removeprefix(self.WORKFLOW_DIR)
