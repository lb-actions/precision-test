"""GitCode PR 拉取公共模块。

与 GitHub 版（github.py）的差异：
- GitHub:  直连 github.com/{repo}/pull/{n}.diff 公开端点下载 unified diff；PR 详情与 base 内容走 REST API（单路无兜底，失败即退出）
- GitCode: 优先通过公开 .diff 端点（gitcode.com/{repo}/pull/{n}.diff）获取原生
  unified diff（自带 rename/delete/new-file 标记，无需 token、不占 REST 配额）；
  失败时兜底用 /pulls/{n}/files API（分页拉取）手动拼接 unified diff，
  并按 patch 中的 new_file/deleted_file/renamed_file 字段合成 git 标记；
  base 内容通过 raw.gitcode.com + base.sha 获取

无论哪条路径，产出的 diff 格式与 GitHub 一致（diff --git / --- a/xxx / +++ b/xxx /
@@ hunk / rename from/to 标记），因此下游 diff 解析（diff_parser 噪音分类、
detect_renames 文件级匹配）完全复用公共逻辑，不感知数据源差异。

提供：
- parse_pr_spec: 解析 owner/repo#pr_number 格式
- fetch_pr_diff: 拉取 PR diff 并保存到临时文件，返回 (diff_file, base_content_getter)
"""

import json
import os
import re
import ssl
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
from typing import Callable


def parse_pr_spec(pr_spec: str) -> tuple[str, str]:
    """解析 PR 标识，返回 (owner/repo, pr_number)。

    支持两种格式：
    - owner/repo#pr_number
    - pr_number（尝试从 git remote origin 推导 owner/repo）
    """
    repo = None
    pr_num = None

    if "#" in pr_spec:
        parts = pr_spec.split("#")
        repo = parts[0]
        pr_num = parts[1]
    else:
        pr_num = pr_spec
        # 尝试获取当前仓库
        try:
            result = subprocess.run(["git", "remote", "get-url", "origin"], capture_output=True, text=True)
            if result.returncode == 0:
                url = result.stdout.strip()
                if "gitcode.com" in url:
                    match = re.search(r"gitcode\.com[/:]([^/]+/[^/]+?)(?:\.git)?$", url)
                    if match:
                        repo = match.group(1)
        except Exception as e:
            print(e)

    if not repo or not pr_num:
        raise ValueError("Cannot parse PR info, please use owner/repo#pr_number format")
    return repo, pr_num


def _ssl_context() -> ssl.SSLContext:
    """创建不校验 SSL 证书的上下文。"""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _gitcode_request(url: str, gitcode_token: str | None, ssl_context) -> str:
    """发起 GitCode API 请求并返回响应文本。

    GitCode 认证方式：access_token 作为查询参数（与 GitHub 的 Bearer header 不同）。
    """
    if gitcode_token:
        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}access_token={urllib.parse.quote(gitcode_token)}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60, context=ssl_context) as response:
        return response.read().decode("utf-8")


