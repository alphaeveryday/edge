"""entity_resolution 테스트 (ALPHA-375).

각 테스트가 검사하는 WHY: 해소가 틀리면 assertion 계보가 **엉뚱한 종목**에 걸리고
(조용히 틀린 데이터), 미해소·충돌이 침묵하면 해소율이 과대평가된다(Rule 12).
"""

from data_pipeline.entity_resolution import (
    AMBIGUOUS,
    RESOLVED,
    UNRESOLVED,
    load_resolution_index,
    resolve,
)


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, sql, params=None):
        assert "instrument" in sql and "equity_profile" in sql

    def fetchall(self):
        return self._rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows

    def cursor(self):
        return _FakeCursor(self._rows)


# (instrument_id, ticker, instrument display_name, issuer display_name)
_MASTER = [
    ("inst_SAMSUNG", "005930", "삼성전자 보통주", "삼성전자"),
    ("inst_HYNIX", "000660", "SK하이닉스 보통주", "SK하이닉스"),
    ("inst_KODEX", "091160", "KODEX 반도체", None),  # ETF — equity_profile 없음
]


def _index(rows=_MASTER):
    return load_resolution_index(_FakeConn(rows))


def test_exact_match_by_ticker_company_name_and_display_name():
    """세 축(티커·회사 정식명·종목명) 완전일치 — 전부 instrument 엔티티로 해소돼야
    다운스트림 event_argument⋈instrument 조인이 성립한다."""
    index = _index()
    assert resolve(index, "005930") == ("inst_SAMSUNG", RESOLVED)
    assert resolve(index, "삼성전자") == ("inst_SAMSUNG", RESOLVED)  # 회사명 → 그 회사 주식
    assert resolve(index, "KODEX 반도체") == ("inst_KODEX", RESOLVED)
    assert resolve(index, " 삼성전자  보통주 ") == ("inst_SAMSUNG", RESOLVED)  # 공백 정규화


def test_unknown_text_is_unresolved_not_guessed():
    """마스터에 없는 표현("삼성"·미등록 종목)을 아무 데나 붙이면 조용히 틀린 계보가
    쌓인다 — None + 사유로 드러나야 로더가 수치로 남긴다."""
    index = _index()
    assert resolve(index, "삼성") == (None, UNRESOLVED)  # 약칭은 완전일치 밖(별칭 축은 별건)
    assert resolve(index, "존재하지않는회사") == (None, UNRESOLVED)
    assert resolve(index, "") == (None, UNRESOLVED)
    assert resolve(index, "   ") == (None, UNRESOLVED)
    assert resolve(index, None) == (None, UNRESOLVED)
    assert resolve(index, 5930) == (None, UNRESOLVED)  # 비문자열


def test_name_collision_is_ambiguous_not_last_writer_wins():
    """같은 이름이 두 엔티티면 아무거나 고르는 순간 적재 순서가 정답을 정한다 —
    ambiguous 로 미해소 처리돼야 한다."""
    rows = _MASTER + [("inst_OTHER", "999999", "삼성전자 보통주", "삼성전자")]
    index = _index(rows)
    assert resolve(index, "삼성전자") == (None, AMBIGUOUS)
    assert resolve(index, "삼성전자 보통주") == (None, AMBIGUOUS)
    # 충돌하지 않은 키는 여전히 산다 — 충돌 전파가 과도하면 해소율이 무너진다.
    assert resolve(index, "005930") == ("inst_SAMSUNG", RESOLVED)
    assert resolve(index, "999999") == ("inst_OTHER", RESOLVED)


def test_same_instrument_repeated_is_not_a_collision():
    """같은 종목이 중복 행으로 와도(조인 중복 등) 자기 자신과는 충돌이 아니다."""
    index = _index(_MASTER + [_MASTER[0]])
    assert resolve(index, "삼성전자") == ("inst_SAMSUNG", RESOLVED)
