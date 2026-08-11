#!/usr/bin/env python3
"""1분 레인 universe.json 을 파일로 뽑는 **로컬 확인용** 래퍼 (ALPHA-735·953).

파생 규칙·산출 형식의 정본은 `data_pipeline.steps.build_minute_universe` 다 — 여기서
재구현하지 않는다. 배포 경로(ECS)는 그 스텝을 직접 돈다(`--universe` 는 **필수**다 —
소비자가 읽는 그 URI 를 그대로 준다):

    python -m data_pipeline.run build-minute-universe \
      --universe s3://edge-dev-pipeline-lake/config/minute/universe.json

**이 스크립트는 업로드하지 않는다.** 파일만 만든다 — 반영(정본 객체 교체)은 위 스텝의
일이고, 그 스텝은 거래일 07:30 KST 이후엔 스스로 거부한다(세션 계획 뒤 교체 금지).

그 시각을 넘겨 오늘 안에 꼭 갈아야 하면 아래 레시피로 확인한 뒤 사람이 반영하되,
세 가지를 함께 해야 한다:

1. 기존 객체를 **지우지 말고** `.bak-수동` 으로 옮긴다 — 사람이 채운 시간외 축
   (`extended_hours_ids`)은 holdings 파생이 아니라 직전 정본에서만 온다. 새 파일에
   그 축을 손으로 옮겨 담아라(스텝이 자동으로 하는 일을 여기선 사람이 한다).
2. 원장의 **`universe_version` 과 `universe_hash` 를 둘 다** 고친다 — 소비자는 그
   쌍을 비교한다(`price_consumer`·`repository`). 하나만 고치면 계속 blocked 다.
3. worker·consumer 를 재기동한다.

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
