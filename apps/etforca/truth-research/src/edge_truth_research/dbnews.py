"""국내 보도(빅카인즈)를 운영 DB 에서 읽는다 (ALPHA-1450).

예전에는 truth-agent 가 매시 갱신하는 말뭉치(state/corpus.jsonl.gz)를 읽었다. 같은 기사가 data-pipeline 의
1분 컨슈머로 장중 `document`·`news_document` 에 들어온다(`source_document_id` = 말뭉치 기사 id 라 판정 근거 id 가
이어진다). 실행마다 최근 DAYS 일치를 한 번 읽어 말뭉치와 같은 Article 사전을 만든다 — 첫 메시지 기사 고르기·
search_news·view_news 는 그대로다. 접속은 분석 엔진의 읽기 전용 계정이고, 붙지 못하면 예외가 그대로 올라가
실행이 실패로 끝난다(사용자 결정 2026-10-24: 옛 기사로 조용히 판정하지 않는다).
"""
from __future__ import annotations

from datetime import datetime, timedelta
import json
import logging

from edge_truth_agent.news import Article

log = logging.getLogger(__name__)

DAYS = 21  # truth-agent 말뭉치 보관 기간(TRUTH_CORPUS_DAYS 기본값)과 같다
SQL = """
SELECT d.source_document_id, d.title, COALESCE(n.lead_text, ''), COALESCE(d.source_uri, ''), COALESCE(n.publisher, ''),
       to_char(d.published_at AT TIME ZONE 'Asia/Seoul', 'YYYY-MM-DD HH24:MI:SS')
FROM document d
LEFT JOIN news_document n ON n.document_id = d.document_id
WHERE d.document_type = 'NEWS' AND d.source_code = 'bigkinds'
  AND d.published_at >= %s AND d.published_at <= %s
"""


def connect(cfg):
    """읽기 전용 접속. 서버 인증서를 RDS CA 로 검증한다."""
    import psycopg
    return psycopg.connect(host=cfg.db_host, port=5432, dbname="edge", user=cfg.db_user, password=cfg.db_password,
                           sslmode="verify-full", sslrootcert=cfg.db_ca, connect_timeout=10,
                           options="-c default_transaction_read_only=on -c statement_timeout=60000")


def _clean(value) -> str:
    return " ".join(str(value or "").split())


def load(cfg, now: datetime, *, connect=connect) -> dict[str, Article]:
    """now 기준 최근 DAYS 일 기사. 제목이 없는 행은 버린다(말뭉치도 그랬다)."""
    texts: dict[str, Article] = {}
    with connect(cfg) as conn, conn.cursor(name="movers_news") as cur:  # 서버 커서: 수만 행을 나눠 받는다
        cur.execute(SQL, (now - timedelta(days=DAYS), now))
        while rows := cur.fetchmany(5000):
            for article_id, title, lead, url, publisher, published in rows:
                if article_id and (title := _clean(title)):
                    texts[article_id] = Article(id=article_id, title=title, lead=_clean(lead), url=url or "",
                                                publisher=publisher or "", published=published or "")
    log.info(json.dumps({"event": "news_loaded", "source": "db", "articles": len(texts), "days": DAYS}))
    return texts
