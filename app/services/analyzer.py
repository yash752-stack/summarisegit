from __future__ import annotations

import hashlib
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
    },
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
        self._chunks_by_id: dict[str, dict[str, Any]] = {}
        self._search_items: list[dict[str, Any]] = []
        self._search_matrix = None
        self._vectorizer: TfidfVectorizer | None = None
        self.entry_points: list[str] = []
        self.tech_stack: list[str] = []
        self.directory_counts: Counter[str] = Counter()
        self.metrics: dict[str, Any] = {}
        self.parse_errors: list[dict[str, str]] = []
        self.skipped_files: int = 0
        self._started_at = time.perf_counter()
        self._report: dict[str, Any] | None = None

    def analyze(self) -> dict[str, Any]:
        self._scan_files()
        self._resolve_imports()
        self._resolve_symbol_calls()
        self._build_chunks()
        self._build_search_index()
        self.tech_stack = self._detect_tech_stack()
        self.metrics = self._build_metrics()
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
            for parsed in executor.map(self._parse_path, paths):
                file_record, symbols, source = parsed
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
            file_symbols = [symbol for symbol in self.symbols if symbol.file_path == file_record.path]
            if file_symbols:
                for symbol in file_symbols:
                    chunks.append(self._make_symbol_chunk(symbol))
            else:
                chunks.append(self._make_file_chunk(file_record))
        self.chunks = chunks
        self._chunks_by_id = {chunk["id"]: chunk for chunk in chunks}

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
        }

    def _build_report(self) -> dict[str, Any]:
        inbound_calls = Counter(target for _, target in self.symbol_call_edges)
        folder_summaries = self.folder_summaries()
        hierarchy = self.build_hierarchy()
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
            "folder_summaries": folder_summaries,
            "hierarchy": hierarchy,
            "file_graph": self._graph_payload("file"),
            "symbol_graph": self._graph_payload("symbol"),
            "architecture": self.explain_architecture(),
            "newcomer_guide": self.explain_architecture(audience="newcomer"),
            "refactor_suggestions": self.refactor_suggestions(),
            "dead_code": self.dead_code_candidates(),
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
        return {
            "target": symbol.qualified_name,
            "entry_file": symbol.file_path,
            "steps": steps,
            "narrative": narrative,
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
            keyword_hits = sum(1 for term in query_terms if term in item["title"].lower() or term in item["summary"].lower() or term in item["snippet"].lower())
            graph_boost = min(0.15, item["graph_context"].get("direct_dependents", 0) * 0.02)
            score = similarity_scores[index] * 0.68 + min(keyword_hits * 0.12, 0.32) + graph_boost
            ranked.append((score, item))

        results = []
        for score, item in sorted(ranked, key=lambda pair: pair[0], reverse=True)[:limit]:
            results.append(
                {
                    "id": item["id"],
                    "kind": item["kind"],
                    "level": item["level"],
                    "name": item["title"],
                    "file_path": item["file_path"],
                    "snippet": item["snippet"],
                    "score": round(float(score), 4),
                    "explanation": self._search_explanation(item),
                }
            )
        return results

    def _fallback_search(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        query_terms = {term.lower() for term in query.split() if term.strip()}
        ranked: list[tuple[float, dict[str, Any]]] = []
        for item in self._search_items:
            haystack = f"{item['title']} {item['summary']} {item['snippet']}".lower()
            score = sum(1 for term in query_terms if term in haystack)
            if score:
                ranked.append((float(score), item))
        results = []
        for score, item in sorted(ranked, key=lambda pair: pair[0], reverse=True)[:limit]:
            results.append(
                {
                    "id": item["id"],
                    "kind": item["kind"],
                    "level": item["level"],
                    "name": item["title"],
                    "file_path": item["file_path"],
                    "snippet": item["snippet"],
                    "score": round(float(score), 4),
                    "explanation": self._search_explanation(item),
                }
            )
        return results

    def _search_explanation(self, item: dict[str, Any]) -> str:
        if item["kind"] == "file":
            file_record = self._files_by_path[item["file_path"]]
            return f"{file_record.path} is a {file_record.language} file with {file_record.line_count} lines and {len(file_record.imports)} resolved dependencies."
        return f"{item['title']} is a {item['level']} chunk in {item['file_path']} with graph-backed context included in retrieval."

    def explain_architecture(self, audience: str = "engineer") -> dict[str, Any]:
        central_files = sorted(self.files, key=lambda item: len(self.file_importers.get(item.path, set())) + len(item.imports), reverse=True)[:5]
        headline = f"{self.repo_root.name} contains {len(self.files)} analyzed source files across {len(self.directory_counts)} top-level areas."
        engineer_narrative = (
            f"The repository is organized around {self._directory_summary(4)}. "
            f"Highest coordination load sits in {', '.join(item.path for item in central_files[:3]) or 'no dominant files'}, which makes these strong architecture entry points."
        )
        newcomer_narrative = (
            f"If you're new to this codebase, start with {', '.join(self.entry_points[:3]) or (central_files[0].path if central_files else 'the central files')} to understand how requests or program startup flow through the system. "
            f"Then look at the busiest folders: {self._directory_summary(4)}."
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
            "calls": symbol.calls,
            "docstring": symbol.docstring,
            "snippet": symbol.snippet,
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
            symbol_count = sum(1 for symbol in self.symbols if symbol.file_path == file_record.path)
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

        duplicate_names = Counter(symbol.name for symbol in self.symbols if symbol.kind in {"function", "method"})
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
                        for symbol in self.symbols
                        if symbol.file_path == file_record.path
                    ][:5],
                }
            )
        return {"folders": list(folders.values())[:20]}

    def folder_summaries(self) -> list[dict[str, Any]]:
        summaries = []
        for folder, count in self.directory_counts.most_common(12):
            files = [item for item in self.files if (str(Path(item.path).parent) if str(Path(item.path).parent) != "." else ".") == folder]
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

    def _summarize_file(self, file_record: FileRecord) -> str:
        symbol_count = sum(1 for symbol in self.symbols if symbol.file_path == file_record.path)
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
        for language, hints in FRAMEWORK_HINTS.items():
            for token, label in hints.items():
                if token in raw_import_text:
                    detected.add(label)
        language_counts = Counter(file_record.language for file_record in self.files)
        if language_counts.get("python"):
            detected.add("Python")
        if language_counts.get("javascript"):
            detected.add("JavaScript/TypeScript")
        if any(file_record.path.endswith("requirements.txt") for file_record in self.files):
            detected.add("pip")
        return sorted(detected)

    def _directory_summary(self, limit: int) -> str:
        return ", ".join(f"{name} ({count})" for name, count in self.directory_counts.most_common(limit)) or "a small number of source directories"

    def _flow_narrative(self, steps: list[dict[str, Any]]) -> str:
        if not steps:
            return "No flow could be traced."
        first = steps[0]["symbol"]
        immediate = ", ".join(steps[1]["symbol"] for steps in [steps] if len(steps) > 1)
        if len(steps) == 1:
            return f"{first} does not call into other known symbols in the analyzed graph."
        next_steps = ", ".join(item["symbol"] for item in steps[1:4])
        return f"The flow starts at {first} and then moves through {next_steps}. This gives a newcomer a practical call chain to inspect first."
