"""명령줄.

    python -m edge_truth_research run                       # 한 번 실행(장중 매시 :35). 환경변수 설정은 config.py
    python -m edge_truth_research tool <이름>               # pi 확장이 부른다: 표준입력 JSON → 표준출력 글
    python -m edge_truth_research tool-specs --web 1
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import logging
import os
from pathlib import Path
import sys


def _tool(name: str) -> int:
    """도구 하나. 도구 오류는 종료 코드 2, 내부 오류는 3 — 확장이 그 글을 모델에게 오류로 보여준다."""
    from edge_truth_agent.tools import ToolError
    from .tools import run
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        args = json.loads(sys.stdin.buffer.read().decode("utf-8") or "{}")
        print(run(name, args, Path(os.environ.get("TRUTH_SESSION_DIR", "."))))
        return 0
    except ToolError as error:
        print(str(error))
        return 2
    except Exception as error:  # noqa: BLE001 - 모델이 읽을 수 있게 그대로 알린다(조용히 삼키지 않는다)
        print(f"도구 내부 오류({type(error).__name__}): {error}")
        return 3


def main(argv: list[str] | None = None) -> int:
    """명령을 실행하고 종료 코드를 돌려준다."""
    parser = argparse.ArgumentParser(prog="edge_truth_research")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="오늘 오른 종목 조사·게시 한 번")
    tool = sub.add_parser("tool", help="에이전트 도구 하나 실행 (pi 확장용)")
    tool.add_argument("name")
    spec = sub.add_parser("tool-specs", help="도구 목록 JSON (pi 확장용)")
    spec.add_argument("--web", choices=["0", "1"], default="0")
    args = parser.parse_args(argv)

    if args.command == "tool":
        return _tool(args.name)
    if args.command == "tool-specs":
        from .toolspec import specs
        sys.stdout.reconfigure(encoding="utf-8")
        print(json.dumps(specs(web=args.web == "1"), ensure_ascii=False))
        return 0
    from edge_truth_agent.runner import RunBusy
    from .config import Config
    from .research import run_research
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    # TRUTH_NOW(ISO, 시간대 포함): 지난 거래일을 다시 조사·검증할 때만 쓴다. 운영 스케줄은 넣지 않는다
    now = datetime.fromisoformat(os.environ["TRUTH_NOW"]) if os.environ.get("TRUTH_NOW") else None
    try:
        report = run_research(Config.from_env(), now=now)
    except RunBusy as busy:
        logging.info(json.dumps({"event": "run_skipped", "reason": str(busy)}, ensure_ascii=False))
        return 0
    # 반영되지 못한 종목이 있으면 실패 코드로 끝내 ECS 태스크 상태에 남긴다(알림은 보내지 않는다). 게시된 것은 그대로 둔다.
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
