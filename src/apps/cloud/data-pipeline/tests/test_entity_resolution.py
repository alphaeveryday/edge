"""entity_resolution 테스트 (ALPHA-375).

각 테스트가 검사하는 WHY: 해소가 틀리면 assertion 계보가 **엉뚱한 종목**에 걸리고
(조용히 틀린 데이터), 미해소·충돌이 침묵하면 해소율이 과대평가된다(Rule 12).
"""

from data_pipeline.entity_resolution import (
    ALIAS_RESOLVED,
    AMBIGUOUS,
    CONCEPT_REJECTED,
    RESOLVED,
    UNRESOLVED,
    load_resolution_index,
    plan_resolution,
    resolve,
    resolve_alias_ticker,
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


# (instrument_id, ticker, instrument display_name, issuer display_name, share_class_code)
_MASTER = [
    ("inst_SAMSUNG", "005930", "삼성전자 보통주", "삼성전자", "COMMON"),
    ("inst_HYNIX", "000660", "SK하이닉스 보통주", "SK하이닉스", "COMMON"),
    ("inst_KODEX", "091160", "KODEX 반도체", None, None),  # ETF — equity_profile 없음
    ("inst_IBK", "024110", "기업은행 보통주", "기업은행", "COMMON"),
    ("inst_HYUNDAI", "005380", "현대차 보통주", "현대차", "COMMON"),
    ("inst_SHINSEGAE", "004170", "신세계 보통주", "신세계", "COMMON"),
    ("inst_LS", "010120", "LS ELECTRIC 보통주", "LS ELECTRIC", "COMMON"),
    ("inst_KEPCO", "015760", "한국전력 보통주", "한국전력", "COMMON"),
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
    assert resolve(index, "삼성") == (None, UNRESOLVED)  # 그룹명은 상장사 귀속이 모호해 제외
    assert resolve(index, "존재하지않는회사") == (None, UNRESOLVED)
    assert resolve(index, "") == (None, UNRESOLVED)
    assert resolve(index, "   ") == (None, UNRESOLVED)
    assert resolve(index, None) == (None, UNRESOLVED)
    assert resolve(index, 5930) == (None, UNRESOLVED)  # 비문자열


def test_preferred_share_does_not_hijack_company_name():
    """우선주가 있는 발행사 — 회사명은 약속대로 **보통주**로 해소돼야 하고(ambiguous 로
    무너지면 안 됨, Codex #132 P2), 우선주는 자기 티커·종목명으로 해소된다."""
    rows = _MASTER + [("inst_SAMSUNG_P", "005935", "삼성전자 우선주", "삼성전자", "PREFERRED")]
    index = _index(rows)
    assert resolve(index, "삼성전자") == ("inst_SAMSUNG", RESOLVED)
    assert resolve(index, "005935") == ("inst_SAMSUNG_P", RESOLVED)
    assert resolve(index, "삼성전자 우선주") == ("inst_SAMSUNG_P", RESOLVED)


def test_name_collision_is_ambiguous_not_last_writer_wins():
    """같은 이름이 두 엔티티면 아무거나 고르는 순간 적재 순서가 정답을 정한다 —
    ambiguous 로 미해소 처리돼야 한다."""
    rows = _MASTER + [("inst_OTHER", "999999", "삼성전자 보통주", "삼성전자", "COMMON")]
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


def test_curated_legal_name_alias_resolves_only_when_master_target_exists():
    """WHY: 정상 런 상위 미해소의 정식명 변형을 회수하되 없는 마스터를 별칭으로 만들면 안 된다."""
    index = _index()
    aliases = {
        "IBK기업은행": "inst_IBK",
        "중소기업은행": "inst_IBK",
        "현대자동차": "inst_HYUNDAI",
        "LS일렉트릭": "inst_LS",
        "한국전력공사": "inst_KEPCO",
        "신세계백화점": "inst_SHINSEGAE",
    }
    for expression, entity_id in aliases.items():
        assert resolve(index, expression) == (None, UNRESOLVED)
        assert resolve(index, expression, allow_aliases=True) == (entity_id, ALIAS_RESOLVED)
    assert resolve_alias_ticker(index, "IBK기업은행") == "024110"
    assert resolve_alias_ticker(index, "삼성") is None


def test_alias_does_not_exist_without_unambiguous_canonical_master():
    """WHY: 배포 순서나 마스터 결손 때 별칭이 임의 instrument를 만들어서는 안 된다."""
    index = _index([row for row in _MASTER if row[1] != "024110"])
    assert resolve(index, "IBK기업은행") == (None, UNRESOLVED)
    assert resolve_alias_ticker(index, "IBK기업은행") is None


def test_alias_already_registered_as_master_name_keeps_batch_ticker_mapping():
    """WHY: 발행사 정식명이 curated 표현과 같아도 배치는 기사 ticker 범위 안에서 그 이름을 써야 한다."""
    rows = [
        ("inst_IBK", "024110", "기업은행", "중소기업은행", "COMMON"),
    ]
    index = _index(rows)
    assert resolve(index, "중소기업은행") == ("inst_IBK", RESOLVED)
    assert resolve_alias_ticker(index, "중소기업은행") == "024110"


def test_mint_policy_rejection_is_not_reported_as_instrument_master_miss():
    """WHY: 숫자뿐인 위치를 종목 미등록으로 진단하면 마스터·alias 보강이라는 잘못된 대응을 부른다."""
    entity_id, reason, minted = plan_resolution(_index(), "LOCATION", "123")
    assert (entity_id, reason, minted) == (None, CONCEPT_REJECTED, None)
