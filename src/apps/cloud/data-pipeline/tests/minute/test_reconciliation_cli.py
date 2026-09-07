"""운영 대사 CLI는 누락 인자·조회 실패·무결성 오류를 성공으로 숨기지 않는다."""

from types import SimpleNamespace

import pytest

from data_pipeline import run
from data_pipeline.minute import reconciliation as mod


def test_reconcile_command_routes_quarantine_arguments(monkeypatch):
    calls = []
    monkeypatch.setattr(run, "reconcile_artifacts_cli", lambda settings, **kw: calls.append(kw) or 0)
    assert run.main(["reconcile-minute-artifacts", "--session-id", "session", "--quarantine",
                     "--actor", "operator", "--reason", "closed session"]) == 0
    assert calls == [{"session_id": "session", "quarantine": True,
                      "actor": "operator", "reason": "closed session"}]


@pytest.mark.parametrize("args", [["relay", "--quarantine"], ["relay", "--actor", "x"],
                                   ["reconcile-minute-artifacts", "--reason", "x"],
                                   ["reconcile-minute-artifacts", "--actor", "x"]])
def test_quarantine_options_cannot_be_silently_ignored(args):
    with pytest.raises(SystemExit):
        run.main(args)


@pytest.mark.parametrize("session_id,db", [(None, object()), ("session", None)])
def test_missing_target_or_database_is_not_success(session_id, db):
    assert mod.reconcile_artifacts_cli(SimpleNamespace(db=db), session_id=session_id) == 2


@pytest.mark.parametrize("report,code", [({"ok": True, "errors": []}, 0),
                                        ({"ok": False, "errors": []}, 1),
                                        ({"ok": False, "errors": ["DB denied"]}, 2)])
def test_cli_preserves_report_failure_exit(monkeypatch, capsys, report, code):
    monkeypatch.setattr(mod, "reconcile_minute_artifacts", lambda **kw: report)
    monkeypatch.setattr("data_pipeline.lake.make_storage", lambda cfg: object())
    assert mod.reconcile_artifacts_cli(SimpleNamespace(db=object(), storage=object()),
                                       session_id="session") == code
    assert '"ok"' in capsys.readouterr().out
