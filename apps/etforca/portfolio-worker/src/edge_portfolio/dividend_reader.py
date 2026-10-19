"""Deterministic reads of public cash distributions through the private gateway."""
import os
from datetime import date
from typing import Literal

import psycopg
from psycopg.rows import dict_row
from pydantic import Field, model_validator

from .contracts import StrictModel


class DividendQuery(StrictModel):
    market_code: Literal['XKRX'] = 'XKRX'
    ticker: str = Field(pattern=r'^[0-9A-Z]{6}$')
    start: date
    end: date

    @model_validator(mode='after')
    def valid_window(self):
        if self.start > self.end or (self.end - self.start).days > 3660:
            raise ValueError('INVALID_DIVIDEND_WINDOW')
        return self


def read_distributions(query: DividendQuery):
    """Read the latest revisions and collection attempt; empty is not missing."""
    with psycopg.connect(os.environ['PORTFOLIO_OUTLOOK_DSN'], connect_timeout=5,
                        row_factory=dict_row, options='-c default_transaction_read_only=on -c statement_timeout=5000') as conn:
        # One snapshot keeps events and their coverage from different commits apart.
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        coverage = conn.execute('''SELECT coverage_from,coverage_to,status,received_at,raw_run_id
            FROM public.cash_distribution_coverage
            WHERE market_code=%s AND ticker=%s AND source_vendor='kis'
            ORDER BY available_at DESC,raw_run_id DESC LIMIT 1''', (query.market_code, query.ticker)).fetchone()
        rows = conn.execute('''SELECT source_vendor,source_event_key,record_date,ex_date,pay_date,
                   amount_per_share,currency,distribution_kind,confirmation,received_at,raw_run_id
            FROM public.cash_distribution_latest
            WHERE market_code=%s AND ticker=%s AND source_vendor='kis'
              AND record_date BETWEEN %s AND %s
            ORDER BY record_date,source_event_key''', (query.market_code, query.ticker, query.start, query.end)).fetchall()
    complete = bool(coverage and coverage['status'] == 'complete'
                    and coverage['coverage_from'] <= query.start and coverage['coverage_to'] >= query.end)
    return {'status': 'found' if rows else 'empty' if complete else 'missing',
            'market_code': query.market_code, 'ticker': query.ticker,
            'coverage': coverage, 'window_complete': complete,
            # Preserve Decimal as text through JSON; no binary-float monetary rounding.
            'events': [{**row, 'amount_per_share': str(row['amount_per_share'])
                        if row['amount_per_share'] is not None else None} for row in rows]}
