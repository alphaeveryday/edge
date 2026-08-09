"""발화 스냅샷 대시보드 테스트 (ALPHA-894).

페이크 RDS(커서 대역)·페이크 S3 로 렌더 계약을 고정한다: 블록↔근거 연결, 봉인
배지의 정직성(불일치·부재를 숨기면 깨진다), 기각 가설 사유, LLM 왕복 원문 잔존,
30일 경계의 요약 전환(경계 밖 발화의 원문이 실리면 깨진다), 결정론(2회 = 동일
바이트), 외부 리소스 0, 그리고 **run() 끝의 재생성 배선**(끊기면 깨진다).
"""
from __future__ import annotations

import hashlib
import io
import json
from datetime import date, timedelta

import pytest

from edge_analysis.report import (
    REPORT_DETAIL_DAYS,
    fetch_snapshot,
    regenerate_dashboard,
    render_dashboard,
)

_BUCKET = "test-lake"
_PREFIX = f"s3://{_BUCKET}/operations_archive/etf_explanations/"


# ── 페이크 원장(RDS)·S3 ────────────────────────────────────────────────────
class _Cursor:
    def __init__(self, tables):
        self._tables = tables
        self._rows: list[tuple] = []

    def execute(self, sql, params=None):
        if "FROM explanation_result" in sql:
            self._rows = self._tables.get("results", [])
        elif "explanation_evidence_row" in sql:
            self._rows = self._tables.get("evidence", [])
        elif "hypothesis_trial" in sql:
            self._rows = self._tables.get("trials", [])
        else:  # pragma: no cover — 새 질의는 대역에 자리를 만들어야 한다
            raise AssertionError(f"대역이 모르는 질의: {sql}")

    def fetchall(self):
        return self._rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self, **tables):
        self._tables = tables

    def cursor(self):
        return _Cursor(self._tables)


class _FakeS3:
    def __init__(self):
        self.objects: dict[tuple[str, str], bytes] = {}

    def put_object(self, *, Bucket, Key, Body, **_kw):
        self.objects[(Bucket, Key)] = Body

    def get_object(self, *, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            raise KeyError(f"NoSuchKey: {Key}")
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}


# ── 합성 발화 재료 ─────────────────────────────────────────────────────────
_DAY = date(2026, 8, 7)
_TRACE_KEY = ("operations_archive/etf_explanations/traces/etf=091160/"
              f"trade_date={_DAY.isoformat()}/req-1.json")
_TRACE_EVENTS = [
    {"event": "llm.request", "seq": 1, "system": "너는 인과 가설 에이전트다",
     "user": "BRIEF-원문"},
    {"event": "llm.response", "seq": 1, "elapsed_s": 2.1,
     "usage": {"prompt_tokens": 100, "completion_tokens": 40, "total_tokens": 140},
     "response": {"hypotheses": ["RESPONSE-원문"]}},
    {"event": "query.done", "sql": "SELECT 1 AS probe", "rows": 1, "ms": 3.2},
]


def _trace_body() -> bytes:
    return json.dumps({"events": _TRACE_EVENTS}, ensure_ascii=False).encode("utf-8")


def _manifest(sha: str | None = None) -> dict:
    return {"s3_uri": f"s3://{_BUCKET}/{_TRACE_KEY}",
            "sha256": sha or hashlib.sha256(_trace_body()).hexdigest(),
            "event_count": len(_TRACE_EVENTS), "schema_version": 1}


def _stage(manifest: dict | None) -> dict:
    return {
        "final_explanation": {"blocks": [
            {"block_code": "1", "block_title": "기여 분해",
             "text": "삼성전자 기여가 가장 컸습니다.", "evidence_row_refs": [1, 2]},
        ]},
        "window": {"requested_start": "09:00", "requested_end": "10:31",
                   "window_min_coverage": 1.0, "dropped_units": []},
        "analysis_trace": manifest,
    }


def _result_row(result_id="res_1", run_id="run_1", day=_DAY, stage=None,
                summary="요청창 설명 원문입니다."):
    return (result_id, run_id, "091160", "inst_ETF", day,
            f"{day.isoformat()}T10:31:00+09:00", "PRICE_ONLY", summary,
            "MEDIUM", stage if stage is not None else _stage(_manifest()),
            "PUBLISHED")


