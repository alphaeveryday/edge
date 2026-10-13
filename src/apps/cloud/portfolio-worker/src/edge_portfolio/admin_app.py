"""SSM-only administrator HTTP service; publish its port on host loopback only."""
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import admin, store


@asynccontextmanager
async def lifespan(app):
    store.initialize()
    yield


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(admin.router)


@app.middleware('http')
async def local_browser_only(request, call_next):
    # IAM/SSM authenticates the tunnel. Reject DNS rebinding and cross-site browser reads.
    host = request.headers.get('host', '')
    if host not in ('localhost:18080', '127.0.0.1:18080'):
        response = Response(status_code=403)
    elif request.headers.get('origin') not in (None, 'http://' + host) or request.headers.get('sec-fetch-site') not in (None, 'same-origin', 'none'):
        response = Response(status_code=403)
    elif request.method not in ('GET', 'HEAD'):
        response = Response(status_code=405)
    else:
        response = await call_next(request)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Content-Security-Policy'] = "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
    return response


@app.get('/health')
def health():
    return {'service': 'orca-admin-ssm'}


@app.get('/')
def index():
    return RedirectResponse('/portfolio/admin.html')


app.mount('/portfolio', StaticFiles(directory=os.getenv('PORTFOLIO_ADMIN_UI_DIR', '/app/admin-ui'), check_dir=False))
