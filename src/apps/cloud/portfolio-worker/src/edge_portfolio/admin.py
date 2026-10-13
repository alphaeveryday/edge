"""Independent administrator sessions; never accept visitor cookies as authorization."""
import hashlib
import os
import re
import secrets
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import Field

from . import admin_store
from .contracts import StrictModel

router = APIRouter(prefix='/api/portfolio/admin')
COOKIE = 'edge_portfolio_admin'
PATH = '/api/portfolio/admin'
Kind = Literal['analyses', 'drafts', 'reports']


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def credential():
    value = os.getenv('PORTFOLIO_ADMIN_PASSWORD_SHA256', '')
    return value if re.fullmatch('[a-f0-9]{64}', value) else None


def require_admin(request: Request):
    token = request.cookies.get(COOKIE, '')
    version = credential()
    if not version or not re.fullmatch('[a-f0-9]{64}', token) or not admin_store.authenticated(digest(token), version):
        raise HTTPException(401, 'ADMIN_LOGIN_REQUIRED')


class Login(StrictModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=256)


@router.post('/login')
def login(body: Login, response: Response):
    version = credential()
    if not version:
        raise HTTPException(503, 'ADMIN_NOT_CONFIGURED')
    if not admin_store.allow_login():
        raise HTTPException(429, 'ADMIN_LOGIN_LIMIT')
    valid_password = secrets.compare_digest(digest(body.password), version)
    if body.username != 'admin' or not valid_password:
        raise HTTPException(401, 'ADMIN_INVALID_CREDENTIALS')
    token = secrets.token_hex(32)
    admin_store.create_session(digest(token), version)
    response.set_cookie(COOKIE, token, max_age=28800, path=PATH, httponly=True, samesite='strict',
                        secure=os.getenv('PORTFOLIO_COOKIE_SECURE', '0') == '1')
    return {'authenticated': True}


@router.post('/logout')
def logout(request: Request, response: Response):
    admin_store.logout(digest(request.cookies.get(COOKIE, '')))
    response.delete_cookie(COOKIE, path=PATH, httponly=True, samesite='strict', secure=os.getenv('PORTFOLIO_COOKIE_SECURE', '0') == '1')
    return {'authenticated': False}


@router.get('/overview', dependencies=[Depends(require_admin)])
def overview():
    return admin_store.overview()


@router.get('/records', dependencies=[Depends(require_admin)])
def records(kind: Kind, days: int = Query(7, ge=1, le=90),
            state: Literal['', 'queued', 'running', 'succeeded', 'failed'] = '', page: int = Query(1, ge=1, le=10000)):
    return admin_store.records(kind, days, state, page)


@router.get('/records/{kind}/{record_id}', dependencies=[Depends(require_admin)])
def detail(kind: Kind, record_id: str):
    if len(record_id) > 36:
        raise HTTPException(404, 'NOT_FOUND')
    row = admin_store.detail(kind, record_id)
    if not row:
        raise HTTPException(404, 'NOT_FOUND')
    return row
