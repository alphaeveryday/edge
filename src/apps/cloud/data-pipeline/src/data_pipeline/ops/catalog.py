"""Task Catalog — 논리 작업의 안정적 카탈로그 ID + 정적 의존 SSOT (ALPHA-530, 스펙 §3.1).

논리 작업은 CLI 문자열이나 SFN state name 이 아니라 **안정적 카탈로그 ID**로 식별한다
(`PRICE_COLLECTION_KIS`). cli_command·sfn_state_name·ecs_task_definition·depends_on 은 그 ID 의
**변경 가능한 속성**이다 — 리팩터로 state name 이 바뀌어도 카탈로그 ID 는 안 바뀐다.

정적 의존의 SSOT 는 이 코드다 — 각 expected_task 행에 required_upstream 을 반복 저장하지
않는다(스펙 §3.1). 대신 pipeline_run 에 catalog_version(배포 SHA)+catalog_content_hash 를 남겨
재현한다.

**MVP 등록 범위**(스펙 §8): 가격 수직 슬라이스 3작업만. 나머지 SFN state 는 카탈로그에 없어
expected_task 가 안 생기고, Reconciler 는 등록 작업만 대조한다. 확장은 아래 CATALOG 에 행 추가.
종목 반복은 개별 작업이 아니라 manifest/completeness 로 관리하고(스펙 §3), 개별 품질 규칙은
quality_check_result 소관이라 카탈로그에 넣지 않는다.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class CatalogEntry:
    """논리 작업 하나. task_key 는 불변 정체성, 나머지는 변경 가능 속성."""

    task_key: str
    stage: str                  # raw · normalize · feature
    dataset: str
    required: bool
    cli_command: tuple[str, ...]  # run.py 에 넘기는 인자(벤더 무관 부분)
    sfn_state_name: str           # ASL state 이름(Reconciler 의 state↔task 매핑)
    ecs_task_definition: str      # task-def 키(kis·bigkinds·rds …)
    depends_on: tuple[str, ...] = ()   # 선행 task_key(BLOCKED/eligible 계산의 정적 SSOT)
    # deadline = expected_at + 이 오프셋. ⚠️ 스테이지별 SLA 가 코드에 없어(조사 결과) 잠정값이다
    # (스펙 §19: 확인 불가 요구사항은 가정으로 명시). 실측 후 조정 대상.
    deadline_offset_seconds: int = 3600


# 가격 수직 슬라이스 3작업. sfn_state_name 은 statemachine.tf 의 실제 state 와 일치해야 한다.
_ENTRIES: tuple[CatalogEntry, ...] = (
    CatalogEntry(
        task_key="PRICE_COLLECTION_KIS",
        stage="raw",
        dataset="price_daily",
        required=True,
        cli_command=("ingest-price-raw", "--source", "kis"),
        sfn_state_name="CollectKisPrice",
        ecs_task_definition="kis",
        depends_on=(),
        deadline_offset_seconds=3600,
    ),
    CatalogEntry(
        task_key="NORMALIZE_PRICE",
        stage="normalize",
        dataset="price_daily",
        required=True,
        cli_command=("normalize-price",),
        sfn_state_name="NormalizePrice",
        ecs_task_definition="bigkinds",
        # raw 부분실패는 downstream 을 막지 않는다(ADR-0030) — 정제는 빈 입력도 정상 처리.
        # 그래도 "가격 정제는 가격 수집 뒤"라는 데이터 계보상의 선행이라 의존으로 둔다
        # (eligible 계산용 — 막는 게 아니라 언제 실행 가능해졌는지 판단).
        depends_on=("PRICE_COLLECTION_KIS",),
        deadline_offset_seconds=5400,
    ),
    CatalogEntry(
        task_key="LOAD_PRICE_DAILY",
        stage="feature",
        dataset="price_daily",
        required=True,
        cli_command=("load-price-daily",),
        sfn_state_name="LoadPriceDaily",
        ecs_task_definition="rds",
        # 정제→(LoadInstruments 직렬)→feature 게이트 뒤에야 canonical 가격을 읽을 수 있다.
        # MASTER_LOAD 는 아직 카탈로그 미등록이라 의존에서 뺀다(등록 시 추가). NORMALIZE_PRICE
        # 가 canonical 을 쓴 뒤라야 이 작업이 읽을 대상이 있다는 것이 핵심 선행이다.
        depends_on=("NORMALIZE_PRICE",),
        deadline_offset_seconds=7200,
    ),
)

CATALOG: dict[str, CatalogEntry] = {e.task_key: e for e in _ENTRIES}

PIPELINE_TYPE = "etf-daily"


def entries() -> tuple[CatalogEntry, ...]:
    """등록된 모든 카탈로그 엔트리(정의 순서)."""
    return _ENTRIES


def get(task_key: str) -> CatalogEntry | None:
    return CATALOG.get(task_key)


def by_sfn_state(state_name: str) -> CatalogEntry | None:
    """SFN state 이름 → 카탈로그 엔트리(Reconciler 의 history 매핑). 없으면 None(미등록 state)."""
    for entry in _ENTRIES:
        if entry.sfn_state_name == state_name:
            return entry
    return None


def content_hash() -> str:
    """카탈로그 내용의 **결정적** 해시. 배포 간 카탈로그가 바뀌었는지 재현·감사한다.

    정렬된 정규형 JSON 을 해싱한다 — dict 순서·공백에 흔들리지 않게. task_key 와 그 정적
    속성(의존·state·taskdef·필수·deadline)만 넣는다(런타임 값 제외).
    """
    canonical = [
        {
            "task_key": e.task_key,
            "stage": e.stage,
            "dataset": e.dataset,
            "required": e.required,
            "cli_command": list(e.cli_command),
            "sfn_state_name": e.sfn_state_name,
            "ecs_task_definition": e.ecs_task_definition,
            "depends_on": sorted(e.depends_on),
            "deadline_offset_seconds": e.deadline_offset_seconds,
        }
        for e in sorted(_ENTRIES, key=lambda x: x.task_key)
    ]
    blob = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def version() -> str:
    """배포 Git SHA. 이미지 빌드가 env 로 주입한다(없으면 'unknown' — 로컬·미주입)."""
    return os.environ.get("OPS_CATALOG_VERSION") or os.environ.get("GIT_SHA") or "unknown"