def _build_unified_diff(files_data: list[dict]) -> str:
    """将 GitCode /pulls/{n}/files 返回的文件列表拼接为 unified diff。

    GitCode 每个文件的 patch 是 dict：{diff, old_path, new_path, new_file,
    deleted_file, renamed_file, too_large, ...}，其中 diff 是以 '@@ hunk'
    开头的片段（不含 '--- a/' 与 '+++ b/' 头），需要手动补头拼接。

    必须按字段合成 git 标记，否则下游链路会失效：
    - rename from/to  -> detect_renames 改名文件走文件级匹配
    - +++ /dev/null   -> 删除文件走文件级 + 被删测试文件从推荐列表剔除
    - --- /dev/null   -> 新增文件识别

    patch.diff 为空时的处理：
    - 纯改名/删除/新增：仍合成完整 git 标记（无 hunk），保住文件级信号
    - 二进制/纯 mode 变更：显式跳过（本就无行级变更）
    - too_large（API 扣留 patch 内容）：打印警告，其行级变更不包含在拼接结果中

    拼接结果与 GitHub diff 格式一致，从而下游 diff_parser 的噪音分类逻辑完全复用。
    """
    diff_blocks = []
    for f in files_data:
        filename = f.get("filename", "")
        patch_data = f.get("patch", {})

        diff_content = ""
        if isinstance(patch_data, dict):
            diff_content = patch_data.get("diff", "")
        elif isinstance(patch_data, str):
            diff_content = patch_data

        # 归一化 diff 片段内的路径头（GitCode 可能用 old_path/new_path 标记 rename）
        old_path = (patch_data.get("old_path") or filename) if isinstance(patch_data, dict) else filename
        new_path = (patch_data.get("new_path") or filename) if isinstance(patch_data, dict) else filename

        # 剔除路径可能带的 a/ b/ 前缀（与 GitHub diff 头格式对齐）
        if old_path.startswith("a/"):
            old_path = old_path[2:]
        if new_path.startswith("b/"):
            new_path = new_path[2:]

        renamed = bool(isinstance(patch_data, dict) and patch_data.get("renamed_file"))
        deleted = bool(isinstance(patch_data, dict) and patch_data.get("deleted_file"))
        added = bool(isinstance(patch_data, dict) and patch_data.get("new_file"))
        too_large = bool((isinstance(patch_data, dict) and patch_data.get("too_large")) or f.get("too_large"))

        if not diff_content:
            if not (renamed or deleted or added):
                # 二进制/纯 mode 变更本就没有行级变更：显式说明后跳过；
                # too_large 则警告该文件的行级变更未被拼接（不可据此认为无变更）
                if too_large:
                    print(f"  Warning: {filename}: patch withheld by GitCode API (too large); "
                          f"its line-level changes are NOT in the stitched diff")
                else:
                    print(f"  Skipping {filename}: binary or mode-only change (no line-level diff)")
                continue
            # 纯改名/删除/新增（如 100% 相似度改名、超限文件）即使无 hunk 内容也必须
            # 合成 git 标记：rename from/to 与 '+++ /dev/null' 是文件级匹配与被删
            # 测试文件剔除的唯一信号，缺失即召回丢失
            if too_large:
                print(f"  Warning: {filename}: patch withheld by GitCode API (too large); "
                      f"only file-level markers are reconstructed")

        if renamed or deleted or added:
            diff_blocks.append(f"diff --git a/{old_path} b/{new_path}")
        if added:
            diff_blocks.append("new file mode 100644")
        if deleted:
            diff_blocks.append("deleted file mode 100644")
        if renamed:
            diff_blocks.append(f"rename from {old_path}")
            diff_blocks.append(f"rename to {new_path}")

        old_header = "--- /dev/null" if added else f"--- a/{old_path}"
        new_header = "+++ /dev/null" if deleted else f"+++ b/{new_path}"
        diff_blocks.append(old_header)
        diff_blocks.append(new_header)
        diff_blocks.append(diff_content)
        diff_blocks.append("")  # 文件间空行

    return "\n".join(diff_blocks)


def _fetch_pr_files(
    base_url: str,
    gitcode_token: str | None,
    ssl_context,
) -> list[dict]:
    """分页拉取 PR 文件列表。

    v5 API 按 page/per_page 分页，不循环拉全时文件数超过一页会静默漏文件。
    """
    files_data: list[dict] = []
    page = 1
    while True:
        files_json = _gitcode_request(f"{base_url}/files?page={page}&per_page=100", gitcode_token, ssl_context)
        page_data = json.loads(files_json)
        if not isinstance(page_data, list) or not page_data:
            break
        files_data.extend(page_data)
        if len(page_data) < 100:
            break
        page += 1
    return files_data


