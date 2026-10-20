"""실제 화면 렌더 검사. 사건 데이터로 사이트를 만들어 헤드리스 Chromium 으로 열어 본다.

계약 검사(cases.py)는 데이터 형식을, 이 검사는 '그 데이터로 화면이 실제로 그려지는가'를 본다.
- 오류(errors): 스크립트 예외, 제목이 비었거나 사건을 못 찾는 화면, 섹션·출처 목록 누락
- 경고(warnings): 사진 주소가 응답하지 않음 — 화면은 사진 없이 그려지므로 게시를 막지는 않는다
"""
from __future__ import annotations

from contextlib import contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import shutil
import tempfile
import threading

from . import site

WEB_FILES = ("index.html", "issue.html", "list.html", "common.js", "intro.js", "style.css", "logo.png")


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002 - 부모 서명 그대로. 요청 로그를 남기지 않는다
        pass


@contextmanager
def _serve(root: Path):
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(_Quiet, directory=str(root)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/truth/"
    finally:
        server.shutdown()
        server.server_close()


def _stage(cases: list[dict], web_dir: Path, root: Path) -> None:
    from datetime import datetime, timezone
    out = root / "truth"
    out.mkdir(parents=True)
    for name in WEB_FILES:
        shutil.copy(web_dir / name, out / name)
    site.write(site.build(cases, datetime.now(timezone.utc)), out / "data")


def render(cases: list[dict], web_dir: Path, focus: list[str], *, timeout_ms: int = 20000) -> dict[str, dict[str, list[str]]]:
    """홈과 focus 사건 상세를 열어 본다. 결과: {'index' 또는 사건 id: {'errors': [...], 'warnings': [...]}}."""
    from playwright.sync_api import Error as PlaywrightError, sync_playwright

    result: dict[str, dict[str, list[str]]] = {}
    with tempfile.TemporaryDirectory() as tmp, sync_playwright() as pw:
        root = Path(tmp)
        _stage(cases, Path(web_dir), root)
        browser = pw.chromium.launch()
        try:
            with _serve(root) as base:
                targets = [("index", base + "index.html")] + [(cid, f"{base}issue.html?id={cid}") for cid in focus]
                for key, url in targets:
                    errors: list[str] = []
                    warnings: list[str] = []
                    page = browser.new_page()
                    # 외부 글꼴·스타일은 받지 않는다 — 글꼴 CDN 이 느리면 멀쩡한 사건이 '그려지지 않음'으로 되돌려진다.
                    # 화면 구조 검사에는 필요 없고, 사진(외부 이미지)은 경고 확인용으로 그대로 받는다.
                    page.route("**/*", lambda route: route.abort() if route.request.resource_type in ("font", "stylesheet")
                               and not route.request.url.startswith(base) else route.continue_())
                    _watch(page, errors, warnings)
                    try:
                        page.goto(url, wait_until="load", timeout=timeout_ms)
                        if key == "index":
                            page.wait_for_function("document.querySelector('#hero')?.textContent.trim().length > 0", timeout=timeout_ms)
                        else:
                            page.wait_for_function("document.querySelector('#article')?.textContent.trim().length > 0", timeout=timeout_ms)
                            errors += _issue_problems(page, next(c for c in cases if c["id"] == key))
                    except PlaywrightError as error:
                        errors.append(f"화면이 {timeout_ms // 1000}초 안에 그려지지 않았습니다: {error}".splitlines()[0])
                    else:
                        # 사진 응답을 기다려 경고를 모은다. 느린 외부 사진 서버 때문에 게시가 막히면 안 되므로 경고다.
                        try:
                            page.wait_for_load_state("networkidle", timeout=timeout_ms)
                        except PlaywrightError:
                            warnings.append(f"외부 자원이 {timeout_ms // 1000}초 안에 끝나지 않았습니다(사진 서버 지연)")
                    finally:
                        page.close()
                    result[key] = {"errors": errors, "warnings": sorted(set(warnings))}
        finally:
            browser.close()
    return result


def _watch(page, errors: list[str], warnings: list[str]) -> None:
    """페이지 이벤트를 오류·경고 목록에 모은다. 외부 글꼴 등 자원 로드 실패는 화면 오류로 치지 않는다."""

    def on_page_error(error) -> None:
        errors.append(f"스크립트 오류: {error}")

    def on_console(message) -> None:
        if message.type == "error" and "Failed to load resource" not in message.text:
            errors.append(f"콘솔 오류: {message.text}")

    def on_response(response) -> None:
        if response.request.resource_type == "image" and response.status >= 400:
            warnings.append(f"사진을 불러오지 못했습니다 ({response.status}): {response.url}")

    def on_request_failed(request) -> None:
        if request.resource_type == "image":
            warnings.append(f"사진 요청 실패: {request.url}")

    page.on("pageerror", on_page_error)
    page.on("console", on_console)
    page.on("response", on_response)
    page.on("requestfailed", on_request_failed)


def _issue_problems(page, case: dict) -> list[str]:
    """상세 화면이 사건 내용을 빠짐없이 그렸는가."""
    shown = page.evaluate("""() => ({
        missing: !!document.querySelector('#article .vcap a[href="index.html"]'),
        h1: document.querySelector('#article h1')?.textContent.trim() || '',
        sections: document.querySelectorAll('#article .sec').length,
        sources: document.querySelectorAll('#article .sources li[id^="src-"]').length,
    })""")
    out = []
    if shown["missing"]:
        return ["상세 화면이 '사건을 찾을 수 없습니다'로 그려졌습니다. 사건 파일을 읽지 못했습니다."]
    if shown["h1"] != (case.get("title") or case["headline"][0]).strip():
        out.append("상세 화면 제목이 title(없으면 headline[0])과 다르게 그려졌습니다.")
    if shown["sections"] != 3:
        out.append(f"핵심 거짓·본문·반론 세 섹션 중 {shown['sections']}개만 그려졌습니다.")
    if shown["sources"] != len(case["sources"]):
        out.append(f"출처 {len(case['sources'])}건 중 {shown['sources']}건만 그려졌습니다.")
    return out
