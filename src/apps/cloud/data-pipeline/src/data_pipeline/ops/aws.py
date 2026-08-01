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


def ssm_client():
    """KIS 토큰 공유 캐시 전용(ALPHA-573). **짧은 타임아웃·재시도 예산을 명시한다.**

    이 캐시의 불변식은 "실패해도 최악이 캐시 없던 시절"인데, boto3 기본값(연결·읽기 각 60초,
    재시도 5회)이면 SSM 이 블랙홀일 때 토큰 발급 전에 수 분을 동기 블록해 그 불변식이
    뒤집힌다(edge-review 지적). 캐시 조회는 없으면 그만인 부가 경로라 빨리 포기해야 한다.
    """
    import boto3
    from botocore.config import Config

    return boto3.client(
        "ssm",
        region_name=_region(),
        config=Config(connect_timeout=3, read_timeout=3, retries={"max_attempts": 2}),
    )


def sqs_client():
    """Outbox Relay 발행용(ALPHA-670). region 명시·지연 import 는 SFN/ECS 와 같은 관례.

    **SDK 자체 재시도를 끈다**(`max_attempts=1`). 재시도 권위는 PostgreSQL 뿐이라는 게
    확정 계약인데(v0.7 12.4), boto3 기본 정책이 자기 backoff 로 여러 번 재호출하면 그
    동안 outbox 의 attempt_count·next_attempt_at 은 그대로라 DB 가 아는 시도 횟수와
    실제가 갈린다. 게다가 그 사이 claim lease 가 만료돼 다른 Relay 가 같은 event 를
    집어 중복 발행이 늘어난다. 한 번 실패하면 DB 에 기록하고 다음 tick 이 정한다.
    """
    import boto3
    from botocore.config import Config

    return boto3.client(
        "sqs",
        region_name=_region(),
        # `max_attempts` 는 **초기 호출을 뺀 재시도 횟수**라 1 이면 총 2회 시도가 된다 —
        # 응답만 유실된 경우 SDK 가 같은 batch 를 다시 보내 outbox 가 모르는 중복 발행이
        # 생긴다. 총 시도 수를 1 로 못 박는다.
        config=Config(
            connect_timeout=5, read_timeout=10,
            retries={"mode": "standard", "total_max_attempts": 1},
        ),
    )
