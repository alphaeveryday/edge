"""migration CHECK 어휘 ↔ 코드 상수 동기화 테스트 (ALPHA-661).

의도: CHECK 어휘와 states.py 가 어긋나면 repository 가 쓰는 값이 DB 에서 거부되거나,
DB 가 허용하는 값을 코드가 모른다 — 둘 다 런타임에야 터진다. SQL 을 파싱해 생성
시점(테스트)에 잡는다. ops/states.py 는 docstring 약속뿐이라 드리프트가 가능했다 —
minute 원장은 기계 검증으로 강제한다.
"""

from __future__ import annotations

import re
from pathlib import Path

from data_pipeline.minute import states

# …/src/apps/cloud/data-pipeline/tests/minute/ → parents[5] = src/
MIGRATIONS = Path(__file__).parents[5] / "libs" / "schema" / "migrations-cloud"
LEDGER_SQL = (MIGRATIONS / "V202607311400__add_minute_ingestion_ledger.sql").read_text(
    encoding="utf-8"
)
OUTBOX_SQL = (MIGRATIONS / "V202607311410__add_dataset_commit_outbox.sql").read_text(
    encoding="utf-8"
)


def check_vocab(sql: str, constraint_name: str) -> frozenset[str]:
    """CONSTRAINT <name> CHECK (col IN ('A','B',…)) 에서 어휘 집합을 뽑는다."""
    pattern = re.compile(
        rf"CONSTRAINT {re.escape(constraint_name)} CHECK \(\s*\w+ IN \(([^)]+)\)", re.DOTALL
    )
    match = pattern.search(sql)
    assert match, f"{constraint_name} CHECK 를 못 찾았다 — 이름이 바뀌었으면 이 테스트도 갱신"
    return frozenset(re.findall(r"'([A-Z_]+)'", match.group(1)))


class TestVocabSync:
    def test_session_phase(self):
        assert check_vocab(LEDGER_SQL, "ck_minute_session_phase") == states.SESSION_PHASES

    def test_window_data_status(self):
        assert check_vocab(LEDGER_SQL, "ck_minute_window_status") == states.WINDOW_DATA_STATUSES

    def test_job_status_shared_lifecycle(self):
        # 뉴스·가격 job 은 같은 lifecycle 블록을 코드로만 공유한다(v0.7 10.5) —
        # 두 테이블의 CHECK 가 서로 그리고 상수와 전부 같아야 한다
        news = check_vocab(LEDGER_SQL, "ck_news_job_status")
        price = check_vocab(LEDGER_SQL, "ck_price_job_status")
        assert news == price == states.JOB_STATUSES

    def test_outbox_status(self):
        assert check_vocab(OUTBOX_SQL, "ck_outbox_status") == states.OUTBOX_STATUSES

    def test_migration_versions_after_dev_head(self):
        # forward-only: 이 PR 의 버전은 작성 시점 origin/dev 최신(V202607311300)보다 뒤다.
        # 머지 직전 pr-cycle 게이트가 최신 dev 와 다시 대조한다.
        versions = sorted(
            int(m.group(1))
            for f in MIGRATIONS.glob("V*__*.sql")
            if (m := re.match(r"V(\d+)__", f.name))
        )
        assert versions[-2:] == [202607311400, 202607311410]
