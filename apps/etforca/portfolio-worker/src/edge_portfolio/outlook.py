"""Purpose-bound single-ETF reads; no listing, scanning, or arbitrary SQL tool."""
import asyncio
import json
import os
import re
from datetime import datetime, timedelta, timezone

import psycopg
import httpx
from psycopg.rows import dict_row
from pydantic import Field

from .contracts import StrictModel


class OutlookQuery(StrictModel):
    etf_code: str = Field(pattern=r'^[0-9A-Z]{6}$')
    purpose: str = Field(min_length=20, max_length=240)
    decision_question: str = Field(min_length=15, max_length=240)


TOOL = {'type': 'function', 'function': {
    'name': 'read_etf_outlook',
    'description': '명시한 국내 ETF 한 종목의 기존 AWS 전망 1건만 조회. 조회 목적과 이번 투자 판단에 필요한 질문 필수. 전체 목록·일괄 조회 불가. 한 분석 최대 3종목, 중복 조회 불가.',
    'parameters': OutlookQuery.model_json_schema(),
}}


def read_one(code, as_of):
    """One parameterized ETF query in a bounded, read-only transaction."""
    with psycopg.connect(os.environ['PORTFOLIO_OUTLOOK_DSN'], connect_timeout=5,
                        row_factory=dict_row, options='-c default_transaction_read_only=on -c statement_timeout=5000') as conn:
        row = conn.execute('''SELECT analysis_id, etf_code, analysis_at, published_at,
                   summary_title, summary, conclusion_title, conclusion_sentence, change_condition
            FROM public.outlook_analyses
            WHERE etf_code=%s AND status='completed' AND published_at<=%s AND analysis_at<=%s
            ORDER BY analysis_at DESC LIMIT 1''', (code, as_of, as_of)).fetchone()
        if row is None:
            return {'status': 'missing', 'etf_code': code}
        if row['analysis_at'] < as_of - timedelta(days=14):
            return {'status': 'stale', 'etf_code': code, 'analysis_at': row['analysis_at'].isoformat()}
        return {'status': 'found', **row}


class OutlookReader:
    def __init__(self, request, as_of, reader=None):
        self.request, self.reader, self.as_of = request, reader, as_of
        self.queries = []

    async def call(self, args, sources):
        query = OutlookQuery.model_validate(args)
        if not query.purpose.strip() or not query.decision_question.strip():
            raise ValueError('OUTLOOK_PURPOSE_REQUIRED')
        if len(self.queries) >= 3 or any(q['etf_code'] == query.etf_code for q in self.queries):
            raise ValueError('OUTLOOK_READ_LIMIT')
        # A code must have been supplied by the investor or identified in evidence.
        known = '\n'.join(h.name for h in self.request.holdings) + '\n' + '\n'.join(s.get('excerpt', '') for s in sources)
        if not re.search(r'(?<![0-9A-Z])' + re.escape(query.etf_code) + r'(?![0-9A-Z])', known):
            raise ValueError('OUTLOOK_IDENTIFY_ETF_FIRST')
        audit = query.model_dump()
        self.queries.append(audit)
        if not self.reader and not os.getenv('PORTFOLIO_OUTLOOK_URL'):
            answer = {'status': 'not_connected'}
        else:
            try:
                if self.reader:
                    answer = await asyncio.to_thread(self.reader, query.etf_code, self.as_of)
                else:
                    async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
                        response = await client.post(os.environ['PORTFOLIO_OUTLOOK_URL'], json={**query.model_dump(), 'as_of': self.as_of.isoformat()})
                        response.raise_for_status()
                        answer = response.json()
            except (psycopg.Error, httpx.HTTPError, ValueError):
                answer = {'status': 'unavailable'}
        audit['status'] = answer['status']
        if answer['status'] != 'found':
            return {**answer, 'etf_code': query.etf_code, 'purpose': query.purpose,
                    'coverage': 'one_requested_KR_ETF', 'forecast_horizon': None,
                    'instruction': '이 종목의 저장 전망 조회 상태이며 전체 외부 자료의 가용성이 아닙니다. 공식 자료로 보완하고 단기 목표가 부재를 장기 배분 불가로 해석하지 마세요.'}
        text = json.dumps(answer, ensure_ascii=False, default=str)[:10000]
        source = {'id': f's{len(sources) + 1}', 'kind': 'prior_analysis',
                  'url': '', 'excerpt': text, 'fetched_at': datetime.now(timezone.utc).isoformat(),
                  'analysis_id': answer['analysis_id'], 'etf_code': query.etf_code,
                  'published_at': str(answer.get('published_at')) if answer.get('published_at') else None,
                  'claim_as_of': str(answer.get('analysis_at')) if answer.get('analysis_at') else None,
                  'purpose': query.purpose, 'forecast_horizon': None, 'coverage': 'one_requested_KR_ETF',
                  'passages': [{'id': f'p{i + 1}', 'text': value} for i, value in enumerate(
                      str(answer[k]) for k in ('summary_title', 'summary', 'conclusion_sentence', 'change_condition') if answer.get(k))]}
        sources.append(source)
        return {**source, 'warning': '기존 분석자의 의견이며 원천 사실이 아니다. 외부 공식 자료와 대조하고 생성 시점·자료 한계를 표시한다.'}
