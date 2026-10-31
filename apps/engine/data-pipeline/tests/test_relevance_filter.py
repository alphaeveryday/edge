"""뉴스 관련성 필터 테스트 (ALPHA-1476).

의도: 스레드 주체 회사의 사건이 아닌 뉴스 기사(자회사·다른 종류의 일·지나가는 언급)는 스레드에 붙지 않고
분석 엔진에서 사건으로 보이지 않아야 한다(REJECTED). 판정 근거가 없는 이벤트(학습 밖 타입·공시·주체 회사
없음·제목/회사명 없음)는 숨기지 않는다 — 판정기 오류보다 기사를 잃는 쪽이 더 나쁘다. 판정기 입력은 학습과 같은
열(주체 회사의 entity.display_name, 제목, news_document.lead_text)이어야 한다. 모델(torch)은 여기서 읽지 않는다.
"""

from data_pipeline.steps import assemble_events
from data_pipeline.steps.assemble_events import RELEVANCE_REJECTED_REASON, reject_irrelevant

_CONTRACT = "COMPANY.CONTRACT.SIGNING"


class _Judge:
    """판정기 대역 — 학습한 타입만 판정하고, 회사명별 확률을 돌려준다. 받은 입력을 남긴다."""

    version = "relevance-test"
    threshold = 0.05

    def __init__(self, probs: dict[str, float]):
        self.probs = probs
        self.seen: list[tuple] = []

    def judges(self, event_type_code):
        return event_type_code == _CONTRACT

    def score(self, items):
        self.seen.extend(items)
        return [self.probs[company] for company, _t, _ti, _l in items]


class _Cursor:
    def __init__(self, conn):
        self.conn = conn
        self.rows: list = []

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        self.conn.executed.append((flat, params))
        if flat.startswith("SELECT instrument_id, issuer_actor_id"):
            self.rows = [(i, a) for i, a in self.conn.issuers.items() if i in params[0]]
        elif flat.startswith("SELECT entity_id, display_name FROM entity"):
            self.rows = [(e, n) for e, n in self.conn.names.items() if e in params[0]]
        elif flat.startswith("SELECT DISTINCT ON (ev.source_event_id)"):
            self.rows = [(sid, t, lead) for sid, (t, lead) in self.conn.texts.items() if sid in params[0]]
        else:
            self.rows = []

    def executemany(self, sql, rows):
        self.conn.batches.append((" ".join(sql.split()), list(rows)))

    def fetchall(self):
        return self.rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Conn:
    def __init__(self, issuers=None, names=None, texts=None):
        self.issuers = issuers or {}
        self.names = names or {}
        self.texts = texts or {}
        self.executed: list = []
        self.batches: list = []

    def cursor(self):
        return _Cursor(self)

    def updates(self):
        return [p for sql, p in self.executed if sql.startswith("UPDATE source_event")]

    def links(self):
        return [r for sql, rows in self.batches if sql.startswith("INSERT INTO event_thread_link") for r in rows]


def _event(sid, role_values, etype=_CONTRACT, source_class="NEWS"):
    return {"source_event_id": sid, "event_type_code": etype, "available_at": "2026-10-30T09:00:00+09:00",
            "role_values": role_values, "lifecycle_stage": None, "predicate_code": None,
            "source_class": source_class}


