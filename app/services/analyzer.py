from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
from collections import Counter, defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
except ImportError:
    TfidfVectorizer = None
    cosine_similarity = None

from .models import FileRecord, SymbolRecord
from .parser import detect_language, parse_javascript_file, parse_python_file

SUPPORTED_EXTENSIONS = {".py", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"}
DEFAULT_EXCLUDE_DIRS = {
    ".git",
    ".venv",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    ".next",
    ".turbo",
    "coverage",
}
FRAMEWORK_HINTS = {
    "python": {
        "fastapi": "FastAPI",
        "flask": "Flask",
        "django": "Django",
        "sqlalchemy": "SQLAlchemy",
        "pydantic": "Pydantic",
        "celery": "Celery",
        "pytest": "Pytest",
        "streamlit": "Streamlit",
    },
    "javascript": {
        "react": "React",
        "next": "Next.js",
        "express": "Express",
        "nestjs": "NestJS",
        "vue": "Vue",
        "svelte": "Svelte",
        "axios": "Axios",
        "d3": "D3.js",
    },
}
GENERIC_NAMES = {"data", "temp", "helper", "util", "process", "handler", "manager", "thing"}
REPO_MAP_ORDER = [
    "Frontend / UI",
    "Backend / API",
    "Core Engine",
    "Data & Persistence",
    "Tests",
    "Infrastructure",
    "Configuration",
    "Other",
]
SECURITY_PATTERNS = {
    "python": [
        ("subprocess shell", re.compile(r"shell\s*=\s*True")),
        ("dynamic execution", re.compile(r"\b(eval|exec)\s*\(")),
        ("pickle loading", re.compile(r"pickle\.load\s*\(")),
    ],
    "javascript": [
        ("unsafe HTML injection", re.compile(r"innerHTML\s*=")),
        ("dynamic execution", re.compile(r"\beval\s*\(")),
    ],
}
PERFORMANCE_PATTERNS = {
    "python": [
        ("nested loops", re.compile(r"for .+:\n(?:\s+.+\n)*?\s+for .+:")),
        ("eager glob scan", re.compile(r"\.rglob\s*\(")),
    ],
    "javascript": [
        ("nested iteration", re.compile(r"\.map\([^\n]+\.map\(|\.forEach\([^\n]+\.forEach\(")),
        ("large JSON stringify", re.compile(r"JSON\.stringify\s*\(")),
    ],
}


class RepositoryAnalyzer:
    def __init__(
        self,
        repo_root: Path,
        *,
        branch: str | None = None,
        include_extensions: list[str] | None = None,
        exclude_dirs: list[str] | None = None,
    ) -> None:
        self.repo_root = repo_root
        self.branch = branch or "default"
        self.include_extensions = self._normalize_extensions(include_extensions)
        self.exclude_dirs = self._normalize_exclude_dirs(exclude_dirs)

        self.files: list[FileRecord] = []
        self.symbols: list[SymbolRecord] = []
        self.chunks: list[dict[str, Any]] = []
        self.sources: dict[str, str] = {}
        self.file_dependency_edges: list[tuple[str, str]] = []
        self.symbol_call_edges: list[tuple[str, str]] = []
        self.symbol_callees: dict[str, set[str]] = defaultdict(set)
        self.file_importers: dict[str, set[str]] = defaultdict(set)
        self.symbol_callers: dict[str, set[str]] = defaultdict(set)
        self._symbols_by_id: dict[str, SymbolRecord] = {}
        self._symbols_by_name: dict[str, list[SymbolRecord]] = defaultdict(list)
        self._files_by_path: dict[str, FileRecord] = {}
        self._search_items: list[dict[str, Any]] = []
        self._search_matrix = None
        self._vectorizer: TfidfVectorizer | None = None
        self.entry_points: list[str] = []
        self.tech_stack: list[str] = []
        self.directory_counts: Counter[str] = Counter()
        self.metrics: dict[str, Any] = {}
        self.parse_errors: list[dict[str, str]] = []
        self.skipped_files: int = 0
        self.repo_map: list[dict[str, Any]] = []
        self.function_cards: list[dict[str, Any]] = []
        self.improvements: dict[str, Any] = {}
        self.health: dict[str, Any] = {}
        self.diagrams: dict[str, str] = {}
        self.context_modes: dict[str, str] = {}
        self._started_at = time.perf_counter()
        self._report: dict[str, Any] | None = None

    def analyze(self) -> dict[str, Any]:
        self._scan_files()
        self._resolve_imports()
        self._resolve_symbol_calls()
        self._build_chunks()
        self._build_search_index()
        self.tech_stack = self._detect_tech_stack()
        self.repo_map = self._build_repo_map()
        self.function_cards = self._build_function_cards()
        self.improvements = self._build_improvements()
        self.health = self._build_health_scores(self.improvements)
        self.diagrams = self._build_diagrams()
        self.metrics = self._build_metrics()
        self.context_modes = self._build_context_modes()
        self._report = self._build_report()
        return self._report

    def _normalize_extensions(self, include_extensions: list[str] | None) -> set[str]:
        if not include_extensions:
            return SUPPORTED_EXTENSIONS
        normalized = {ext if ext.startswith(".") else f".{ext}" for ext in include_extensions}
        return {ext.lower() for ext in normalized if ext.lower() in SUPPORTED_EXTENSIONS} or SUPPORTED_EXTENSIONS

    def _normalize_exclude_dirs(self, exclude_dirs: list[str] | None) -> set[str]:
        normalized = set(DEFAULT_EXCLUDE_DIRS)
        if exclude_dirs:
            normalized.update(item.strip() for item in exclude_dirs if item.strip())
        return normalized

    def _scan_files(self) -> None:
        paths = self._discover_source_files()
        self.directory_counts = Counter(
            Path(path.relative_to(self.repo_root)).parts[0] if len(Path(path.relative_to(self.repo_root)).parts) > 1 else "."
            for path in paths
        )
        max_workers = min(8, max(2, (os.cpu_count() or 4)))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            for file_record, symbols, source in executor.map(self._parse_path, paths):
                self.files.append(file_record)
                self._files_by_path[file_record.path] = file_record
                self.symbols.extend(symbols)
                self.sources[file_record.path] = source
                if file_record.errors:
                    self.parse_errors.extend({"path": file_record.path, "error": err} for err in file_record.errors)
                if file_record.path.endswith(("main.py", "app.py", "index.js", "server.js", "manage.py")):
                    self.entry_points.append(file_record.path)

        self.files.sort(key=lambda item: item.path)
        self.symbols.sort(key=lambda item: (item.file_path, item.line_start, item.qualified_name))
        for symbol in self.symbols:
            self._symbols_by_id[symbol.id] = symbol
            self._symbols_by_name[symbol.name.lower()].append(symbol)
            self._symbols_by_name[symbol.qualified_name.lower()].append(symbol)

    def _discover_source_files(self) -> list[Path]:
        paths: list[Path] = []
        for path in sorted(self.repo_root.rglob("*")):
            if not path.is_file():
                continue
            if any(part in self.exclude_dirs or part.startswith(".git") for part in path.parts):
                self.skipped_files += 1
                continue
            language = detect_language(path)
            if language is None or path.suffix.lower() not in self.include_extensions:
                continue
            paths.append(path)
        return paths

    def _parse_path(self, path: Path) -> tuple[FileRecord, list[SymbolRecord], str]:
        language = detect_language(path)
        if language == "python":
            file_record, symbols = parse_python_file(path, self.repo_root)
        else:
            file_record, symbols = parse_javascript_file(path, self.repo_root)
        source = path.read_text(encoding="utf-8", errors="ignore")
        return file_record, symbols, source

    def _resolve_imports(self) -> None:
        for file_record in self.files:
            resolved: list[str] = []
            for raw_import in file_record.raw_imports:
                target = self._resolve_import_target(file_record, raw_import)
                if target:
                    resolved.append(target)
                    self.file_dependency_edges.append((file_record.path, target))
                    self.file_importers[target].add(file_record.path)
            file_record.imports = sorted(set(resolved))

    def _resolve_import_target(self, file_record: FileRecord, raw_import: str) -> str | None:
        current_path = Path(file_record.path)
        if file_record.language == "python":
            if raw_import.startswith("."):
                level = len(raw_import) - len(raw_import.lstrip("."))
                module = raw_import[level:]
                base = current_path.parent
                for _ in range(max(level - 1, 0)):
                    base = base.parent
                if module:
                    base = base / Path(module.replace(".", "/"))
                for candidate in [base.with_suffix(".py"), base / "__init__.py"]:
                    candidate_str = candidate.as_posix()
                    if candidate_str in self._files_by_path:
                        return candidate_str
                return None
            base = Path(raw_import.replace(".", "/"))
            for candidate in [base.with_suffix(".py"), base / "__init__.py"]:
                candidate_str = candidate.as_posix()
                if candidate_str in self._files_by_path:
                    return candidate_str
            return None

        if raw_import.startswith("."):
            try:
                base = (current_path.parent / raw_import).resolve().relative_to(self.repo_root.resolve())
            except ValueError:
                return None
            candidates = [
                base,
                base.with_suffix(".js"),
                base.with_suffix(".ts"),
                base.with_suffix(".jsx"),
                base.with_suffix(".tsx"),
                base / "index.js",
                base / "index.ts",
            ]
            for candidate in candidates:
                candidate_str = candidate.as_posix()
                if candidate_str in self._files_by_path:
                    return candidate_str
        return None

    def _resolve_symbol_calls(self) -> None:
        for symbol in self.symbols:
            for raw_call in symbol.calls:
                callee = self._resolve_callee(symbol, raw_call)
                if not callee:
                    continue
                self.symbol_call_edges.append((symbol.id, callee.id))
                self.symbol_callees[symbol.id].add(callee.id)
                self.symbol_callers[callee.id].add(symbol.id)

    def _resolve_callee(self, symbol: SymbolRecord, raw_call: str) -> SymbolRecord | None:
        call_name = raw_call.split(".")[-1].lower()
        candidates = self._symbols_by_name.get(call_name, [])
        if not candidates:
            return None
        same_file = [candidate for candidate in candidates if candidate.file_path == symbol.file_path]
        if same_file:
            return same_file[0]

        imported_files = set(self._files_by_path[symbol.file_path].imports)
        imported_candidates = [candidate for candidate in candidates if candidate.file_path in imported_files]
        if imported_candidates:
            return imported_candidates[0]
        return candidates[0]

    def _build_chunks(self) -> None:
        chunks: list[dict[str, Any]] = []
        for file_record in self.files:
            file_symbols = self._symbols_for_file(file_record.path)
            if file_symbols:
                for symbol in file_symbols:
                    chunks.append(self._make_symbol_chunk(symbol))
            else:
                chunks.append(self._make_file_chunk(file_record))
        self.chunks = chunks

    def _make_symbol_chunk(self, symbol: SymbolRecord) -> dict[str, Any]:
        retrieval_text = self._optimize_text_for_retrieval(symbol.snippet, symbol.language)
        return {
            "id": f"chunk:{symbol.id}",
            "kind": "symbol",
            "level": "function" if symbol.kind in {"function", "async_function", "method"} else symbol.kind,
            "title": symbol.qualified_name,
            "file_path": symbol.file_path,
            "language": symbol.language,
            "summary": self._summarize_symbol(symbol),
            "text": retrieval_text,
            "snippet": symbol.snippet,
            "graph_context": {
                "calls": symbol.calls,
                "direct_dependents": len(self.symbol_callers.get(symbol.id, set())),
            },
        }

    def _make_file_chunk(self, file_record: FileRecord) -> dict[str, Any]:
        source = self.sources.get(file_record.path, "")
        retrieval_text = self._optimize_text_for_retrieval(source, file_record.language)
        return {
            "id": f"chunk:file:{file_record.path}",
            "kind": "file",
            "level": "file",
            "title": file_record.path,
            "file_path": file_record.path,
            "language": file_record.language,
            "summary": self._summarize_file(file_record),
            "text": retrieval_text,
            "snippet": source[:1800],
            "graph_context": {
                "imports": file_record.imports,
                "direct_dependents": len(self.file_importers.get(file_record.path, set())),
            },
        }

    def _optimize_text_for_retrieval(self, text: str, language: str) -> str:
        if language == "python":
            text = re.sub(r"(?m)^\s*#.*$", "", text)
            text = re.sub(r'"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'', " ", text)
        if language == "javascript":
            text = re.sub(r"//.*$", "", text, flags=re.MULTILINE)
            text = re.sub(r"/\*[\s\S]*?\*/", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    def _build_search_index(self) -> None:
        self._search_items = []
        documents: list[str] = []
        for chunk in self.chunks:
            documents.append(
                " ".join(
                    [
                        chunk["title"],
                        chunk["file_path"],
                        chunk["summary"],
                        chunk["text"][:3000],
                    ]
                )
            )
            self._search_items.append(chunk)

        if TfidfVectorizer is None:
            self._vectorizer = None
            self._search_matrix = None
            return
        self._vectorizer = TfidfVectorizer(stop_words="english")
        if documents:
            self._search_matrix = self._vectorizer.fit_transform(documents)

    def _build_metrics(self) -> dict[str, Any]:
        duration_ms = round((time.perf_counter() - self._started_at) * 1000, 2)
        return {
            "analysis_duration_ms": duration_ms,
            "chunk_count": len(self.chunks),
            "parse_error_count": len(self.parse_errors),
            "skipped_files": self.skipped_files,
            "search_backend": "tfidf" if self._vectorizer is not None else "keyword-fallback",
            "files_per_second": round(len(self.files) / max(duration_ms / 1000, 0.001), 2),
            "repo_map_categories": len(self.repo_map),
            "function_cards": len(self.function_cards),
        }

    def _build_report(self) -> dict[str, Any]:
        inbound_calls = Counter(target for _, target in self.symbol_call_edges)
        summary = {
            "repo_name": self.repo_root.name,
            "repo_root": str(self.repo_root),
            "branch": self.branch,
            "total_files": len(self.files),
            "total_functions": sum(1 for symbol in self.symbols if symbol.kind in {"function", "async_function", "method"}),
            "total_classes": sum(1 for symbol in self.symbols if symbol.kind == "class"),
            "total_chunks": len(self.chunks),
            "languages": dict(Counter(file_record.language for file_record in self.files)),
            "entry_points": self.entry_points,
            "tech_stack": self.tech_stack,
            "filters": {
                "include_extensions": sorted(self.include_extensions),
                "exclude_dirs": sorted(self.exclude_dirs),
            },
            "biggest_files": [
                {
                    "path": file_record.path,
                    "line_count": file_record.line_count,
                    "imports": len(file_record.imports),
                }
                for file_record in sorted(self.files, key=lambda item: item.line_count, reverse=True)[:8]
            ],
            "most_used_functions": [
                {
                    "name": self._symbols_by_id[symbol_id].qualified_name,
                    "file_path": self._symbols_by_id[symbol_id].file_path,
                    "call_count": count,
                }
                for symbol_id, count in inbound_calls.most_common(10)
            ],
        }
        fingerprint = hashlib.sha1(
            "|".join(f"{item.path}:{item.line_count}:{item.language}" for item in self.files).encode("utf-8")
        ).hexdigest()[:12]
        return {
            "summary": summary,
            "metrics": self.metrics,
            "repo_fingerprint": fingerprint,
            "files": [self._serialize_file(file_record) for file_record in self.files],
            "symbols": [self._serialize_symbol(symbol) for symbol in self.symbols],
            "chunks": self.chunks,
            "repo_map": self.repo_map,
            "function_cards": self.function_cards,
            "folder_summaries": self.folder_summaries(),
            "hierarchy": self.build_hierarchy(),
            "file_graph": self._graph_payload("file"),
            "symbol_graph": self._graph_payload("symbol"),
            "architecture": self.explain_architecture(),
            "newcomer_guide": self.explain_architecture(audience="newcomer"),
            "refactor_suggestions": self.refactor_suggestions(),
            "dead_code": self.dead_code_candidates(),
            "improvements": self.improvements,
            "health": self.health,
            "diagrams": self.diagrams,
            "context_modes": self.context_modes,
            "sources": self.sources,
            "parse_errors": self.parse_errors,
            "retrieval_strategy": {
                "chunking": "function and class level, with file-level fallback",
                "search": self.metrics["search_backend"],
                "graph_context": True,
            },
        }

    def _serialize_file(self, file_record: FileRecord) -> dict[str, Any]:
        return {
            "path": file_record.path,
            "language": file_record.language,
            "line_count": file_record.line_count,
            "imports": file_record.imports,
            "errors": file_record.errors,
            "summary": self._summarize_file(file_record),
            "category": self._categorize_file(file_record),
            "responsibilities": self._file_responsibilities(file_record),
        }

    def _serialize_symbol(self, symbol: SymbolRecord) -> dict[str, Any]:
        return {
            "id": symbol.id,
            "name": symbol.name,
            "qualified_name": symbol.qualified_name,
            "kind": symbol.kind,
            "file_path": symbol.file_path,
            "language": symbol.language,
            "line_start": symbol.line_start,
            "line_end": symbol.line_end,
            "docstring": symbol.docstring,
            "snippet": symbol.snippet,
            "calls": symbol.calls,
            "parameters": symbol.parameters,
            "return_hint": symbol.return_hint,
            "summary": self._summarize_symbol(symbol),
        }

    def _graph_payload(self, kind: str) -> dict[str, Any]:
        if kind == "file":
            nodes = [
                {
                    "id": file_record.path,
                    "label": file_record.path,
                    "kind": "file",
                    "size": max(14, min(42, file_record.line_count // 6 + 14)),
                    "group": file_record.language,
                    "category": self._categorize_file(file_record),
                }
                for file_record in self.files
            ]
            links = [{"source": source, "target": target, "kind": "imports"} for source, target in self.file_dependency_edges]
            return {"nodes": nodes, "links": links}

        inbound_calls = Counter(target for _, target in self.symbol_call_edges)
        nodes = [
            {
                "id": symbol.id,
                "label": symbol.qualified_name,
                "kind": symbol.kind,
                "size": max(12, min(34, inbound_calls[symbol.id] * 3 + 12)),
                "group": symbol.file_path,
                "risk": self._estimate_symbol_risk(symbol)["score"],
            }
            for symbol in self.symbols
        ]
        links = [{"source": source, "target": target, "kind": "calls"} for source, target in self.symbol_call_edges]
        return {"nodes": nodes, "links": links}

    def impact_analysis(self, target: str) -> dict[str, Any]:
        symbol = self._match_symbol(target)
        if symbol:
            return self._impact_for_symbol(symbol)
        file_record = self._match_file(target)
        if file_record:
            return self._impact_for_file(file_record)
        raise KeyError(f"No symbol or file matched '{target}'.")

    def _impact_for_symbol(self, symbol: SymbolRecord) -> dict[str, Any]:
        direct_ids = sorted(self.symbol_callers.get(symbol.id, set()))
        indirect_ids, depth = self._reverse_reachable(symbol.id, self.symbol_callers)
        indirect_ids = sorted(indirect_ids.difference(direct_ids))
        affected_files = sorted({symbol.file_path} | {self._symbols_by_id[item].file_path for item in set(direct_ids) | set(indirect_ids)})
        suggested_tests = self._suggest_tests(affected_files, symbol.name)
        file_criticality = self._file_criticality(symbol.file_path)
        risk_breakdown = {
            "dependency_depth": depth,
            "number_of_dependents": len(direct_ids) + len(indirect_ids),
            "file_criticality": file_criticality,
            "test_coverage_gap": 1 if not suggested_tests else 0,
        }
        risk_score = min(
            100,
            depth * 12
            + len(direct_ids) * 9
            + len(indirect_ids) * 4
            + file_criticality * 15
            + risk_breakdown["test_coverage_gap"] * 20,
        )
        return {
            "target_type": "symbol",
            "target": symbol.qualified_name,
            "file_path": symbol.file_path,
            "direct_dependents": [self._serialize_symbol(self._symbols_by_id[item]) for item in direct_ids],
            "indirect_dependents": [self._serialize_symbol(self._symbols_by_id[item]) for item in indirect_ids],
            "affected_files": affected_files,
            "risk_score": risk_score,
            "risk_breakdown": risk_breakdown,
            "suggested_tests": suggested_tests,
            "explanation": (
                f"Changing {symbol.qualified_name} affects {len(direct_ids)} direct callers and "
                f"{len(indirect_ids)} transitive dependents across {len(affected_files)} files."
            ),
        }

    def _impact_for_file(self, file_record: FileRecord) -> dict[str, Any]:
        direct_files = sorted(self.file_importers.get(file_record.path, set()))
        indirect_files, depth = self._reverse_reachable(file_record.path, self.file_importers)
        indirect_files = sorted(indirect_files.difference(direct_files))
        suggested_tests = self._suggest_tests([file_record.path] + direct_files + indirect_files, Path(file_record.path).stem)
        file_criticality = self._file_criticality(file_record.path)
        risk_breakdown = {
            "dependency_depth": depth,
            "number_of_dependents": len(direct_files) + len(indirect_files),
            "file_criticality": file_criticality,
            "test_coverage_gap": 1 if not suggested_tests else 0,
        }
        risk_score = min(
            100,
            depth * 12
            + len(direct_files) * 9
            + len(indirect_files) * 4
            + file_criticality * 15
            + risk_breakdown["test_coverage_gap"] * 20,
        )
        return {
            "target_type": "file",
            "target": file_record.path,
            "direct_dependents": direct_files,
            "indirect_dependents": indirect_files,
            "affected_files": [file_record.path] + direct_files + indirect_files,
            "risk_score": risk_score,
            "risk_breakdown": risk_breakdown,
            "suggested_tests": suggested_tests,
            "explanation": (
                f"Changing {file_record.path} affects {len(direct_files)} direct importing files and "
                f"{len(indirect_files)} transitive dependents."
            ),
        }

    def flow_analysis(self, target: str, max_depth: int = 4) -> dict[str, Any]:
        symbol = self._match_symbol(target)
        if not symbol:
            raise KeyError(f"No symbol matched '{target}'.")
        visited = {symbol.id}
        queue: deque[tuple[str, int]] = deque([(symbol.id, 0)])
        steps: list[dict[str, Any]] = []
        while queue:
            current_id, depth = queue.popleft()
            current_symbol = self._symbols_by_id[current_id]
            steps.append(
                {
                    "depth": depth,
                    "symbol": current_symbol.qualified_name,
                    "file_path": current_symbol.file_path,
                    "kind": current_symbol.kind,
                    "calls": [self._symbols_by_id[item].qualified_name for item in sorted(self.symbol_callees.get(current_id, set()))],
                }
            )
            if depth >= max_depth:
                continue
            for callee_id in sorted(self.symbol_callees.get(current_id, set())):
                if callee_id in visited:
                    continue
                visited.add(callee_id)
                queue.append((callee_id, depth + 1))

        narrative = self._flow_narrative(steps)
        mermaid = self._flow_mermaid(steps)
        return {
            "target": symbol.qualified_name,
            "entry_file": symbol.file_path,
            "steps": steps,
            "narrative": narrative,
            "mermaid": mermaid,
        }

    def search(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        if not query.strip():
            return []
        if self._vectorizer is None or self._search_matrix is None or cosine_similarity is None:
            return self._fallback_search(query, limit)

        query_vector = self._vectorizer.transform([query])
        similarity_scores = cosine_similarity(query_vector, self._search_matrix).flatten()
        query_terms = {term.lower() for term in query.split() if term.strip()}

        ranked: list[tuple[float, dict[str, Any]]] = []
        for index, item in enumerate(self._search_items):
            keyword_hits = sum(
                1
                for term in query_terms
                if term in item["title"].lower() or term in item["summary"].lower() or term in item["snippet"].lower()
            )
            graph_boost = min(0.15, item["graph_context"].get("direct_dependents", 0) * 0.02)
            score = similarity_scores[index] * 0.68 + min(keyword_hits * 0.12, 0.32) + graph_boost
            ranked.append((score, item))

        return [self._serialize_search_result(score, item) for score, item in sorted(ranked, key=lambda pair: pair[0], reverse=True)[:limit]]

    def _fallback_search(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        query_terms = {term.lower() for term in query.split() if term.strip()}
        ranked: list[tuple[float, dict[str, Any]]] = []
        for item in self._search_items:
            haystack = f"{item['title']} {item['summary']} {item['snippet']}".lower()
            score = sum(1 for term in query_terms if term in haystack)
            if score:
                ranked.append((float(score), item))
        return [self._serialize_search_result(score, item) for score, item in sorted(ranked, key=lambda pair: pair[0], reverse=True)[:limit]]

    def _serialize_search_result(self, score: float, item: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": item["id"],
            "kind": item["kind"],
            "level": item["level"],
            "name": item["title"],
            "file_path": item["file_path"],
            "snippet": item["snippet"],
            "score": round(float(score), 4),
            "explanation": self._search_explanation(item),
        }

    def explain_architecture(self, audience: str = "engineer") -> dict[str, Any]:
        central_files = sorted(self.files, key=lambda item: len(self.file_importers.get(item.path, set())) + len(item.imports), reverse=True)[:5]
        headline = f"{self.repo_root.name} contains {len(self.files)} analyzed source files across {len(self.directory_counts)} top-level areas."
        engineer_narrative = (
            f"The repository is organized around {self._directory_summary(4)}. "
            f"Highest coordination load sits in {', '.join(item.path for item in central_files[:3]) or 'no dominant files'}, which makes these strong architecture entry points."
        )
        newcomer_narrative = (
            f"If you're new to this codebase, start with {', '.join(self.entry_points[:3]) or (central_files[0].path if central_files else 'the central files')} to understand how requests or startup flow through the system. "
            f"Then move into the busiest folders: {self._directory_summary(4)}."
        )
        return {
            "headline": headline,
            "audience": audience,
            "directories": [{"directory": name, "files": count} for name, count in self.directory_counts.most_common(8)],
            "central_files": [
                {
                    "path": item.path,
                    "fan_in": len(self.file_importers.get(item.path, set())),
                    "fan_out": len(item.imports),
                    "summary": self._summarize_file(item),
                }
                for item in central_files
            ],
            "narrative": newcomer_narrative if audience == "newcomer" else engineer_narrative,
        }

    def explain_symbol(self, target: str, audience: str = "engineer") -> dict[str, Any]:
        symbol = self._match_symbol(target)
        if not symbol:
            raise KeyError(f"No symbol matched '{target}'.")
        callers = [self._symbols_by_id[item].qualified_name for item in sorted(self.symbol_callers.get(symbol.id, set()))]
        calls = [self._symbols_by_id[item].qualified_name for item in sorted(self.symbol_callees.get(symbol.id, set()))]
        card = self._build_function_card(symbol)
        if audience == "newcomer":
            summary = (
                f"{symbol.qualified_name} is a {symbol.kind} in {symbol.file_path}. "
                f"Think of it as a building block that other parts of the code call when they need {symbol.name}."
            )
        else:
            summary = (
                f"{symbol.qualified_name} is a {symbol.kind} defined in {symbol.file_path} between lines {symbol.line_start} and {symbol.line_end}."
            )
        return {
            "target": symbol.qualified_name,
            "file_path": symbol.file_path,
            "summary": summary,
            "callers": callers,
            "calls": calls,
            "docstring": symbol.docstring,
            "snippet": symbol.snippet,
            "card": card,
        }

    def explain_file(self, target: str, audience: str = "engineer") -> dict[str, Any]:
        file_record = self._match_file(target)
        if not file_record:
            raise KeyError(f"No file matched '{target}'.")
        dependents = sorted(self.file_importers.get(file_record.path, set()))
        if audience == "newcomer":
            summary = (
                f"{file_record.path} is part of the {file_record.language} layer and acts like a module with {len(file_record.imports)} outgoing dependencies. "
                f"If you want to learn this repo, this file matters because {len(dependents)} other files depend on it."
            )
        else:
            summary = (
                f"{file_record.path} is a {file_record.language} file with {file_record.line_count} lines, {len(file_record.imports)} outgoing dependencies, and {len(dependents)} incoming dependents."
            )
        return {
            "target": file_record.path,
            "summary": summary,
            "imports": file_record.imports,
            "dependents": dependents,
            "errors": file_record.errors,
            "source_preview": self.sources.get(file_record.path, "")[:2000],
            "responsibilities": self._file_responsibilities(file_record),
            "category": self._categorize_file(file_record),
        }

    def dead_code_candidates(self) -> list[dict[str, Any]]:
        candidates = []
        for symbol in self.symbols:
            if symbol.kind not in {"function", "async_function", "method"}:
                continue
            if symbol.name.startswith("__") and symbol.name.endswith("__"):
                continue
            if "test" in symbol.file_path.lower():
                continue
            if self.symbol_callers.get(symbol.id):
                continue
            if symbol.file_path in self.entry_points:
                continue
            candidates.append(
                {
                    "name": symbol.qualified_name,
                    "file_path": symbol.file_path,
                    "reason": "No inbound calls were detected in the analyzed graph.",
                }
            )
        return candidates[:15]

    def refactor_suggestions(self) -> list[dict[str, Any]]:
        suggestions = []
        for file_record in sorted(self.files, key=lambda item: item.line_count, reverse=True):
            symbol_count = len(self._symbols_for_file(file_record.path))
            if file_record.line_count > 280 or symbol_count > 12:
                suggestions.append(
                    {
                        "target": file_record.path,
                        "reason": (
                            f"Large file ({file_record.line_count} lines, {symbol_count} symbols). "
                            "Consider splitting by responsibility or extracting utility modules."
                        ),
                    }
                )

        duplicate_names = Counter(symbol.name for symbol in self.symbols if symbol.kind in {"function", "method", "async_function"})
        for name, count in duplicate_names.items():
            if count >= 3:
                suggestions.append(
                    {
                        "target": name,
                        "reason": f"Function name appears {count} times across the repo. Standardizing naming or consolidating behavior may reduce confusion.",
                    }
                )
        return suggestions[:12]

    def build_hierarchy(self) -> dict[str, Any]:
        folders: dict[str, dict[str, Any]] = {}
        for file_record in self.files:
            folder = str(Path(file_record.path).parent)
            folders.setdefault(folder, {"folder": folder, "files": []})
            folders[folder]["files"].append(
                {
                    "path": file_record.path,
                    "summary": self._summarize_file(file_record),
                    "top_symbols": [
                        {
                            "name": symbol.qualified_name,
                            "kind": symbol.kind,
                            "summary": self._summarize_symbol(symbol),
                        }
                        for symbol in self._symbols_for_file(file_record.path)[:5]
                    ],
                }
            )
        return {"folders": list(folders.values())[:20]}

    def folder_summaries(self) -> list[dict[str, Any]]:
        summaries = []
        for folder, count in self.directory_counts.most_common(12):
            files = [item for item in self.files if self._folder_name(item.path) == folder]
            key_files = ", ".join(item.path for item in sorted(files, key=lambda entry: entry.line_count, reverse=True)[:3])
            summaries.append(
                {
                    "folder": folder,
                    "file_count": count,
                    "summary": f"{folder} contains {count} source files. Key files include {key_files or 'none'}.",
                }
            )
        return summaries

    def review_diff(self, diff_text: str) -> dict[str, Any]:
        touched_files = sorted(set(re.findall(r"^\+\+\+\s+b/(.+)$", diff_text, flags=re.MULTILINE)))
        file_impacts = []
        risk_scores = []
        for file_path in touched_files:
            file_record = self._match_file(file_path)
            if not file_record:
                continue
            impact = self._impact_for_file(file_record)
            file_impacts.append(impact)
            risk_scores.append(impact["risk_score"])
        return {
            "touched_files": touched_files,
            "summary": f"The diff touches {len(touched_files)} files. Highest predicted risk is {max(risk_scores) if risk_scores else 0}.",
            "risk_areas": file_impacts,
        }

    def ask_repo(self, question: str) -> dict[str, Any]:
        question = question.strip()
        if not question:
            return {
                "mode": "empty",
                "answer": "Ask about architecture, risky functions, where logic lives, or what happens when a flow runs.",
                "evidence": [],
                "next_steps": ["Try: What happens when /api/analyze is called?"],
            }

        target = self._extract_target_from_text(question)
        lowered = question.lower()

        if any(phrase in lowered for phrase in ["what happens", "flow", "when "]):
            if target and self._match_symbol(target):
                flow = self.flow_analysis(target)
                return {
                    "mode": "flow",
                    "answer": flow["narrative"],
                    "evidence": [f"{step['symbol']} ({step['file_path']})" for step in flow["steps"][:6]],
                    "next_steps": ["Open the first two functions in the flow and inspect their call chain."],
                    "mermaid": flow["mermaid"],
                }

        if any(phrase in lowered for phrase in ["change", "modify", "break", "risky"]):
            if target:
                try:
                    impact = self.impact_analysis(target)
                    return {
                        "mode": "impact",
                        "answer": impact["explanation"],
                        "evidence": impact["affected_files"][:8],
                        "next_steps": impact["suggested_tests"] or ["No matched tests; add coverage before refactoring."],
                    }
                except KeyError:
                    pass
            hotspots = self.improvements.get("risk_hotspots", [])[:5]
            return {
                "mode": "risk-hotspots",
                "answer": "These are the highest-risk areas based on dependency centrality, file size, and test gaps.",
                "evidence": [f"{item['target']} ({item['score']})" for item in hotspots],
                "next_steps": ["Inspect the top hotspot and the tests covering it."],
            }

        if any(phrase in lowered for phrase in ["refactor", "improve", "cleanup"]):
            suggestions = self.improvements.get("architecture_suggestions", [])[:4]
            refactors = self.refactor_suggestions()[:4]
            evidence = [item["current_issue"] for item in suggestions] + [item["target"] for item in refactors]
            return {
                "mode": "refactor",
                "answer": "The main refactor pressure comes from oversized coordinators, missing boundaries, and risky hotspots.",
                "evidence": evidence[:8],
                "next_steps": [item.get("suggestion", item.get("reason", "Review suggested refactors.")) for item in suggestions[:3]] or ["Start by splitting the most central file by responsibility."],
            }

        if any(phrase in lowered for phrase in ["architecture", "explain this repo", "interview"]):
            architecture = self.explain_architecture(audience="newcomer" if "interview" in lowered else "engineer")
            return {
                "mode": "architecture",
                "answer": architecture["narrative"],
                "evidence": [item["path"] for item in architecture["central_files"][:5]],
                "next_steps": ["Start with the top entry point, then inspect the busiest dependency hub."],
            }

        search_results = self.search(question, limit=5)
        if not search_results:
            return {
                "mode": "fallback",
                "answer": "I couldn't find a strong direct match. Try naming a function, file, or architecture concern explicitly.",
                "evidence": [],
                "next_steps": ["Try: Where is authentication handled?", "Try: Which files are risky?"],
            }

        return {
            "mode": "search",
            "answer": f"The strongest match is {search_results[0]['name']} in {search_results[0]['file_path']}.",
            "evidence": [f"{item['name']} ({item['file_path']})" for item in search_results],
            "next_steps": ["Open the top result and follow its imports or callees."],
        }

    def export_pack(self, kind: str, diff_text: str | None = None) -> dict[str, Any]:
        kind = kind.lower().strip()
        if kind == "claude":
            files = [
                self._export_file("repo_context.md", self._repo_context_markdown()),
                self._export_file("architecture.md", self._architecture_markdown()),
                self._export_file("function_map.md", self._function_map_markdown()),
                self._export_file("improvement_plan.md", self._improvement_plan_markdown()),
                self._export_file("dependency_graph.json", self._dependency_graph_json(), "application/json"),
            ]
            return {"kind": kind, "files": files}

        if kind == "interview":
            return {
                "kind": kind,
                "files": [self._export_file("interview_pack.md", self._interview_pack_markdown())],
            }

        if kind == "architecture":
            return {
                "kind": kind,
                "files": [
                    self._export_file("architecture.md", self._architecture_markdown()),
                    self._export_file("architecture_diagram.mmd", self.diagrams.get("pipeline_mermaid", ""), "text/plain"),
                ],
            }

        if kind == "pr":
            return {
                "kind": kind,
                "files": [self._export_file("pr_review_context.md", self._pr_review_context_markdown(diff_text or ""))],
            }

        if kind == "repo-intelligence-pack":
            return {
                "kind": kind,
                "files": [
                    self._export_file("repo_context.md", self._repo_context_markdown()),
                    self._export_file("architecture.md", self._architecture_markdown()),
                    self._export_file("function_map.md", self._function_map_markdown()),
                    self._export_file("improvement_plan.md", self._improvement_plan_markdown()),
                    self._export_file("architecture_diagram.mmd", self.diagrams.get("pipeline_mermaid", ""), "text/plain"),
                    self._export_file("dependency_graph.json", self._dependency_graph_json(), "application/json"),
                ],
            }

        raise KeyError(f"Unsupported export kind '{kind}'.")

    def _build_repo_map(self) -> list[dict[str, Any]]:
        categories: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for file_record in self.files:
            categories[self._categorize_file(file_record)].append(
                {
                    "path": file_record.path,
                    "summary": self._summarize_file(file_record),
                    "responsibilities": self._file_responsibilities(file_record),
                    "symbol_count": len(self._symbols_for_file(file_record.path)),
                    "imports": len(file_record.imports),
                }
            )
        ordered = []
        for category in REPO_MAP_ORDER:
            if categories.get(category):
                ordered.append({"name": category, "files": sorted(categories[category], key=lambda item: item["path"])})
        return ordered

    def _build_function_cards(self) -> list[dict[str, Any]]:
        cards = []
        for symbol in self.symbols:
            if symbol.kind not in {"function", "async_function", "method"}:
                continue
            cards.append(self._build_function_card(symbol))
        cards.sort(key=lambda item: (-item["risk_score"], item["file_path"], item["function"]))
        return cards

    def _build_function_card(self, symbol: SymbolRecord) -> dict[str, Any]:
        risk = self._estimate_symbol_risk(symbol)
        calls = [self._symbols_by_id[item].qualified_name for item in sorted(self.symbol_callees.get(symbol.id, set()))]
        inputs = symbol.parameters or ["None inferred"]
        return {
            "id": symbol.id,
            "function": symbol.qualified_name,
            "file_path": symbol.file_path,
            "purpose": self._symbol_purpose(symbol),
            "inputs": inputs,
            "output": symbol.return_hint or "Return type not inferred",
            "calls": calls[:8],
            "risk_score": risk["score"],
            "risk_label": risk["label"],
            "why_risky": risk["reason"],
            "summary": self._summarize_symbol(symbol),
        }

    def _build_improvements(self) -> dict[str, Any]:
        large_files = self._large_file_findings()
        god_functions = self._god_function_findings()
        missing_tests = self._missing_test_findings()
        risky_dependencies = self._risky_dependency_findings()
        duplicate_logic = self._duplicate_logic_findings()
        poor_naming = self._poor_naming_findings()
        dead_code = self.dead_code_candidates()
        architecture_bottlenecks = self._architecture_bottleneck_findings()
        security_issues = self._security_findings()
        performance_issues = self._performance_findings()
        architecture_suggestions = self._architecture_suggestions(large_files, architecture_bottlenecks, god_functions)
        risk_hotspots = self._risk_hotspots(risky_dependencies, architecture_bottlenecks, god_functions, missing_tests)

        return {
            "counts": {
                "large_files": len(large_files),
                "god_functions": len(god_functions),
                "missing_tests": len(missing_tests),
                "risk_hotspots": len(risk_hotspots),
                "dead_code": len(dead_code),
            },
            "sections": {
                "code_smells": large_files + god_functions + poor_naming,
                "large_files": large_files,
                "god_functions": god_functions,
                "missing_tests": missing_tests,
                "risky_dependencies": risky_dependencies,
                "duplicate_logic": duplicate_logic,
                "poor_naming": poor_naming,
                "dead_code": dead_code,
                "architecture_bottlenecks": architecture_bottlenecks,
                "security_issues": security_issues,
                "performance_issues": performance_issues,
            },
            "risk_hotspots": risk_hotspots,
            "architecture_suggestions": architecture_suggestions,
            "suggested_structure": self._suggested_structure(),
        }

    def _build_health_scores(self, improvements: dict[str, Any]) -> dict[str, Any]:
        counts = improvements.get("counts", {})
        large_files = counts.get("large_files", 0)
        god_functions = counts.get("god_functions", 0)
        missing_tests = counts.get("missing_tests", 0)
        dead_code = counts.get("dead_code", 0)
        risk_hotspots = counts.get("risk_hotspots", 0)

        architecture_health = max(10, 100 - large_files * 7 - risk_hotspots * 5 - len(self.parse_errors) * 6)
        maintainability_score = max(10, 100 - god_functions * 7 - missing_tests * 3 - dead_code * 2)

        if maintainability_score >= 80:
            maintainability = "High"
        elif maintainability_score >= 60:
            maintainability = "Medium"
        else:
            maintainability = "Low"

        return {
            "architecture_health": architecture_health,
            "maintainability_score": maintainability_score,
            "maintainability": maintainability,
            "risk_hotspots": risk_hotspots,
            "dead_code_candidates": dead_code,
            "missing_tests": missing_tests,
        }

    def _build_diagrams(self) -> dict[str, str]:
        return {
            "pipeline_mermaid": self._pipeline_mermaid(),
            "repo_map_mermaid": self._repo_map_mermaid(),
            "block_diagram": self._block_diagram_text(),
        }

    def _build_context_modes(self) -> dict[str, str]:
        return {
            "tiny": self._tiny_summary(),
            "medium": self._medium_summary(),
            "deep": self._deep_summary(),
            "claude": self._claude_summary(),
            "interview": self._interview_summary(),
        }

    def _symbols_for_file(self, file_path: str) -> list[SymbolRecord]:
        return [symbol for symbol in self.symbols if symbol.file_path == file_path]

    def _folder_name(self, file_path: str) -> str:
        parent = str(Path(file_path).parent)
        return "." if parent == "." else parent

    def _categorize_file(self, file_record: FileRecord) -> str:
        path = file_record.path.lower()
        if path.startswith("static/") or any(token in path for token in ["frontend", "client", "ui", ".css", ".tsx", ".jsx"]):
            return "Frontend / UI"
        if path.startswith("tests/") or "/tests/" in path or file_record.path.startswith("test_"):
            return "Tests"
        if path.startswith("app/main") or "/api/" in path or path.endswith("main.py") or path.endswith("server.js"):
            return "Backend / API"
        if any(token in path for token in ["models", "db", "database", "schema", "repository"]):
            return "Data & Persistence"
        if any(token in path for token in ["docker", "compose", "terraform", "k8s", "infra", "workflow"]):
            return "Infrastructure"
        if any(token in path for token in ["config", "settings", ".env"]):
            return "Configuration"
        if any(token in path for token in ["analyzer", "parser", "graph", "retrieval", "services"]):
            return "Core Engine"
        return "Other"

    def _file_responsibilities(self, file_record: FileRecord) -> list[str]:
        source = self.sources.get(file_record.path, "")
        symbols = self._symbols_for_file(file_record.path)
        responsibilities: list[str] = []

        if file_record.path.endswith("app/main.py"):
            responsibilities.extend([self._route_responsibility(route) for route in re.findall(r'@app\.(?:get|post)\("([^"]+)"\)', source)[:4]])
        if "fetch(" in source:
            responsibilities.append("handles API calls")
        if "d3" in source.lower() or "graph" in source.lower():
            responsibilities.append("renders graphs")
        if "state =" in source or "const state =" in source:
            responsibilities.append("manages report state")
        if any(token in file_record.path.lower() for token in ["analyzer", "parser"]):
            responsibilities.append("drives repository analysis")
        if any(token in source for token in ["impact_analysis", "flow_analysis", "search("]):
            responsibilities.append("performs impact, flow, or search reasoning")
        if any(token in file_record.path.lower() for token in ["repository", "materialize"]):
            responsibilities.append("materializes repository input")
        if not responsibilities and symbols:
            responsibilities.append(f"defines {len(symbols)} key symbols")
        if not responsibilities and file_record.imports:
            responsibilities.append(f"coordinates {len(file_record.imports)} internal dependencies")
        if not responsibilities:
            responsibilities.append("supports the analyzed codebase")
        return responsibilities[:4]

    def _route_responsibility(self, route: str) -> str:
        if route == "/api/analyze":
            return "accepts repo input and starts analysis"
        if route.endswith("/impact"):
            return "computes change impact"
        if route.endswith("/flow"):
            return "traces function flow"
        if route.endswith("/search"):
            return "answers structural code search"
        return f"serves {route}"

    def _symbol_purpose(self, symbol: SymbolRecord) -> str:
        if symbol.docstring:
            return symbol.docstring.strip().splitlines()[0][:180]
        action = self._action_phrase(symbol.name)
        file_category = self._categorize_file(self._files_by_path[symbol.file_path])
        calls = [self._symbols_by_id[item].qualified_name for item in sorted(self.symbol_callees.get(symbol.id, set()))][:3]
        if calls:
            return f"{action.capitalize()} within the {file_category.lower()} layer, then delegates to {', '.join(calls)}."
        return f"{action.capitalize()} within the {file_category.lower()} layer."

    def _action_phrase(self, name: str) -> str:
        lowered = name.lower()
        if lowered.startswith(("get", "fetch", "load")):
            return f"fetches or loads {self._friendly_name(name)}"
        if lowered.startswith(("create", "build", "make")):
            return f"builds {self._friendly_name(name)}"
        if lowered.startswith(("parse", "extract")):
            return f"parses or extracts {self._friendly_name(name)}"
        if lowered.startswith(("validate", "check")):
            return f"validates {self._friendly_name(name)}"
        if lowered.startswith(("handle", "run", "process")):
            return f"handles {self._friendly_name(name)}"
        if lowered.startswith(("render", "show")):
            return f"renders {self._friendly_name(name)}"
        if lowered.startswith(("analyze", "summarize", "explain")):
            return f"analyzes {self._friendly_name(name)}"
        return f"drives {self._friendly_name(name)}"

    def _friendly_name(self, name: str) -> str:
        spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name).replace("_", " ")
        return spaced.strip().lower()

    def _estimate_symbol_risk(self, symbol: SymbolRecord) -> dict[str, Any]:
        direct = len(self.symbol_callers.get(symbol.id, set()))
        indirect, depth = self._reverse_reachable(symbol.id, self.symbol_callers)
        score = min(
            100,
            direct * 12 + max(len(indirect) - direct, 0) * 4 + self._file_criticality(symbol.file_path) * 15 + min(len(symbol.parameters), 6) * 4,
        )
        if score >= 75:
            label = "High"
        elif score >= 45:
            label = "Medium"
        else:
            label = "Low"
        reason = f"{direct} direct dependents, depth {depth}, and file criticality {self._file_criticality(symbol.file_path)}."
        return {"score": score, "label": label, "reason": reason}

    def _large_file_findings(self) -> list[dict[str, Any]]:
        findings = []
        for file_record in self.files:
            symbol_count = len(self._symbols_for_file(file_record.path))
            if file_record.line_count < 220 and symbol_count < 10:
                continue
            findings.append(
                {
                    "target": file_record.path,
                    "severity": "medium" if file_record.line_count < 320 else "high",
                    "reason": f"{file_record.line_count} lines and {symbol_count} extracted symbols make this file harder to reason about.",
                }
            )
        return findings[:12]

    def _god_function_findings(self) -> list[dict[str, Any]]:
        findings = []
        for symbol in self.symbols:
            if symbol.kind not in {"function", "async_function", "method"}:
                continue
            span = symbol.line_end - symbol.line_start + 1
            if span < 35 and len(symbol.parameters) < 5 and len(symbol.calls) < 7:
                continue
            findings.append(
                {
                    "target": symbol.qualified_name,
                    "severity": "medium" if span < 60 else "high",
                    "reason": f"Spans {span} lines, takes {len(symbol.parameters)} inputs, and calls {len(symbol.calls)} functions.",
                }
            )
        return findings[:12]

    def _missing_test_findings(self) -> list[dict[str, Any]]:
        test_files = [file_record.path.lower() for file_record in self.files if "test" in file_record.path.lower()]
        findings = []
        for file_record in self.files:
            lower_path = file_record.path.lower()
            if "test" in lower_path:
                continue
            stem = Path(file_record.path).stem.lower()
            if any(stem in test_path for test_path in test_files):
                continue
            findings.append(
                {
                    "target": file_record.path,
                    "severity": "medium",
                    "reason": "No obvious matching test file was found by filename or path heuristic.",
                }
            )
        return findings[:15]

    def _risky_dependency_findings(self) -> list[dict[str, Any]]:
        findings = []
        for file_record in self.files:
            fan_in = len(self.file_importers.get(file_record.path, set()))
            fan_out = len(file_record.imports)
            score = fan_in * 2 + fan_out + math.ceil(file_record.line_count / 120)
            if score < 6:
                continue
            findings.append(
                {
                    "target": file_record.path,
                    "score": score,
                    "reason": f"fan-in {fan_in}, fan-out {fan_out}, {file_record.line_count} lines.",
                }
            )
        findings.sort(key=lambda item: item["score"], reverse=True)
        return findings[:12]

    def _duplicate_logic_findings(self) -> list[dict[str, Any]]:
        findings = []
        snippet_buckets: dict[str, list[SymbolRecord]] = defaultdict(list)
        for symbol in self.symbols:
            if symbol.kind not in {"function", "async_function", "method"}:
                continue
            normalized = re.sub(r"\s+", " ", symbol.snippet).strip().lower()
            if len(normalized) < 80:
                continue
            digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()
            snippet_buckets[digest].append(symbol)

        for items in snippet_buckets.values():
            if len(items) < 2:
                continue
            names = ", ".join(item.qualified_name for item in items[:3])
            findings.append(
                {
                    "target": items[0].qualified_name,
                    "severity": "medium",
                    "reason": f"Similar function bodies found across {len(items)} symbols, including {names}.",
                }
            )
        return findings[:10]

    def _poor_naming_findings(self) -> list[dict[str, Any]]:
        findings = []
        for symbol in self.symbols:
            base = symbol.name.lower()
            if base in GENERIC_NAMES or len(base) <= 2:
                findings.append(
                    {
                        "target": symbol.qualified_name,
                        "severity": "low",
                        "reason": "The symbol name is generic and may hide the intent of the code path.",
                    }
                )
        return findings[:12]

    def _architecture_bottleneck_findings(self) -> list[dict[str, Any]]:
        findings = []
        for file_record in self.files:
            fan_in = len(self.file_importers.get(file_record.path, set()))
            fan_out = len(file_record.imports)
            if fan_in + fan_out < 4:
                continue
            if file_record.line_count < 120:
                continue
            findings.append(
                {
                    "target": file_record.path,
                    "score": fan_in + fan_out + math.ceil(file_record.line_count / 150),
                    "reason": f"Central dependency hub with {fan_in} incoming and {fan_out} outgoing links.",
                }
            )
        findings.sort(key=lambda item: item["score"], reverse=True)
        return findings[:10]

    def _security_findings(self) -> list[dict[str, Any]]:
        findings = []
        for file_record in self.files:
            source = self.sources.get(file_record.path, "")
            for label, pattern in SECURITY_PATTERNS.get(file_record.language, []):
                if pattern.search(source):
                    findings.append(
                        {
                            "target": file_record.path,
                            "severity": "high",
                            "reason": f"Possible security hotspot: {label}.",
                        }
                    )
        return findings[:10]

    def _performance_findings(self) -> list[dict[str, Any]]:
        findings = []
        for file_record in self.files:
            source = self.sources.get(file_record.path, "")
            for label, pattern in PERFORMANCE_PATTERNS.get(file_record.language, []):
                if pattern.search(source):
                    findings.append(
                        {
                            "target": file_record.path,
                            "severity": "medium",
                            "reason": f"Potential performance concern: {label}.",
                        }
                    )
        return findings[:10]

    def _architecture_suggestions(
        self,
        large_files: list[dict[str, Any]],
        architecture_bottlenecks: list[dict[str, Any]],
        god_functions: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        suggestions = []
        if large_files:
            suggestions.append(
                {
                    "current_issue": f"{large_files[0]['target']} is acting as a large coordinator file.",
                    "suggestion": "Split route handling, parsing, graph logic, and exports into dedicated modules.",
                }
            )
        if architecture_bottlenecks:
            suggestions.append(
                {
                    "current_issue": f"{architecture_bottlenecks[0]['target']} is a dependency bottleneck.",
                    "suggestion": "Introduce clearer boundaries between API, core analysis, retrieval, and reporting layers.",
                }
            )
        if god_functions:
            suggestions.append(
                {
                    "current_issue": f"{god_functions[0]['target']} is taking on too much logic in one place.",
                    "suggestion": "Extract subroutines for data preparation, risk scoring, and response formatting.",
                }
            )
        suggestions.append(
            {
                "current_issue": "The repository would benefit from stronger separation between parsing, graphing, and reporting concerns.",
                "suggestion": "Consider a structure like app/api, app/core, app/parsers, app/graph, app/retrieval, and app/reports.",
            }
        )
        return suggestions[:6]

    def _risk_hotspots(
        self,
        risky_dependencies: list[dict[str, Any]],
        architecture_bottlenecks: list[dict[str, Any]],
        god_functions: list[dict[str, Any]],
        missing_tests: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        hotspots: list[dict[str, Any]] = []
        for item in risky_dependencies[:4]:
            hotspots.append({"target": item["target"], "score": item["score"], "reason": item["reason"]})
        for item in architecture_bottlenecks[:3]:
            hotspots.append({"target": item["target"], "score": item["score"] + 4, "reason": item["reason"]})
        for item in god_functions[:3]:
            hotspots.append({"target": item["target"], "score": 55, "reason": item["reason"]})
        for item in missing_tests[:3]:
            hotspots.append({"target": item["target"], "score": 48, "reason": item["reason"]})
        hotspots.sort(key=lambda item: item["score"], reverse=True)
        return hotspots[:8]

    def _suggested_structure(self) -> str:
        return (
            "app/\n"
            "  api/\n"
            "  core/\n"
            "  parsers/\n"
            "  graph/\n"
            "  retrieval/\n"
            "  reports/\n"
            "static/\n"
            "tests/\n"
        )

    def _pipeline_mermaid(self) -> str:
        return (
            "flowchart TD\n"
            "A[Repo Input] --> B[Materialize Repository]\n"
            "B --> C[Parse Files]\n"
            "C --> D[Extract Symbols]\n"
            "D --> E[Build Dependency Graph]\n"
            "E --> F[Generate Report]\n"
            "F --> G[Repo Intelligence Pack]\n"
            "G --> H[Ask Repo / Exports / UI]\n"
        )

    def _repo_map_mermaid(self) -> str:
        lines = ["flowchart LR"]
        root = self.repo_root.name.replace("-", "_")
        lines.append(f"root[{self.repo_root.name}]")
        for index, category in enumerate(self.repo_map[:5], start=1):
            cat_id = f"cat{index}"
            lines.append(f"root --> {cat_id}[{category['name']}]")
            for item_index, file_item in enumerate(category["files"][:3], start=1):
                file_id = f"{cat_id}_{item_index}"
                label = file_item["path"].replace('"', "")
                lines.append(f"{cat_id} --> {file_id}[{label}]")
        return "\n".join(lines) + "\n"

    def _block_diagram_text(self) -> str:
        return (
            "User\n"
            "  ↓\n"
            "Frontend\n"
            "  ↓\n"
            "FastAPI /api/analyze\n"
            "  ↓\n"
            "Repository Materializer\n"
            "  ↓\n"
            "Parser\n"
            "  ↓\n"
            "Symbol Extractor\n"
            "  ↓\n"
            "Dependency Graph\n"
            "  ↓\n"
            "Search / Impact / Flow Engine\n"
            "  ↓\n"
            "UI Report\n"
        )

    def _tiny_summary(self) -> str:
        central = ", ".join(item["path"] for item in self.explain_architecture()["central_files"][:3]) or "none"
        return (
            f"{self.repo_root.name} is a {', '.join(self.tech_stack) or 'multi-language'} repo with {len(self.files)} source files. "
            f"Start at {', '.join(self.entry_points[:2]) or central}. Main hotspots: {central}."
        )

    def _medium_summary(self) -> str:
        return self._repo_context_markdown(include_function_cards=False, max_function_cards=0)

    def _deep_summary(self) -> str:
        return self._repo_context_markdown(include_function_cards=True, max_function_cards=20)

    def _claude_summary(self) -> str:
        return self._repo_context_markdown(include_function_cards=True, max_function_cards=12, compressed=True)

    def _interview_summary(self) -> str:
        return self._interview_pack_markdown()

    def _repo_context_markdown(
        self,
        *,
        include_function_cards: bool = True,
        max_function_cards: int = 10,
        compressed: bool = False,
    ) -> str:
        lines = [
            f"# Repo Context: {self.repo_root.name}",
            "",
            f"- Branch: {self.branch}",
            f"- Files analyzed: {len(self.files)}",
            f"- Functions analyzed: {sum(1 for symbol in self.symbols if symbol.kind in {'function', 'async_function', 'method'})}",
            f"- Tech stack: {', '.join(self.tech_stack) or 'Not inferred'}",
            f"- Entry points: {', '.join(self.entry_points) or 'Not inferred'}",
            f"- Architecture health: {self.health.get('architecture_health', 'n/a')}/100",
            f"- Maintainability: {self.health.get('maintainability', 'Unknown')}",
            "",
            "## Repo Map",
            "",
        ]
        for category in self.repo_map:
            lines.append(f"### {category['name']}")
            for item in category["files"][: (2 if compressed else 4)]:
                lines.append(f"- `{item['path']}`")
                for responsibility in item["responsibilities"][: (2 if compressed else 3)]:
                    lines.append(f"  - {responsibility}")
            lines.append("")

        lines.extend([
            "## Architecture Summary",
            "",
            self.explain_architecture(audience="engineer")["narrative"],
            "",
            "## Risk Hotspots",
            "",
        ])
        for item in self.improvements.get("risk_hotspots", [])[:6]:
            lines.append(f"- `{item['target']}` ({item['score']}): {item['reason']}")
        lines.append("")

        if include_function_cards:
            lines.extend(["## Function Cards", ""])
            for card in self.function_cards[:max_function_cards]:
                lines.append(f"### {card['function']}")
                lines.append(f"- File: `{card['file_path']}`")
                lines.append(f"- Purpose: {card['purpose']}")
                lines.append(f"- Inputs: {', '.join(card['inputs'])}")
                lines.append(f"- Output: {card['output']}")
                if card["calls"]:
                    lines.append(f"- Calls: {', '.join(card['calls'][:5])}")
                lines.append(f"- Risk: {card['risk_label']} ({card['risk_score']}) because {card['why_risky']}")
                lines.append("")

        lines.extend([
            "## Improvement Plan",
            "",
            self._improvement_plan_markdown(include_header=False),
        ])
        return "\n".join(lines).strip() + "\n"

    def _architecture_markdown(self) -> str:
        architecture = self.explain_architecture(audience="engineer")
        lines = [
            f"# Architecture Report: {self.repo_root.name}",
            "",
            architecture["headline"],
            "",
            "## Narrative",
            "",
            architecture["narrative"],
            "",
            "## Central Files",
            "",
        ]
        for item in architecture["central_files"]:
            lines.append(f"- `{item['path']}` | fan-in {item['fan_in']} | fan-out {item['fan_out']} | {item['summary']}")
        lines.extend([
            "",
            "## Mermaid",
            "",
            "```mermaid",
            self.diagrams.get("pipeline_mermaid", "").rstrip(),
            "```",
            "",
            "## Repo Map",
            "",
        ])
        for category in self.repo_map:
            lines.append(f"### {category['name']}")
            for item in category["files"][:4]:
                lines.append(f"- `{item['path']}`")
                for responsibility in item["responsibilities"][:3]:
                    lines.append(f"  - {responsibility}")
            lines.append("")
        return "\n".join(lines).strip() + "\n"

    def _function_map_markdown(self) -> str:
        lines = [f"# Function Map: {self.repo_root.name}", ""]
        for card in self.function_cards[:30]:
            lines.extend([
                f"## {card['function']}",
                f"- File: `{card['file_path']}`",
                f"- Purpose: {card['purpose']}",
                f"- Inputs: {', '.join(card['inputs'])}",
                f"- Output: {card['output']}",
                f"- Calls: {', '.join(card['calls']) or 'No resolved callees'}",
                f"- Risk: {card['risk_label']} ({card['risk_score']}) because {card['why_risky']}",
                "",
            ])
        return "\n".join(lines).strip() + "\n"

    def _improvement_plan_markdown(self, *, include_header: bool = True) -> str:
        lines = [f"# Improvement Plan: {self.repo_root.name}", ""] if include_header else []
        lines.extend([
            f"- Architecture health: {self.health.get('architecture_health', 'n/a')}/100",
            f"- Maintainability: {self.health.get('maintainability', 'Unknown')}",
            f"- Risk hotspots: {self.health.get('risk_hotspots', 0)}",
            f"- Dead code candidates: {self.health.get('dead_code_candidates', 0)}",
            f"- Missing tests: {self.health.get('missing_tests', 0)}",
            "",
            "## Suggested Refactors",
            "",
        ])
        for item in self.improvements.get("architecture_suggestions", [])[:6]:
            lines.append(f"- Current issue: {item['current_issue']}")
            lines.append(f"  - Suggestion: {item['suggestion']}")
        lines.extend([
            "",
            "## Recommended Structure",
            "",
            "```text",
            self.improvements.get("suggested_structure", self._suggested_structure()).rstrip(),
            "```",
            "",
            "## Code Smells",
            "",
        ])
        for item in self.improvements.get("sections", {}).get("code_smells", [])[:10]:
            lines.append(f"- `{item['target']}`: {item['reason']}")
        return "\n".join(lines).strip() + "\n"

    def _interview_pack_markdown(self) -> str:
        architecture = self.explain_architecture(audience="newcomer")
        hotspots = self.improvements.get("risk_hotspots", [])[:5]
        lines = [
            f"# Interview Pack: {self.repo_root.name}",
            "",
            "## One-Liner",
            "",
            f"{self.repo_root.name} is a code intelligence system that parses repositories into dependency-aware summaries, impact analysis, and exportable context packs.",
            "",
            "## Architecture Talking Points",
            "",
            f"- {architecture['narrative']}",
            f"- The strongest technical differentiator is the dependency graph plus function-level retrieval.",
            f"- The main value is helping a new engineer understand what breaks when a file or function changes.",
            "",
            "## Demo Flow",
            "",
            "1. Analyze a repo from URL, local path, or zip.",
            "2. Open the repo map to find the important backend, UI, and engine files.",
            "3. Click a function card to inspect its purpose, inputs, outputs, calls, and risk.",
            "4. Use impact analysis and ask-repo mode to explain changes or flows.",
            "5. Export the Repo Intelligence Pack for Claude or interview prep.",
            "",
            "## Risk Hotspots",
            "",
        ]
        for item in hotspots:
            lines.append(f"- `{item['target']}` ({item['score']}): {item['reason']}")
        return "\n".join(lines).strip() + "\n"

    def _pr_review_context_markdown(self, diff_text: str) -> str:
        review = self.review_diff(diff_text) if diff_text.strip() else {"summary": "No diff provided.", "touched_files": [], "risk_areas": []}
        lines = [
            f"# PR Review Context: {self.repo_root.name}",
            "",
            review["summary"],
            "",
            "## Touched Files",
            "",
        ]
        for file_path in review.get("touched_files", []):
            lines.append(f"- `{file_path}`")
        lines.extend(["", "## Risk Areas", ""])
        for item in review.get("risk_areas", []):
            lines.append(f"- `{item['target']}` ({item['risk_score']}): {item['explanation']}")
            if item.get("suggested_tests"):
                lines.append(f"  - Suggested tests: {', '.join(item['suggested_tests'])}")
        if not review.get("risk_areas"):
            lines.append("- No matched files were found in the analyzed repo graph.")
        return "\n".join(lines).strip() + "\n"

    def _dependency_graph_json(self) -> str:
        payload = {
            "file_graph": self._graph_payload("file"),
            "symbol_graph": self._graph_payload("symbol"),
        }
        return json.dumps(payload, indent=2)

    def _export_file(self, name: str, content: str, content_type: str = "text/markdown") -> dict[str, Any]:
        return {"name": name, "content": content, "content_type": content_type}

    def _search_explanation(self, item: dict[str, Any]) -> str:
        if item["kind"] == "file":
            file_record = self._files_by_path[item["file_path"]]
            return f"{file_record.path} is a {file_record.language} file with {file_record.line_count} lines and {len(file_record.imports)} resolved dependencies."
        return f"{item['title']} is a {item['level']} chunk in {item['file_path']} with graph-backed context included in retrieval."

    def _suggest_tests(self, affected_files: list[str], token_source: str) -> list[str]:
        token = token_source.lower()
        tests = []
        for file_record in self.files:
            lower_path = file_record.path.lower()
            if "test" not in lower_path and "/tests/" not in lower_path:
                continue
            if any(Path(affected).stem.lower() in lower_path for affected in affected_files) or token in lower_path:
                tests.append(file_record.path)
        return sorted(set(tests))[:10]

    def _reverse_reachable(self, start: str, reverse_graph: dict[str, set[str]]) -> tuple[set[str], int]:
        seen: set[str] = set()
        queue: deque[tuple[str, int]] = deque([(start, 0)])
        max_depth = 0
        while queue:
            current, depth = queue.popleft()
            max_depth = max(max_depth, depth)
            for neighbor in reverse_graph.get(current, set()):
                if neighbor in seen:
                    continue
                seen.add(neighbor)
                queue.append((neighbor, depth + 1))
        return seen, max_depth

    def _file_criticality(self, file_path: str) -> int:
        file_record = self._files_by_path[file_path]
        inbound_imports = len(self.file_importers.get(file_path, set()))
        entry_bonus = 1 if file_path in self.entry_points else 0
        return min(4, math.ceil(file_record.line_count / 180) + (1 if inbound_imports > 0 else 0) + entry_bonus)

    def _match_symbol(self, target: str) -> SymbolRecord | None:
        lookup = target.lower().strip()
        for symbol in self.symbols:
            if lookup in {symbol.id.lower(), symbol.name.lower(), symbol.qualified_name.lower()}:
                return symbol
        for symbol in self.symbols:
            if lookup in symbol.qualified_name.lower():
                return symbol
        return None

    def _match_file(self, target: str) -> FileRecord | None:
        lookup = target.lower().strip()
        for file_record in self.files:
            if lookup == file_record.path.lower():
                return file_record
        for file_record in self.files:
            if lookup in file_record.path.lower():
                return file_record
        return None

    def _extract_target_from_text(self, text: str) -> str | None:
        lowered = text.lower()
        for symbol in sorted(self.symbols, key=lambda item: len(item.qualified_name), reverse=True):
            if symbol.qualified_name.lower() in lowered or symbol.name.lower() in lowered:
                return symbol.qualified_name
        for file_record in sorted(self.files, key=lambda item: len(item.path), reverse=True):
            if file_record.path.lower() in lowered or Path(file_record.path).stem.lower() in lowered:
                return file_record.path
        return None

    def _summarize_file(self, file_record: FileRecord) -> str:
        symbol_count = len(self._symbols_for_file(file_record.path))
        return (
            f"{file_record.path} is a {file_record.language} file with {symbol_count} extracted symbols, "
            f"{len(file_record.imports)} resolved imports, and {file_record.line_count} lines."
        )

    def _summarize_symbol(self, symbol: SymbolRecord) -> str:
        call_count = len(self.symbol_callers.get(symbol.id, set()))
        if symbol.docstring:
            doc_preview = symbol.docstring.strip().split("\n")[0][:120]
            return f"{symbol.qualified_name} is a {symbol.kind}. Doc hint: {doc_preview}. It currently has {call_count} known dependents."
        return f"{symbol.qualified_name} is a {symbol.kind} in {symbol.file_path} with {call_count} known dependents."

    def _detect_tech_stack(self) -> list[str]:
        detected: set[str] = set()
        raw_import_text = " ".join(" ".join(file_record.raw_imports) for file_record in self.files).lower()
        for hints in FRAMEWORK_HINTS.values():
            for token, label in hints.items():
                if token in raw_import_text:
                    detected.add(label)
        language_counts = Counter(file_record.language for file_record in self.files)
        if language_counts.get("python"):
            detected.add("Python")
        if language_counts.get("javascript"):
            detected.add("JavaScript/TypeScript")
        root_requirements = self.repo_root / "requirements.txt"
        package_json = self.repo_root / "package.json"
        if root_requirements.exists():
            detected.add("pip")
        if package_json.exists():
            detected.add("npm")
        return sorted(detected)

    def _directory_summary(self, limit: int) -> str:
        return ", ".join(f"{name} ({count})" for name, count in self.directory_counts.most_common(limit)) or "a small number of source directories"

    def _flow_narrative(self, steps: list[dict[str, Any]]) -> str:
        if not steps:
            return "No flow could be traced."
        first = steps[0]["symbol"]
        if len(steps) == 1:
            return f"{first} does not call into other known symbols in the analyzed graph."
        next_steps = ", ".join(item["symbol"] for item in steps[1:4])
        return f"The flow starts at {first} and then moves through {next_steps}. This gives a newcomer a practical call chain to inspect first."

    def _flow_mermaid(self, steps: list[dict[str, Any]]) -> str:
        if not steps:
            return "flowchart TD\nA[No flow]\n"
        lines = ["flowchart TD"]
        for index, step in enumerate(steps):
            node_id = f"n{index}"
            label = step["symbol"].replace('"', "")
            lines.append(f"{node_id}[{label}]")
            if index > 0:
                lines.append(f"n{index - 1} --> {node_id}")
        return "\n".join(lines) + "\n"
