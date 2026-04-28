from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SymbolRecord:
    id: str
    name: str
    qualified_name: str
    kind: str
    file_path: str
    language: str
    line_start: int
    line_end: int
    parent: str | None = None
    docstring: str = ""
    snippet: str = ""
    calls: list[str] = field(default_factory=list)


@dataclass
class FileRecord:
    path: str
    abs_path: str
    language: str
    line_count: int
    imports: list[str] = field(default_factory=list)
    raw_imports: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
