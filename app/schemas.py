from __future__ import annotations

from pydantic import BaseModel


class AnalyzeResponse(BaseModel):
    report_id: str
    repo_name: str
    repo_root: str
    summary: dict
