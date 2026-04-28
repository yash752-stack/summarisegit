from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import uuid
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .schemas import AnalyzeResponse
from .services.analyzer import RepositoryAnalyzer
from .services.repository import materialize_repository


BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="summarisegit", version="2.0.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@dataclass
class CachedAnalysis:
    report: dict[str, Any]
    analyzer: RepositoryAnalyzer


class DiffReviewRequest(BaseModel):
    diff_text: str


REPORT_STORE: dict[str, CachedAnalysis] = {}
REQUEST_CACHE: dict[str, str] = {}


@app.get("/")
def home() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/api/analyze", response_model=AnalyzeResponse)
async def analyze_repository(
    local_path: str | None = None,
    repo_url: str | None = None,
    branch: str | None = None,
    extensions: str | None = None,
    exclude_dirs: str | None = None,
    upload: UploadFile | None = File(default=None),
):
    upload_bytes = await upload.read() if upload is not None else None
    upload_name = upload.filename if upload is not None else None
    include_extensions = _split_csv(extensions)
    excluded = _split_csv(exclude_dirs)

    request_key = _request_cache_key(
        local_path=local_path,
        repo_url=repo_url,
        branch=branch,
        include_extensions=include_extensions,
        exclude_dirs=excluded,
        upload_name=upload_name,
    )
    if request_key in REQUEST_CACHE and REQUEST_CACHE[request_key] in REPORT_STORE:
        report_id = REQUEST_CACHE[request_key]
        cached = REPORT_STORE[report_id].report
        return AnalyzeResponse(
            report_id=report_id,
            repo_name=cached["summary"]["repo_name"],
            repo_root=cached["summary"]["repo_root"],
            summary=cached["summary"],
        )

    try:
        with materialize_repository(
            local_path=local_path,
            repo_url=repo_url,
            branch=branch,
            upload_bytes=upload_bytes,
            upload_name=upload_name,
        ) as repo_root:
            analyzer = RepositoryAnalyzer(
                repo_root,
                branch=branch,
                include_extensions=include_extensions,
                exclude_dirs=excluded,
            )
            report = analyzer.analyze()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    report_id = str(uuid.uuid4())
    REPORT_STORE[report_id] = CachedAnalysis(report=report, analyzer=analyzer)
    REQUEST_CACHE[request_key] = report_id
    return AnalyzeResponse(
        report_id=report_id,
        repo_name=report["summary"]["repo_name"],
        repo_root=report["summary"]["repo_root"],
        summary=report["summary"],
    )


@app.get("/api/reports/{report_id}")
def get_report(report_id: str) -> dict[str, Any]:
    cached = _get_cached(report_id)
    report = cached.report
    return {
        "summary": report["summary"],
        "metrics": report["metrics"],
        "repo_fingerprint": report["repo_fingerprint"],
        "files": report["files"],
        "symbols": report["symbols"],
        "folder_summaries": report["folder_summaries"],
        "hierarchy": report["hierarchy"],
        "architecture": report["architecture"],
        "newcomer_guide": report["newcomer_guide"],
        "refactor_suggestions": report["refactor_suggestions"],
        "dead_code": report["dead_code"],
        "parse_errors": report["parse_errors"],
        "retrieval_strategy": report["retrieval_strategy"],
    }


@app.get("/api/reports/{report_id}/graph")
def get_graph(report_id: str, kind: str = Query(default="file")) -> dict[str, Any]:
    report = _get_cached(report_id).report
    if kind == "symbol":
        return report["symbol_graph"]
    return report["file_graph"]


@app.get("/api/reports/{report_id}/impact")
def get_impact(report_id: str, target: str) -> dict[str, Any]:
    analyzer = _get_cached(report_id).analyzer
    try:
        return analyzer.impact_analysis(target)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/reports/{report_id}/flow")
def get_flow(report_id: str, target: str, max_depth: int = Query(default=4, ge=1, le=8)) -> dict[str, Any]:
    analyzer = _get_cached(report_id).analyzer
    try:
        return analyzer.flow_analysis(target, max_depth=max_depth)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/reports/{report_id}/search")
def search_report(report_id: str, q: str) -> dict[str, Any]:
    analyzer = _get_cached(report_id).analyzer
    return {"query": q, "results": analyzer.search(q)}


@app.get("/api/reports/{report_id}/explain")
def explain_report(
    report_id: str,
    mode: str,
    target: str | None = None,
    audience: str = Query(default="engineer"),
) -> dict[str, Any]:
    cached = _get_cached(report_id)
    report = cached.report
    analyzer = cached.analyzer

    if mode == "architecture":
        return analyzer.explain_architecture(audience=audience)
    if mode == "newcomer":
        return analyzer.explain_architecture(audience="newcomer")
    if mode == "refactor":
        return {"items": report["refactor_suggestions"]}
    if mode == "dead-code":
        return {"items": report["dead_code"]}
    if mode == "symbol":
        if not target:
            raise HTTPException(status_code=400, detail="target is required for mode=symbol")
        try:
            return analyzer.explain_symbol(target, audience=audience)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    if mode == "file":
        if not target:
            raise HTTPException(status_code=400, detail="target is required for mode=file")
        try:
            return analyzer.explain_file(target, audience=audience)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    raise HTTPException(status_code=400, detail="Unsupported mode.")


@app.get("/api/reports/{report_id}/source")
def get_source(report_id: str, path: str) -> dict[str, Any]:
    report = _get_cached(report_id).report
    source = report["sources"].get(path)
    if source is None:
        raise HTTPException(status_code=404, detail="Source file not found in report.")
    return {"path": path, "source": source}


@app.post("/api/reports/{report_id}/review-diff")
def review_diff(report_id: str, payload: DiffReviewRequest) -> dict[str, Any]:
    analyzer = _get_cached(report_id).analyzer
    return analyzer.review_diff(payload.diff_text)


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "reports_cached": len(REPORT_STORE)}



def _get_cached(report_id: str) -> CachedAnalysis:
    cached = REPORT_STORE.get(report_id)
    if not cached:
        raise HTTPException(status_code=404, detail="Report not found.")
    return cached



def _split_csv(value: str | None) -> list[str] | None:
    if not value:
        return None
    items = [item.strip() for item in value.split(",") if item.strip()]
    return items or None



def _request_cache_key(
    *,
    local_path: str | None,
    repo_url: str | None,
    branch: str | None,
    include_extensions: list[str] | None,
    exclude_dirs: list[str] | None,
    upload_name: str | None,
) -> str:
    return "|".join(
        [
            local_path or "",
            repo_url or "",
            branch or "",
            ",".join(include_extensions or []),
            ",".join(exclude_dirs or []),
            upload_name or "",
        ]
    )
