"""PR 内容检测（独立模块）：从 PR diff 中提取与测试选择相关的内容。

- 新/修改的测试文件：逐行解析 diff，匹配规则由仓库适配器提供
- 删除的测试文件：逐行解析 diff 中的 '--- a/...' + '+++ /dev/null' 对
- 全量触发变更（如 vllm 的 csrc 目录）：委托仓库适配器判断

测试文件匹配规则通过 adapter.is_test_file() 提供，公共逻辑与仓库规则解耦。
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .repos.base import RepoAdapter


def get_test_files_from_pr_diff(diff_content: str, adapter: "RepoAdapter") -> list[str]:
    """从 PR diff 中提取新/修改的测试文件。

    逐行解析 diff：
    - '+++ b/<path>' 或 '+++ a/<path>'：新/修改文件
    - 'rename to <path>'：重命名文件的新路径

    匹配规则由 adapter.is_test_file() 决定。

    Args:
        diff_content: PR diff 文本内容
        adapter: 仓库适配器

    Returns:
        匹配的测试文件路径列表（直接加入推荐列表，不做覆盖率匹配）
    """
    test_files_found = []

    changed_test_files = set()
    for line in diff_content.splitlines():
        if line.startswith("+++ b/"):
            test_file_path = line.removeprefix("+++ b/")
        elif line.startswith("+++ a/"):
            test_file_path = line.removeprefix("+++ a/")
        elif line.startswith("rename to "):
            test_file_path = line.removeprefix("rename to ")
        else:
            continue

        if adapter.is_test_file(test_file_path):
            changed_test_files.add(test_file_path)

    if not changed_test_files:
        return test_files_found

    print(f"  Found {len(changed_test_files)} changed test file(s): {changed_test_files}")

    # 所有变更的测试文件直接加入推荐列表（不与 test_case_map 匹配）
    for changed_file in sorted(changed_test_files):
        if changed_file not in test_files_found:
            test_files_found.append(changed_file)

    return test_files_found


def get_deleted_test_files_from_pr(diff_content: str, adapter: "RepoAdapter") -> list[str]:
    """从 PR diff 中提取删除的测试文件。

    逐行解析：'--- a/<path>' 后紧跟 '+++ /dev/null' 表示文件被删除。
    匹配规则由 adapter.is_test_file() 决定。

    Args:
        diff_content: PR diff 文本内容
        adapter: 仓库适配器

    Returns:
        删除的测试文件路径列表
    """
    deleted_test_files = []

    lines = diff_content.splitlines()
    for old_line, new_line in zip(lines, lines[1:]):
        if not old_line.startswith("--- a/") or new_line != "+++ /dev/null":
            continue

        test_file_path = old_line.removeprefix("--- a/")
        if adapter.is_test_file(test_file_path):
            deleted_test_files.append(test_file_path)

    if deleted_test_files:
        print(f"  Found {len(deleted_test_files)} deleted test file(s): {deleted_test_files}")

    return deleted_test_files


def has_full_suite_changes(diff_content: str, adapter: "RepoAdapter") -> bool:
    """判断 PR diff 是否包含触发全量测试套件的变更（如 vllm 的 csrc 目录）。

    Args:
        diff_content: PR diff 文本内容
        adapter: 仓库适配器

    Returns:
        True 表示应运行全量测试套件
    """
    return adapter.has_full_suite_changes(diff_content)
