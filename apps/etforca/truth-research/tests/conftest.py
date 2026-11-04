"""공용 픽스처: 시험용 기사·설정. 네트워크(KIND·Naver·Yahoo·모델)는 쓰지 않는다 — 각 시험이 가짜로 바꾼다."""
from __future__ import annotations

from pathlib import Path

import pytest

from edge_truth_agent.news import Article
from edge_truth_research.config import Config


def article(title: str, lead: str = "", *, aid: str = "a" * 64, published: str = "2026-10-22 10:00:00",
            url: str = "https://www.yna.co.kr/view/AKR1", publisher: str = "연합뉴스") -> Article:
    """시험용 기사."""
    return Article(id=aid, title=title, lead=lead, url=url, publisher=publisher, published=published)


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    return Config(store=str(tmp_path / "store"), publish=str(tmp_path / "public"), deepseek_key="test", exa_key=None,
                  model="deepseek/deepseek-flash", thinking="low", pi_dir=tmp_path, python="python",
                  session_timeout=60, max_tool_calls=30, web_calls=10)


@pytest.fixture(autouse=True)
def news_from_store(monkeypatch):
    """조사 시험의 국내 보도: 운영 DB 대신 시험이 저장소에 넣은 기사(말뭉치 형식)를 읽는다. DB 읽기 자체는 test_dbnews 가 본다."""
    from edge_truth_agent import corpus
    from edge_truth_agent.store import open_store
    from edge_truth_research import research
    monkeypatch.setattr(research, "load_news", lambda cfg, now: corpus.load(open_store(cfg.store)))
