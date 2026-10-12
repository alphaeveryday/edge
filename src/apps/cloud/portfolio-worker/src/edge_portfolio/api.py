import hashlib
import os
import secrets
from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import FastAPI, Header, HTTPException, Request, Response

from . import store
from .contracts import AnalysisInput


@asynccontextmanager
async def lifespan(app):
    store.initialize()
    yield


app = FastAPI(lifespan=lifespan)
COOKIE = "edge_portfolio_session"


def owner(request):
    value = request.cookies.get(COOKIE, "")
    if len(value) != 64:
        raise HTTPException(401, "SESSION_REQUIRED")
    return hashlib.sha256(value.encode()).hexdigest()


@app.middleware("http")
async def origin_check(request, call_next):
    if request.method == "POST" and request.headers.get("origin") != os.getenv("PORTFOLIO_ORIGIN", "http://localhost:5180"):
        return Response(status_code=403)
    if int(request.headers.get("content-length", "0")) > 32_000:
        return Response(status_code=413)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/api/portfolio/session")
def session(request: Request, response: Response):
    if len(request.cookies.get(COOKIE, "")) != 64:
        response.set_cookie(COOKIE, secrets.token_hex(32), httponly=True, samesite="strict",
                            secure=os.getenv("PORTFOLIO_COOKIE_SECURE", "0") == "1", max_age=86400 * 30)
    return {"estimated_seconds": 300}


def public(row):
    return {k: row[k] for k in ("id", "state", "stage", "result", "failure", "created_at", "started_at", "finished_at")}


@app.post("/api/portfolio/analyses", status_code=202)
def submit(body: AnalysisInput, request: Request, idempotency_key: UUID = Header()):
    try:
        return public(store.submit(owner(request), str(idempotency_key), body.model_dump(mode="json")))
    except ValueError as error:
        code = str(error)
        raise HTTPException(409 if code == "IDEMPOTENCY_CONFLICT" else 429, code) from None


@app.get("/api/portfolio/analyses/{job_id}")
def analysis(job_id: str, request: Request):
    row = store.read(job_id, owner(request))
    if not row:
        raise HTTPException(404, "NOT_FOUND")
    return public(row)


@app.get("/health")
def health():
    return {"status": "ok"}
