"""TestSelector - 基于代码变更选择受影响测试用例（行级粒度）。

功能：
- 路径规范化：通用版（adapter.normalize_changed_path，兼容 PRODUCT_PREFIX 与 '{repo_name}/'）
- 源文件解析：adapter.resolve_source_file（source_dir/<repo_name>/<rel_path>）
- 函数区间复用：每文件只解析一次 function_ranges
- 函数级匹配使用 get_lines_functions（区间线性扫描）
"""

from collections import defaultdict
from pathlib import Path

from .function_parser import FunctionParser
from .repos.base import RepoAdapter


class TestSelector:
    """测试选择器 - 根据代码变更选择要运行的测试用例（行级粒度）"""

    def __init__(self, test_case_map: dict, adapter: RepoAdapter):
        self.test_case_map = test_case_map
        self.adapter = adapter

    def select_tests(
        self,
        changed_files_with_lines: dict[str, set[int]],
        min_affected_lines: int = 1,
        source_dir: str | None = None,
        enable_line_match: bool = True,
        enable_function_match: bool = True,
        enable_file_match: bool = True,
        enable_skip_imports: bool = False,
        enable_dedup: bool = False,
    ) -> tuple[list[tuple[str, dict[str, set[int]], int]], str]:
        """
        基于变更文件选择受影响的测试用例，支持 3 种独立匹配粒度：
        - 行级匹配：变更行与覆盖行的精确交集
        - 函数级匹配：整个函数体区间匹配
        - 文件级匹配：覆盖该文件的任意行即匹配

        各级联：仅当当前粒度找不到测试时才尝试下一级。

        Args:
            changed_files_with_lines: 变更文件及其行号 {filepath: {lineno, ...}}
            min_affected_lines: 最小受影响行数，低于此值不选择
            source_dir: 源代码目录，用于函数/文件级展开
            enable_line_match: 是否启用行级匹配
            enable_function_match: 是否启用函数级匹配
            enable_file_match: 是否启用文件级匹配
            enable_skip_imports: 是否跳过 import 语句行（仅函数级匹配有效）
            enable_dedup: 是否启用去重

        Returns:
            (selected_tests, expand_reason)
            - selected_tests: [(test_case_name, {filepath: {covered_lines}}, total_affected_lines), ...]
            - expand_reason: 展开原因（'' 表示无展开，'line'/'function'/'file' 表示所用粒度）
        """
        selected = []
        expand_reason = ""

        # 规范化变更文件路径：剥离 PRODUCT_PREFIX 或 '{repo_name}/' 前缀（通用版）
        normalized_changed = {}
        for f, lines in changed_files_with_lines.items():
            normalized_changed[self.adapter.normalize_changed_path(f)] = lines

        total_changed_lines = sum(len(lines) for lines in normalized_changed.values())

        # ===== 行级匹配 + 函数级匹配（并行执行，合并去重） =====
        line_results = []  # [(test_case, affected_detail, total_lines)]
        func_results = []  # [(test_case, affected_detail, total_lines)]

        # ----- Stage 1: 行级匹配 -----
        if enable_line_match:
            for test_case, data in self.test_case_map.items():
                covered_files = data["files"]  # {filepath: {lineno, ...}}

                # 行级匹配：计算该测试覆盖了哪些变更行
                affected_detail = {}  # {filepath: set of covered changed lines}
                all_intersected_lines = set()  # 所有文件交集的并集

                for changed_file, changed_lines in normalized_changed.items():
                    if changed_file in covered_files:
                        covered_lines = covered_files[changed_file]
                        # 计算变更行与覆盖行的交集
                        intersected_lines = changed_lines & covered_lines
                        if intersected_lines:
                            affected_detail[changed_file] = intersected_lines
                            all_intersected_lines.update(intersected_lines)

                # 计算整体覆盖密度：交集行数 / 总变更行数
                overall_density = len(all_intersected_lines) / total_changed_lines if total_changed_lines else 0

                # 按覆盖密度与最小受影响行数过滤
                if (
                    all_intersected_lines
                    and overall_density >= 0.0  # COVERAGE_DENSITY_THRESHOLD = 0.0
                    and len(all_intersected_lines) >= min_affected_lines
                ):
                    line_results.append((test_case, affected_detail, len(all_intersected_lines)))

            # 按受影响行数排序（多者在前）
            line_results.sort(key=lambda x: x[2], reverse=True)

            # 行级去重：覆盖相同行时仅选择一个测试
            if line_results and enable_dedup:
                claimed_lines = set()
                deduplicated = []
                for test_case, affected_detail, total_lines in line_results:
                    # 收集该测试覆盖的所有行
                    test_lines = set()
                    for lines in affected_detail.values():
                        test_lines.update(lines)
                    # 仅保留有新行的测试
                    unclaimed = test_lines - claimed_lines
                    if unclaimed:
                        deduplicated.append((test_case, affected_detail, len(unclaimed)))
                        claimed_lines.update(test_lines)
                line_results = deduplicated

        # ----- Stage 2: 函数级匹配 -----
        if enable_function_match and source_dir:
            # 收集变更行所属的函数
            changed_functions = {}  # {filepath: {func_name: Set[linenos]}}
            changed_function_ranges = {}  # {filepath: function_ranges} - 每个文件只解析一次

            for changed_file, changed_lines in normalized_changed.items():
                source_file = self.adapter.resolve_source_file(Path(source_dir), changed_file)

                if not source_file:
                    continue

                # 每个变更文件只解析一次函数区间，供行映射与后续区间查询复用
                function_ranges = FunctionParser.get_function_ranges(str(source_file))

                # 获取变更行的函数映射（复用预解析区间）
                line_to_function = FunctionParser.get_lines_functions(
                    str(source_file), changed_lines, skip_imports=enable_skip_imports,
                    function_ranges=function_ranges,
                )

                # 按函数名分组
                func_to_lines = defaultdict(set)
                for line, func_name in line_to_function.items():
                    func_to_lines[func_name].add(line)

                if func_to_lines:
                    changed_functions[changed_file] = func_to_lines
                    changed_function_ranges[changed_file] = function_ranges

            if changed_functions:
                # 构建 函数 -> 覆盖该函数的测试 映射
                func_to_tests = defaultdict(list)

                for test_case, data in self.test_case_map.items():
                    covered_files = data["files"]

                    for changed_file, func_to_lines in changed_functions.items():
                        if changed_file not in covered_files:
                            continue

                        covered_lines = covered_files[changed_file]

                        # 每个变更文件只解析一次源文件
                        source_file = self.adapter.resolve_source_file(Path(source_dir), changed_file)

                        if not source_file:
                            continue

                        # 复用收集阶段解析的函数区间
                        func_ranges = changed_function_ranges.get(changed_file, {})

                        # 过滤 import 语句行（仅展示用），只计算一次
                        if enable_skip_imports:
                            import_lines = FunctionParser._get_import_lines(str(source_file))
                            display_changed_lines = normalized_changed.get(changed_file, set()) - import_lines
                        else:
                            display_changed_lines = normalized_changed.get(changed_file, set())

                        for func_name in func_to_lines:
                            if func_name not in func_ranges:
                                continue

                            # 合并所有匹配的函数区间
                            func_all_lines = set()
                            for func_start, func_end in func_ranges[func_name]:
                                func_all_lines.update(range(func_start, func_end + 1))

                            if not func_all_lines:
                                continue

                            # 检查该测试是否覆盖此函数的任意行
                            covered_in_func = covered_lines & func_all_lines
                            if covered_in_func:
                                # 获取测试覆盖行与实际变更行的交集（仅展示用）
                                covered_changed_lines = covered_lines & display_changed_lines
                                func_to_tests[func_name].append((test_case, covered_in_func, covered_changed_lines))

                # 选择覆盖变更函数其他行的测试（去重）
                for changed_file, func_to_lines in changed_functions.items():
                    for func_name in func_to_lines:
                        if func_name in func_to_tests:
                            for test_case, covered_in_func, covered_changed_lines in func_to_tests[func_name]:
                                existing = [s[0] for s in func_results]
                                if test_case not in existing and covered_in_func:
                                    # 有变更行覆盖则展示，否则展示函数覆盖
                                    display_lines = covered_changed_lines if covered_changed_lines else set()
                                    func_results.append(
                                        (
                                            test_case,
                                            {changed_file: display_lines},
                                            len(display_lines) or len(covered_in_func),
                                        )
                                    )
                                    print(
                                        f"  [Function match] {test_case} covers function '{func_name}' in"
                                        f" {changed_file}"
                                    )

                func_results.sort(key=lambda x: x[2], reverse=True)

        # ===== 合并行级与函数级结果，去重 =====
        if line_results or func_results:
            # 按测试用例去重，保留行级结果（更精确）
            seen = set()
            for test_case, affected_detail, total_lines in line_results:
                if test_case not in seen:
                    seen.add(test_case)
                    selected.append((test_case, affected_detail, total_lines))

            # 添加函数级独占结果
            for test_case, affected_detail, total_lines in func_results:
                if test_case not in seen:
                    seen.add(test_case)
                    selected.append((test_case, affected_detail, total_lines))

            # 按受影响行数排序
            selected.sort(key=lambda x: x[2], reverse=True)

            if selected:
                print(
                    f"  Line match: {len(line_results)} tests, Function match: {len(func_results)} tests, "
                    f"Total: {len(selected)} tests"
                )
                return selected, "line+function"

        # ===== Stage 3: 文件级匹配 =====
        if not selected and enable_file_match:
            print("  Using file-level matching (renamed/deleted files)...")
            expand_reason = "file"

            # 文件级匹配：覆盖变更文件的任意测试即被选择
            for test_case, data in self.test_case_map.items():
                covered_files = data["files"]

                for changed_file in normalized_changed:
                    if changed_file in covered_files:
                        covered_lines = covered_files[changed_file]
                        if covered_lines:
                            selected.append((test_case, {changed_file: covered_lines}, len(covered_lines)))
                            break

            # 去重：同一测试用例只选择一次
            if selected:
                seen = set()
                deduplicated = []
                for s in selected:
                    if s[0] not in seen:
                        seen.add(s[0])
                        deduplicated.append(s)
                selected = deduplicated

            selected.sort(key=lambda x: x[2], reverse=True)

        return selected, expand_reason

    def print_selection(
        self,
        selected: list[tuple[str, dict[str, set[int]], int]],
        changed_files: dict[str, set[int]],
        min_affected_lines: int = 1,
        expand_reason: str = "",
    ):
        """打印选择结果"""
        total_changed_lines = sum(len(v) for v in changed_files.values())

        print("\n" + "=" * 70)
        print(f"Code changes: {len(changed_files)} files, {total_changed_lines} lines")

        # 展示展开原因
        gran_names = {
            "line": "Line match",
            "function": "Function match",
            "file": "File match",
            "line+function": "Line+Function match",
        }
        gran_detail_titles = {
            "line": "Details (Line match)",
            "function": "Details (Function match)",
            "file": "Details (File match)",
            "line+function": "Details (Line+Function match)",
        }
        if expand_reason and expand_reason in gran_names:
            print(f"Selected: {len(selected)} test cases ({gran_names[expand_reason]})")
        else:
            print(f"Selected: {len(selected)} test cases (min affected: {min_affected_lines} lines)")
        print("=" * 70)

        if not selected:
            print("\nNo test cases cover the changed code lines!")
            print(f"Change details: {self._format_changed_files(changed_files)}")
            return

        print(f"\n{'#':<4} {'Test Case':<50} {'Affected Lines'}")
        print("-" * 70)

        for i, (test_case, affected_detail, total_lines) in enumerate(selected, 1):
            # 构建覆盖行展示
            line_parts = []
            for filepath, lines in sorted(affected_detail.items()):
                line_parts.append(self._format_line_range(sorted(lines)))
            line_display = f" ({', '.join(line_parts)})" if line_parts else ""
            print(f"{i:<4} {test_case:<50} {total_lines}{line_display}")

        print(f"\n{gran_detail_titles.get(expand_reason, 'Details')}:")
        for test_case, affected_detail, total_lines in selected[:10]:
            print(f"\n  {test_case} ({total_lines} lines):")
            for filepath, lines in sorted(affected_detail.items()):
                line_str = self._format_line_range(sorted(lines))
                print(f"    - {filepath}: {line_str}")

    @staticmethod
    def _format_line_range(lines: list[int]) -> str:
        """将行号列表压缩为区间表示"""
        if not lines:
            return ""

        lines = sorted(set(lines))
        ranges = []
        start = lines[0]
        end = lines[0]

        for line in lines[1:]:
            if line == end + 1:
                end = line
            else:
                if start == end:
                    ranges.append(str(start))
                else:
                    ranges.append(f"{start}-{end}")
                start = end = line

        if start == end:
            ranges.append(str(start))
        else:
            ranges.append(f"{start}-{end}")

        return ", ".join(ranges)

    def _format_changed_files(self, changed_files: dict[str, set[int]]) -> str:
        """格式化变更文件"""
        result = []
        for f, lines in sorted(changed_files.items()):
            if len(lines) > 10:
                result.append(f"{f}: {len(lines)} lines")
            else:
                result.append(f"{f}: {sorted(lines)}")
        return ", ".join(result[:5]) + ("..." if len(changed_files) > 5 else "")
