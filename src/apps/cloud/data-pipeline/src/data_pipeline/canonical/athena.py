"""Athena 실행기 — **실패를 삼키지 않는다.**

boto3 의 start_query_execution 은 비동기라, 폴링을 빼먹으면 실패한 질의가 성공처럼 보인다.
그래서 이 모듈은 항상 종료까지 기다리고, SUCCEEDED 가 아니면 예외를 던진다. DDL·MERGE 를
돌리는 자리에서 조용한 실패는 "테이블이 없는데 있다고 믿는" 상태를 만든다.

워크그룹은 기존 `market_data`(engine v3)를 재사용한다. 결과 위치가 워크그룹에 강제되어
있으므로(EnforceWorkGroupConfiguration=true) 여기서 OutputLocation 을 넘기지 않는다 -
넘기면 거부된다.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

WORKGROUP = os.environ.get("ATHENA_WORKGROUP", "market_data")
REGION = os.environ.get("AWS_REGION", "ap-northeast-2")
_TERMINAL = ("SUCCEEDED", "FAILED", "CANCELLED")


class AthenaError(RuntimeError):
    """질의가 SUCCEEDED 로 끝나지 않았다. 사유와 SQL 앞머리를 함께 담는다."""


@dataclass
class Athena:
    """질의 실행기. `client` 를 주입하면 망을 타지 않는다(테스트).

    `scanned` 는 누적 스캔 바이트다 - Athena 과금 단위이므로 로그에 남긴다. 안 남기면
    파티션이 잘못 잡혀 전량 스캔하는 질의를 청구서로 알게 된다.
    """

    workgroup: str = WORKGROUP
    region: str = REGION
    profile: str = ""
    client: object | None = None
    poll: float = 1.0
    scanned: int = 0
    log: list[str] = field(default_factory=list)

    def _client(self):
        if self.client is None:
            import boto3

            session = boto3.Session(
                profile_name=self.profile or None, region_name=self.region)
            self.client = session.client("athena")
        return self.client

    def run(self, sql: str, *, database: str = "") -> list[tuple[str, ...]]:
        c = self._client()
        kwargs: dict = {"QueryString": sql, "WorkGroup": self.workgroup}
        if database:
            kwargs["QueryExecutionContext"] = {"Database": database}
        qid = c.start_query_execution(**kwargs)["QueryExecutionId"]
        self.log.append(qid)
        while True:
            ex = c.get_query_execution(QueryExecutionId=qid)["QueryExecution"]
            state = ex["Status"]["State"]
            if state in _TERMINAL:
                break
            time.sleep(self.poll)
        self.scanned += ex.get("Statistics", {}).get("DataScannedInBytes", 0) or 0
        if state != "SUCCEEDED":
            raise AthenaError(
                f"{state}: {ex['Status'].get('StateChangeReason', '')} "
                f"| sql={' '.join(sql.split())[:200]}")
        logger.info("athena %s %s scanned=%s", state, qid, self.scanned)
        res = c.get_query_results(QueryExecutionId=qid, MaxResults=1000)
        rows = res["ResultSet"]["Rows"]
        return [tuple(cell.get("VarCharValue", "") for cell in r.get("Data", []))
                for r in rows]

    def scalar(self, sql: str, *, database: str = "") -> str:
        """머리행을 버리고 첫 값. 개수 확인처럼 한 값만 필요한 자리."""
        rows = self.run(sql, database=database)
        return rows[1][0] if len(rows) > 1 and rows[1] else ""
