from __future__ import annotations

import ast
import re
from pathlib import Path

from .models import FileRecord, SymbolRecord


IGNORED_CALL_NAMES = {
    "if",
    "for",
    "while",
    "switch",
    "catch",
    "return",
    "typeof",
    "console",
    "super",
}


def detect_language(path: Path) -> str | None:
    suffix = path.suffix.lower()
    if suffix == ".py":
        return "python"
    if suffix in {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"}:
        return "javascript"
    return None


class _PythonVisitor(ast.NodeVisitor):
    def __init__(self, relative_path: str, source: str) -> None:
        self.relative_path = relative_path
        self.source = source
        self.lines = source.splitlines()
        self.imports: list[str] = []
        self.symbols: list[SymbolRecord] = []
        self._stack: list[str] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.imports.append(alias.name)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        dots = "." * node.level
        self.imports.append(f"{dots}{module}")

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        qname = ".".join(self._stack + [node.name])
        record = self._make_symbol(node, node.name, qname, "class")
        self.symbols.append(record)
        self._stack.append(node.name)
        self.generic_visit(node)
        self._stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._handle_function(node, "function")

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._handle_function(node, "async_function")

    def _handle_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef, kind: str) -> None:
        parent = ".".join(self._stack) if self._stack else None
        qname = ".".join(self._stack + [node.name])
        symbol_kind = "method" if self._stack else kind
        record = self._make_symbol(node, node.name, qname, symbol_kind, parent=parent)
        call_visitor = _CallCollector()
        for child in node.body:
            call_visitor.visit(child)
        record.calls = call_visitor.calls
        self.symbols.append(record)
        self._stack.append(node.name)
        self.generic_visit(node)
        self._stack.pop()

    def _make_symbol(
        self,
        node: ast.AST,
        name: str,
        qualified_name: str,
        kind: str,
        parent: str | None = None,
    ) -> SymbolRecord:
        line_start = getattr(node, "lineno", 1)
        line_end = getattr(node, "end_lineno", line_start)
        snippet = "\n".join(self.lines[line_start - 1 : line_end])[:2000]
        return SymbolRecord(
            id=f"symbol:{self.relative_path}:{qualified_name}",
            name=name,
            qualified_name=qualified_name,
            kind=kind,
            file_path=self.relative_path,
            language="python",
            line_start=line_start,
            line_end=line_end,
            parent=parent,
            docstring=ast.get_docstring(node) or "",
            snippet=snippet,
        )


class _CallCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.calls: list[str] = []

    def visit_Call(self, node: ast.Call) -> None:
        name = self._extract_name(node.func)
        if name:
            self.calls.append(name)
        self.generic_visit(node)

    def _extract_name(self, node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            parts: list[str] = []
            current: ast.AST | None = node
            while isinstance(current, ast.Attribute):
                parts.append(current.attr)
                current = current.value
            if isinstance(current, ast.Name):
                parts.append(current.id)
            return ".".join(reversed(parts))
        return None


def parse_python_file(path: Path, repo_root: Path) -> tuple[FileRecord, list[SymbolRecord]]:
    source = path.read_text(encoding="utf-8", errors="ignore")
    relative_path = path.relative_to(repo_root).as_posix()
    file_record = FileRecord(
        path=relative_path,
        abs_path=str(path),
        language="python",
        line_count=len(source.splitlines()),
    )
    try:
        tree = ast.parse(source, filename=relative_path)
    except SyntaxError as exc:
        file_record.errors.append(f"SyntaxError: {exc}")
        return file_record, []

    visitor = _PythonVisitor(relative_path, source)
    visitor.visit(tree)
    file_record.raw_imports = visitor.imports
    return file_record, visitor.symbols


IMPORT_RE = re.compile(
    r"""(?:import\s+(?:.+?)\s+from\s+['\"](?P<from>[^'\"]+)['\"])|(?:require\(\s*['\"](?P<require>[^'\"]+)['\"]\s*\))"""
)
CLASS_RE = re.compile(r"^\s*class\s+([A-Za-z_$][A-Za-z0-9_$]*)", re.MULTILINE)
FUNC_RE = re.compile(
    r"""
    ^\s*(?:export\s+)?function\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*\(
    |
    ^\s*(?:export\s+)?const\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*\([^)]*\)\s*=>
    |
    ^\s*(?:export\s+)?const\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*function\s*\(
    """,
    re.MULTILINE | re.VERBOSE,
)
CALL_RE = re.compile(r"([A-Za-z_$][A-Za-z0-9_$\.]*)\s*\(")


def parse_javascript_file(path: Path, repo_root: Path) -> tuple[FileRecord, list[SymbolRecord]]:
    source = path.read_text(encoding="utf-8", errors="ignore")
    relative_path = path.relative_to(repo_root).as_posix()
    lines = source.splitlines()
    file_record = FileRecord(
        path=relative_path,
        abs_path=str(path),
        language="javascript",
        line_count=len(lines),
    )

    raw_imports: list[str] = []
    for match in IMPORT_RE.finditer(source):
        import_target = match.group("from") or match.group("require")
        if import_target:
            raw_imports.append(import_target)
    file_record.raw_imports = raw_imports

    symbol_ranges: list[tuple[int, int, str, str]] = []
    for match in CLASS_RE.finditer(source):
        name = match.group(1)
        line_start = source[: match.start()].count("\n") + 1
        symbol_ranges.append((line_start, match.start(), name, "class"))
    for match in FUNC_RE.finditer(source):
        name = next(group for group in match.groups() if group)
        line_start = source[: match.start()].count("\n") + 1
        symbol_ranges.append((line_start, match.start(), name, "function"))

    symbol_ranges.sort(key=lambda item: item[1])
    symbols: list[SymbolRecord] = []
    for index, (line_start, start_offset, name, kind) in enumerate(symbol_ranges):
        end_offset = symbol_ranges[index + 1][1] if index + 1 < len(symbol_ranges) else len(source)
        snippet = source[start_offset:end_offset].strip()[:2000]
        call_names = []
        for call_match in CALL_RE.finditer(snippet):
            callee = call_match.group(1)
            if callee.split(".")[-1] in IGNORED_CALL_NAMES:
                continue
            call_names.append(callee)
        line_end = line_start + snippet.count("\n")
        symbols.append(
            SymbolRecord(
                id=f"symbol:{relative_path}:{name}",
                name=name,
                qualified_name=name,
                kind=kind,
                file_path=relative_path,
                language="javascript",
                line_start=line_start,
                line_end=line_end,
                snippet=snippet,
                calls=call_names,
            )
        )
    return file_record, symbols
