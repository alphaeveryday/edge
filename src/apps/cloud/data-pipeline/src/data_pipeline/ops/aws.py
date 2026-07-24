"""boto3 클라이언트 lazy 팩토리 (ALPHA-530).

data-pipeline 에 SFN/ECS 클라이언트 전례가 없어(조사 결과) 여기서 도입한다. 레이크 s3 백엔드와
같은 관례로 **지연 import** 한다 — 원장을 안 쓰는 태스크·단위테스트가 boto3 SFN/ECS 없이 돌게.
Planner/Reconciler 는 이 팩토리를 기본으로 쓰되, 테스트는 가짜 클라이언트를 주입한다.
"""

from __future__ import annotations


def stepfunctions_client():
    import boto3

    return boto3.client("stepfunctions")


def ecs_client():
    import boto3

    return boto3.client("ecs")
