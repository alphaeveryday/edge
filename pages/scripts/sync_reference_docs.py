#!/usr/bin/env python3
"""docs/ 원본을 pages/docs/reference/ 로 복사한다(빌드 산출물).

repo root 에서 실행한다고 가정한다. reference/ 는 매 실행마다 지우고 다시 만든다.
원본 docs/ 는 절대 수정하지 않는다 — 단방향 복사만 한다.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

REPO_ROOT = Path.cwd()
DOCS = REPO_ROOT / "docs"
REFERENCE = REPO_ROOT / "pages" / "docs" / "reference"

# (원본, 대상) — 파일은 파일로, 디렉터리는 디렉터리로 복사한다.
COPIES = [
    (DOCS / "architecture.md", REFERENCE / "architecture.md"),
    (DOCS / "schema.md", REFERENCE / "schema.md"),
    (DOCS / "adr", REFERENCE / "adr"),
]


def main() -> int:
    if not DOCS.is_dir():
        print(f"[sync] docs/ 를 찾을 수 없습니다: {DOCS}", file=sys.stderr)
        return 1

    if REFERENCE.exists():
        shutil.rmtree(REFERENCE)
    REFERENCE.mkdir(parents=True)

    missing = []
    for src, dst in COPIES:
        if not src.exists():
            missing.append(src)
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
        print(f"[sync] {src.relative_to(REPO_ROOT)} -> {dst.relative_to(REPO_ROOT)}")

    if missing:
        for path in missing:
            print(f"[sync] 원본 없음(실패): {path.relative_to(REPO_ROOT)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
