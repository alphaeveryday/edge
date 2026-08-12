#!/usr/bin/env python3
"""docs/ 원본을 pages/docs/reference/ 로, 브랜드 로고·파비콘을 assets/images/ 로 복사한다(빌드 산출물).

repo root 에서 실행한다고 가정한다. reference/ 는 매 실행마다 지우고 다시 만든다.
복사하면서, 복사 트리 밖을 가리키는 상대 링크(루트 README 등)는 GitHub 소스 URL 로
재작성한다 — 포털에서 404 가 되던 링크를 유효하게 만들기 위함이다.
원본 docs/ 는 절대 수정하지 않는다(읽기만 하고 reference/ 로만 쓴다).
브랜드 로고·파비콘의 캐노니컬은 src/libs/ui-kit/src/assets (콘솔과 공용, ALPHA-950·952) —
포털은 빌드 시 여기서 복사받고 사본은 커밋하지 않는다(.gitignore).
"""
from __future__ import annotations

import os
import posixpath
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path.cwd()
DOCS = REPO_ROOT / "docs"
REFERENCE = REPO_ROOT / "pages" / "docs" / "reference"
BRAND = REPO_ROOT / "src" / "libs" / "ui-kit" / "src" / "assets"
IMAGES = REPO_ROOT / "pages" / "docs" / "assets" / "images"

ANIMATED_LOGO_OVERLAY = r"""
<style>
  .edge-animation { pointer-events: none; }
  .edge-data-1, .edge-data-2, .edge-data-3,
  .edge-start, .edge-signal, .edge-finish { opacity: 0; }
  .edge-data-1 { animation: edge-data-1 7.2s ease-in-out infinite; }
  .edge-data-2 { animation: edge-data-2 7.2s ease-in-out infinite; }
  .edge-data-3 { animation: edge-data-3 7.2s ease-in-out infinite; }
  .edge-start { animation: edge-start 7.2s ease-in-out infinite; }
  .edge-signal { animation: edge-signal 7.2s ease-in-out infinite; }
  .edge-finish { animation: edge-finish 7.2s ease-in-out infinite; }
  @keyframes edge-data-1 {
    0%, 5%, 18%, 100% { opacity: 0; }
    8%, 14% { opacity: .8; }
  }
  @keyframes edge-data-2 {
    0%, 10%, 23%, 100% { opacity: 0; }
    13%, 19% { opacity: .8; }
  }
  @keyframes edge-data-3 {
    0%, 15%, 29%, 100% { opacity: 0; }
    18%, 25% { opacity: .85; }
  }
  @keyframes edge-start {
    0%, 23%, 37%, 100% { opacity: 0; }
    28% { opacity: .9; }
    33% { opacity: .35; }
  }
  @keyframes edge-signal {
    0%, 29%, 67%, 100% { opacity: 0; }
    32%, 63% { opacity: .95; }
  }
  @keyframes edge-finish {
    0%, 65%, 80%, 100% { opacity: 0; }
    70% { opacity: .9; }
    76% { opacity: .25; }
  }
  @media (prefers-reduced-motion: reduce) {
    .edge-animation { display: none; }
  }
</style>
<g class="edge-animation" fill="#8FB9FF" aria-hidden="true">
  <g class="edge-data-1">
    <path d="M0 64H15V79H0Z"/>
    <rect x="38" y="26" width="15" height="15"/>
    <rect x="21" y="140" width="15" height="15"/>
  </g>
  <g class="edge-data-2">
    <rect x="38" y="102" width="15" height="15"/>
    <rect x="115" y="26" width="15" height="15"/>
    <rect x="53" y="178" width="15" height="15"/>
  </g>
  <g class="edge-data-3">
    <rect x="78" y="64" width="45" height="15"/>
    <rect x="60" y="140" width="70" height="15"/>
    <rect x="120" y="178" width="20" height="15"/>
    <path d="M115 102H155V117H115Z"/>
  </g>
  <circle class="edge-start" cx="180" cy="110" r="19" fill="none" stroke="#BBD3FF" stroke-width="7"/>
  <circle class="edge-signal" cx="0" cy="0" r="7">
    <animateMotion dur="7.2s" repeatCount="indefinite"
      path="M180 110H415V200H660L750 110H1075"
      keyPoints="0;0;1;1" keyTimes="0;.3;.67;1" calcMode="linear"/>
  </circle>
  <circle class="edge-finish" cx="1075" cy="110" r="19" fill="none" stroke="#BBD3FF" stroke-width="7"/>
</g>
"""

# (원본, 대상) — 파일은 파일로, 디렉터리는 디렉터리로 복사한다.
COPIES = [
    (DOCS / "context.md", REFERENCE / "context.md"),
    (DOCS / "scope.md", REFERENCE / "scope.md"),
    (DOCS / "implementation.md", REFERENCE / "implementation.md"),
    (DOCS / "roadmap.md", REFERENCE / "roadmap.md"),
    (DOCS / "writing-rules.md", REFERENCE / "writing-rules.md"),
    (DOCS / "architecture", REFERENCE / "architecture"),
    (DOCS / "contracts", REFERENCE / "contracts"),
    (DOCS / "domain", REFERENCE / "domain"),
    (DOCS / "console-ia", REFERENCE / "console-ia"),
    (DOCS / "adr", REFERENCE / "adr"),
    # 브랜드 로고 — 명명은 배경 기준(black = 어두운 배경용 흰 로고).
    (BRAND / "edge-logo-black.svg", IMAGES / "edge-logo-black.svg"),
    (BRAND / "edge-logo-white.svg", IMAGES / "edge-logo-white.svg"),
    (BRAND / "edge-favicon.svg", IMAGES / "edge-favicon.svg"),
]