def test_irrelevant_news_event_is_rejected_and_kept_out_of_threading():
    # WHY: 지주사(SK)가 주체로 잡힌 자회사(SK하이닉스) 수주 기사가 SK 계약 스레드에 붙으면 다른 회사의 일이
    # SK 의 사건으로 이어진다. 거른 이벤트는 REJECTED(분석 엔진이 사건 묶음에서 뺀다) + 사유·확률을 남긴 UNKNOWN
    # 링크가 되고 스레드 입력에서 빠진다. 통과한 이벤트는 그대로 스레드로 간다.
    conn = _Conn(
        issuers={"inst_SK": "actor_SK", "inst_LGES": "actor_LGES"},
        names={"actor_SK": "SK", "actor_LGES": "LG에너지솔루션"},
        texts={"evt_sub": ("SK하이닉스, 1조 장비 발주", None),
               "evt_own": ("LG에너지솔루션, GM과 배터리 공급계약", "리드 본문")},
    )
    judge = _Judge({"SK": 0.01, "LG에너지솔루션": 0.9})
    sub = _event("evt_sub", {"SUPPLIER": "inst_SK"})
    own = _event("evt_own", {"SUPPLIER": "inst_LGES"})

    kept, rejected = reject_irrelevant(conn, [sub, own], judge)

    assert kept == [own] and rejected == 1
    assert conn.updates() == [(["evt_sub"],)]
    [link] = conn.links()
    sid, _link_type, _evaluated_at, reason = link
    assert sid == "evt_sub" and reason == f"{RELEVANCE_REJECTED_REASON} relevance-test p=0.0100"
    # 입력은 스레드 주체 회사(종목이 아니라 발행사 액터)의 표시명 + 제목 + 리드 — 학습 데이터와 같은 열.
    assert sorted(judge.seen) == [("LG에너지솔루션", _CONTRACT, "LG에너지솔루션, GM과 배터리 공급계약", "리드 본문"),
                                  ("SK", _CONTRACT, "SK하이닉스, 1조 장비 발주", None)]


def test_probability_at_the_threshold_passes():
    # WHY: 기준은 P >= 0.05 통과(연구에서 고른 설정). 경계값을 거르면 평가한 필터와 다른 필터가 된다.
    conn = _Conn(names={"inst_A": "A"}, texts={"evt": ("A, 공급계약", None)})
    kept, rejected = reject_irrelevant(conn, [_event("evt", {"SUPPLIER": "inst_A"})], _Judge({"A": 0.05}))
    assert rejected == 0 and len(kept) == 1 and conn.updates() == []


def test_events_without_grounds_for_judgment_are_never_hidden():
    # WHY: 판정기는 학습한 뉴스 타입만 안다. 공시·학습 밖 타입·주체 회사 없는 이벤트(UNKNOWN 대상)·
    # 회사명이나 제목을 못 찾은 이벤트까지 거르면 근거 없이 기사를 잃는다 — 판정하지 않고 그대로 넘긴다.
    conn = _Conn(names={"inst_A": "A"}, texts={"evt_no_name": ("B, 공급계약", None)})
    judge = _Judge({})
    events = [
        _event("evt_disc", {"SUPPLIER": "inst_A"}, source_class="DISCLOSURE"),
        _event("evt_other_type", {"ISSUER": "inst_A"}, etype="COMPANY.CAPITAL.DIVIDEND"),
        _event("evt_no_company", {}),
        _event("evt_no_name", {"SUPPLIER": "inst_B"}),
        _event("evt_no_title", {"SUPPLIER": "inst_A"}),
    ]
    kept, rejected = reject_irrelevant(conn, events, judge)
    assert kept == events and rejected == 0
    assert judge.seen == [] and conn.updates() == [] and conn.links() == []


def test_filter_off_touches_nothing():
    # WHY: RELEVANCE_FILTER 기본은 꺼짐 — 꺼진 배포는 이 기능 이전과 같은 DB 작업만 해야 한다.
    conn = _Conn()
    events = [_event("evt", {"SUPPLIER": "inst_A"})]
    assert reject_irrelevant(conn, events, None) == (events, 0)
    assert conn.executed == []


def test_judged_company_is_the_thread_company():
    # WHY: 판정 대상 회사가 기사가 붙을 스레드의 회사와 다르면, 통과한 기사가 판정받지 않은 회사의 스레드로 간다.
    # 둘은 같은 함수(_news_company)에서 나온다 — 계약은 SUPPLIER 가 CUSTOMER 보다 앞선다.
    event = _event("evt", {"CUSTOMER": "inst_C", "SUPPLIER": "inst_S"})
    assert assemble_events._news_company(event, event["role_values"]) == "inst_S"
    assert assemble_events._news_thread_base(event, event["role_values"]).endswith("company=inst_S")
