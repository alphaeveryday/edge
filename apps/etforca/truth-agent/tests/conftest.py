"""공용 픽스처: seed 사건과 기사."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from edge_truth_agent.news import Article
from edge_truth_agent import media


@pytest.fixture(autouse=True)
def no_media_network(monkeypatch):
    """시험은 기사·YouTube 서버에 접속하지 않는다. 사진·영상을 보는 시험은 이 두 함수를 따로 바꾼다."""
    monkeypatch.setattr(media, "og_image", lambda url: None)
    monkeypatch.setattr(media, "video_ok", lambda video_id: False)

ROOT = Path(__file__).resolve().parents[1]
SEED = ROOT / "seed"
WEB = ROOT.parent / "truth-web"


def seed(name: str) -> dict:
    """seed 사건 하나 (매번 새 사본)."""
    return json.loads((SEED / f"{name}.json").read_text(encoding="utf-8"))


def article(title: str, lead: str = "", *, aid: str = "a" * 64, published: str = "2026-10-20 10:00:00",
            url: str = "https://www.yna.co.kr/view/AKR1", publisher: str = "연합뉴스") -> Article:
    """시험용 기사."""
    return Article(id=aid, title=title, lead=lead, url=url, publisher=publisher, published=published)


def chromium_ready() -> bool:
    """Playwright Chromium 이 설치돼 있는가 (렌더 시험 전제)."""
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            pw.chromium.launch().close()
        return True
    except Exception:  # noqa: BLE001 - 설치 여부만 본다
        return False


_CHROMIUM = chromium_ready()
if not _CHROMIUM and os.environ.get("TRUTH_REQUIRE_CHROMIUM"):
    # CI 는 Chromium 을 설치한다. 설치가 깨졌는데 시험을 건너뛰면 렌더·실행 시험 없이 초록이 된다.
    raise RuntimeError("TRUTH_REQUIRE_CHROMIUM 이 켜져 있는데 Chromium 을 띄우지 못했습니다.")
needs_chromium = pytest.mark.skipif(not _CHROMIUM, reason="Playwright Chromium 미설치 — `playwright install chromium` 후 실행")
