"""데이터 파이프라인 운영 원장 (ALPHA-530).

SFN/ECS 실행을 사후 복구 가능하게 관측하는 Postgres projection. 실행을 제어하지 않는다
(관측만 — ADR-0030). 구성:

- `states`   : 상태 4축 어휘(plan_status·task_outcome·execution_status·data_status) + 이슈/사유 코드
- `catalog`  : Task Catalog(안정적 카탈로그 ID·의존·content hash). 정적 의존의 SSOT
- `ledger`   : 5테이블 repository(lazy 커넥션, 멱등 insert, bounded backoff)
- `planner`  : 실행 전 pipeline_run+expected_task 원자 생성 → SFN StartExecution(멱등)
- `wrapper`  : 3작업 instrumentation(attempt 시작/종료·data_status 관측)
- `reconciler`: 예정↔실제(SFN/ECS 증거) 대조 — MISSED/BLOCKED/STALLED/LEDGER_GAP 등

`db.py`(레이크가 S3 SSOT 이듯 DB 접속 SSOT)를 재사용하되, 원장 커넥션은 **lazy** 다 — 원장
설정이 없는 기존 수집·정제 태스크가 이 모듈 import 만으로 죽지 않아야 한다(스펙 §6).
"""

from __future__ import annotations

from . import catalog, states

__all__ = ["catalog", "states"]
