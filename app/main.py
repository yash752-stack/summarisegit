from __future__ import annotations

from collections import defaultdict
import uuid
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .schemas import AnalyzeResponse
from .services.analyzer import RepositoryAnalyzer
from .services.models import FileRecord, SymbolRecord
from .services.repository import materialize_repository


BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="summarisegit", version="1.0.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

REPORT_STORE: dict[str, dict] = {}


@app.get("/")
def home() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/api/analyze", response_model=AnalyzeResponse)
async def analyze_repository(
    local_path: str | None = None,
    repo_url: str | None = None,
    upload: UploadFile | None = File(default=None),
):
    upload_bytes = await upload.read() if upload is not None else None
    upload_name = upload.filename if upload is not None else None

    try:
        with materialize_repository(
            local_path=local_path,
            repo_url=repo_url,
            upload_bytes=upload_bytes,
            upload_name=upload_name,
        ) as repo_root:
            analyzer = RepositoryAnalyzer(repo_root)
            report = analyzer.analyze()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    report_id = str(uuid.uuid4())
    REPORT_STORE[report_id] = report
    return AnalyzeResponse(
        report_id=report_id,
        repo_name=report["summary"]["repo_name"],
        repo_root=report["summary"]["repo_root"],
        summary=report["summary"],
    )


@app.get("/api/reports/{report_id}")
def get_report(report_id: str) -> dict:
    report = REPORT_STORE.get(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found.")
    return report


@app.get("/api/reports/{report_id}/graph")
def get_graph(report_id: str, kind: str = Query(default="file")) -> dict:
    report = REPORT_STORE.get(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found.")
    if kind == "symbol":
        return report["symbol_graph"]
    return report["file_graph"]


@app.get("/api/reports/{report_id}/impact")
def get_impact(report_id: str, target: str) -> dict:
    report = REPORT_STORE.get(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found.")
    analyzer = _restore_analyzer(report)
    try:
        return analyzer.impact_analysis(target)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/reports/{report_id}/search")
def search_report(report_id: str, q: str) -> dict:
    report = REPORT_STORE.get(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found.")
    analyzer = _restore_analyzer(report)
    return {"query": q, "results": analyzer.search(q)}


@app.get("/api/reports/{report_id}/explain")
def explain_report(report_id: str, mode: str, target: str | None = None) -> dict:
    report = REPORT_STORE.get(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found.")
    analyzer = _restore_analyzer(report)
    if mode == "architecture":
        return report["architecture"]
    if mode == "refactor":
        return {"items": report["refactor_suggestions"]}
    if mode == "dead-code":
        return {"items": report["dead_code"]}
    if mode == "symbol":
        if not target:
            raise HTTPException(status_code=400, detail="target is required for mode=symbol")
        try:
            return analyzer.explain_symbol(target)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    if mode == "file":
        if not target:
            raise HTTPException(status_code=400, detail="target is required for mode=file")
        try:
            return analyzer.explain_file(target)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    raise HTTPException(status_code=400, detail="Unsupported mode.")


@app.get("/api/reports/{report_id}/source")
def get_source(report_id: str, path: str) -> dict:
    report = REPORT_STORE.get(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found.")
    source = report["sources"].get(path)
    if source is None:
        raise HTTPException(status_code=404, detail="Source file not found in report.")
    return {"path": path, "source": source}


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "reports_cached": len(REPORT_STORE)}



def _restore_analyzer(report: dict) -> RepositoryAnalyzer:
    analyzer = RepositoryAnalyzer(Path(report["summary"]["repo_root"]))
    analyzer.files = []
    analyzer.symbols = []
    analyzer.sources = report["sources"]
    analyzer.entry_points = report["summary"].get("entry_points", [])

    for file_payload in report["files"]:
        analyzer.files.append(
            FileRecord(
                path=file_payload["path"],
                abs_path=file_payload["path"],
                language=file_payload["language"],
                line_count=file_payload["line_count"],
                imports=file_payload["imports"],
                errors=file_payload["errors"],
            )
        )
    for symbol_payload in report["symbols"]:
        analyzer.symbols.append(
            SymbolRecord(
                id=symbol_payload["id"],
                name=symbol_payload["name"],
                qualified_name=symbol_payload["qualified_name"],
                kind=symbol_payload["kind"],
                file_path=symbol_payload["file_path"],
                language=symbol_payload["language"],
                line_start=symbol_payload["line_start"],
                line_end=symbol_payload["line_end"],
                docstring=symbol_payload["docstring"],
                snippet=symbol_payload["snippet"],
                calls=symbol_payload["calls"],
            )
        )
    analyzer._files_by_path = {item.path: item for item in analyzer.files}
    analyzer._symbols_by_id = {item.id: item for item in analyzer.symbols}
    analyzer._symbols_by_name = defaultdict(list)
    for symbol in analyzer.symbols:
        analyzer._symbols_by_name[symbol.name.lower()].append(symbol)
        analyzer._symbols_by_name[symbol.qualified_name.lower()].append(symbol)
    analyzer.file_dependency_edges = [(link["source"], link["target"]) for link in report["file_graph"]["links"]]
    analyzer.symbol_call_edges = [(link["source"], link["target"]) for link in report["symbol_graph"]["links"]]
    analyzer.file_importers = defaultdict(set)
    analyzer.symbol_callers = defaultdict(set)
    for source, target in analyzer.file_dependency_edges:
        analyzer.file_importers[target].add(source)
    for source, target in analyzer.symbol_call_edges:
        analyzer.symbol_callers[target].add(source)
    analyzer._build_search_index()
    return analyzer
