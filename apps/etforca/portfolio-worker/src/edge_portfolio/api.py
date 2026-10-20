import hashlib
import logging
import os
import secrets
from contextlib import asynccontextmanager
from uuid import UUID
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, Request, Response

from . import store
from . import admin_store
from . import dashboard
from .catalog import canonicalize, directory
from starlette.middleware.gzip import GZipMiddleware
from .contracts import AnalysisInput, DraftInput, StrictModel
from pydantic import Field
from psycopg import Error as DatabaseError


@asynccontextmanager
async def lifespan(app):
    store.initialize()
    # Build local exchange calendars before accepting the first customer's save.
    from .market_data import expected_sessions, year_before
    for key in ('KR:069500', 'US:VOO'):
        end = dashboard_work.price_end(key, dashboard_work.today())
        expected_sessions(key, end, end)
        expected_sessions(key, year_before(end), end)
    yield


app = FastAPI(lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=1000)
COOKIE = "edge_portfolio_session"


def owner(request):
    value = request.cookies.get(COOKIE, "")
    if len(value) != 64:
        raise HTTPException(401, "SESSION_REQUIRED")
    return hashlib.sha256(value.encode()).hexdigest()


@app.middleware("http")
async def origin_check(request, call_next):
    origin_secret = os.getenv('PORTFOLIO_ORIGIN_SECRET')
    if request.url.path != '/health' and origin_secret and not secrets.compare_digest(request.headers.get('x-portfolio-origin', ''), origin_secret):
        return Response(status_code=403)
    if request.method == "POST" and request.headers.get("origin") != os.getenv("PORTFOLIO_ORIGIN", "http://localhost:5180"):
        return Response(status_code=403)
    try:
        size = int(request.headers.get("content-length", "0"))
    except ValueError:
        return Response(status_code=400)
    limit = 128_000 if request.url.path == '/api/portfolio/workbook' else 32_000
    if size > limit:
        return Response(status_code=413)
    if request.method == "POST" and len(await request.body()) > limit:
        return Response(status_code=413)
    response = await call_next(request)
    response.headers["Cache-Control"] = "public, max-age=3600" if request.url.path == '/api/portfolio/catalog' else "no-store"
    return response


@app.get("/api/portfolio/session")
def session(request: Request, response: Response):
    value = request.cookies.get(COOKIE, '')
    if len(value) != 64:
        value = secrets.token_hex(32)
    # Renew the same owner so active visitors do not lose their saved workbook after 30 days.
    response.set_cookie(COOKIE, value, httponly=True, samesite="strict",
                        secure=os.getenv("PORTFOLIO_COOKIE_SECURE", "0") == "1", max_age=86400 * 30)
    is_test = None
    try:
        with store.connect() as db:
            is_test = bool(db.execute(f'SELECT 1 FROM ({admin_store.TEST_OWNERS}) AS tests WHERE owner=%s',
                (hashlib.sha256(value.encode()).hexdigest(),)).fetchone())
    except DatabaseError as error:
        logging.getLogger(__name__).warning('visitor_classification_unavailable code=%s', type(error).__name__)
    return {"estimated_seconds": 300, "is_test": is_test}


@app.get('/api/portfolio/catalog')
def catalog():
    return directory()


@app.post('/api/portfolio/presence')
def presence(request: Request):
    admin_store.heartbeat(owner(request))
    return {'status': 'recorded'}


def public(row):
    return {k: row[k] for k in ("id", "state", "stage", "input", "result", "failure", "created_at", "started_at", "finished_at")}


class ChatInput(StrictModel):
    question: str = Field(min_length=1, max_length=1000)
    mode: Literal['question', 'recovery'] = 'question'


