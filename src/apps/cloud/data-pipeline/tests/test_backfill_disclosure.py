"""보관 DART raw → canonical/fact/event 백필 오케스트레이션 (ALPHA-895)."""

from data_pipeline.config import DbConfig
from data_pipeline.steps import backfill_disclosure


def test_backfill_reuses_full_forward_stages_with_optional_filing_window(monkeypatch):
    """별도 변환 구현은 forward/backfill 결과를 갈라놓는다. 같은 네 단계와 같은 접수일
    창을 공유하고, raw input_run_id는 None이어야 보관분 전체를 읽는다."""
    calls = []

    monkeypatch.setattr(backfill_disclosure.normalize_disclosure, "run",
                        lambda *a, **k: calls.append(("supply", a, k)) or 0)
    monkeypatch.setattr(backfill_disclosure.normalize_disclosure_segment, "run",
                        lambda *a, **k: calls.append(("segment", a, k)) or 0)
    monkeypatch.setattr(backfill_disclosure.load_disclosure, "run",
                        lambda *a, **k: calls.append(("load", a, k)) or 0)
    monkeypatch.setattr(backfill_disclosure.assemble_disclosure_events, "run",
                        lambda *a, **k: calls.append(("assemble", a, k)) or 0)

    assert backfill_disclosure.run(
        object(), "B1", db=DbConfig(password="x"),
        from_date="2026-01-01", to_date="2026-06-30") == 0

    assert [name for name, _a, _k in calls] == ["supply", "segment", "load", "assemble"]
    assert calls[0][1][2] is None and calls[1][1][2] is None  # input_run_id=전체 raw
    for _name, _args, kwargs in calls:
        assert kwargs["from_date"] == "2026-01-01"
        assert kwargs["to_date"] == "2026-06-30"


def test_backfill_stops_before_loading_when_normalization_fails(monkeypatch):
    """canonical 생성 실패 뒤 load를 계속하면 과거 성공분만 다시 조립해 백필 성공처럼 보인다."""
    monkeypatch.setattr(backfill_disclosure.normalize_disclosure, "run", lambda *a, **k: 1)
    monkeypatch.setattr(backfill_disclosure.load_disclosure, "run",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("load called")))

    assert backfill_disclosure.run(object(), "B1", db=DbConfig(password="x")) == 1
