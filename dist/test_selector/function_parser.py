"""Python 函数解析（独立模块）。

- get_function_ranges: 解析文件，返回 函数名 -> [(start_line, end_line), ...]
- _get_import_lines: 获取 import 语句行号
- get_lines_functions: 查询行号所属函数

get_lines_functions 将全部函数区间展平为按起始行排序的单一区间列表，
对每个查询行做一次线性扫描即可匹配，避免：
- 每个函数集合展开（O(函数数 x 区间长度) 内存）
- O(lines x functions) 双重循环

语义：嵌套函数中外层起始行更小先被匹配（与 ast.walk 父节点先于子节点的顺序一致）。
"""

import ast
from collections import defaultdict
from pathlib import Path


class FunctionParser:
    """Python 函数解析器 - 获取函数与分支的行号范围"""

    @staticmethod
    def get_function_ranges(filepath: str) -> dict[str, list[tuple[int, int]]]:
        """
        解析 Python 文件，返回 函数名 -> [(start_line, end_line), ...] 映射。
        支持同名函数多次出现（返回全部匹配区间）。
        """
        function_ranges = defaultdict(list)
        try:
            with open(filepath, encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=filepath)

            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    function_ranges[node.name].append((node.lineno, node.end_lineno or node.lineno))
        except Exception as e:
            print(f"  Warning: Failed to parse function definition {filepath}: {e}")

        return function_ranges

    @staticmethod
    def _get_import_lines(filepath: str) -> set[int]:
        """
        获取文件中所有 import 语句行号。
        """
        import_lines = set()
        try:
            with open(filepath, encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=filepath)
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    import_lines.add(node.lineno)
                    if hasattr(node, "end_lineno") and node.end_lineno:
                        import_lines.update(range(node.lineno, node.end_lineno + 1))
        except Exception:
            pass
        return import_lines

    @staticmethod
    def get_lines_functions(
        filepath: str,
        lines: set[int],
        skip_imports: bool = False,
        function_ranges: dict[str, list[tuple[int, int]]] | None = None,
    ) -> dict[int, str]:
        """
        查询每个行号所属的函数名（sglang 优化版：区间线性扫描 + 区间复用）。

        Args:
            filepath: 源文件路径
            lines: 待查询的行号集合
            skip_imports: 是否跳过 import 语句行
            function_ranges: 预解析的函数区间（复用避免重复解析文件）；
                None 时内部解析

        Returns:
            {行号: 函数名} 映射
        """
        line_to_function = {}
        if not lines:
            return line_to_function

        if function_ranges is None:
            function_ranges = FunctionParser.get_function_ranges(filepath)
        if not function_ranges:
            return line_to_function

        # 将所有函数区间展平为单一区间列表并按起始行排序，
        # 然后对每个查询行做一次线性扫描匹配。
        # 语义保持：嵌套函数中外层起始行更小先被找到，与原始
        # ast.walk（父节点先于子节点）的顺序一致。
        intervals = []
        for func_name, ranges in function_ranges.items():
            for start, end in ranges:
                intervals.append((start, end, func_name))
        intervals.sort(key=lambda x: x[0])

        for line in lines:
            for start, end, func_name in intervals:
                if start > line:
                    break
                if line <= end:
                    line_to_function[line] = func_name
                    break

        return line_to_function
