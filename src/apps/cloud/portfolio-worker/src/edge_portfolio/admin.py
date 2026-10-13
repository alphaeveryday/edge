"""Read-only routes mounted exclusively by the loopback SSM administrator app."""
from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from . import admin_store

router = APIRouter(prefix='/api/portfolio/admin')
Kind = Literal['analyses', 'drafts', 'reports']


@router.get('/overview')
def overview():
    return admin_store.overview()


@router.get('/records')
def records(kind: Kind, days: int = Query(7, ge=1, le=90),
            state: Literal['', 'queued', 'running', 'succeeded', 'failed'] = '', page: int = Query(1, ge=1, le=10000)):
    return admin_store.records(kind, days, state, page)


@router.get('/records/{kind}/{record_id}')
def detail(kind: Kind, record_id: str):
    if len(record_id) > 36:
        raise HTTPException(404, 'NOT_FOUND')
    row = admin_store.detail(kind, record_id)
    if not row:
        raise HTTPException(404, 'NOT_FOUND')
    return row
