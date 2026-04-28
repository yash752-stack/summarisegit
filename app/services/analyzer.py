from __future__ import annotations

import math
from collections import Counter, defaultdict, deque
from pathlib import Path

try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
except ImportError:
    TfidfVectorizer = None
    cosine_similarity = None

from .models import FileRecord, SymbolRecord
from .parser import detect_language, parse_javascript_file, parse_python_file


class RepositoryAnalyzer:
    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root
        self.files: list[FileRecord] = []
        self.symbols: list[SymbolRecord] = []
        self.sources: dict[str, str] = {}
        self.file_dependency_edges: list[tuple[str, str]] = []
        self.symbol_call_edges: list[tuple[str, str]] = []
        self.file_importers: dict[str, set[str]] = defaultdict(set)
        self.symbol_callers: dict[str, set[str]] = defaultdict(set)
        self._symbols_by_id: dict[str, SymbolRecord] = {}
        self._symbols_by_name: dict[str, list[SymbolRecord]] = defaultdict(list)
        self._files_by_path: dict[str, FileRecord] = {}
        self._search_items: list[dict] = []
        self._search_matrix = None
        self._vectorizer: TfidfVectorizer | None = None
        self.entry_points: list[str] = []

    def analyze(self) -> dict:
        self._scan_files()
        self._resolve_imports()
        self._resolve_symbol_calls()
        self._build_search_index()
        return self._build_report()

    def _scan_files(self) -> None:
        for path in sorted(self.repo_root.rglob("*")):
            if not path.is_file():
                continue
            if any(part.startswith(".git") or part in {"node_modules", ".venv", "__pycache__"} for part in path.parts):
                continue
            language = detect_language(path)
            if language is None:
                continue
            if language == "python":
                file_record, symbols = parse_python_file(path, self.repo_root)
            else:
                file_record, symbols = parse_javascript_file(path, self.repo_root)
            self.files.append(file_record)
            self._files_by_path[file_record.path] = file_record
            self.symbols.extend(symbols)
            self.sources[file_record.path] = path.read_text(encoding="utf-8", errors="ignore")
            if file_record.path.endswith(("main.py", "app.py", "index.js", "server.js")):
                self.entry_points.append(file_record.path)

        for symbol in self.symbols:
            self._symbols_by_id[symbol.id] = symbol
            self._symbols_by_name[symbol.name.lower()].append(symbol)
            self._symbols_by_name[symbol.qualified_name.lower()].append(symbol)

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
        if len(imported_candidates) == 1:
            return imported_candidates[0]
        if imported_candidates:
            return imported_candidates[0]
        return candidates[0]

    def _build_search_index(self) -> None:
        documents: list[str] = []
        items: list[dict] = []

        for file_record in self.files:
            content = self.sources.get(file_record.path, "")
            documents.append(f"{file_record.path} {file_record.language} {' '.join(file_record.imports)} {content[:3000]}")
            items.append(
                {
                    "kind": "file",
                    "id": file_record.path,
                    "name": file_record.path,
                    "file_path": file_record.path,
                    "snippet": content[:700],
                }
            )

        for symbol in self.symbols:
            documents.append(
                " ".join(
                    [
                        symbol.name,
                        symbol.qualified_name,
                        symbol.kind,
                        symbol.file_path,
                        symbol.docstring,
                        symbol.snippet[:1500],
                    ]
                )
            )
            items.append(
                {
                    "kind": "symbol",
                    "id": symbol.id,
                    "name": symbol.qualified_name,
                    "file_path": symbol.file_path,
                    "snippet": symbol.snippet[:700],
                }
            )

        self._search_items = items
        if TfidfVectorizer is None:
            self._vectorizer = None
            self._search_matrix = None
            return
        self._vectorizer = TfidfVectorizer(stop_words="english")
        if documents:
            self._search_matrix = self._vectorizer.fit_transform(documents)

    def _build_report(self) -> dict:
        inbound_calls = Counter(target for _, target in self.symbol_call_edges)
        summary = {
            "repo_name": self.repo_root.name,
            "repo_root": str(self.repo_root),
            "total_files": len(self.files),
            "total_functions": sum(1 for symbol in self.symbols if symbol.kind in {"function", "async_function", "method"}),
            "total_classes": sum(1 for symbol in self.symbols if symbol.kind == "class"),
            "languages": dict(Counter(file_record.language for file_record in self.files)),
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
            "entry_points": self.entry_points,
        }
        return {
            "summary": summary,
            "files": [self._serialize_file(file_record) for file_record in self.files],
            "symbols": [self._serialize_symbol(symbol) for symbol in self.symbols],
            "file_graph": self._graph_payload("file"),
            "symbol_graph": self._graph_payload("symbol"),
            "architecture": self.explain_architecture(),
            "refactor_suggestions": self.refactor_suggestions(),
            "dead_code": self.dead_code_candidates(),
            "sources": self.sources,
        }

    def _serialize_file(self, file_record: FileRecord) -> dict:
        return {
            "path": file_record.path,
            "language": file_record.language,
            "line_count": file_record.line_count,
            "imports": file_record.imports,
            "errors": file_record.errors,
        }

    def _serialize_symbol(self, symbol: SymbolRecord) -> dict:
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
        }

    def _graph_payload(self, kind: str) -> dict:
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

    def impact_analysis(self, target: str) -> dict:
        symbol = self._match_symbol(target)
        if symbol:
            return self._impact_for_symbol(symbol)
        file_record = self._match_file(target)
        if file_record:
            return self._impact_for_file(file_record)
        raise KeyError(f"No symbol or file matched '{target}'.")

    def _impact_for_symbol(self, symbol: SymbolRecord) -> dict:
        direct_ids = sorted(self.symbol_callers.get(symbol.id, set()))
        indirect_ids, depth = self._reverse_reachable(symbol.id, self.symbol_callers)
        indirect_ids = sorted(indirect_ids.difference(direct_ids))
        affected_files = sorted({symbol.file_path} | {self._symbols_by_id[item].file_path for item in set(direct_ids) | set(indirect_ids)})
        suggested_tests = self._suggest_tests(affected_files, symbol.name)
        file_criticality = self._file_criticality(symbol.file_path)
        risk_breakdown = {
            "dependency_depth": depth,
            "direct_dependents": len(direct_ids),
            "indirect_dependents": len(indirect_ids),
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

    def _impact_for_file(self, file_record: FileRecord) -> dict:
        direct_files = sorted(self.file_importers.get(file_record.path, set()))
        indirect_files, depth = self._reverse_reachable(file_record.path, self.file_importers)
        indirect_files = sorted(indirect_files.difference(direct_files))
        suggested_tests = self._suggest_tests([file_record.path] + direct_files + indirect_files, Path(file_record.path).stem)
        file_criticality = self._file_criticality(file_record.path)
        risk_breakdown = {
            "dependency_depth": depth,
            "direct_dependents": len(direct_files),
            "indirect_dependents": len(indirect_files),
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

    def search(self, query: str, limit: int = 8) -> list[dict]:
        if not query.strip():
            return []
        if self._vectorizer is None or self._search_matrix is None or cosine_similarity is None:
            return self._fallback_search(query, limit)

        query_vector = self._vectorizer.transform([query])
        similarity_scores = cosine_similarity(query_vector, self._search_matrix).flatten()
        query_terms = {term.lower() for term in query.split() if term.strip()}

        ranked: list[tuple[float, dict]] = []
        for index, item in enumerate(self._search_items):
            keyword_hits = sum(1 for term in query_terms if term in item["name"].lower() or term in item["snippet"].lower())
            graph_boost = 0.0
            if item["kind"] == "symbol":
                graph_boost = min(0.15, len(self.symbol_callers.get(item["id"], set())) * 0.02)
            score = similarity_scores[index] * 0.65 + min(keyword_hits * 0.12, 0.35) + graph_boost
            ranked.append((score, item))

        results = []
        for score, item in sorted(ranked, key=lambda pair: pair[0], reverse=True)[:limit]:
            results.append(
                {
                    **item,
                    "score": round(float(score), 4),
                    "explanation": self._search_explanation(item),
                }
            )
        return results

    def _fallback_search(self, query: str, limit: int = 8) -> list[dict]:
        query_terms = {term.lower() for term in query.split() if term.strip()}
        ranked: list[tuple[float, dict]] = []
        for item in self._search_items:
            haystack = f"{item['name']} {item['snippet']}".lower()
            score = sum(1 for term in query_terms if term in haystack)
            if score:
                ranked.append((float(score), item))
        results = []
        for score, item in sorted(ranked, key=lambda pair: pair[0], reverse=True)[:limit]:
            results.append({**item, 'score': round(score, 4), 'explanation': self._search_explanation(item)})
        return results

    def _search_explanation(self, item: dict) -> str:
        if item["kind"] == "file":
            file_record = self._files_by_path[item["file_path"]]
            return f"{file_record.path} is a {file_record.language} file with {file_record.line_count} lines and {len(file_record.imports)} resolved dependencies."
        symbol = self._symbols_by_id[item["id"]]
        callers = len(self.symbol_callers.get(symbol.id, set()))
        return f"{symbol.qualified_name} is a {symbol.kind} in {symbol.file_path} with {callers} known dependents."

    def explain_architecture(self) -> dict:
        directory_counts = Counter(Path(file_record.path).parts[0] if len(Path(file_record.path).parts) > 1 else "." for file_record in self.files)
        central_files = sorted(self.files, key=lambda item: len(self.file_importers.get(item.path, set())) + len(item.imports), reverse=True)[:5]
        return {
            "headline": f"{self.repo_root.name} contains {len(self.files)} analyzed source files across {len(directory_counts)} top-level areas.",
            "directories": [{"directory": name, "files": count} for name, count in directory_counts.most_common(8)],
            "central_files": [
                {
                    "path": item.path,
                    "fan_in": len(self.file_importers.get(item.path, set())),
                    "fan_out": len(item.imports),
                }
                for item in central_files
            ],
            "narrative": self._architecture_narrative(directory_counts, central_files),
        }

    def _architecture_narrative(self, directory_counts: Counter, central_files: list[FileRecord]) -> str:
        directory_summary = ", ".join(f"{name} ({count})" for name, count in directory_counts.most_common(4))
        central_summary = ", ".join(item.path for item in central_files[:3]) if central_files else "no dominant files detected"
        return (
            f"The repository is organized around {directory_summary}. "
            f"Files with the highest coordination load include {central_summary}, which suggests these are good starting points for architecture walkthroughs."
        )

    def explain_symbol(self, target: str) -> dict:
        symbol = self._match_symbol(target)
        if not symbol:
            raise KeyError(f"No symbol matched '{target}'.")
        callers = [self._symbols_by_id[item].qualified_name for item in sorted(self.symbol_callers.get(symbol.id, set()))]
        return {
            "target": symbol.qualified_name,
            "file_path": symbol.file_path,
            "summary": (
                f"{symbol.qualified_name} is a {symbol.kind} defined in {symbol.file_path} "
                f"between lines {symbol.line_start} and {symbol.line_end}."
            ),
            "callers": callers,
            "calls": symbol.calls,
            "docstring": symbol.docstring,
            "snippet": symbol.snippet,
        }

    def explain_file(self, target: str) -> dict:
        file_record = self._match_file(target)
        if not file_record:
            raise KeyError(f"No file matched '{target}'.")
        dependents = sorted(self.file_importers.get(file_record.path, set()))
        return {
            "target": file_record.path,
            "summary": (
                f"{file_record.path} is a {file_record.language} file with {file_record.line_count} lines, "
                f"{len(file_record.imports)} outgoing dependencies, and {len(dependents)} incoming dependents."
            ),
            "imports": file_record.imports,
            "dependents": dependents,
            "errors": file_record.errors,
            "source_preview": self.sources.get(file_record.path, "")[:2000],
        }

    def dead_code_candidates(self) -> list[dict]:
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

    def refactor_suggestions(self) -> list[dict]:
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
