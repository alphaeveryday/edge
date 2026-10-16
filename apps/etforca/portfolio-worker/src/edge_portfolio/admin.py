"""Read-only routes mounted exclusively by the loopback SSM administrator app."""
from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from . import admin_store, verification

router = APIRouter(prefix='/api/portfolio/admin')
Kind = Literal['analyses', 'drafts', 'reports']
Scope = Literal['all', 'customers', 'tests']


@router.get('/verification-runs')
def verification_runs():
    return verification.runs()


@router.get('/verification-runs/{run_id}')
def verification_run(run_id: str):
    result = verification.detail(run_id)
    if result is None:
        raise HTTPException(404, 'NOT_FOUND')
    return result


@router.get('/verification-runs/{run_id}/{case_id}/{attempt}')
def verification_attempt(run_id: str, case_id: int, attempt: int):
    result = verification.attempt_detail(run_id, case_id, attempt)
    if result is None:
        raise HTTPException(404, 'NOT_FOUND')
    return result


@router.get('/overview')
def overview(scope: Scope = 'all'):
    return admin_store.overview(scope)


@router.get('/records')
def records(kind: Kind, days: int = Query(7, ge=1, le=90),
            state: Literal['', 'queued', 'running', 'succeeded', 'failed'] = '', page: int = Query(1, ge=1, le=10000), scope: Scope = 'all'):
    return admin_store.records(kind, days, state, page, scope)


@router.get('/records/{kind}/{record_id}')
def detail(kind: Kind, record_id: str):
    if len(record_id) > 36:
        raise HTTPException(404, 'NOT_FOUND')
    row = admin_store.detail(kind, record_id)
    if not row:
        raise HTTPException(404, 'NOT_FOUND')
    return row
