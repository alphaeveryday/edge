"""명령줄: 사건 검사와 공개 데이터 생성.

    python -m edge_truth_agent validate <사건 폴더>
    python -m edge_truth_agent build-site --cases <사건 폴더> --out <출력 폴더>
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import sys

from . import site
from .cases import CaseError, load_dir


def main(argv: list[str] | None = None) -> int:
    """명령을 실행하고 종료 코드를 돌려준다."""
    parser = argparse.ArgumentParser(prog="edge_truth_agent")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("validate", help="사건 파일 계약 검사")
    check.add_argument("cases", type=Path)
    build = sub.add_parser("build-site", help="사이트 공개 데이터 생성")
    build.add_argument("--cases", type=Path, required=True)
    build.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    try:
        cases = load_dir(args.cases)
    except CaseError as error:
        print(error, file=sys.stderr)
        return 1
    if args.command == "validate":
        print(f"사건 {len(cases)}건 통과")
        return 0
    site.write(site.build(cases, datetime.now(timezone.utc)), args.out)
    print(f"사건 {len(cases)}건 → {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
