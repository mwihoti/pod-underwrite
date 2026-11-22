"""FastAPI application: the paste box, the report, the badge and the JSON API."""

from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import badge as badge_mod
from . import chain, engine, samples, store, tasks, telegram, verdict as V, watch
from .config import BASE_DIR, settings

TEMPLATES = Jinja2Templates(directory=str(BASE_DIR / "templates"))
STATIC = BASE_DIR / "static"

TONES = {code: V.tone(code) for code in (
    V.PASS, V.VECTOR_MISMATCH, V.BEST_FIRST_AT_RISK, V.TRIVIAL_TASK, V.INVALID,
    V.GATE_LIMIT, V.UNRESOLVED, V.INCONCLUSIVE, V.UNSUPPORTED,
)}

_hits: dict[str, deque[float]] = defaultdict(deque)


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = settings()
    store.init()
    try:
        rows = tasks.refresh()
        store.upsert_tasks([t.as_row() for t in rows])
    except Exception:
        cached = tasks.load_rows(store.task_rows())
        if not cached:
            pass  # index page will show a clear "task index unavailable" state
    task = None
    if s.enable_watcher:
        task = asyncio.create_task(watch.loop())
    try:
        yield
    finally:
        if task:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass


app = FastAPI(title="PoD Underwrite", version=settings().engine_version, lifespan=lifespan)
if STATIC.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


# --------------------------------------------------------------------------- helpers
def _client(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _rate_ok(request: Request) -> bool:
    s = settings()
    key = _client(request)
    now = time.monotonic()
    q = _hits[key]
    while q and now - q[0] > 3600:
        q.popleft()
    if len(q) >= s.rate_per_hour:
        return False
    q.append(now)
    return True


def _all_tasks() -> list[tasks.Task]:
    try:
        return tasks.all_tasks()
    except Exception:
        return sorted(
            (t for t in (tasks.get(i) for i in store.task_ids()) if t), key=lambda t: t.task_id
        )


def _cached_task_list() -> list[dict]:
    rows = store.task_rows()
    if rows:
        return rows
    try:
        return [t.as_row() for t in tasks.all_tasks()]
    except Exception:
        return []


def _ctx(request: Request, **kw: Any) -> dict:
    s = settings()
    base = s.base_url or str(request.base_url).rstrip("/")
    return {
        "request": request,
        "base": base,
        "tones": TONES,
        "samples": samples.SAMPLES,
        "pod_mining": s.pod_mining,
        "processor": s.processor,
        "chain_id": s.chain_id,
        "engine_version": s.engine_version,
        "banned": V.BANNED_WORDS,
        **kw,
    }


def _run_audit(req: engine.AuditRequest) -> tuple[dict, int]:
    result = engine.audit(req)
    audit_id = store.save_audit(result)
    row = store.get_audit(audit_id) or {"report": result.as_dict()}
    return row, audit_id


# --------------------------------------------------------------------------- pages
@app.get("/", response_class=HTMLResponse)
def index(request: Request, task: int | None = None):
    rows = _cached_task_list()
    if not rows:
        try:
            rows = [t.as_row() for t in tasks.refresh()]
        except Exception:
            rows = []
    recent = store.recent(8)
    stats = store.stats()
    return TEMPLATES.TemplateResponse(
        request,
        "index.html",
        _ctx(request, tasks=rows, recent=recent, stats=stats, selected=task),
    )


@app.post("/audit")
def run_form(
    request: Request,
    task_id: int = Form(...),
    netlist: str = Form(...),
    circuit_id: str = Form(""),
    reference: str = Form(""),
):
    if not _rate_ok(request):
        return TEMPLATES.TemplateResponse(
            request,
            "error.html",
            _ctx(request, title="Slow down", message="Rate limit reached for this address. Try again in an hour."),
            status_code=429,
        )
    try:
        cid = int(circuit_id) if circuit_id.strip() else None
    except ValueError:
        cid = None
    req = engine.AuditRequest(
        task_id=task_id,
        netlist_hex=netlist,
        circuit_id=cid,
        reference_hex=reference.strip() or None,
    )
    try:
        _, audit_id = _run_audit(req)
    except Exception as exc:
        return TEMPLATES.TemplateResponse(
            request,
            "error.html",
            _ctx(request, title="Audit failed", message=f"The engine could not finish: {type(exc).__name__}: {exc}"),
            status_code=500,
        )
    return RedirectResponse(f"/a/{audit_id}", status_code=303)


@app.get("/sample/{slug}")
