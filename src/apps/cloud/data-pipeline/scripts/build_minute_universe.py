#!/usr/bin/env python3
"""1분 레인 universe.json 을 파일로 뽑는 **로컬 확인용** 래퍼 (ALPHA-735·953).

파생 규칙·산출 형식의 정본은 `data_pipeline.steps.build_minute_universe` 다 — 여기서
재구현하지 않는다. 배포 경로(ECS)는 그 스텝을 직접 돈다:

    python -m data_pipeline.run build-minute-universe

**이 스크립트는 업로드하지 않는다.** 파일만 만든다 — 반영(정본 객체 교체)은 위 스텝의
일이고, 그 스텝은 세션 계획 전 창에서만 도는 자리라 장중 교체 위험이 없다. 손으로
올릴 일이 남았다면(스텝 배선 전 급한 교체 등) 아래 레시피대로 확인 후 하라.

실행(레포 루트 기준):

    cd src/apps/cloud/data-pipeline
    AWS_PROFILE=edge DATA_PIPELINE_STORAGE__BACKEND=s3 \
      DATA_PIPELINE_STORAGE__BUCKET=edge-dev-pipeline-lake \
      uv run python scripts/build_minute_universe.py --out /tmp/universe.json

    # 확인 후 반영(세션 계획 전에)
    aws s3 cp /tmp/universe.json s3://edge-dev-pipeline-lake/config/minute/universe.json

`--out` 없으면 stdout 으로 낸다.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from data_pipeline.config import load_settings  # noqa: E402
from data_pipeline.lake.storage import make_storage  # noqa: E402
from data_pipeline.steps.build_minute_universe import (  # noqa: E402
    build_from_settings,
    payload_of,
    summary_of,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", help="쓸 파일 경로(없으면 stdout)")
    args = parser.parse_args()

    settings = load_settings()
    universe = build_from_settings(settings, make_storage(settings.storage))
    payload = payload_of(universe)
    # 요약은 **두 경로 모두** 낸다(stdout 경로에서 침묵하면 무엇을 만들었는지 안 보인다).
    print(f"{args.out or '(stdout)'}: {summary_of(universe)}", file=sys.stderr)
    if args.out:
        Path(args.out).write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)


if __name__ == "__main__":
    main()
