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


def check_vocab(constraint_name: str) -> frozenset[str]:
    """CONSTRAINT <name> CHECK (col IN ('A','B',…)) 의 어휘 집합.

    버전순 **전체** migration 을 훑어 마지막 정의를 취한다 — 후속 forward migration 이
    같은 이름으로 CHECK 를 재생성해도 유효 스키마 기준으로 대조된다.

    천장: DROP 후 **다른 이름**으로 교체하면 정적 파싱으론 못 잡는다 — 그 드리프트의
    실물 검증 지점은 CI schema-validate 의 ephemeral DB 적용이고, 이름을 바꾸는 PR 이
    이 테스트의 constraint 이름도 같이 바꾸는 것이 규약이다.
    """
    latest: frozenset[str] | None = None
    pattern = re.compile(
        rf"CONSTRAINT {re.escape(constraint_name)} CHECK \(\s*\w+ IN \(([^)]+)\)", re.DOTALL
    )
    for path in sorted(MIGRATIONS.glob("V*__*.sql")):
        match = pattern.search(path.read_text(encoding="utf-8"))
        if match:
            latest = frozenset(re.findall(r"'([A-Z_]+)'", match.group(1)))
    assert latest is not None, (
        f"{constraint_name} CHECK 를 못 찾았다 — 이름이 바뀌었으면 이 테스트도 갱신"
    )
    return latest


class TestVocabSync:
    def test_session_phase(self):
        assert check_vocab("ck_minute_session_phase") == states.SESSION_PHASES

    def test_window_data_status(self):
        assert check_vocab("ck_minute_window_status") == states.WINDOW_DATA_STATUSES

    def test_job_status_shared_lifecycle(self):
        # 뉴스·가격 job 은 같은 lifecycle 블록을 코드로만 공유한다(v0.7 10.5) —
        # 두 테이블의 CHECK 가 서로 그리고 상수와 전부 같아야 한다
        news = check_vocab("ck_news_job_status")
        price = check_vocab("ck_price_job_status")
        assert news == price == states.JOB_STATUSES

    def test_outbox_status(self):
        assert check_vocab("ck_outbox_status") == states.OUTBOX_STATUSES
