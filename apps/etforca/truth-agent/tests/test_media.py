"""대표 사진·영상. 모델이 지어낸 주소가 공개 화면에 실리지 않게, 코드가 등록된 출처에서만 채우는지 본다."""
from __future__ import annotations

import http.client
import io
import time
import urllib.error

import pytest

from conftest import SEED, seed
from edge_truth_agent import media, runner, tools
from edge_truth_agent.tools import ToolError
from test_news_render_runner import NOW, _cfg
from test_tools import ws  # noqa: F401 - 도구 시험 픽스처 재사용

REAL_OG_IMAGE, REAL_IMAGE_OK, REAL_VIDEO_OK = media.og_image, media.image_ok, media.video_ok


class _Page(io.BytesIO):
    def __init__(self, body: str, content_type: str = "text/html; charset=utf-8", status: int = 200):
        super().__init__(body.encode("utf-8"))
        self.headers, self.status = {"Content-Type": content_type}, status

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        return None


@pytest.mark.parametrize(("html", "expected"), [
    ('<meta property="og:image" content="https://img.yna.co.kr/a.jpg?x=1&amp;y=2">', "https://img.yna.co.kr/a.jpg?x=1&y=2"),
    ('<meta content="https://dimg.donga.com/b.jpg" property="og:image" />', "https://dimg.donga.com/b.jpg"),
    ('<meta property="og:image" content="//img.hani.co.kr/c.jpg">', "https://img.hani.co.kr/c.jpg"),
    # 평문 http·자바스크립트 주소는 공개 화면에 싣지 않는다
    ('<meta property="og:image" content="http://img.example.com/d.jpg">', None),
    ('<meta property="og:image" content="javascript:alert(1)">', None),
    ('<title>사진 없음</title>', None),
])
def test_og_image_reads_only_https_images_from_the_page(monkeypatch, html, expected):
    monkeypatch.setattr(media.urllib.request, "urlopen", lambda req, timeout: _Page(f"<html><head>{html}</head></html>"))
    assert REAL_OG_IMAGE("https://www.yna.co.kr/view/AKR1") == expected


def test_og_image_never_raises_on_unreachable_or_non_html_pages(monkeypatch):
    # 사진은 게시를 막지 않는다 — 기사 서버 장애가 실행 전체를 멈추면 안 된다.
    def down(req, timeout):
        raise urllib.error.URLError("down")
    monkeypatch.setattr(media.urllib.request, "urlopen", down)
    assert REAL_OG_IMAGE("https://www.yna.co.kr/view/AKR1") is None
    monkeypatch.setattr(media.urllib.request, "urlopen", lambda req, timeout: _Page("%PDF", "application/pdf"))
    assert REAL_OG_IMAGE("https://www.yna.co.kr/view/AKR1") is None


def test_image_ok_requires_an_image_response(monkeypatch):
    # 기사 페이지가 준 사진 주소가 HTML 오류 페이지면 싣지 않는다.
    monkeypatch.setattr(media.urllib.request, "urlopen", lambda req, timeout: _Page("", "image/jpeg"))
    assert REAL_IMAGE_OK("https://img.yna.co.kr/a.jpg")
    monkeypatch.setattr(media.urllib.request, "urlopen", lambda req, timeout: _Page("<html>404</html>"))
    assert not REAL_IMAGE_OK("https://img.yna.co.kr/a.jpg")


def test_malformed_addresses_are_unusable_not_fatal():
    # 기사 메타데이터의 깨진 주소가 예외로 새면 매시 실행 전체가 게시 없이 끝난다.
    assert REAL_IMAGE_OK("https://[bad/a.jpg") is False
    assert REAL_OG_IMAGE("https://[bad/view") is None


def test_video_check_failure_never_stops_the_run(monkeypatch):
    # 응답을 읽다 끊기는 오류(URLError 가 아닌 것)도 '확인 못 함'일 뿐, 매시 실행 전체를 멈추면 안 된다.
    def cut(video_id):
        raise http.client.IncompleteRead(b"")
    monkeypatch.setattr(media, "_check_video", cut)
    assert REAL_VIDEO_OK("eGnCRa5nd58") is False
    monkeypatch.setattr(media, "_check_video", lambda video_id: (_ for _ in ()).throw(ConnectionResetError()))
    assert REAL_VIDEO_OK("eGnCRa5nd58") is False


