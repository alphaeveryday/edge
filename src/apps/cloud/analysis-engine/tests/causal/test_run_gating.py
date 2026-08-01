"""P2 호출 게이트 — 사건 후보의 생존 여부는 LLM 호출 조건이 아니다.

실측 근거: ETF 기여 1위의 57%가 당일 사건이 없다(설계 §2). 종전 `if alive:` 는
그 셀 전부를 구조적으로 설명 불가로 만들었다 - P2 의 계약(후보 밖 원인·무사건
충격 1급, DOMAIN_SAY 의 flow·no_event)과 정면 모순. LLM 을 막는 유일한 게이트는
무설명항(잔차가 자기 귀무 안)이어야 한다.
"""
from types import SimpleNamespace

from edge_analysis.causal import run as R
from edge_analysis.causal.contracts import Fingerprint


def _q(no_explanandum: bool):
    return SimpleNamespace(no_explanandum=no_explanandum, residual=0.03,
                           p_scan=0.01, null_note="", explanandum="r⊥=+3%")


def _stub_pipeline(monkeypatch, question, called: dict):
    monkeypatch.setattr(R.p0, "ask", lambda *a, **k: question)
    monkeypatch.setattr(R.p1, "take", lambda *a, **k: Fingerprint(axes=[]))
    monkeypatch.setattr(R.p2, "propose",
                        lambda *a, **k: called.__setitem__("p2", True) or [])
    monkeypatch.setattr(R.p6, "evaluate", lambda *a, **k: [])
    monkeypatch.setattr(R.p7, "negative_controls", lambda *a, **k: [])
    monkeypatch.setattr(R, "_screen", lambda *a, **k: None)
    monkeypatch.setattr(R, "_outcome_sd", lambda *a, **k: {})
    monkeypatch.setattr(R.p8, "dispose", lambda **k: SimpleNamespace())
    monkeypatch.setattr(R.p8, "narrate", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(R.p8, "audit_block", lambda **k: {})


def _explain(question, monkeypatch, candidates):
    called: dict = {}
    _stub_pipeline(monkeypatch, question, called)
    from datetime import date
    out = R.explain(None, None, etf_name="x", etf_instrument_id="i",
                    trade_date=date(2026, 6, 1), as_of="2026-06-01T15:40:00+09:00",
                    observed=0.03, route_code="EVENT", contributors=[],
                    candidates=candidates)
    assert out == {"ok": True}
    return called


def test_p2_runs_for_eventless_cell(monkeypatch):
    # 후보 0건 + 설명항 있음 → P2 는 반드시 불린다 (수급·무사건 원인의 유일한 입구).
    called = _explain(_q(no_explanandum=False), monkeypatch, candidates=[])
    assert called.get("p2") is True


def test_p2_skipped_only_by_no_explanandum(monkeypatch):
    # 잔차가 자기 귀무 안 → LLM 을 부르면 반증 불가 서사가 나온다 - 유일한 정당한 스킵.
    called = _explain(_q(no_explanandum=True), monkeypatch, candidates=[])
    assert "p2" not in called
