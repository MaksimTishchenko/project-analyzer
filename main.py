from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from app.service import analyze_github_project, analyze_local_project
from app.github_fetcher import (
    CloneFailed,
    GitHubFetcherError,
    GitHubFetcherNotImplemented,
    GitNotInstalled,
    InvalidRepoUrl,
)
from app.settings import settings

app = FastAPI(title="Python Project Analyzer", version="0.1.0")


@app.get("/")
async def root():
    return {"service": "Python Project Analyzer", "ok": True}


@app.get("/health")
async def health_check() -> dict[str, str]:
    return {"status": "ok"}


class AnalyzeLocalRequest(BaseModel):
    path: str
    use_llm: bool = False
    include_tech_stack: bool = True
    diagram_group_by_module: bool = True
    diagram_public_only: bool = False
    diagram_format: str = "plantuml"  # "plantuml" | "mermaid"
    diagram_max_classes: int = 40


class AnalyzeGitHubRequest(BaseModel):
    repo_url: str
    ref: str | None = None
    use_llm: bool = False
    include_tech_stack: bool = True
    diagram_format: str = "plantuml"
    diagram_max_classes: int = 40
    diagram_group_by_module: bool = True
    diagram_public_only: bool = False


def _validate_local_path(raw_path: str) -> Path:
    raw = (raw_path or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="path is required")

    p = Path(raw)

    if not p.exists():
        raise HTTPException(status_code=404, detail=f"Path not found: {raw}")

    if not p.is_dir():
        raise HTTPException(status_code=400, detail=f"Path is not a directory: {raw}")

    return p


def _extract_diagram(result: dict, fallback_format: str) -> tuple[str, str]:
    fmt = (fallback_format or "plantuml").strip().lower()
    text: str | None = None

    diagram = result.get("diagram") if isinstance(result, dict) else None
    if isinstance(diagram, dict):
        fmt = str(diagram.get("format") or fmt).strip().lower()
        t = diagram.get("text")
        if isinstance(t, str) and t.strip():
            text = t

    if not text:
        # backward compatibility
        t2 = result.get("diagram_plantuml") if isinstance(result, dict) else None
        if isinstance(t2, str) and t2.strip():
            text = t2
            fmt = "plantuml"

    if not text:
        raise HTTPException(status_code=500, detail="Diagram text is empty")

    if fmt not in {"plantuml", "mermaid"}:
        raise HTTPException(status_code=400, detail="diagram_format must be 'plantuml' or 'mermaid'")

    return fmt, text


def _diagram_response(fmt: str, text: str) -> PlainTextResponse:
    if fmt == "plantuml":
        media_type = "text/vnd.plantuml; charset=utf-8"
        filename = "diagram.puml"
    else:
        media_type = "text/markdown; charset=utf-8"
        filename = "diagram.mmd"

    headers = {
        "X-Diagram-Format": fmt,
        "Content-Disposition": f'inline; filename="{filename}"',
    }
    return PlainTextResponse(text, media_type=media_type, headers=headers)


@app.post("/analyze/local")
async def analyze_local(request: AnalyzeLocalRequest):
    p = _validate_local_path(request.path)
    try:
        return analyze_local_project(
            path=p,
            use_llm=request.use_llm,
            include_tech_stack=request.include_tech_stack,
            diagram_group_by_module=request.diagram_group_by_module,
            diagram_public_only=request.diagram_public_only,
            diagram_format=request.diagram_format,
            diagram_max_classes=request.diagram_max_classes,
        )
    except PermissionError as e:
        raise HTTPException(status_code=400, detail=f"Permission denied: {e}") from e
    except (ValueError, OSError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/analyze/local/diagram", response_class=PlainTextResponse)
async def analyze_local_diagram(request: AnalyzeLocalRequest):
    p = _validate_local_path(request.path)
    try:
        result = analyze_local_project(
            path=p,
            use_llm=request.use_llm,
            include_tech_stack=False,  # быстрее
            diagram_group_by_module=request.diagram_group_by_module,
            diagram_public_only=request.diagram_public_only,
            diagram_format=request.diagram_format,
            diagram_max_classes=request.diagram_max_classes,
        )
    except PermissionError as e:
        raise HTTPException(status_code=400, detail=f"Permission denied: {e}") from e
    except (ValueError, OSError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    fmt, text = _extract_diagram(result, request.diagram_format)
    return _diagram_response(fmt, text)


@app.post("/analyze/github")
async def analyze_github(request: AnalyzeGitHubRequest):
    try:
        return analyze_github_project(
            repo_url=request.repo_url,
            ref=request.ref,
            use_llm=request.use_llm,
            include_tech_stack=request.include_tech_stack,
            diagram_group_by_module=request.diagram_group_by_module,
            diagram_public_only=request.diagram_public_only,
            diagram_format=request.diagram_format,
            diagram_max_classes=request.diagram_max_classes,
            allow_clone=settings.github_fetcher_allow_clone,
            workspace_dir=settings.github_fetcher_workspace_dir,
            timeout_sec=settings.github_fetcher_timeout_sec,
            cache_ttl_hours=settings.github_fetcher_cache_ttl_hours,
        )
    except InvalidRepoUrl as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except GitHubFetcherNotImplemented as e:
        raise HTTPException(status_code=501, detail=str(e)) from e
    except (GitNotInstalled, CloneFailed) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except GitHubFetcherError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/analyze/github/diagram", response_class=PlainTextResponse)
async def analyze_github_diagram(request: AnalyzeGitHubRequest):
    try:
        result = analyze_github_project(
            repo_url=request.repo_url,
            ref=request.ref,
            use_llm=request.use_llm,
            include_tech_stack=False,  # быстрее
            diagram_group_by_module=request.diagram_group_by_module,
            diagram_public_only=request.diagram_public_only,
            diagram_format=request.diagram_format,
            diagram_max_classes=request.diagram_max_classes,
            allow_clone=settings.github_fetcher_allow_clone,
            workspace_dir=settings.github_fetcher_workspace_dir,
            timeout_sec=settings.github_fetcher_timeout_sec,
            cache_ttl_hours=settings.github_fetcher_cache_ttl_hours,
        )
    except InvalidRepoUrl as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except GitHubFetcherNotImplemented as e:
        raise HTTPException(status_code=501, detail=str(e)) from e
    except (GitNotInstalled, CloneFailed) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except GitHubFetcherError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    fmt, text = _extract_diagram(result, request.diagram_format)
    return _diagram_response(fmt, text)