def test_youtube_id_ignores_other_sites_that_mention_youtube():
    assert media.youtube_id("https://www.youtube.com/watch?v=eGnCRa5nd58&t=3") == "eGnCRa5nd58"
    assert media.youtube_id("https://youtu.be/eGnCRa5nd58") == "eGnCRa5nd58"
    assert media.youtube_id("https://evil.example/?u=youtube.com/watch?v=eGnCRa5nd58") is None


def test_fill_uses_news_sources_only_and_keeps_existing_media(monkeypatch):
    asked = []
    monkeypatch.setattr(media, "og_image", lambda url: asked.append(url) or ("https://img.yna.co.kr/x.jpg" if "yna" in url else None))
    monkeypatch.setattr(media, "image_ok", lambda url: True)
    case = seed("geumyang-monlaa")
    case["sources"] = [{"id": "web-1", "kind": "web", "url": "https://blog.example.com/post", "title": "블로그"},
                       {"id": "news-1", "kind": "news", "url": "https://www.yna.co.kr/view/AKR1", "title": "연합"}]
    case["cover"], case["video"] = None, None
    assert media.fill(case) and case["cover"] == "https://img.yna.co.kr/x.jpg"
    # 언론 도메인이 아닌 출처의 사진은 쓰지 않는다(종류 표시가 아니라 주소로 판정).
    assert asked == ["https://www.yna.co.kr/view/AKR1"]
    asked.clear()
    assert not media.fill(case) and asked == []  # 이미 있는 사진은 다시 찾지 않는다


def test_a_broken_image_is_not_attached_so_a_cleared_cover_stays_clear(monkeypatch):
    # 모델이 깨진 사진을 null 로 비운 뒤, 다음 실행이 같은 깨진 사진을 다시 붙이면 비운 의미가 없다.
    monkeypatch.setattr(media, "og_image", lambda url: "https://img.yna.co.kr/broken.jpg")
    case = seed("geumyang-monlaa")
    case["cover"] = None
    assert not media.fill(case) and case["cover"] is None


def test_fill_stops_looking_once_the_run_budget_is_spent(monkeypatch):
    asked = []
    monkeypatch.setattr(media, "og_image", lambda url: asked.append(url))
    case = seed("geumyang-monlaa")
    case["cover"] = None
    assert not media.fill(case, deadline=time.monotonic() - 1) and asked == []


def test_fill_takes_video_only_from_a_youtube_source_that_is_public(monkeypatch):
    case = seed("geumyang-monlaa")
    case["video"] = None
    case["sources"] = [{"id": "video-1", "kind": "video", "url": "https://www.youtube.com/watch?v=eGnCRa5nd58", "title": "MBN 보도"}]
    assert not media.fill(case) and case["video"] is None  # 비공개·삭제 영상(oEmbed 실패)은 싣지 않는다
    monkeypatch.setattr(media, "video_ok", lambda video_id: True)
    assert media.fill(case) and case["video"] == {"youtube": "eGnCRa5nd58", "title": "MBN 보도"}


def test_model_cannot_write_media_addresses_but_can_clear_them(ws):  # noqa: F811
    tools.view_case(ws, {})
    for path, value in (("cover", "https://made.up/image.jpg"), ("video", {"youtube": "AAAAAAAAAAA", "title": "x"}),
                        ("video.youtube", "AAAAAAAAAAA")):
        with pytest.raises(ToolError, match="코드가 등록된 출처"):
            tools.set_value(ws, {"path": path, "value": value})
    with pytest.raises(ToolError, match="코드가 등록된 출처"):
        tools.str_replace(ws, {"path": "cover", "old": "https", "new": "https"})
    tools.set_value(ws, {"path": "cover", "value": None})
    assert ws.case()["cover"] is None


