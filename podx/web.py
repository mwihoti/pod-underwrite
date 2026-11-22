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
def run_sample(request: Request, slug: str):
    sample = samples.by_slug(slug)
    if sample is None:
        raise HTTPException(404, "no such sample")
    req = engine.AuditRequest(task_id=sample.task_id, netlist_hex=sample.hex)
    _, audit_id = _run_audit(req)
    return RedirectResponse(f"/a/{audit_id}", status_code=303)


@app.get("/a/{audit_id:int}", response_class=HTMLResponse)
def report(request: Request, audit_id: int):
    row = store.get_audit(audit_id)
    if row is None:
        raise HTTPException(404, "no such audit")
    rep = row.get("report") or {}
    embed = f'<img src="{_ctx(request)["base"]}/badge/{audit_id}.svg" alt="PoD audit: {rep.get("verdict","")}">'
    return TEMPLATES.TemplateResponse(
        request,
        "report.html",
        _ctx(
            request,
            audit=row,
            rep=rep,
            embed=embed,
            audit_id=audit_id,
            tone=V.tone(rep.get("verdict", V.UNRESOLVED)),
        ),
    )


@app.get("/a/{audit_id}.json")
def report_json(audit_id: int):
    row = store.get_audit(audit_id)
    if row is None:
        raise HTTPException(404, "no such audit")
    body = json.dumps(row.get("report") or {}, indent=1)
    return Response(content=body, media_type="application/json", headers={"Cache-Control": "public, max-age=60"})


@app.get("/badge/{audit_id}.svg")
def badge_svg(audit_id: int):
    row = store.get_audit(audit_id)
    if row is None:
        raise HTTPException(404, "no such audit")
    svg = badge_mod.render(row)
    etag = '"%s"' % (row.get("netlist_sha256") or str(audit_id))[:32]
    return Response(
        content=svg,
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=300", "ETag": etag},
    )


@app.get("/badge/{processor}/{circuit_id}.svg")
def badge_for_circuit(processor: str, circuit_id: int):
    row = store.latest_for(processor, circuit_id)
    if row is None:
        raise HTTPException(404, "no audit stored for that circuit yet")
    return Response(
        content=badge_mod.render(row),
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=300"},
    )


@app.get("/tasks", response_class=HTMLResponse)
def task_list(request: Request):
    rows = _cached_task_list()
    return TEMPLATES.TemplateResponse(request, "tasks.html", _ctx(request, tasks=rows))


@app.get("/stats", response_class=HTMLResponse)
def stats_page(request: Request):
    return TEMPLATES.TemplateResponse(
        request,
        "stats.html",
        _ctx(request, stats=store.stats(), events=store.events(12), recent=store.recent(30)),
    )


@app.get("/how", response_class=HTMLResponse)
def how(request: Request):
    return TEMPLATES.TemplateResponse(request, "how.html", _ctx(request))


# --------------------------------------------------------------------------- JSON API
@app.get("/api/tasks.json")
def api_tasks():
    return JSONResponse(_cached_task_list(), headers={"Cache-Control": "public, max-age=300"})


@app.post("/api/audit")
async def api_audit(request: Request):
    if not _rate_ok(request):
        raise HTTPException(429, "rate limit reached")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "body must be JSON")
    if not isinstance(body, dict) or "task_id" not in body or "netlist" not in body:
        raise HTTPException(400, "expected {task_id, netlist, circuit_id?, reference?}")
    req = engine.AuditRequest(
        task_id=int(body["task_id"]),
        netlist_hex=str(body["netlist"]),
        circuit_id=int(body["circuit_id"]) if body.get("circuit_id") else None,
        reference_hex=(str(body["reference"]) if body.get("reference") else None),
    )
    row, audit_id = _run_audit(req)
    rep = dict(row.get("report") or {})
    rep["audit_id"] = audit_id
    rep["url"] = f"{_ctx(request)['base']}/a/{audit_id}"
    return JSONResponse(rep, headers={"Cache-Control": "no-store"})


@app.get("/api/audit/{audit_id}.json")
def api_audit_get(audit_id: int):
    row = store.get_audit(audit_id)
    if row is None:
        raise HTTPException(404, "no such audit")
    return JSONResponse(row.get("report") or {}, headers={"Cache-Control": "public, max-age=60"})


# --------------------------------------------------------------------------- ops
@app.get("/healthz")
def healthz():
    s = settings()
    problems: list[str] = []
    try:
        store.init()
        store.stats()
        db_ok = True
    except Exception as exc:
        db_ok = False
        problems.append(f"db: {exc}")
    head = None
    try:
        head = chain.head()
    except Exception as exc:
        problems.append(f"rpc: {exc}")
    lag = None
    if head is not None:
        last = store.get_state("last_scan_head")
        if last is not None:
            lag = head - int(last)
    age = store.state_age("last_scan")
    body = {
        "ok": db_ok and head is not None and not problems,
        "db_writable": db_ok,
        "chain_id": s.chain_id,
        "head": head,
        "watcher_seconds_since_poll": age,
        "watcher_block_lag": lag,
        "engine_version": s.engine_version,
        "problems": problems,
    }
    return JSONResponse(body, status_code=200 if body["ok"] else 503, headers={"Cache-Control": "no-store"})


@app.post("/admin/fulfil")
async def admin_fulfil(request: Request):
    s = settings()
    token = request.headers.get("x-admin-token") or request.query_params.get("token", "")
    if not s.admin_token or token != s.admin_token:
        raise HTTPException(403, "admin token required")
    body = await request.json()
    ok = store.fulfil(int(body["audit_id"]), str(body.get("ref", "")))
    return {"fulfilled": ok}


@app.post("/tg/{secret}")
async def telegram_webhook(request: Request, secret: str):
    s = settings()
    if not s.telegram_secret or secret != s.telegram_secret:
        raise HTTPException(403, "bad webhook secret")
    update = await request.json()
    asyncio.create_task(telegram.handle_update(update))
    return PlainTextResponse("ok")
