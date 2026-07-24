"""boto3 클라이언트 lazy 팩토리 (ALPHA-530).

data-pipeline 에 SFN/ECS 클라이언트 전례가 없어(조사 결과) 여기서 도입한다. 레이크 s3 백엔드와
같은 관례로 **지연 import** 한다 — 원장을 안 쓰는 태스크·단위테스트가 boto3 SFN/ECS 없이 돌게.
Planner/Reconciler 는 이 팩토리를 기본으로 쓰되, 테스트는 가짜 클라이언트를 주입한다.

⚠️ **region 을 명시 전달한다** — task-def 는 비표준 `AWS_REGION_NAME`(local.env)만 주입하는데
botocore 는 그 변수를 region 으로 안 읽는다(표준은 AWS_REGION/AWS_DEFAULT_REGION). SFN/ECS 는
regional 엔드포인트라 region 이 없으면 첫 호출에서 NoRegionError 로 죽는다(s3 는 us-east-1
폴백이 있어 안 죽던 것과 다르다, Codex P1). AWS_REGION_NAME 이 없으면(로컬) None → boto3 표준
해소에 맡긴다.
"""

from __future__ import annotations

import os


def _region() -> str | None:
    return os.environ.get("AWS_REGION_NAME")


def stepfunctions_client():
    import boto3

    return boto3.client("stepfunctions", region_name=_region())


def ecs_client():
    import boto3

    return boto3.client("ecs", region_name=_region())