class ReportRequest(StrictModel):
    email: str = Field(max_length=254, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class NewsletterRequest(ReportRequest):
    consent: Literal[True]


class NewsletterConfirmation(StrictModel):
    token: str = Field(min_length=40, max_length=64, pattern=r'^[A-Za-z0-9_-]+$')


@app.post('/api/portfolio/analyses/{job_id}/newsletter', status_code=202)
def newsletter_subscribe(job_id: str, body: NewsletterRequest, request: Request):
    from . import newsletter_store
    try:
        return {'status': newsletter_store.subscribe(job_id, owner(request), body.email), 'delivery_ready': newsletter_store.delivery_ready()}
    except ValueError as error:
        code = str(error)
        raise HTTPException(404 if code == 'NOT_FOUND' else 429 if code == 'SUBSCRIPTION_LIMIT' else 422, code) from None


@app.get('/api/portfolio/workbook/newsletter')
def dashboard_newsletter_status(request: Request):
    from . import newsletter_store
    return newsletter_store.dashboard_status(owner(request))


@app.post('/api/portfolio/workbook/newsletter', status_code=202)
def dashboard_newsletter_subscribe(body: NewsletterRequest, request: Request):
    return newsletter_subscribe(None, body, request)


@app.post('/api/portfolio/newsletter/confirm')
def newsletter_confirm(body: NewsletterConfirmation):
    from . import newsletter_store
    try:
        return {'status': newsletter_store.confirm(body.token), 'delivery_ready': newsletter_store.delivery_ready()}
    except ValueError:
        raise HTTPException(410, 'INVALID_CONFIRMATION') from None


@app.post("/api/portfolio/analyses/{job_id}/chat", status_code=202)
def chat(job_id: str, body: ChatInput, request: Request, idempotency_key: UUID = Header()):
    if not body.question.strip():
        raise HTTPException(422, "EMPTY_QUESTION")
    try:
        return public(store.submit(owner(request), str(idempotency_key), body.model_dump(exclude_defaults=True), parent_id=job_id))
    except ValueError as error:
        code = str(error)
        raise HTTPException(404 if code == "PARENT_NOT_FOUND" else 409 if code in ("CHAT_LIMIT", "RECOVERY_LIMIT", "IDEMPOTENCY_CONFLICT") else 429, code) from None


@app.post("/api/portfolio/analyses/{job_id}/report-request", status_code=201)
def report_request(job_id: str, body: ReportRequest, request: Request):
    if not store.request_report(job_id, owner(request), body.email):
        raise HTTPException(404, "NOT_FOUND")
    return {"status": "received", "delivery_status": "not_scheduled"}


@app.post("/api/portfolio/analyses", status_code=202)
def submit(body: AnalysisInput, request: Request, idempotency_key: UUID = Header()):
    # Drafts and historical reports may be incomplete; new paid/trial jobs must be computable.
    if body.input_mode == 'names':
        raise HTTPException(422, 'HOLDING_WEIGHTS_REQUIRED')
    from .execution import parse_funding
    contribution = body.preferences.contribution.strip()
    if not contribution:
        raise HTTPException(422, 'CONTRIBUTION_CHOICE_REQUIRED')
    if contribution.startswith('매달 꾸준히'):
        if not parse_funding(contribution):
            raise HTTPException(422, 'MONTHLY_AMOUNT_REQUIRED')
    elif contribution not in {'이미 다 매수한 상태에요', '목돈 한 번에', '아직 고민 중'} and not parse_funding(contribution):
        raise HTTPException(422, 'CONTRIBUTION_CHOICE_REQUIRED')
    try:
        body = canonicalize(body)
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    try:
        return public(store.submit(owner(request), str(idempotency_key), body.model_dump(mode="json"), reuse=True))
    except ValueError as error:
        code = str(error)
        raise HTTPException(404 if code == 'NOT_FOUND' else 409 if code == "IDEMPOTENCY_CONFLICT" else 429, code) from None


@app.post('/api/portfolio/drafts/{draft_id}')
def save_draft(draft_id: UUID, body: DraftInput, request: Request):
    try:
        body = canonicalize(body)
        store.save_draft(str(draft_id), owner(request), body.model_dump(mode='json'))
    except ValueError as error:
        code = str(error)
        raise HTTPException(404 if code == 'NOT_FOUND' else 429 if code == 'DRAFT_LIMIT' else 422, code) from None
    return {'status': 'saved'}


@app.get('/api/portfolio/analyses/latest')
def latest_analysis(request: Request):
    row = store.latest(owner(request))
    return public(row) if row else None


@app.get("/api/portfolio/analyses/{job_id}")
def analysis(job_id: str, request: Request):
    row = store.read(job_id, owner(request))
    if not row:
        raise HTTPException(404, "NOT_FOUND")
    return public(row)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get('/api/portfolio/workbook')
def workbook(request: Request):
    saved = dashboard.read(owner(request))
    try:
        saved['tracking'] = dashboard_work.tracking(owner(request))
    except DatabaseError as error:
        logging.getLogger(__name__).warning('workbook_tracking_unavailable code=%s', type(error).__name__)
    return saved


@app.post('/api/portfolio/workbook')
def save_workbook(body: dashboard.Save, request: Request):
    try:
        saved = dashboard.save(owner(request), body)
    except ValueError as error:
        raise HTTPException(409 if str(error) == 'WORKBOOK_CONFLICT' else 422, str(error)) from None
    try:
        saved['tracking'] = dashboard_work.tracking(owner(request))
    except DatabaseError as error:
        logging.getLogger(__name__).warning('saved_workbook_tracking_unavailable code=%s', type(error).__name__)
    return saved


from . import dashboard_work


@app.get('/api/portfolio/workbook/tracking')
def workbook_tracking(request: Request):
    return dashboard_work.tracking(owner(request))


@app.post('/api/portfolio/backtests', status_code=202)
def backtest(body: dashboard_work.Backtest, request: Request):
    try:
        return dashboard_work.submit(owner(request), body)
    except ValueError as error:
        raise HTTPException(429 if str(error) in ('BACKTEST_LIMIT', 'QUEUE_FULL') else 409, str(error)) from None


@app.get('/api/portfolio/backtests/{key}')
def backtest_result(key: str, request: Request):
    result = dashboard_work.read_backtest(owner(request), key)
    if not result:
        raise HTTPException(404, 'NOT_FOUND')
    return result


@app.get('/api/portfolio/analyses/{job_id}/workbook')
def analysis_workbook(job_id: str, request: Request):
    row = store.read(job_id, owner(request))
    if not row or row['kind'] != 'analysis':
        raise HTTPException(404, 'NOT_FOUND')
    return dashboard.from_analysis(row)
