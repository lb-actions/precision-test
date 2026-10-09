"""GitHub PR 拉取公共模块。

提供：
- parse_pr_spec: 解析 owner/repo#pr_number 格式
- fetch_pr_diff: 拉取 PR diff 并保存到临时文件，返回 (diff_file, base_content_getter)
"""

import base64
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
                if "github.com" in url:
                    match = re.search(r"github\.com[/:]([^/]+/[^/]+?)(?:\.git)?$", url)
                    if match:
                        repo = match.group(1)
        except Exception as e:
            print(e)

    if not repo or not pr_num:
        raise ValueError("Cannot parse PR info, please use owner/repo#pr_number format")
    return repo, pr_num


def _github_request(url: str, github_token: str | None, accept: str = "application/vnd.github.v3+json") -> urllib.request.Request:
    """构造带认证头的 GitHub API 请求。"""
    headers = {"User-Agent": "test-selector/1.0", "Accept": accept}
    if github_token:
        headers["Authorization"] = f"Bearer {github_token}"
    return urllib.request.Request(url, headers=headers)


def _ssl_context() -> ssl.SSLContext:
    """创建不校验 SSL 证书的上下文。"""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def fetch_pr_diff(pr_spec: str) -> tuple[str, Callable[[str], str]]:
    """拉取 PR 的 diff 内容，保存到临时文件。

    Args:
        pr_spec: PR 标识（owner/repo#pr_number 或 pr_number）

    Returns:
        (diff_file, base_content_getter)
        - diff_file: 保存到临时目录的 diff 文件路径
        - base_content_getter: 通过 GitHub contents API 获取 base 文件内容的回调
          （单路获取，失败即退出，不允许降级）

    Raises:
        SystemExit: 拉取失败时
    """
    repo, pr_num = parse_pr_spec(pr_spec)
    print(f"Fetching changes from GitHub PR: {repo}#{pr_num}")

    github_token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    ssl_context = _ssl_context()

    # 使用跨平台临时目录
    diff_file = os.path.join(tempfile.gettempdir(), "pr.diff")
    max_retries = 3
    # 重试间隔 30 秒：边缘限流窗口通常 30-60 秒，1s 间隔的 3 次重试会
    # 全部落在同一被限流窗口内必然失败；30s 让下一次重试等到窗口过后
    retry_wait_seconds = 30
    base_sha = None

    # 1) 获取 PR 详情（base.sha 用于后续获取 base 文件内容）。
    #    失败即退出，不允许降级：base 内容缺失会导致 AST 分类
    #    退化（候选对两侧计入），精度无法保证
    for attempt in range(1, max_retries + 1):
        print(f"  Attempt {attempt}/{max_retries} to get PR info via GitHub API...")
        try:
            pr_url = f"https://api.github.com/repos/{repo}/pulls/{pr_num}"
            req = _github_request(pr_url, github_token)
            with urllib.request.urlopen(req, timeout=30, context=ssl_context) as response:
                pr_data = json.loads(response.read().decode())
                base_sha = pr_data.get("base", {}).get("sha")
            break
        except Exception as e:
            print(f"  Attempt {attempt} failed: {e}")
            if attempt < max_retries:
                time.sleep(retry_wait_seconds)
    if not base_sha:
        print("  Error: failed to get PR info (base sha) after all attempts, exiting")
        exit(1)

    # 2) 下载 diff：直连公开 .diff 端点（与 API 返回的 diff_url 相同，
    #    但不占 REST API 配额，限流时仍可用）
    #diff_url = f"https://github.com/{repo}/pull/{pr_num}.diff"
    diff_url = f"https://api.github.com/repos/{repo}/pulls/{pr_num}"
    for attempt in range(1, max_retries + 1):
        print(f"  Attempt {attempt}/{max_retries} to get PR diff via GitHub API (diff media type)...")
        try:
            # 二进制模式避免行尾转换；API diff media type返回text/plain，
            req = _github_request(diff_url, github_token, accept="application/vnd.github.v3.diff")
            with urllib.request.urlopen(req, timeout=60, context=ssl_context) as response:
                diff_bytes = response.read()
            if not diff_bytes:
                raise Exception("PR diff is empty")
            with open(diff_file, "wb") as f:
                f.write(diff_bytes)
            print("  Using GitHub API diff media type to get diff")
            break
        except Exception as e:
            print(f"  Attempt {attempt} failed: {e}")
            if attempt == max_retries:
                print(f"  All {max_retries} attempts failed, exiting")
                exit(1)
            time.sleep(retry_wait_seconds)

    print(f"  PR diff saved to: {diff_file}")

    def _fetch_base_content(path: str) -> str:
        """获取 base（变更前）文件内容，用于 ast 分类。

        contents API 单路获取；失败即退出，不允许降级：
        base 内容缺失会导致 AST 分类退化（候选对两侧计入），精度无法保证。
        大于1M文件时自动改用 git blob API 获取内容（base64编码）
        """
        content_url = f"https://api.github.com/repos/{repo}/contents/{urllib.parse.quote(path)}?ref={base_sha}"
        for attempt in range(1, max_retries + 1):
            try:
                req = _github_request(content_url, github_token)
                with urllib.request.urlopen(req, timeout=30, context=ssl_context) as response:
                    data = json.loads(response.read().decode())
                if data.get("encoding") == "base64":
                    #常规文件：content API直接返回base64编码内容
                    return base64.b64decode(data["content"]).decode("utf-8")
                blob_sha = data.get("sha")
                if not blob_sha:
                    raise ValueError(f"unexpected response: {data.get("encoding")!r}")
                blob_url = f"https://api.github.com/repos/{repo}/git/blobs/{blob_sha}"
                req = _github_request(blob_url, github_token)
                with urllib.request.urlopen(req, timeout=60, context=ssl_context) as response:
                    blob = json.loads(response.read().decode())
                if blob.get("encoding") != "base64" or not blob.get("content"):
                    raise ValueError(f"unexpected git blob encoding: {blob.get('encoding')!r}")
                return base64.b64decode(blob["content"]).decode("utf-8")
            except Exception as e:
                print(f"  Attempt {attempt}/{max_retries} to fetch base content for {path} failed: {e}")
                if attempt < max_retries:
                    time.sleep(retry_wait_seconds)
        print(f"  Error: failed to fetch base content for {path} after all attempts, exiting")
        exit(1)

    return diff_file, _fetch_base_content