_EVIDENCE = [
    ("res_1", 1, "PRICE", "당일 5분봉", "KRX", "10:30",
     None, None, None, None, None, None, None, None, None, None, None),
    ("res_1", 2, "STAT_TEST", None, "derived", None,
     "MATCHED_ATT", "IDIO", "SIMILAR_STOCKS", {"event_title": "공급계약"},
     40, "DAY", 0.012, 0.003, 1, "UPPER",
     ["삼성전자 일봉 수익률", "매칭군 일봉 수익률"]),
]
_TRIALS = [
    ("run_1", "091160", _DAY, "REJECTED", "점:CONTRACT.SIGNING", None, None,
     None, "REJECTED", None, None, "채널 중복 — 심사 기각"),
    ("run_1", "091160", _DAY, "TESTED", "계열:거래량", "Q수량", "노출A", "고유",
     "ESTABLISHED", 40, 0.003, ""),
]


def _fresh_s3() -> _FakeS3:
    s3 = _FakeS3()
    s3.objects[(_BUCKET, _TRACE_KEY)] = _trace_body()
    return s3


def _html(conn, s3) -> str:
    return render_dashboard(*fetch_snapshot(conn, s3))


def test_blocks_are_linked_to_their_evidence_rows():
    """① 산문 블록 아래에 **그 블록의** 근거 행 카드가 붙는다(evidence_render 재사용).

    연결이 끊기면 고객 문장이 무엇을 딛고 섰는지 페이지에서 사라진다(ALPHA-888 §7).
    """
    html = _html(_FakeConn(results=[_result_row()], evidence=_EVIDENCE,
                           trials=_TRIALS), _fresh_s3())

    block_at = html.index("삼성전자 기여가 가장 컸습니다.")
    assert "[1] 가격   당일 5분봉" in html
    assert "[2] 통계검정" in html                      # STAT_TEST 는 문형+슬롯 조립
    assert "「공급계약」이 난 종목이" in html           # render_template 경유
    assert "차이     평균 +1.20%p" in html             # §3.4 추가정보 렌더
    assert html.index("[1] 가격", block_at) > block_at  # 카드가 블록 뒤에 붙는다


def test_missing_evidence_row_is_rendered_as_a_gap_not_hidden():
    """참조된 근거 행이 원장에 없으면 그 자리에 결손 사유가 남는다(Rule 12)."""
    html = _html(_FakeConn(results=[_result_row()], evidence=[_EVIDENCE[0]],
                           trials=[]), _fresh_s3())

    assert "[2] 결손: 근거 행이 원장에 없다" in html


def test_hash_mismatch_is_rendered_as_a_broken_seal():
    """⑤ trace sha256 재계산·대조 — 불일치를 숨기면 봉인 배지가 있으나 마나다."""
    stage = _stage(_manifest(sha="f" * 64))
    html = _html(_FakeConn(results=[_result_row(stage=stage)],
                           evidence=_EVIDENCE, trials=_TRIALS), _fresh_s3())

    assert "봉인 불일치" in html
    assert "f" * 64 in html                              # 원장 manifest 쪽 해시
    assert hashlib.sha256(_trace_body()).hexdigest() in html  # 재계산 쪽 해시
    assert "봉인 일치" not in html


def test_intact_seal_and_absent_trace_are_told_apart():
    ok = _result_row()
    missing = _result_row(result_id="res_2", run_id="run_2",
                          stage=_stage(None))
    html = _html(_FakeConn(results=[ok, missing], evidence=_EVIDENCE,
                           trials=_TRIALS), _fresh_s3())

    assert "봉인 일치" in html
    assert "trace 부재" in html
    assert "trace manifest 없음" in html                 # 부재의 사유


def test_s3_fetch_failure_is_a_gap_with_a_reason_and_generation_continues():
    html = _html(_FakeConn(results=[_result_row()], evidence=_EVIDENCE,
                           trials=_TRIALS), _FakeS3())   # trace 객체가 없는 S3

    assert "trace 부재" in html
    assert "S3 조회 실패" in html
    assert "<h1>발화 스냅샷 대시보드</h1>" in html        # 생성 자체는 계속됐다


def test_rejected_hypothesis_keeps_its_reason():
    """② 가설 원장 — 기각 행의 사유가 표에 남는다. 사라지면 '왜 안 세웠나'를 못 본다."""
    html = _html(_FakeConn(results=[_result_row()], evidence=_EVIDENCE,
                           trials=_TRIALS), _fresh_s3())

    assert "채널 중복 — 심사 기각" in html
    assert "점:CONTRACT.SIGNING" in html
    assert "ESTABLISHED" in html                          # 검정 전건도 같은 표에


