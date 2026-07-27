"""Task Catalog — 논리 작업의 안정적 카탈로그 ID + 정적 의존 SSOT (ALPHA-530, 스펙 §3.1).

논리 작업은 CLI 문자열이나 SFN state name 이 아니라 **안정적 카탈로그 ID**로 식별한다
(`PRICE_COLLECTION_KIS`). cli_command·sfn_state_name·ecs_task_definition·depends_on 은 그 ID 의
**변경 가능한 속성**이다 — 리팩터로 state name 이 바뀌어도 카탈로그 ID 는 안 바뀐다.

정적 의존의 SSOT 는 이 코드다 — 각 expected_task 행에 required_upstream 을 반복 저장하지
않는다(스펙 §3.1). 대신 pipeline_run 에 catalog_version(배포 SHA)+catalog_content_hash 를 남겨
재현한다.

**등록 범위: ECS Task state 31개 중 21개**(ALPHA-181 확대 → ALPHA-578 수집 2 → ALPHA-553 PR2
뉴스 레인 이관으로 27→21). 미등록 state 는 카탈로그에 없어 expected_task 가 안 생기고,
Reconciler 도 대조하지 않는다. 종목 반복은 개별 작업이 아니라 manifest/completeness 로
관리하고(스펙 §3), 개별 품질 규칙은 quality_check_result 소관이라 카탈로그에 넣지 않는다.

**제외 10개와 해제 조건** — 숫자를 조용히 줄이지 않기 위해 여기 적어 둔다(Rule 12):

| 제외 | state | 왜 |
|---|---|---|
| `fmp` task-def | CollectFmpNews·FmpPrice·FmpFinancial·FmpEtf | **FMP 공용키 bandwidth 한도 소진**으로 US 수집을 SFN 토글로 껐다(`us_fmp_enabled=false`, ALPHA-558 — 1분봉 백필이 쿼터를 태워 daily 수집까지 막았다). 안 도는 스텝을 등록하면 매 런 MISSED 가 쌓인다 → **한도 회복 후 토글을 켤 때 함께 등록**한다. DB env 부재는 아래 `instrumented=False` 로 해소되므로 더는 장애물이 아니다 |
| 뉴스 레인 4 | CollectBigKindsNews·NormalizeNews·TagNews·LoadDocuments (+시장 SFN 에서 제거된 직렬 LoadAssertions·AssembleEvents) | **뉴스 SFN 이관**(ALPHA-553 PR2, `edge-dev-data-pipeline-news` 하루 3슬롯). 그 레인은 Planner 를 안 거치므로 등록하면 매 일일런 MISSED 다 → **뉴스 레인 원장 편입**(자체 pipeline_type·3슬롯 기대) 후속 티켓에서 되살린다 |
| `dart` 재무 | CollectDartFinancial | **하류 소비자가 0** 이다 — `financial_statements` 를 읽는 정제·적재·분석 코드가 없다(수집 자신과 레이크 경로 빌더뿐). 매일 돌지만 아무도 안 쓰는 데이터라, 등록하면 대응할 이유 없는 실패 경보가 화면에 뜬다. 소비자가 생기거나 수집을 내리기로 하면 그때 정리한다 |
| `analysis` | AnalyzeOne | 다른 이미지·다른 진입점이라 `run.py` 를 안 타고 `run_id` 도 안 받는다. 게다가 Map 팬아웃 31종이 한 state 이름으로 뭉쳐 Reconciler 가 마지막 occurrence 로 판정하므로(30 실패 + 1 성공 = FULFILLED) **등록하는 순간 거짓 초록**이 된다 |

**`instrumented=False` 2개**(CollectKrxEtf·CollectDartDisclosure) —
task-def 에 DB env 가 없어(`tasks.tf`: host 만 주면 password 없는 DbConfig 로 `load_settings()` 가
통째로 실패, 부분 주입 불가) 컨테이너가 자기 attempt 를 못 쓴다. 그렇다고 빼면 안 된다:
Reconciler 의 SFN·ECS 증거 backfill 이 **유일하지만 정확한** 기록 경로이기 때문이다(그 경우
attempt 결측은 버그가 아니므로 LEDGER_GAP 을 열지 않는다). 즉 등록에 신뢰경계 변경 — 벤더 API
컨테이너에 RDS 마스터 비밀번호를 주는 것 — 은 필요 없다.

**대신 무엇을 얻고 무엇을 못 얻는지 분명히 하자**(Rule 12). 얻는 것: 실행 여부와 성패
(`task_outcome`·`execution_status` — ECS 종료가 증거다). 못 얻는 것: `records_out`·
`failed_records`·`data_status` — 로그 관측(`_observe_from_log`)이 컨테이너 안에서 도는데 그
컨테이너가 원장에 못 쓰기 때문이다. 즉 **exit 0 인데 부분 유실**(공시 미매핑 등)은 이 경로로
안 보인다 — 그건 여전히 S3 `collection_log` 를 봐야 한다. 판정 시점도 Reconciler 실행 때다.

⚠️ 수집 커버리지는 시장 레인 11개 중 6개다(FMP 4개는 토글 off, DART 재무는 소비자 0, BigKinds
뉴스는 뉴스 레인 이관). 조용한 누락이 실제로 나는 곳이 수집이므로(ALPHA-387·578) 커버리지의
**모양**이 숫자보다 중요하다.
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
    # RUNNING attempt 를 STALLED 로 의심하기까지의 시간. 작업마다 정상 실행 시간이 다르다 —
    # LLM 스텝(tag-news·assemble-events)은 기본 1시간을 정상적으로 넘고, SFN 자체 타임아웃은
    # 6시간이다. 전역 상수로 두면 그 스텝들이 **정상 실행 중에 STALLED 로 찍히고**, STALLED 는
    # resolve 경로가 없어 영구 OPEN 으로 쌓인다(ALPHA-181).
    stalled_after_seconds: int = 3600
    # ── 관측(_observe_from_log)이 로그를 찾고 해석하는 데 필요한 정적 속성 (ALPHA-181) ──
    # 수집 스텝만 벤더가 있다. 빈 문자열 = 정제·적재 스텝(collection_log 가 아니라 quality_log).
    # 이 한 필드가 "로그 2종·경로 빌더 2개" 분기를 대신한다.
    source_vendor: str = ""
    # 로그 파티션의 dataset 이 도메인 dataset 과 다를 때만 채운다(적재 스텝의 `_load` 접미사 등).
    # 빈 문자열 = dataset 과 동일.
    log_dataset: str = ""
    # 0건이 이 데이터셋의 계약상 정상인가. 기본 False = 0건이면 정직하게 UNKNOWN — 근거 없이
    # VALID_EMPTY 로 올리면 "할 일이 없었다"와 "증거가 없다"가 섞인다(derive_data_status 계약).
    empty_allowed: bool = False
    # KR 거래일 달력을 따르는 작업인가(비거래일이면 Planner 가 SKIPPED). `is_trading_day` 는
    # **KR 전용**이라 미국 시장 작업(FMP)에 걸면 KR 공휴일에 실제로 돈 수집이 "휴장이라 안 했다"로
    # 기록된다. dataset 문자열로 가르지 않는 이유: `price_daily` 는 fmp·kis 공통이다(ALPHA-181).
    kr_trading_calendar: bool = False
    # 이 작업의 컨테이너가 **스스로 원장에 쓸 수 있는가**. False = task-def 에 DB env 가 없어
    # wrapper 가 attempt 를 못 만든다 → 그 작업의 attempt 결측은 버그가 아니라 정상이고,
    # Reconciler 가 SFN 증거로 backfill 하는 것이 유일·정확한 경로다(LEDGER_GAP 을 열지 않는다).
    # 등록은 하는 이유: **게이트 멤버**라서 빠지면 의존 판정이 거짓이 된다(ALPHA-181).
    instrumented: bool = True

    def log_partition_dataset(self) -> str:
        """로그 파티션에 쓰이는 dataset(미지정이면 도메인 dataset)."""
        return self.log_dataset or self.dataset


# 등록 21작업. sfn_state_name·cli_command·ecs_task_definition 은 statemachine.tf 의 실제
# state·command_expr·taskdef_key 와 일치해야 한다(test_ops_catalog 이 삼중항으로 대조한다).
# 앞 3개는 ALPHA-530 MVP 슬라이스라 필드를 풀어 썼고, 나머지는 압축 표기다.
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
        source_vendor="kis",
        kr_trading_calendar=True,
        # 거래일에 가격 0건은 정상이 아니다 — VALID_EMPTY 를 막아 UNKNOWN 으로 남긴다.
        empty_allowed=False,
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
        kr_trading_calendar=True,
    ),
    CatalogEntry(
        task_key="LOAD_PRICE_DAILY",
        stage="feature",
        dataset="price_daily",
        required=True,
        cli_command=("load-price-daily",),
        sfn_state_name="LoadPriceDaily",
        ecs_task_definition="rds",
        # 정제→(LoadInstruments·EnrichCorpCode 직렬)→feature 게이트 뒤에야 canonical 가격을
        # 읽을 수 있다. 원래 주석이 "MASTER_LOAD 는 미등록이라 뺀다(등록 시 추가)"라고 예고했고
        # ALPHA-181 에서 등록됐으므로 **형제 feature 6개와 같은 게이트 축**으로 맞춘다 — 안 그러면
        # 같은 게이트가 닫힌 런에서 이 작업만 MISSED, 나머지는 BLOCKED 로 갈려 원장이 같은 원인을
        # 다르게 기록한다(Rule 7 — 섞지 말고 하나를 고른다).
        depends_on=("ENRICH_CORP_CODE",),
        deadline_offset_seconds=7200,
        # 적재 스텝의 quality_log 는 `_load` 접미사 파티션에 쓴다(정제 로그와 안 섞이게).
        log_dataset="price_daily_load",
        kr_trading_calendar=True,
        empty_allowed=False,
    ),
    # ── KIS 수집 4 ────────────────────────────────────────────────────────────────
    # ⚠️ `kr_trading_calendar` 는 **기존 3작업 말고는 전부 False** 다(ALPHA-181). True 면 KR
    # 휴장일에 Planner 가 SKIPPED 로 계획하는데, SFN 은 휴장일에도 돌아 컨테이너가 실제로
    # 실행된다 — 그 실행 결과·실패가 원장에서 통째로 사라진다(SKIPPED 면 wrapper 가 attempt 를
    # 안 만든다). 게다가 이 작업들은 휴장일에 할 일이 정말 없지도 않다: NAV·투자자는 소급창으로
    # 직전 거래일을 회수하고, ETF 프로필은 날짜창 없는 전량 스냅샷이며, 적재 로더들은 창 미지정
    # 이면 canonical 풀스캔으로 백로그를 줍는다. 0건이면 DUE 인 채 UNKNOWN 이 정직하다.
    # (기존 3작업은 ALPHA-530 스펙 §3.3 의 결정이라 동작 보존을 위해 그대로 둔다.)
    CatalogEntry(
        task_key="NAV_COLLECTION_KIS", stage="raw", dataset="etf_nav", required=True,
        cli_command=("ingest-raw-nav",), sfn_state_name="CollectKisNav",
        ecs_task_definition="kis", source_vendor="kis",
    ),
    CatalogEntry(
        task_key="ETF_PROFILE_COLLECTION_KIS", stage="raw", dataset="etf_profile", required=True,
        cli_command=("ingest-raw-etf-profile",), sfn_state_name="CollectKisEtfProfile",
        ecs_task_definition="kis", source_vendor="kis",
    ),
    CatalogEntry(
        task_key="INVESTOR_COLLECTION_KIS", stage="raw", dataset="investor_flow_daily",
        required=True, cli_command=("ingest-raw-investor",), sfn_state_name="CollectKisInvestor",
        ecs_task_definition="kis", source_vendor="kis",
    ),
    # ── 뉴스 레인 6작업(NEWS_COLLECTION_BIGKINDS·NORMALIZE_NEWS·TAG_NEWS·LOAD_DOCUMENTS·
    # LOAD_ASSERTIONS·ASSEMBLE_EVENTS)은 카탈로그에 없다(ALPHA-553 PR2) ──────────────
    # 뉴스 SFN(edge-dev-data-pipeline-news, 하루 3슬롯)으로 이관됐는데 그 레인은 Planner 를
    # 안 거치므로(PR1 주석 — 운영 원장 미편입) 여기 남겨두면 매 일일런이 6작업 MISSED 를 연다.
    # 커버리지 27→21 은 조용한 축소가 아니라 이 주석이 그 사실이다(Rule 12) — 뉴스 레인의
    # 원장 편입(자체 pipeline_type·3슬롯 기대)은 후속 티켓에서 되살린다.
    # ── KRX·DART 수집 2 (DB env 없는 task-def — instrumented=False) ────────────────
    # 컨테이너가 원장에 못 쓰므로 attempt 결측이 정상이고, Reconciler 의 SFN·ECS 증거
    # backfill 이 유일·정확한 기록 경로다(LEDGER_GAP 을 열지 않는다). 등록하지 않으면 수집
    # 실패가 원장에 **자리조차 없다**. 2026-07-27 KRX 수집을 손으로 죽였는데(exit 137) 화면에 아무것도 안 뜬
    # 것이 그 실증이다. 등록만으로 그 종료가 FAILED 로 판정된다(_judge_outcome 은 terminal 을
    # 그대로 옮긴다). 비밀번호를 벤더 컨테이너에 주는 신뢰경계 변경은 여전히 필요 없다.
    # ⚠️ `stalled_after_seconds` 를 SFN 타임아웃에 맞춘다(AssembleEvents 와 같은 근거). 기본 1시간은
    # 이 둘의 **정상 재시도**보다 짧다: PoliteClient 는 요청당 4회 시도 + 백오프(1·2·4초)라
    # KRX(타임아웃 45초·ETF 31종)는 최악 31×(45×4+7) ≈ 5,797초, DART 도 대상 corp 수만큼 직렬이다.
    # 정상 실행 중 STALLED 가 붙으면 resolve 경로가 없어 **영구 OPEN** 이다(ALPHA-181). 진짜 행에
    # 걸린 실행은 SFN 타임아웃이 끝내고 그건 별도 CloudWatch 알람이 잡는다.
    CatalogEntry(
        task_key="ETF_HOLDINGS_COLLECTION_KRX", stage="raw", dataset="etf_holdings", required=True,
        cli_command=("ingest-raw-etf", "--source", "krx"), sfn_state_name="CollectKrxEtf",
        ecs_task_definition="krx", source_vendor="krx", instrumented=False,
        stalled_after_seconds=21600,
    ),
    CatalogEntry(
        task_key="DISCLOSURE_COLLECTION_DART", stage="raw", dataset="disclosures", required=True,
        cli_command=("ingest-raw-disclosure",), sfn_state_name="CollectDartDisclosure",
        ecs_task_definition="dart", source_vendor="dart", instrumented=False,
        stalled_after_seconds=21600,
    ),
    # ── 정제 8 (bigkinds task-def 재사용 — 레이크만 읽어 벤더 키 불요) ─────────────
    # 정제의 depends_on 은 **비운다**: raw 부분실패는 뒤를 막지 않고(ADR-0030) 정제는 빈 입력을
    # 정상 성공으로 처리하므로, raw 를 선행으로 걸면 수집 실패 런에서 **실제로 돌아 성공한 정제**가
    # BLOCKED 로 오귀속된다. 반면 정제→feature 는 진짜 게이트라 아래에서 의존으로 그린다.
    CatalogEntry(
        task_key="NORMALIZE_DISCLOSURE", stage="normalize", dataset="supply_contract_fact",
        required=True, cli_command=("normalize-disclosure",), sfn_state_name="NormalizeDisclosure",
        ecs_task_definition="bigkinds", deadline_offset_seconds=5400,
    ),
    CatalogEntry(
        task_key="NORMALIZE_DISCLOSURE_SEGMENT", stage="normalize",
        dataset="business_segment_fact", required=True,
        cli_command=("normalize-disclosure-segment",), sfn_state_name="NormalizeDisclosureSegment",
        ecs_task_definition="bigkinds", deadline_offset_seconds=5400,
    ),
    CatalogEntry(
        task_key="NORMALIZE_ETF", stage="normalize", dataset="etf_holdings", required=True,
        cli_command=("normalize-etf",), sfn_state_name="NormalizeEtf",
        ecs_task_definition="bigkinds", deadline_offset_seconds=5400,
    ),
    CatalogEntry(
        task_key="NORMALIZE_ETF_PROFILE", stage="normalize", dataset="etf_profile", required=True,
        cli_command=("normalize-etf-profile",), sfn_state_name="NormalizeEtfProfile",
        ecs_task_definition="bigkinds", deadline_offset_seconds=5400,
    ),
    CatalogEntry(
        task_key="NORMALIZE_ETF_NAV", stage="normalize", dataset="etf_nav", required=True,
        cli_command=("normalize-etf-nav",), sfn_state_name="NormalizeEtfNav",
        ecs_task_definition="bigkinds", deadline_offset_seconds=5400,
    ),
    CatalogEntry(
        task_key="NORMALIZE_INVESTOR", stage="normalize", dataset="investor_flow_daily",
        required=True, cli_command=("normalize-investor",), sfn_state_name="NormalizeInvestor",
        ecs_task_definition="bigkinds", deadline_offset_seconds=5400,
    ),
    # ── 적재 6 (rds task-def) ─────────────────────────────────────────────────────
    CatalogEntry(
        task_key="LOAD_INSTRUMENTS", stage="feature", dataset="instrument_master", required=True,
        cli_command=("load-instruments",), sfn_state_name="LoadInstruments",
        ecs_task_definition="rds", deadline_offset_seconds=7200,
        # ASL `NormalizeCheckResults` = 정제 **전량 성공** 게이트. 하나라도 죽으면 이 뒤가 통째로
        # 미진입인데, 의존을 비워 두면 그게 MISSED("시작조차 안 됐다")로 찍힌다 — 진실은
        # BLOCKED(게이트가 닫혔다)다. ADR-0030 과 충돌하지 않는다: 그건 raw→정제 얘기고
        # (그래서 정제 엔트리는 의존이 비어 있다) 여긴 정제→feature 게이트다.
        depends_on=(
            "NORMALIZE_PRICE", "NORMALIZE_DISCLOSURE",
            "NORMALIZE_DISCLOSURE_SEGMENT", "NORMALIZE_ETF", "NORMALIZE_ETF_PROFILE",
            "NORMALIZE_ETF_NAV", "NORMALIZE_INVESTOR",
        ),
    ),
    CatalogEntry(
        task_key="LOAD_PRICE_TRIGGERS", stage="feature", dataset="price_movement_trigger",
        required=True, cli_command=("load-price-triggers",), depends_on=("ENRICH_CORP_CODE",), sfn_state_name="LoadPriceTriggers",
        ecs_task_definition="rds", deadline_offset_seconds=7200,
    ),
    CatalogEntry(
        task_key="LOAD_ETF_NAV", stage="feature", dataset="etf_nav_daily", required=True,
        cli_command=("load-etf-nav",), depends_on=("ENRICH_CORP_CODE",), sfn_state_name="LoadEtfNav",
        ecs_task_definition="rds", deadline_offset_seconds=7200,
    ),
    CatalogEntry(
        task_key="LOAD_ETF_HOLDINGS", stage="feature", dataset="etf_holding_snapshot",
        required=True, cli_command=("load-etf-holdings",), depends_on=("ENRICH_CORP_CODE",), sfn_state_name="LoadEtfHoldings",
        ecs_task_definition="rds", deadline_offset_seconds=7200,
    ),
    CatalogEntry(
        task_key="LOAD_ETF_FLOW", stage="feature", dataset="investor_flow_load", required=True,
        cli_command=("load-etf-flow",), depends_on=("ENRICH_CORP_CODE",), sfn_state_name="LoadEtfFlow",
        ecs_task_definition="rds", deadline_offset_seconds=7200,
    ),
    CatalogEntry(
        task_key="LOAD_DISCLOSURE", stage="feature", dataset="disclosure_document", required=True,
        cli_command=("load-disclosure",), depends_on=("ENRICH_CORP_CODE",), sfn_state_name="LoadDisclosure",
        ecs_task_definition="rds", deadline_offset_seconds=7200,
    ),
    # ── corp_code enrichment (rds_dart) ───────────────────────────────────────────
    CatalogEntry(
        task_key="ENRICH_CORP_CODE", stage="feature", dataset="company_profile", required=True,
        cli_command=("enrich-corp-code",), depends_on=("LOAD_INSTRUMENTS",),
        sfn_state_name="EnrichCorpCode",
        ecs_task_definition="rds_dart", deadline_offset_seconds=7200,
    ),
)

CATALOG: dict[str, CatalogEntry] = {e.task_key: e for e in _ENTRIES}

PIPELINE_TYPE = "etf-daily"

# `--source` 미지정 시 run.py 가 쓰는 기본 벤더(`args.source or "fmp"`). 벤더 인자가 없는
# 카탈로그 엔트리는 이 벤더를 뜻한다 — 두 표현(`ingest-raw` 와 `ingest-raw --source fmp`)이
# 같은 작업으로 해소돼야 한다.
_DEFAULT_VENDOR = "fmp"


def entries() -> tuple[CatalogEntry, ...]:
    """등록된 모든 카탈로그 엔트리(정의 순서)."""
    return _ENTRIES


def get(task_key: str) -> CatalogEntry | None:
    return CATALOG.get(task_key)


def by_cli(step: str, source: str | None = None) -> CatalogEntry | None:
    """CLI `(step, --source)` → 엔트리. 없으면 None(미등록 작업).

    해소 규칙: **벤더를 명시한 엔트리가 우선**이고, 없으면 벤더 인자가 없는 엔트리로 떨어진다.
    - `ingest-price-raw --source kis` → KIS 엔트리(명시 일치)
    - `ingest-price-raw`(미지정=fmp) → KIS 엔트리와 안 맞음. FMP 는 미등록이라 None
    - `normalize-price --source kis` → 벤더 축이 **없는** 스텝이라 `--source` 는 무시된다.
      여기서 None 을 돌려주면 수동 회수(`--source` 를 습관적으로 붙이는 경우)의 계측이
      조용히 끊긴다 — 실제 dispatch 는 source 를 안 보고 정상 실행한다.
    벤더를 섞어 해소하면 원장이 남의 벤더 결과를 그 작업으로 기록하므로, 명시 일치가 항상 이긴다.
    """
    candidates = [e for e in _ENTRIES if e.cli_command and e.cli_command[0] == step]
    # 이 스텝이 카탈로그에서 벤더로 갈리는가. 갈리는 스텝에 **모르는 벤더**가 오면 폴백하지
    # 않는다 — 폴백하면 `ingest-raw --source bogus` 가 FMP 뉴스 작업으로 기록된다.
    vendor_split = any("--source" in e.cli_command for e in candidates)
    # 미지정은 기본 벤더다 — ASL 이 `--source fmp` 를 명시하는 잡도 있고(CollectFmpNews) 사람이
    # 생략하고 도는 경로도 있는데, 둘이 같은 작업으로 해소돼야 수동 회수가 계측된다.
    vendor = source or _DEFAULT_VENDOR
    fallback = None
    for entry in candidates:
        if "--source" in entry.cli_command:
            idx = entry.cli_command.index("--source") + 1
            entry_vendor = entry.cli_command[idx] if idx < len(entry.cli_command) else ""
            if entry_vendor == vendor:
                return entry
        elif fallback is None:
            fallback = entry
    if vendor_split and vendor != _DEFAULT_VENDOR:
        return None
    return fallback


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
            "stalled_after_seconds": e.stalled_after_seconds,
            "kr_trading_calendar": e.kr_trading_calendar,
            "instrumented": e.instrumented,
            "source_vendor": e.source_vendor,
            "log_dataset": e.log_dataset,
            "empty_allowed": e.empty_allowed,
        }
        for e in sorted(_ENTRIES, key=lambda x: x.task_key)
    ]
    blob = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def version() -> str:
    """배포 Git SHA. 이미지 빌드가 env 로 주입한다(없으면 'unknown' — 로컬·미주입)."""
    return os.environ.get("OPS_CATALOG_VERSION") or os.environ.get("GIT_SHA") or "unknown"
