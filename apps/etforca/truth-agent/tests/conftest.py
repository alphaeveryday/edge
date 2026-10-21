"""공용 픽스처: seed 사건과 기사."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil

import pytest

from edge_truth_agent.news import Article

ROOT = Path(__file__).resolve().parents[1]
SEED = ROOT / "seed"
WEB = ROOT.parent / "truth-web"


FLOW_SEEDS = ("geumyang-monlaa", "homeplus-short-term-bonds", "hybe-ipo-2019", "koreazinc-offering-2024")


def flow_seed(tmp_path: Path) -> Path:
    """실행 흐름 시험용 seed 폴더(처음 4건). 시작 사건이 늘어도 흐름 시험의 후보·렌더 범위가 바뀌지 않게."""
    out = tmp_path / "flow-seed"
    out.mkdir(exist_ok=True)
    for name in FLOW_SEEDS:
        shutil.copy(SEED / f"{name}.json", out)
    return out


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
