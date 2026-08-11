#!/usr/bin/env python3
"""docs/ 원본을 pages/docs/reference/ 로 복사한다(빌드 산출물).

repo root 에서 실행한다고 가정한다. reference/ 는 매 실행마다 지우고 다시 만든다.
복사하면서, 복사 트리 밖을 가리키는 상대 링크(루트 README 등)는 GitHub 소스 URL 로
재작성한다 — 포털에서 404 가 되던 링크를 유효하게 만들기 위함이다.
원본 docs/ 는 절대 수정하지 않는다(읽기만 하고 reference/ 로만 쓴다).
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
]

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

    if missing:
        for path in missing:
            print(f"[sync] 원본 없음(실패): {path.relative_to(REPO_ROOT)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
