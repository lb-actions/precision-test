"""map 噪音处理（独立模块）：过滤覆盖率数据中的非可执行行。

覆盖率 arc 数据可能记录以下不可执行行，需在构建 test_case_map 时过滤，
避免与变更行误匹配：
1. import / from...import 语句行
2. 函数定义行（def 行及多行签名头）
3. 类定义行
4. docstring 行
5. 空白行（覆盖率的控制流节点，如 if/return 后的块边界）

带文件级缓存（_noise_lines_cache），同一文件只解析一次。
"""

import ast
from pathlib import Path

from .function_parser import FunctionParser


class NoiseFilter:
    """覆盖率噪音行过滤器。"""

    def __init__(self):
        #: filepath -> set of noise lines（import + def + class + docstring + blank）
        self._noise_lines_cache: dict[str, set[int]] = {}

    def _get_function_def_lines(self, filepath: str) -> set[int]:
        """
        获取函数定义行号（仅 def 行，不含函数体）。

        Args:
            filepath: 源文件路径

        Returns:
            函数定义发生处的行号集合
        """
        def_lines = set()
        try:
            with open(filepath, encoding="utf-8") as f:
                source = f.read()
                lines = source.splitlines()

            tree = ast.parse(source, filename=filepath)

            TARGET_DECORATORS = {"staticmethod", "classmethod", "property"}
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    # 加入装饰器行（仅 @staticmethod / @classmethod / @property）
                    for decorator in node.decorator_list:
                        if isinstance(decorator, ast.Name) and decorator.id in TARGET_DECORATORS:
                            if hasattr(decorator, "lineno") and decorator.lineno:
                                def_lines.add(decorator.lineno)
                                # 处理多行装饰器表达式
                                if hasattr(decorator, "end_lineno") and decorator.end_lineno:
                                    for i in range(decorator.lineno, decorator.end_lineno + 1):
                                        def_lines.add(i)

                    def_lines.add(node.lineno)

                    # 括号计数寻找签名头结束行
                    start_idx = node.lineno - 1
                    paren_count = lines[start_idx].count("(") - lines[start_idx].count(")")

                    line_idx = start_idx
                    while paren_count > 0 and line_idx < len(lines):
                        line_idx += 1
                        paren_count += lines[line_idx].count("(") - lines[line_idx].count(")")

                    header_end = line_idx + 1  # 转为 1-indexed

                    # 扩展至返回类型注解
                    if node.returns:
                        header_end = max(header_end, node.returns.end_lineno)

                    # 记录 def 到签名头结束的所有行
                    for i in range(node.lineno, header_end + 1):
                        def_lines.add(i)
        except Exception:
            pass
        return def_lines

    def _get_class_def_lines(self, filepath: str) -> set[int]:
        """
        获取所有类定义行号。

        Args:
            filepath: 源文件路径

        Returns:
            类定义发生处的行号集合
        """
        class_lines = set()
        try:
            with open(filepath, encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=filepath)

            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    class_lines.add(node.lineno)
        except Exception:
            pass
        return class_lines

    def _get_docstring_lines(self, filepath: str) -> set[int]:
        """
        获取所有 docstring 行号（模块、类、函数级）。

        docstring 是模块/类/函数体中的第一个字符串字面量语句。

        Args:
            filepath: 源文件路径

        Returns:
            docstring 发生处的行号集合
        """
        docstring_lines = set()
        try:
            with open(filepath, encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=filepath)

            # 模块级 docstring
            if tree.body and isinstance(tree.body[0], ast.Expr) and isinstance(tree.body[0].value, ast.Constant):
                docstring_lines.add(tree.body[0].lineno)

            # 类与函数 docstring
            for node in ast.walk(tree):
                if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    if (
                        node.body
                        and isinstance(node.body[0], ast.Expr)
                        and isinstance(node.body[0].value, ast.Constant)
                    ):
                        docstring_lines.add(node.body[0].lineno)
        except Exception:
            pass
        return docstring_lines

    def _get_all_lines(self, filepath: str) -> set[int]:
        """
        获取模块级 `__all__` 赋值行号（含续行）。

        `__all__` 通常出现在 __init__.py 或工具模块（如 checksum.py）的导出声明处，
        覆盖率数据可能将其记录为已执行行（模块导入时执行），但业务改动不会直接
        命中这些行，须过滤以避免误匹配。

        Args:
            filepath: 源文件路径

        Returns:
            `__all__` 赋值发生处的行号集合
        """
        all_lines = set()
        try:
            with open(filepath, encoding="utf-8") as f:
                content = f.read()

            tree = ast.parse(content, filename=filepath)

            for node in tree.body:
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and target.id == "__all__":
                            start_line = node.lineno
                            end_line = (
                                node.end_lineno
                                if hasattr(node, "end_lineno") and node.end_lineno
                                else start_line
                            )
                            for line_num in range(start_line, end_line + 1):
                                all_lines.add(line_num)
        except Exception:
            pass
        return all_lines

    @staticmethod
    def _get_blank_lines(filepath: str) -> set[int]:
        """
        获取文件中所有空白行号。

        覆盖率 arc 数据可能将空白行记录为控制流节点（如 if/return 语句后的
        块边界），这些行不可执行，必须过滤以避免误匹配。

        Args:
            filepath: 源文件路径

        Returns:
            空白行号集合
        """
        blank_lines = set()
        try:
            with open(filepath, encoding="utf-8") as f:
                for line_no, line in enumerate(f, start=1):
                    if not line.strip():
                        blank_lines.add(line_no)
        except Exception:
            pass
        return blank_lines

    def filter_noise_lines(self, filepath: str, lines: set[int]) -> set[int]:
        """
        从覆盖率数据中过滤无效噪音行：
        1. import / from...import 语句行
        2. 函数定义行（仅 def 行）
        3. 类定义行
        4. docstring 行
        5. 空白行

        Args:
            filepath: 源文件路径
            lines: 原始覆盖行号集合

        Returns:
            过滤噪音行后的集合
        """
        if not lines:
            return lines

        # 使用缓存避免同一文件重复解析
        if filepath not in self._noise_lines_cache:
            import_lines = FunctionParser._get_import_lines(filepath)
            def_lines = self._get_function_def_lines(filepath)
            class_lines = self._get_class_def_lines(filepath)
            docstring_lines = self._get_docstring_lines(filepath)
            all_lines = self._get_all_lines(filepath)
            blank_lines = self._get_blank_lines(filepath)
            self._noise_lines_cache[filepath] = (
                import_lines | def_lines | class_lines | docstring_lines | all_lines | blank_lines
            )

        return lines - self._noise_lines_cache[filepath]
