"""국내 보도를 운영 DB 에서 읽기 (ALPHA-1450).

WHY: truth-agent 말뭉치를 끊고 DB 를 읽는다. 기사 id 가 말뭉치와 같아야(source_document_id) 이미 남긴 판정 근거가
이어지고, 발행 시각이 한국 시각 문자열이어야 '판정 뒤 새 보도' 비교가 그대로 맞는다. DB 에 못 붙으면 옛 기사로
조용히 판정하지 말고 실행이 실패해야 한다.
"""
from datetime import datetime, timezone

import pytest

from edge_truth_research import dbnews


class FakeCursor:
    def __init__(self, rows, seen):
        self.rows, self.seen = list(rows), seen

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params):
        self.seen.append((sql, params))

    def fetchmany(self, n):
        out, self.rows = self.rows[:n], self.rows[n:]
        return out


class FakeConn:
    def __init__(self, rows, seen):
        self.rows, self.seen = rows, seen

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self, name=None):
        assert name, "수만 행이라 서버 커서로 나눠 받는다"
        return FakeCursor(self.rows, self.seen)


def test_rows_become_corpus_shaped_articles_keyed_by_the_corpus_id(cfg):
    now = datetime(2026, 10, 26, 1, 35, tzinfo=timezone.utc)  # 10:35 KST
    seen = []
    rows = [("a" * 64, "  [특징주]  에이비  급등 ", " 수주\n공시 ", "https://n/1", "연합뉴스", "2026-10-26 10:20:00"),
            ("b" * 64, None, "본문만", "", "", "2026-10-26 10:21:00"),          # 제목 없음: 버린다
            ("c" * 64, "다른 기사", None, None, None, None)]
    texts = dbnews.load(cfg, now, connect=lambda c: FakeConn(rows, seen))
    assert sorted(texts) == ["a" * 64, "c" * 64]
    a = texts["a" * 64]
    assert (a.title, a.lead, a.url, a.publisher, a.published) == ("[특징주] 에이비 급등", "수주 공시", "https://n/1", "연합뉴스",
                                                                  "2026-10-26 10:20:00")
    assert texts["c" * 64].lead == "" and texts["c" * 64].published == ""
    # 판정 뒤 새 보도 비교(미국의 빅카인즈 대체 기사)는 research._kst_iso 로 ISO 를 만든다
    from edge_truth_research.research import _after, _kst_iso
    assert _after(_kst_iso(a.published), "2026-10-26T10:00:00+09:00")
    assert not _after(_kst_iso(a.published), "2026-10-26T10:30:00+09:00")


def test_cannot_reach_the_database_fails_the_run(cfg):
    def down(c):
        raise OSError("connection refused")
    with pytest.raises(OSError):
        dbnews.load(cfg, datetime(2026, 10, 26, tzinfo=timezone.utc), connect=down)


def test_run_requires_database_settings(monkeypatch):
    from edge_truth_research.config import Config, ConfigError
    for k, v in {"TRUTH_STORE": "s", "TRUTH_PUBLISH": "p", "DEEPSEEK_RESIST_NEWS_API_KEY": "k"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("TRUTH_DB_HOST", raising=False)
    monkeypatch.delenv("TRUTH_DB_PASSWORD", raising=False)
    with pytest.raises(ConfigError, match="TRUTH_DB_HOST"):
        Config.from_env()
    monkeypatch.setenv("TRUTH_DB_HOST", "db")
    monkeypatch.setenv("TRUTH_DB_PASSWORD", "pw")
    assert Config.from_env().db_host == "db"


def test_session_environment_never_carries_the_database_password(cfg, tmp_path, monkeypatch):
    from edge_truth_agent.workspace import Workspace
    from edge_truth_research import session
    monkeypatch.setenv("TRUTH_DB_PASSWORD", "pw-secret")
    env = session._env(cfg, Workspace(tmp_path / "ws"), tmp_path / "c.sqlite")
    assert "TRUTH_DB_PASSWORD" not in env



def test_publication_time_reads_both_the_database_and_the_old_corpus_format():
    # 예전 말뭉치는 ISO('...T10:20:00+09:00')와 공백 형식이 섞여 있었고, ISO 에 '+09:00' 을 또 붙여 읽기에 실패했다
    from edge_truth_research.research import _after, _kst_iso
    judged = "2026-10-26T10:00:00+09:00"
    assert _kst_iso("2026-10-26 10:20:00") == "2026-10-26T10:20:00+09:00"
    assert _kst_iso("2026-10-26T10:20:00+09:00") == "2026-10-26T10:20:00+09:00"
    assert _after(_kst_iso("2026-10-26T10:20:00+09:00"), judged)
    assert _kst_iso("2026-10-26") is None and _kst_iso("") is None
