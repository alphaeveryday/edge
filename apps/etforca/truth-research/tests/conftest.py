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