def _write_animated_logo(src: Path, dst: Path) -> None:
    """공용 정적 로고에 Pages 전용 모션 레이어를 더한 독립 SVG를 생성한다."""
    logo = src.read_text(encoding="utf-8")
    closing_tag = "</svg>"
    if logo.count(closing_tag) != 1:
        raise ValueError(f"[sync] 로고 SVG 종료 태그가 예상과 다릅니다: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    animated = logo.replace(closing_tag, ANIMATED_LOGO_OVERLAY + closing_tag)
    dst.write_text(animated, encoding="utf-8")

# reference/ 로 복사되는 repo 경로들 — 이 안을 가리키는 링크는 그대로 두어도 해결된다.
_COPIED_FILES = (
    "docs/context.md",
    "docs/scope.md",
    "docs/implementation.md",
    "docs/roadmap.md",
    "docs/writing-rules.md",
)
_COPIED_DIRS = ("docs/adr", "docs/architecture", "docs/contracts", "docs/domain", "docs/console-ia")
_LINK_RE = re.compile(r"\]\(([^)]+)\)")


def ensure_repo_root() -> None:
    # 대상 경로가 root 기준이다. 다른 디렉터리(특히 자체 docs/ 를 가진 하위 폴더)에서
    # 실행하면 엉뚱한 docs/ 를 복사하므로, root 가 아니면 실패한다(generate 와 동일 가드).
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"[sync] git 저장소(repo root)에서 실행하세요: {REPO_ROOT}")
    root = Path(result.stdout.strip()).resolve()
    if root != REPO_ROOT.resolve():
        raise SystemExit(f"[sync] repo root 에서 실행하세요. cwd={REPO_ROOT}, repo root={root}")


def detect_repo() -> str:
    # 링크 재작성용 owner/repo. CI 는 GITHUB_REPOSITORY, 로컬은 origin 리모트에서 얻는다.
    env = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if env:
        return env
    result = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    if result.returncode != 0:
        return ""
    # https://github.com/owner/repo(.git) 또는 git@github.com:owner/repo(.git)
    match = re.search(r"[:/]([^/:]+/[^/]+?)(?:\.git)?$", result.stdout.strip())
    return match.group(1) if match else ""


def _is_internal(resolved: str) -> bool:
    # reference/ 안에서 정상 해결되는(=복사되는) 대상인지.
    return resolved in _COPIED_FILES or any(
        resolved == d or resolved.startswith(d + "/") for d in _COPIED_DIRS
    )


def _rewrite_links(text: str, repo_relpath: str, repo: str, ref: str) -> str:
    # 복사 트리를 벗어나는 상대 링크만 GitHub 소스 URL 로 바꾼다. repo 를 못 찾으면 그대로 둔다.
    if not repo:
        return text
    src_dir = posixpath.dirname(repo_relpath)

    def repl(match: "re.Match[str]") -> str:
        target = match.group(1)
        if target.startswith(("http://", "https://", "#", "/", "mailto:")):
            return match.group(0)
        path_part, sep, frag = target.partition("#")
        if not path_part:
            return match.group(0)
        resolved = posixpath.normpath(posixpath.join(src_dir, path_part))
        if _is_internal(resolved):
            return match.group(0)
        return f"](https://github.com/{repo}/blob/{ref}/{resolved}{sep}{frag})"

    return _LINK_RE.sub(repl, text)


def _copy_file(src: Path, dst: Path, repo: str, ref: str) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.suffix == ".md":
        repo_relpath = src.relative_to(REPO_ROOT).as_posix()
        text = _rewrite_links(src.read_text(encoding="utf-8"), repo_relpath, repo, ref)
        dst.write_text(text, encoding="utf-8")
    else:
        shutil.copy2(src, dst)


def _copy_tree(src_dir: Path, dst_dir: Path, repo: str, ref: str) -> None:
    for item in sorted(src_dir.rglob("*")):
        target = dst_dir / item.relative_to(src_dir)
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            _copy_file(item, target, repo, ref)


def main() -> int:
    ensure_repo_root()
    if not DOCS.is_dir():
        print(f"[sync] docs/ 를 찾을 수 없습니다: {DOCS}", file=sys.stderr)
        return 1

    repo = detect_repo()
    ref = os.environ.get("GITHUB_REF_NAME", "main").strip() or "main"
    if not repo:
        print("[sync] repo 를 못 찾아 외부 링크는 그대로 둡니다(로컬 빌드).", file=sys.stderr)

    if REFERENCE.exists():
        shutil.rmtree(REFERENCE)
    REFERENCE.mkdir(parents=True)

    missing = []
    for src, dst in COPIES:
        if not src.exists():
            missing.append(src)
            continue
        if src.is_dir():
            dst.mkdir(parents=True, exist_ok=True)
            _copy_tree(src, dst, repo, ref)
        else:
            _copy_file(src, dst, repo, ref)
        print(f"[sync] {src.relative_to(REPO_ROOT)} -> {dst.relative_to(REPO_ROOT)}")

    animated_logo = IMAGES / "edge-logo-animated.svg"
    static_logo = BRAND / "edge-logo-black.svg"
    if static_logo.exists():
        _write_animated_logo(static_logo, animated_logo)
        print(
            f"[sync] {static_logo.relative_to(REPO_ROOT)} + Pages animation"
            f" -> {animated_logo.relative_to(REPO_ROOT)}"
        )

    if missing:
        for path in missing:
            print(f"[sync] 원본 없음(실패): {path.relative_to(REPO_ROOT)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