def test_llm_roundtrip_raw_text_survives_into_the_accordion():
    """③ 프롬프트·응답·SQL 원문이 <details> 안에 그대로 남는다 — 요약·절단 금지."""
    html = _html(_FakeConn(results=[_result_row()], evidence=_EVIDENCE,
                           trials=_TRIALS), _fresh_s3())

    assert "너는 인과 가설 에이전트다" in html
    assert "BRIEF-원문" in html
    assert "RESPONSE-원문" in html
    assert "SELECT 1 AS probe" in html
    assert "<details>" in html
    # ④ usage 합산과 창 좌표
    assert "&quot;total_tokens&quot;: 140" in html
    assert "&quot;requested_end&quot;: &quot;10:31&quot;" in html


def test_old_utterances_fold_to_summary_rows_at_the_boundary():
    """③ 크기 천장 — 경계(REPORT_DETAIL_DAYS) 밖 발화는 요약 행만 남는다.

    경계 밖 발화의 산문 원문이 실리면 천장이 없는 것이고, 요약 행에서 결과 id 가
    빠지면 원장으로 되짚을 열쇠가 없다. 전환 기준은 페이지 머리에 있어야 한다.
    """
    old_day = _DAY - timedelta(days=REPORT_DETAIL_DAYS)   # 경계 하루 밖
    edge_day = _DAY - timedelta(days=REPORT_DETAIL_DAYS - 1)  # 경계 안 마지막 날
    rows = [
        _result_row(),
        _result_row(result_id="res_edge", run_id="run_e", day=edge_day,
                    summary="경계일 상세 원문"),
        _result_row(result_id="res_old", run_id="run_o", day=old_day,
                    summary="옛날 발화 산문 원문 — 실리면 안 된다"),
    ]
    html = _html(_FakeConn(results=rows, evidence=_EVIDENCE, trials=_TRIALS),
                 _fresh_s3())

    assert "옛날 발화 산문 원문" not in html              # 조용한 상세 금지
    assert "res_old" in html                              # 요약 행의 열쇠는 남는다
    assert "요약 구간" in html and "상세는 원장·trace 에 있다" in html
    cutoff = edge_day.isoformat()
    assert cutoff in html                                 # 전환 기준이 머리에 적힌다
    assert f"최근 {REPORT_DETAIL_DAYS}일" in html
    # 경계 안 마지막 날은 상세다 — 요약으로 접으면 하루를 잃는다
    assert "경계일 상세 원문" in html or "res_edge" in html


def test_same_input_renders_the_same_bytes():
    """정렬 결정론 + 벽시계 비의존 — 같은 입력이면 같은 바이트다."""
    def once():
        return _html(_FakeConn(results=[_result_row()], evidence=_EVIDENCE,
                               trials=_TRIALS), _fresh_s3())

    assert once().encode("utf-8") == once().encode("utf-8")


def test_page_references_no_external_resources():
    """외부 **참조** 0 — 파일 하나로 열리는 계약.

    의도 변경(ALPHA-894 후속): 필터 바가 인라인 <script> 를 쓰므로 '<script 태그
    자체 금지'에서 '외부 참조(src=·http(s)·<link>·@import·url()) 금지 + 인라인
    script 허용'으로 좁혔다. 네트워크 없이 단일 파일로 열리는 계약은 그대로다.
    """
    html = _html(_FakeConn(results=[_result_row()], evidence=_EVIDENCE,
                           trials=_TRIALS), _fresh_s3())

    assert "<link" not in html and "<img" not in html
    assert "src=" not in html                             # <script src> 포함 전면 금지
    assert "http://" not in html and "https://" not in html
    assert "@import" not in html and "url(" not in html


def test_filter_bar_lists_every_date_and_ticker_with_latest_date_default():
    """필터 바 — 전 일자(내림차순)·전 종목(가나다순)+'전체' 옵션이 있어야
    사용자가 어떤 일자·종목이든 도달할 수 있다. 기본 선택은 select 의 첫 옵션
    이므로 최신 일자가 맨 앞이어야 초기 스크롤이 하루치로 준다."""
    older = _DAY - timedelta(days=1)
    rows = [_result_row(),
            _result_row(result_id="res_b", run_id="run_b", day=older)]
    html = _html(_FakeConn(results=rows, evidence=_EVIDENCE, trials=_TRIALS),
                 _fresh_s3())

    assert f'<option value="{_DAY.isoformat()}">' in html
    assert f'<option value="{older.isoformat()}">' in html
    assert '<option value="">전체</option>' in html
    assert '<option value="091160">' in html
    # 최신 일자가 첫 옵션(=기본 선택) — 내림차순
    date_sel = html[html.index('id="f-date"'):html.index("f-ticker")]
    assert date_sel.index(_DAY.isoformat()) < date_sel.index(older.isoformat())


