"""Internal-only read gateway. The model worker never receives the outlook DB password."""
from datetime import datetime, timezone

import psycopg
from fastapi import FastAPI, HTTPException

from .outlook import OutlookQuery, read_one
from .dividend_reader import DividendQuery, read_distributions

app = FastAPI()


@app.post('/dividends')
def dividends(body: DividendQuery):
    try:
        return read_distributions(body)
    except psycopg.Error:
        return {'status': 'unavailable'}


class ReadRequest(OutlookQuery):
    as_of: datetime


@app.post('/outlook')
def outlook(body: ReadRequest):
    if not body.purpose.strip() or not body.decision_question.strip() or body.as_of.tzinfo is None or body.as_of > datetime.now(timezone.utc):
        raise HTTPException(422, 'INVALID_PURPOSE_OR_TIME')
    try:
        return read_one(body.etf_code, body.as_of)
    except psycopg.Error:
        return {'status': 'unavailable'}