def fetch_pr_diff(pr_spec: str) -> tuple[str, Callable[[str], str]]:
    """拉取 GitCode PR 的 diff 内容，保存到临时文件。

    Args:
        pr_spec: PR 标识（owner/repo#pr_number 或 pr_number）

    Returns:
        (diff_file, base_content_getter)
        - diff_file: 保存到临时目录的 diff 文件路径
        - base_content_getter: 通过 raw.gitcode.com + base.sha 获取 base 文件内容的回调
          （单路获取，失败即退出，不允许降级）

    Raises:
        SystemExit: 拉取失败时
    """
    repo, pr_num = parse_pr_spec(pr_spec)
    print(f"Fetching changes from GitCode PR: {repo}#{pr_num}")

    gitcode_token = os.environ.get("GITCODE_TOKEN") or ""
    ssl_context = _ssl_context()

    # 使用跨平台临时目录
    diff_file = os.path.join(tempfile.gettempdir(), "pr_gitcode.diff")
    max_retries = 3
    # 重试间隔 30 秒：边缘限流窗口通常 30-60 秒，1s 间隔的 3 次重试会
    # 全部落在同一被限流窗口内必然失败；30s 让下一次重试等到窗口过后
    retry_wait_seconds = 30
    base_sha = None

    base_url = f"https://api.gitcode.com/api/v5/repos/{repo}/pulls/{pr_num}"

    # 1) 获取 PR 详情（base.sha 用于后续获取 base 文件内容）。
    #    失败即退出，不允许降级：base 内容缺失会导致 AST 分类
    #    退化（候选对两侧计入），精度无法保证
    for attempt in range(1, max_retries + 1):
        print(f"  Attempt {attempt}/{max_retries} to get PR info via GitCode API...")
        try:
            pr_json = _gitcode_request(base_url, gitcode_token, ssl_context)
            pr_data = json.loads(pr_json)
            base_sha = pr_data.get("base", {}).get("sha")
            break
        except Exception as e:
            print(f"  Attempt {attempt} failed: {e}")
            if attempt < max_retries:
                time.sleep(retry_wait_seconds)
    if not base_sha:
        print("  Error: failed to get PR info (base sha) after all attempts, exiting")
        exit(1)

    # 2) 获取 diff：优先公开 .diff 端点（原生 git diff，自带 rename/delete/
    #    new-file 标记，无需 token、不占 REST 配额），失败兜底 /files 拼接
    diff_url = f"https://gitcode.com/{repo}/pull/{pr_num}.diff"
    diff_text = ""
    for attempt in range(1, max_retries + 1):
        print(f"  Attempt {attempt}/{max_retries} to get PR diff via GitCode .diff endpoint...")
        try:
            req = urllib.request.Request(diff_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=60, context=ssl_context) as response:
                diff_text = response.read().decode("utf-8")
            if not diff_text:
                raise ValueError("PR diff is empty")
            print("  Using GitCode .diff endpoint to get diff")
            break
        except Exception as e:
            print(f"  Attempt {attempt} failed: {e}")
            if attempt < max_retries:
                time.sleep(retry_wait_seconds)

    if not diff_text:
        print("  Falling back to /pulls/{n}/files API...")
        for attempt in range(1, max_retries + 1):
            try:
                files_data = _fetch_pr_files(base_url, gitcode_token, ssl_context)
                print(f"  Found {len(files_data)} changed files")
                diff_text = _build_unified_diff(files_data)
                break
            except Exception as e:
                print(f"  Fallback attempt {attempt}/{max_retries} failed: {e}")
                if attempt < max_retries:
                    time.sleep(retry_wait_seconds)
        if not diff_text:
            print("  Error: all diff fetch attempts failed, exiting")
            exit(1)
        print("  Using /pulls/{n}/files API to get diff")

    with open(diff_file, "w", encoding="utf-8") as f:
        f.write(diff_text)

    print(f"  PR diff saved to: {diff_file}")

    def _fetch_base_content(path: str) -> str:
        """获取 base（变更前）文件内容，用于 ast 分类。

        GitCode raw 接口：https://raw.gitcode.com/{repo}/raw/{sha}/{path}。
        仅此单路获取；失败即退出，不允许降级：base 内容缺失会导致
        AST 分类退化（候选对两侧计入），精度无法保证。
        """
        raw_url = f"https://raw.gitcode.com/{repo}/raw/{base_sha}/{urllib.parse.quote(path)}"
        for attempt in range(1, max_retries + 1):
            try:
                req = urllib.request.Request(raw_url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=30, context=ssl_context) as response:
                    return response.read().decode("utf-8", errors="replace")
            except Exception as e:
                print(f"  Attempt {attempt}/{max_retries} to fetch base content for {path} failed: {e}")
                if attempt < max_retries:
                    time.sleep(retry_wait_seconds)
        print(f"  Error: failed to fetch base content for {path} after all attempts, exiting")
        exit(1)

    return diff_file, _fetch_base_content