def test_sections_carry_date_and_ticker_hooks_and_page_has_inline_filter_script():
    """섹션마다 data-date·data-ticker 가 붙어야 JS 토글이 잡을 수 있고,
    <noscript> 폴백이 있어야 JS 꺼진 환경에서 전체 목록 안내가 남는다."""
    html = _html(_FakeConn(results=[_result_row()], evidence=_EVIDENCE,
                           trials=_TRIALS), _fresh_s3())

    assert f'<section data-date="{_DAY.isoformat()}" data-ticker="091160">' in html
    assert "<script>" in html and "addEventListener" in html
    assert "<noscript>" in html


def test_orphan_trials_are_not_silently_dropped():
    orphan = ("run_gone", "091160", _DAY, "REJECTED", "점:X", None, None, None,
              "REJECTED", None, None, "run 확정 전 사망")
    html = _html(_FakeConn(results=[_result_row()], evidence=_EVIDENCE,
                           trials=[*_TRIALS, orphan]), _fresh_s3())

    assert "런 미연결 가설 시행" in html and "run 확정 전 사망" in html


def test_regenerate_overwrites_the_single_dashboard_key():
    s3 = _fresh_s3()
    conn = _FakeConn(results=[_result_row()], evidence=_EVIDENCE, trials=_TRIALS)

    first = regenerate_dashboard(conn=conn, s3=s3, result_prefix=_PREFIX)
    second = regenerate_dashboard(conn=conn, s3=s3, result_prefix=_PREFIX)

    key = "operations_archive/etf_explanations/reports/dashboard.html"
    assert first == second == f"s3://{_BUCKET}/{key}"
    assert (_BUCKET, key) in s3.objects                   # 사용자가 보는 그 파일 하나


def test_regenerate_out_dir_writes_a_local_file(tmp_path):
    conn = _FakeConn(results=[_result_row()], evidence=_EVIDENCE, trials=_TRIALS)

    path = regenerate_dashboard(conn=conn, s3=_fresh_s3(), result_prefix=_PREFIX,
                                out_dir=str(tmp_path))

    body = (tmp_path / "dashboard.html").read_text(encoding="utf-8")
    assert path == str(tmp_path / "dashboard.html")
    assert "발화 스냅샷 대시보드" in body


# ── run() 끝의 배선 (test_pipeline 의 실물 대역 재사용) ────────────────────
from test_pipeline import (  # noqa: E402
    _PREREQS_OK, _SETTINGS, _TRIGGER, _FakeClient, _FakeLake, _FakeS3 as _PipeS3,
    _FakeStore,
)
from edge_analysis.pipeline import run  # noqa: E402


def test_run_regenerates_the_dashboard_after_the_archive(monkeypatch):
    """런 완주가 대시보드 재생성을 부른다 — 이 배선이 끊기면 페이지는 영영 낡는다.

    호출 인자까지 본다: 그 런의 S3 클라이언트와 결과 prefix 로 불러야 같은 버킷의
    같은 키를 덮어쓴다(멱등).
    """
    calls = []
    monkeypatch.setattr("edge_analysis.report.regenerate_dashboard",
                        lambda **kw: calls.append(kw))
    store = _FakeStore(trigger=_TRIGGER, prereqs=_PREREQS_OK)
    s3 = _PipeS3()

    assert run(_SETTINGS, lake=_FakeLake(), store=store, client=_FakeClient(),
               s3=s3) == 0
    assert len(calls) == 1, "run() 끝의 재생성 배선이 끊겼다"
    assert calls[0]["s3"] is s3
    assert calls[0]["result_prefix"] == _SETTINGS.result_s3_prefix


def test_dashboard_failure_does_not_kill_the_run_but_is_logged(monkeypatch, capsys):
    """재생성 실패는 런을 죽이지 않되 `report.regenerate_failed` 로 드러난다.

    설명은 이미 영속됐다 — 대시보드가 런을 물귀신처럼 끌고 내려가면 안 되고,
    조용히 삼키면 페이지가 낡은 채로 아무도 모른다(Rule 12).
    """
    def _boom(**_kw):
        raise RuntimeError("s3 down")

    monkeypatch.setattr("edge_analysis.report.regenerate_dashboard", _boom)
    store = _FakeStore(trigger=_TRIGGER, prereqs=_PREREQS_OK)

    assert run(_SETTINGS, lake=_FakeLake(), store=store, client=_FakeClient(),
               s3=_PipeS3()) == 0                          # 런은 산다
    assert "persist_explanation" in store.calls
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()
              if line.startswith("{")]
    failed = [e for e in events if e.get("event") == "report.regenerate_failed"]
    assert failed and "s3 down" in failed[0]["error"]