def test_hourly_run_fills_cover_from_sources_and_publishes_it(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "SEED_DIR", SEED)
    monkeypatch.setattr(runner, "render", lambda cases, web, focus: {k: {"errors": [], "warnings": []} for k in [*focus, "index"]})
    monkeypatch.setattr(media, "og_image", lambda url: "https://img.yna.co.kr/cover.jpg")
    monkeypatch.setattr(media, "image_ok", lambda url: True)
    report = runner.run(_cfg(tmp_path), "hourly", now=NOW)
    # hybe 는 사람이 넣은 사진이 이미 있어 그대로, 나머지 seed 는 출처 기사 사진으로 채운다.
    assert "hybe-ipo-2019" not in report["changed"] and "geumyang-monlaa" in report["changed"]
    import json
    public = json.loads((tmp_path / "public" / "cases" / "geumyang-monlaa.json").read_text(encoding="utf-8"))
    assert public["cover"] == "https://img.yna.co.kr/cover.jpg"


def test_fill_tries_at_most_three_news_sources_per_case(monkeypatch):
    # 매시 실행이 사건 하나의 출처를 전부 두드리면 시간 상한을 한 사건이 다 쓴다.
    asked = []
    monkeypatch.setattr(media, "og_image", lambda url: asked.append(url))
    case = seed("geumyang-monlaa")
    case["cover"] = None
    case["sources"] = [{"id": f"news-{i}", "kind": "news", "url": f"https://www.yna.co.kr/view/AKR{i}", "title": "연합"} for i in range(5)]
    media.fill(case)
    assert len(asked) == media.SOURCES_PER_CASE


def test_out_of_time_is_not_reported_as_not_found(monkeypatch, caplog):
    # 시간이 없어 찾지 않은 사건을 '못 찾음'으로 남기면 사진이 왜 없는지 잘못 읽게 된다.
    case = seed("geumyang-monlaa")
    case["cover"] = None
    with caplog.at_level("INFO", logger=media.__name__):
        media.fill(case, deadline=time.monotonic() - 1)
        assert "media_miss" not in caplog.text
        media.fill(case)
        assert "media_miss" in caplog.text


def test_hourly_run_leaves_locked_cases_media_to_people(tmp_path, monkeypatch):
    # locked 는 사람이 정정한 사건이다. 코드가 사진을 바꾸면 사람의 정정을 덮는다.
    import json
    seeds = tmp_path / "seed"
    seeds.mkdir()
    locked = seed("geumyang-monlaa")
    locked["locked"], locked["cover"] = True, None
    (seeds / "geumyang-monlaa.json").write_text(json.dumps(locked, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(runner, "SEED_DIR", seeds)
    monkeypatch.setattr(runner, "render", lambda cases, web, focus: {k: {"errors": [], "warnings": []} for k in [*focus, "index"]})
    monkeypatch.setattr(media, "og_image", lambda url: "https://img.yna.co.kr/cover.jpg")
    monkeypatch.setattr(media, "image_ok", lambda url: True)
    report = runner.run(_cfg(tmp_path), "hourly", now=NOW)
    assert report["changed"] == []
    public = json.loads((tmp_path / "public" / "cases" / "geumyang-monlaa.json").read_text(encoding="utf-8"))
    assert public["cover"] is None


def test_hourly_run_reports_when_the_media_budget_runs_out(tmp_path, monkeypatch, caplog):
    # 시간 상한에 걸리면 사진이 왜 안 붙는지 실행 로그에서 보여야 한다(조용히 건너뛰지 않는다).
    monkeypatch.setattr(runner, "SEED_DIR", SEED)
    monkeypatch.setattr(runner, "render", lambda cases, web, focus: {k: {"errors": [], "warnings": []} for k in [*focus, "index"]})
    monkeypatch.setattr(media, "RUN_SECONDS", 0)
    with caplog.at_level("WARNING", logger=runner.__name__):
        runner.run(_cfg(tmp_path), "hourly", now=NOW)
    assert "media_deadline" in caplog.text
