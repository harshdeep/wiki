"""FastAPI app: routes, templates, auth middleware."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import auth, nkr_client, sources_writer, sync
from .nkr_client import get_status as _nkr_status
from .content import holder

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
log = logging.getLogger("wiki")

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def _static_version() -> str:
    """File-mtime cache buster for /static assets.

    The CSS link is rendered as `style.css?v=<mtime>`, so the URL changes
    whenever the file is edited and browsers cleanly drop the old copy.
    Computed per render — cheap (one stat) and always current.
    """
    css = BASE_DIR.parent / "static" / "style.css"
    try:
        return str(int(css.stat().st_mtime))
    except OSError:
        return "0"


templates.env.globals["static_version"] = _static_version


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("starting wiki")
    sync.startup_load()
    yield
    log.info("stopping wiki")


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None)
app.mount(
    "/static",
    StaticFiles(directory=str(BASE_DIR.parent / "static")),
    name="static",
)


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


async def require_auth(request: Request) -> None:
    if not auth.is_authed(request):
        raise HTTPException(status_code=307, headers={"location": f"/login?next={request.url.path}"})
    await sync.ensure_fresh()


@app.exception_handler(HTTPException)
async def http_exc(request: Request, exc: HTTPException):
    # Auth redirects are modeled as HTTPException(307) to keep the dep simple.
    if exc.status_code == 307 and "location" in (exc.headers or {}):
        return RedirectResponse(exc.headers["location"], status_code=303)
    if exc.status_code == 404:
        return templates.TemplateResponse(
            request, "404.html", {"message": exc.detail or "Not found"}, status_code=404
        )
    return Response(
        content=str(exc.detail or ""),
        status_code=exc.status_code,
        headers=exc.headers,
    )


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------


@app.get("/login", response_class=HTMLResponse)
async def login_get(request: Request, next: str = "/"):
    if auth.is_authed(request):
        return RedirectResponse(next or "/", status_code=303)
    return templates.TemplateResponse(
        request, "login.html", {"error": None, "next": next}
    )


@app.post("/login")
async def login_post(request: Request, password: str = Form(...), next: str = Form("/")):
    if not auth.check_password(password):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Incorrect password.", "next": next},
            status_code=401,
        )
    resp = RedirectResponse(next or "/", status_code=303)
    secure = os.environ.get("COOKIE_SECURE", "1") == "1"
    resp.set_cookie(
        auth.COOKIE_NAME,
        auth.issue_token(),
        max_age=auth.MAX_AGE_SECONDS,
        httponly=True,
        secure=secure,
        samesite="lax",
    )
    return resp


@app.post("/logout")
async def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(auth.COOKIE_NAME)
    return resp


@app.get("/healthz")
async def healthz():
    return {"ok": True, "loaded": holder.loaded}


# ---------------------------------------------------------------------------
# Content routes
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
async def home(request: Request, _: None = Depends(require_auth)):
    idx = holder.get()
    return templates.TemplateResponse(
        request, "home.html", {"topics": idx.topic_list(), "index": idx}
    )


@app.get("/topics/{slug}", response_class=HTMLResponse)
async def topic_page(request: Request, slug: str, _: None = Depends(require_auth)):
    idx = holder.get()
    topic = idx.topics.get(slug)
    if not topic:
        raise HTTPException(404, "Topic not found")
    concepts = [idx.concepts[s] for s, _t in topic.concepts if s in idx.concepts]
    sources = [idx.sources[s] for s, _t in topic.sources if s in idx.sources]
    # Split summaries out from concepts; they're more like digest entries.
    real_concepts = [c for c in concepts if not c.is_summary]
    summaries = [c for c in concepts if c.is_summary]
    return templates.TemplateResponse(
        request,
        "topic.html",
        {
            "topic": topic,
            "concepts": sorted(real_concepts, key=lambda c: c.mtime, reverse=True),
            "summaries": sorted(summaries, key=lambda c: c.mtime, reverse=True),
            "sources": sorted(sources, key=lambda s: s.mtime, reverse=True),
        },
    )


@app.get("/concepts/{slug}", response_class=HTMLResponse)
async def concept_page(request: Request, slug: str, _: None = Depends(require_auth)):
    idx = holder.get()
    c = idx.concepts.get(slug)
    if not c:
        raise HTTPException(404, "Concept not found")
    topic = None
    if c.topic:
        from .slugs import slugify

        topic = idx.topics.get(slugify(c.topic))
    return templates.TemplateResponse(
        request, "concept.html", {"c": c, "topic": topic}
    )


@app.post("/sources/new")
async def add_source(
    request: Request,
    url: str = Form(...),
    topic: str = Form(""),
    _: None = Depends(require_auth),
):
    wants_json = "application/json" in request.headers.get("accept", "").lower()

    u = url.strip()
    if not u.lower().startswith(("http://", "https://")):
        if wants_json:
            return JSONResponse(
                {"error": "URL must start with http:// or https://"},
                status_code=400,
            )
        raise HTTPException(400, "URL must start with http:// or https://")

    # If this URL was already added, return/redirect to the existing source
    # instead of writing a duplicate file or kicking off another nkr run.
    target = sources_writer.normalize_url(u)
    idx = holder.get()
    for existing in idx.sources.values():
        if existing.url and sources_writer.normalize_url(existing.url) == target:
            if wants_json:
                return JSONResponse({"slug": existing.slug, "duplicate": True})
            return RedirectResponse(f"/sources/{existing.slug}", status_code=303)

    t = topic.strip() or None
    await nkr_client.trigger_add(u, t)
    if wants_json:
        return JSONResponse({"status": "queued"})
    return RedirectResponse("/", status_code=303)


@app.get("/sources/status")
async def source_status(_: None = Depends(require_auth)):
    return JSONResponse(_nkr_status())


@app.get("/sources/{slug}", response_class=HTMLResponse)
async def source_page(request: Request, slug: str, _: None = Depends(require_auth)):
    idx = holder.get()
    s = idx.sources.get(slug)
    if not s:
        raise HTTPException(404, "Source not found")
    topic = None
    if s.topic:
        from .slugs import slugify

        topic = idx.topics.get(slugify(s.topic))
    # Concepts that cite this source.
    citing = [
        c for c in idx.concepts.values()
        if slug in c.sources and not c.is_summary
    ]
    summaries = [
        c for c in idx.concepts.values()
        if slug in c.sources and c.is_summary
    ]
    return templates.TemplateResponse(
        request,
        "source.html",
        {
            "s": s,
            "topic": topic,
            "citing": sorted(citing, key=lambda c: c.mtime, reverse=True),
            "summaries": sorted(summaries, key=lambda c: c.mtime, reverse=True),
        },
    )
