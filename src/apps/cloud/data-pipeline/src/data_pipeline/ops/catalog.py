"""Task Catalog — 논리 작업의 안정적 카탈로그 ID + 정적 의존 SSOT (ALPHA-530, 스펙 §3.1).

논리 작업은 CLI 문자열이나 SFN state name 이 아니라 **안정적 카탈로그 ID**로 식별한다
(`PRICE_COLLECTION_KIS`). cli_command·sfn_state_name·ecs_task_definition·depends_on 은 그 ID 의
**변경 가능한 속성**이다 — 리팩터로 state name 이 바뀌어도 카탈로그 ID 는 안 바뀐다.

정적 의존의 SSOT 는 이 코드다 — 각 expected_task 행에 required_upstream 을 반복 저장하지
않는다(스펙 §3.1). 대신 pipeline_run 에 catalog_version(배포 SHA)+catalog_content_hash 를 남겨
재현한다.

**등록 범위: ECS Task state 36개 중 30개**(ALPHA-181 확대 → ALPHA-578 수집 2 → ALPHA-553 PR2
뉴스 레인 이관으로 27→21 → ALPHA-591 뉴스 레인 원장 편입으로 21→27 → **ALPHA-724 공시 레인
컷오버로 소유 레인 이동(총계 27 유지)** → **ALPHA-769 장중 수급 레인 신설로 27→30**). state 수
36 = 시장 SFN 31 + 뉴스 SFN 직렬 2(NewsLoadAssertions·NewsAssembleEvents — 병렬 브랜치 4개는
statemachine.tf 잡 정의를 재사용해 이름이 겹치지 않는다) + 장중 수급 3(ALPHA-769 가 잡 리스트에
새로 더한 것 — 공시·뉴스가 소유만 옮겨 총계를 안 바꾼 것과 다르다). 미등록 state 는 카탈로그에 없어 expected_task 가 안
생기고, Reconciler 도 대조하지 않는다. 종목 반복은 개별 작업이 아니라 manifest/completeness 로
관리하고(스펙 §3), 개별 품질 규칙은 quality_check_result 소관이라 카탈로그에 넣지 않는다.

**장중 상주 실행체(1분 Price Collector·News Scanner)는 여기 편입하지 않는다**(ALPHA-651).
분당 1행도 창 단위 expected_task 도 만들지 않는다 — expected/actual 권위는 이미
minute_ingestion_window(장 시작 시 하루치 materialize — 실행체가 안 떠도 결손이 원장에
드러난다)에 있고, ops 행 복제는 같은 사실을 두 원장에 살게 한다(모니터링 계획 §2-1).
콘솔 편입은 요약 관측(super-admin-api `/api/v1/sources/minute`)으로 한다.

⚠️ 공시 레인 컷오버(ALPHA-724)가 **선택이 아니라 전제인 이유**: 작업 정체성의 정본은
`by_cli(step, source)` 인데 두 레인의 CLI 가 글자 그대로 같다(`ingest-raw-disclosure`·
`normalize-disclosure`…). 같은 스텝을 두 레인이 동시에 소유하면 `by_cli` 가 먼저 온 엔트리를
돌려줘 장중 런의 attempt 가 시장 레인 task_key 로 기록된다 — 장중 런은 영구 MISSED 다.
그래서 "둘 다 등록"이라는 선택지가 애초에 없고, 소유 레인을 옮기는 것이 컷오버의 본체다.

**레인 이동에 중간 미등록 상태를 두지 않는다**(ALPHA-724). 카탈로그(이미지 CD)와 SFN 배선
(terraform-apply)은 순서 보장이 없지만, **어느 순서든 영구 결함이 없다**:
* 카탈로그가 먼저 뜨면 — 시장 런은 공시를 기대하지 않는데 시장 SFN 은 아직 돌린다. 그 실행은
  `find_expected_task` 가 None 이라 wrapper 가 투명 통과하고(`ops/wrapper.py`), Reconciler 는
  `expected_tasks_for(run_id)` 만 순회하므로(`ops/reconciler.py`) 짝 없는 SFN 이력을 그냥
  넘긴다 — attempt 도 이슈도 안 생긴다.
* 배선이 먼저 뜨면 — 시장 런이 그 4작업을 기대하는데 안 돌아 한 런에서 MISSED 4건이 뜬다.
  `resolve_issue` 가 MISSED 를 자동 해소하므로 다음 런에서 정리된다.
잠깐의 MISSED 는 자가 해소되지만 **미등록 유예는 잊히면 조용히 영구화된다**(공시가 원장 밖에서
도는데 화면에 아무 흔적이 없다) — 그래서 관대한 쪽이 아니라 시끄러운 쪽을 골랐다(Rule 12).

**레인(pipeline_type) 축**(ALPHA-591·724·769): 카탈로그는 시장 레인(`etf-daily`, 17작업)·뉴스 레인
(`news`, 6작업)·공시 레인(`disclosure`, 4작업)·장중 수급 레인(`investor-intraday`, 3작업)을 함께 담는다. Planner 는 `entries(pipeline_type)` 로 자기 레인만 계획한다 —
뉴스 SFN 은 하루 3슬롯이라 일일런 기대에 뉴스 작업을 섞으면 매 일일런 MISSED 다(그 반대도
같다). `by_cli`·`by_sfn_state`·`content_hash` 는 전 레인 검색이다: 컨테이너는 자기 레인을
모르고(CLI 가 정체성), state 이름은 레인 간 유일하며, 해시는 카탈로그 전체의 감사값이다.

**제외 6개와 해제 조건** — 숫자를 조용히 줄이지 않기 위해 여기 적어 둔다(Rule 12):

| 제외 | state | 왜 |
|---|---|---|
| `fmp` task-def | CollectFmpNews·FmpPrice·FmpFinancial·FmpEtf | **FMP 공용키 bandwidth 한도 소진**으로 US 수집을 SFN 토글로 껐다(`us_fmp_enabled=false`, ALPHA-558 — 1분봉 백필이 쿼터를 태워 daily 수집까지 막았다). 안 도는 스텝을 등록하면 매 런 MISSED 가 쌓인다 → **한도 회복 후 토글을 켤 때 함께 등록**한다(CollectFmpNews 는 뉴스 레인으로). DB env 는 그때 `tasks.tf` 에 `local.db_env`+password 를 얹으면 된다(ALPHA-596 이 krx·dart 로 한 것과 같은 두 줄) |
| `dart` 재무 | CollectDartFinancial | **하류 소비자가 0** 이다 — `financial_statements` 를 읽는 정제·적재·분석 코드가 없다(수집 자신과 레이크 경로 빌더뿐). 매일 돌지만 아무도 안 쓰는 데이터라, 등록하면 대응할 이유 없는 실패 경보가 화면에 뜬다. 소비자가 생기거나 수집을 내리기로 하면 그때 정리한다 |

**등록 30작업이 전부 `instrumented=True` 다 — 미계측은 0개다**(ALPHA-596 이 krx·dart 를,
ALPHA-610 이 TagNews 를 승격). `instrumented` 필드 자체는 남긴다: FMP 4스텝을 되살릴 때 배선
전에 등록하는 경로가 위 표에 예고돼 있고, 미배선 task-def 의 `False` 는 여전히 정당하다.

**배선이 플래그보다 한 배포 앞선다**(ALPHA-596 #359→#362, ALPHA-610 #379→이 PR). 이미지 CD 와
terraform-apply 가 독립 워크플로라 플래그가 먼저 뜨면 새 카탈로그가 DB env 없는 옛 task
revision 위에서 돌고, Reconciler 가 resolve 불가한 LEDGER_GAP 을 연다. 다음에 같은 승격을 할
때도 이 순서를 지켜라 — `test_ops_catalog._WIRING_AHEAD_OF_FLAG` 가 그 중간 상태를 위한
한 배포짜리 유예이고, 플래그가 올라가는 순간 스스로 실패해 제거를 강제한다.

⚠️ **`instrumented=True` 는 task-def 에 DB env 가 있다는 주장이다.** 없는 task-def 에 True 를
달면 wrapper 가 `load_settings()` 단계에서 db 섹션 없이 뜨고, 그 작업은 **PENDING 행만 남긴 채
조용히 계측 없이 돈다**(화면에서 "대기"로 굳는다). 코드 안에서는 확인할 수 없는 사실이라
`test_instrumented_entries_have_db_env_in_taskdef` 가 `tasks.tf` 를 읽어 대조한다.

**ALPHA-596 이 뒤집은 것.** 이 자리엔 원래 "krx·dart 는 DB env 가 없어 `instrumented=False`,
등록에 신뢰경계 변경(벤더 컨테이너에 RDS 비밀번호)이 전제"라고 적혀 있었다. 실제 인프라를 읽어
보니 그 전제는 **IAM·네트워크 층에서 이미 무너져 있었다**: 실행 역할이 task-def 전체 공유라
`iam.tf` 가 DB 시크릿을 이미 허용하고, 보안그룹도 하나라 krx·dart 컨테이너는 그전에도 RDS 에
닿았다. 결정적으로 `kis` 가 반례다 — 벤더 API 컨테이너인데 DB password 를 받는다. 즉 경계는
신뢰경계 판단의 결과가 아니라 ALPHA-530 MVP 슬라이스가 고른 순서의 잔재였다.

원장 결합이 수집을 위태롭게 하지도 않는다: `Ledger` 커넥션은 lazy 고 쓰기 실패는 예외를 던지지
않는다(스펙 §3.4) — RDS 가 죽어도 수집은 backoff 뒤 그대로 진행한다.

⚠️ 수집 커버리지는 시장 레인 10개 중 5개 + 뉴스 1개(BigKinds) + 공시 1개(DART)다(FMP 4개는
토글 off, DART 재무는 소비자 0). 조용한 누락이 실제로 나는 곳이 수집이므로(ALPHA-387·578) 커버리지의
**모양**이 숫자보다 중요하다.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field

from . import contracts


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
    # 이 작업이 속한 레인(ALPHA-591). Planner 가 `entries(pipeline_type)` 로 자기 레인만
    # 계획한다 — 시장 일일런 기대에 뉴스 작업이 섞이면(또는 그 반대) 매 런 MISSED 다.
    pipeline_type: str = "etf-daily"
    # 데이터 전달 계약은 별도 typed registry가 소유한다(ADR-0043). Catalog는 stable key만 참조.
    contract_key: str | None = None

    def log_partition_dataset(self) -> str:
        """로그 파티션에 쓰이는 dataset(미지정이면 도메인 dataset)."""
        return self.log_dataset or self.dataset


# 등록 30작업(시장 17 + 뉴스 6 + 공시 4 + 장중수급 3). sfn_state_name·cli_command·ecs_task_definition 은
# statemachine.tf·news_pipeline.tf·disclosure_pipeline.tf·investor_intraday_pipeline.tf 의 실제
# state·command_expr·taskdef_key 와 일치해야 한다 (test_ops_catalog 이 삼중항으로 대조한다).
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
    # ── KRX 수집 1 (공시 수집은 아래 공시 레인 절로 이동 — ALPHA-724) ─────────────
    # 등록하지 않으면 수집 실패가 원장에 **자리조차 없다**. 2026-07-27 KRX 수집을 손으로
    # 죽였는데(exit 137) 화면에 아무것도 안 뜬 것이 그 실증이다(ALPHA-578).
    # ALPHA-596 에서 `tasks.tf` 가 두 task-def 에 DB env 를 주면서 **직접 계측**으로 올렸다 —
    # 컨테이너 종료 즉시 판정되고, 그전엔 못 얻던 `records_out`·`failed_records`·`data_status` 가
    # 함께 올라온다. 두 수집기 다 `collection_log` 에 `ops` 봉투를 이미 쓰고 있어서
    # `_observe_from_log` 가 그대로 읽는다(새 계측을 심지 않는다 — ALPHA-182 정신).
    # Reconciler backfill 은 이제 백스톱이다: attempt 가 없으면 그건 정상이 아니라 LEDGER_GAP 이다.
    # ⚠️ **배선(terraform)이 먼저, 플래그 해제가 나중이다** — 이 순서를 지켜야 한다(edge-review).
    # 이미지 배포(deploy-data-pipeline.yml)와 task-def apply(terraform-apply.yml)는 독립
    # 워크플로라 순서 보장이 없다. 플래그가 먼저 뜨면 Reconciler 가 DB env 없는 컨테이너를 계측
    # 대상으로 보고 **영구 거짓 LEDGER_GAP** 을 연다(resolve_issue 는 MISSED·PLANNER_MISSING
    # 전용이라 자동 해소가 없다). 그래서 ALPHA-596 은 PR 을 둘로 쪼갰다 — #359 가 배선만 보내고
    # (그 중간 상태는 wrapper 가 이미 기록하되 Reconciler 는 결측을 정상으로 봐 어느 순서로
    # 배포돼도 안전하다), apply 성공 확인 뒤 이 플래그를 풀었다. FMP 를 되살릴 때도 같은 순서다.

    # ⚠️ `stalled_after_seconds` 를 SFN 타임아웃에 맞춘다(AssembleEvents 와 같은 근거). 기본 1시간은
    # 이 둘의 **정상 재시도**보다 짧다: PoliteClient 는 요청당 4회 시도 + 백오프(1·2·4초)라
    # KRX(타임아웃 45초·ETF 31종)는 최악 31×(45×4+7) ≈ 5,797초, DART 도 대상 corp 수만큼 직렬이다.
    # 정상 실행 중 STALLED 가 붙으면 resolve 경로가 없어 **영구 OPEN** 이다(ALPHA-181). 진짜 행에
    # 걸린 실행은 SFN 타임아웃이 끝내고 그건 별도 CloudWatch 알람이 잡는다.
    CatalogEntry(
        task_key="ETF_HOLDINGS_COLLECTION_KRX", stage="raw", dataset="etf_holdings", required=True,
        cli_command=("ingest-raw-etf", "--source", "krx"), sfn_state_name="CollectKrxEtf",
        ecs_task_definition="krx", source_vendor="krx",
        stalled_after_seconds=21600,
        contract_key=contracts.ETF_HOLDINGS_KRX_EOD,
    ),
    # ── 정제 6 (bigkinds task-def 재사용 — 레이크만 읽어 벤더 키 불요) ─────────────
    # 정제의 depends_on 은 **비운다**: raw 부분실패는 뒤를 막지 않고(ADR-0030) 정제는 빈 입력을
    # 정상 성공으로 처리하므로, raw 를 선행으로 걸면 수집 실패 런에서 **실제로 돌아 성공한 정제**가
    # BLOCKED 로 오귀속된다. 반면 정제→feature 는 진짜 게이트라 아래에서 의존으로 그린다.
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
        # ⚠️ 공시 정제 2개가 빠졌다(ALPHA-724) — 그 스텝이 시장 SFN 의 `NormalizeCheckResults`
        # 게이트 멤버가 아니게 됐기 때문이다. 남겨두면 시장 런에서 **영영 충족되지 않는 의존**이
        # 되어 이 작업이 BLOCKED 로 굳는다(그 정제는 다른 SFN 에서 돌므로 이 런엔 자리가 없다).
        depends_on=(
            "NORMALIZE_PRICE", "NORMALIZE_ETF", "NORMALIZE_ETF_PROFILE",
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
    # ── corp_code enrichment (rds_dart) ───────────────────────────────────────────
    CatalogEntry(
        task_key="ENRICH_CORP_CODE", stage="feature", dataset="company_profile", required=True,
        cli_command=("enrich-corp-code",), depends_on=("LOAD_INSTRUMENTS",),
        sfn_state_name="EnrichCorpCode",
        ecs_task_definition="rds_dart", deadline_offset_seconds=7200,
    ),
    # ══ 뉴스 레인 6작업 (pipeline_type="news" — 뉴스 SFN edge-dev-data-pipeline-news, 하루
    # 3슬롯, ALPHA-591 원장 편입) ═══════════════════════════════════════════════════
    # ALPHA-553 PR2 로 빠졌던 6작업의 복원이되 **그대로 복사가 아니다**:
    # * 직렬 2개의 state 이름이 다르다 — 뉴스 SFN 은 NewsLoadAssertions·NewsAssembleEvents 다
    #   (병렬 브랜치 4개는 statemachine.tf 잡 정의 재사용이라 이름 그대로).
    # * depends_on 은 뉴스 SFN 의 게이트 축으로 다시 그렸다 — 옛 시장 의존(LOAD_ASSERTIONS ←
    #   feature 7개 등)을 복사하면 뉴스 런에 존재하지 않는 작업을 기다려 영영 eligible 이 안 된다.
    # * `kr_trading_calendar` 전부 False — 뉴스 SFN 은 공휴일에도 돈다(평일 크론). True 면 실행
    #   결과가 SKIPPED 뒤로 통째로 사라진다(ALPHA-181 의 함정).
    CatalogEntry(
        # 뉴스는 휴장일에도 나온다 — kr_trading_calendar=False(비거래일에도 DUE).
        task_key="NEWS_COLLECTION_BIGKINDS", stage="raw", dataset="stock_news", required=True,
        cli_command=("ingest-raw", "--source", "bigkinds"), sfn_state_name="CollectBigKindsNews",
        ecs_task_definition="bigkinds", source_vendor="bigkinds", pipeline_type="news",
    ),
    # 정제 의존은 시장 레인과 같은 이유로 비운다 — 뉴스 SFN 도 raw 부분 실패 시 통보 후 계속
    # 진행한다(NewsNotifyRawPartial → NewsNormalizeParallel).
    CatalogEntry(
        task_key="NORMALIZE_NEWS", stage="normalize", dataset="news_articles", required=True,
        cli_command=("normalize-news",), sfn_state_name="NormalizeNews",
        ecs_task_definition="bigkinds", deadline_offset_seconds=5400, pipeline_type="news",
    ),
    # ── 태깅 (deepseek task-def) ─────────────────────────────────────
    # **NewsFeatureCheckResults 게이트의 멤버**라, 빼면 TagNews 만 죽은 런에서 LOAD_ASSERTIONS 의
    # 의존이 전부 충족된 것으로 보여 BLOCKED 여야 할 것이 MISSED("시작조차 안 됐다")로
    # 찍힌다(Codex #273 P1 과 같은 축).
    # ALPHA-610 이 계측으로 올렸다. 이 스텝은 기사별 LLM 실패를 격리해 exit 0 으로 끝나는데
    # (07-27 잔액 소진 때 940/940 전건 실패도 exit 0 였다 — ALPHA-589), `failed_records` 는
    # ops 봉투에 이미 싣고 있었고 판정도 `derive_data_status` 에 이미 있었다(failed>0 →
    # INCOMPLETE). 없던 것은 배선뿐이라, wrapper 가 안 돌아 그 봉투를 아무도 안 읽었다.
    CatalogEntry(
        task_key="TAG_NEWS", stage="feature", dataset="news_assertions", required=True,
        cli_command=("tag-news",), sfn_state_name="TagNews",
        ecs_task_definition="deepseek", depends_on=("NORMALIZE_NEWS",),
        deadline_offset_seconds=7200, stalled_after_seconds=21600,
        pipeline_type="news",
    ),
    # NewsNormalizeCheckResults 게이트 뒤 병렬 — 시장 레인의 ENRICH_CORP_CODE 의존과 다르다
    # (뉴스 SFN 엔 EnrichCorpCode 가 없다 — instrument 마스터는 교차 SFN 읽기 전용 공유).
    CatalogEntry(
        task_key="LOAD_DOCUMENTS", stage="feature", dataset="document", required=True,
        cli_command=("load-documents",), depends_on=("NORMALIZE_NEWS",),
        sfn_state_name="LoadDocuments",
        ecs_task_definition="rds", deadline_offset_seconds=7200, pipeline_type="news",
    ),
    CatalogEntry(
        task_key="LOAD_ASSERTIONS", stage="feature", dataset="document_assertion", required=True,
        cli_command=("load-assertions",), sfn_state_name="NewsLoadAssertions",
        ecs_task_definition="rds", deadline_offset_seconds=7200, pipeline_type="news",
        # ASL `NewsFeatureCheckResults` = feature 전량 성공 게이트(직렬 진입 조건).
        depends_on=("TAG_NEWS", "LOAD_DOCUMENTS"),
    ),
    # ── 이벤트 조립 (events task-def — LLM 분류 + DB 적재) ─────────────────────────
    # LLM 이라 정상 실행이 1시간을 넘을 수 있다. STALLED 임계를 SFN 타임아웃에 맞춘다 —
    # 기본 1시간이면 정상 실행 중에 STALLED 가 붙고 resolve 경로가 없어 영구 OPEN 이다.
    CatalogEntry(
        task_key="ASSEMBLE_EVENTS", stage="feature", dataset="source_event", required=True,
        cli_command=("assemble-events",), depends_on=("LOAD_ASSERTIONS",),
        sfn_state_name="NewsAssembleEvents",
        ecs_task_definition="events", deadline_offset_seconds=10800,
        stalled_after_seconds=21600, pipeline_type="news",
    ),
    # ══ 공시 레인 4작업 (pipeline_type="disclosure" — 공시 SFN edge-dev-data-pipeline-disclosure,
    # 평일 09~18시 정각 10슬롯, ALPHA-722·724) ═══════════════════════════════════════
    # 시장 레인에 있던 4작업의 **소유 레인 이동**이다(신설이 아니다). 옮기면서 두 값을 조정했다:
    #
    # * `deadline_offset_seconds` 를 3600/5400/7200 → 1200/1800/2400 으로 줄였다. 옛 값은 하루
    #   1회 런에 맞춘 것이라 슬롯 간격(3600s)보다 커서 결측이 다음 슬롯 뒤에나 드러났다.
    #   ⚠️ 기준은 간격이 아니라 **간격 − Reconciler 주기**다: 판정은 15분마다 도는 Reconciler 가
    #   하므로 deadline 직후가 아니라 최대 900s 뒤에 찍힌다. 3000s 로 뒀더니 3000+900 > 3600 이라
    #   "다음 슬롯 예정 전에 판정된다"는 계약이 실제로는 안 지켜졌다(edge-review).
    # * `stalled_after_seconds` 를 21600(6시간) → 1800 으로 줄였다. 옛 값의 근거는 "DART 도
    #   대상 corp 수만큼 직렬"이었는데 ALPHA-714 가 그 순회를 없앴다. ⚠️ SFN 타임아웃(2400s)과
    #   **같게 두면 안 된다** — 판정이 `> threshold` 인데 SFN 이 2400s 에 실행을 죽이므로 경과가
    #   2400 을 넘는 순간이 오지 않아 **영원히 발화하지 않는다**. 타임아웃보다 낮춰야 "어느
    #   스텝이 매달렸나"를 작업 단위로 남길 수 있다(실행 단위 타임아웃 알람은 그걸 못 준다).
    #
    # `kr_trading_calendar` 는 전부 False 다 — 크론이 MON-FRI 라 평일 공휴일에도 돈다. True 면
    # 그날 실제로 돈 실행 결과가 SKIPPED 뒤로 통째로 사라진다(ALPHA-181 의 함정).
    CatalogEntry(
        task_key="DISCLOSURE_COLLECTION_DART", stage="raw", dataset="disclosures", required=True,
        cli_command=("ingest-raw-disclosure",), sfn_state_name="CollectDartDisclosure",
        ecs_task_definition="dart", source_vendor="dart",
        deadline_offset_seconds=1200, stalled_after_seconds=1800,
        pipeline_type="disclosure",
    ),
    # 정제 의존은 시장·뉴스 레인과 같은 이유로 비운다 — raw 부분 실패는 뒤를 막지 않고
    # (ADR-0030) 정제는 빈 입력을 정상 성공으로 처리하므로, raw 를 선행으로 걸면 수집 실패
    # 런에서 **실제로 돌아 성공한 정제**가 BLOCKED 로 오귀속된다.
    CatalogEntry(
        task_key="NORMALIZE_DISCLOSURE", stage="normalize", dataset="supply_contract_fact",
        required=True, cli_command=("normalize-disclosure",), sfn_state_name="NormalizeDisclosure",
        ecs_task_definition="bigkinds",
        deadline_offset_seconds=1800, stalled_after_seconds=1800,
        pipeline_type="disclosure",
    ),
    CatalogEntry(
        task_key="NORMALIZE_DISCLOSURE_SEGMENT", stage="normalize",
        dataset="business_segment_fact", required=True,
        cli_command=("normalize-disclosure-segment",), sfn_state_name="NormalizeDisclosureSegment",
        ecs_task_definition="bigkinds",
        deadline_offset_seconds=1800, stalled_after_seconds=1800,
        pipeline_type="disclosure",
    ),
    # ⚠️ 의존은 공시 SFN 의 `DisclosureNormalizeCheckResults` 게이트다 — **ENRICH_CORP_CODE 를
    # 걸 수 없다.** 그건 시장 레인 작업이라 이 런에 존재하지 않아 영영 eligible 이 안 된다
    # (뉴스 레인이 옛 시장 의존을 복사하지 않은 것과 같은 함정). 실제 데이터 의존은 남아 있고
    # **레인 간 읽기 전용 공유**로 성립한다: `company_profile.dart_corp_code` 를 시장 SFN 의
    # EnrichCorpCode 가 채우고, 미해소 건은 이 스텝이 `skipped_unresolved_issuer` 로 계측한 뒤
    # 다음 일일런 이후 슬롯이 줍는다(조용한 유실이 아니라 계측된 지연).
    CatalogEntry(
        task_key="LOAD_DISCLOSURE", stage="feature", dataset="disclosure_document", required=True,
        cli_command=("load-disclosure",), sfn_state_name="LoadDisclosure",
        ecs_task_definition="rds",
        depends_on=("NORMALIZE_DISCLOSURE", "NORMALIZE_DISCLOSURE_SEGMENT"),
        deadline_offset_seconds=2400, stalled_after_seconds=1800,
        pipeline_type="disclosure",
    ),
    # ══ 장중 수급 레인 3작업 (pipeline_type="investor-intraday" — 장중 수급 SFN
    # edge-dev-data-pipeline-investor-intraday, 평일 5슬롯, ALPHA-767·768·769) ═══════════
    # 공시와 달리 **레인 이동이 아니라 신설**이다 — 시장 SFN 이 이 스텝들을 돈 적이 없다.
    #
    # 시간 상수 세 값은 최소 슬롯 간격(09:35→10:05 = 1800s)이 정한다:
    # * `deadline_offset_seconds` 600/700/800 — 기준은 간격이 아니라 **간격 − Reconciler 주기**다
    #   (판정은 15분마다 도는 Reconciler 가 하므로 deadline 직후가 아니라 최대 900s 뒤에 찍힌다).
    #   800+900=1700 < 1800 이라 스테이지 셋 다 "다음 슬롯 예정 전에 판정된다"가 실제로 지켜진다.
    #   실측(2026-08-06 dev)은 수집 210s·정제 ~90s·적재 ~60s 라 여유가 3배 이상이다.
    # * `stalled_after_seconds` 900 — SFN 타임아웃(1500s)보다 **낮아야** 한다. 같게 두면 판정이
    #   `> threshold` 인데 SFN 이 1500s 에 실행을 죽여 경과가 1500 을 넘는 순간이 오지 않아
    #   **영원히 발화하지 않는다**(공시 레인이 실제로 밟은 함정).
    #
    # ⚠️ `kr_trading_calendar` 가 **작업마다 다르다** — 수집·정제는 True, 적재는 False.
    # 기준은 레인이 아니라 "그 작업이 공휴일에 **실제로 일을 하는가**"다(ALPHA-181 의 함정은
    # 실제로 돌아서 값을 만든 실행이 SKIPPED 뒤로 사라지는 것이다).
    #
    # * 수집 True — 장중 투자자 추정은 **비거래일에 존재 자체를 하지 않는다.** 그날 어댑터는
    #   `skip_reason` 으로 돌아서고(kis_investor_estimate) 받는 행이 0이다. False 로 두면
    #   공휴일마다 "기대했는데 증거가 없다"(0건 + empty_allowed=False → UNKNOWN)가 쌓여 진짜
    #   결손과 섞인다.
    # * 정제 True — `--input-run-id $.run_id` 로 **그 런이 수집한 raw 만** 읽으므로(run.py)
    #   공휴일엔 입력이 진짜 0건이다. 가려질 산출이 없다.
    # * 적재 **False** — 여기만 다르다. `load-investor-intraday` 는 창 인자 없이 도는
    #   **canonical 전량 스캔**이라(load_investor_intraday.py) 공휴일에도 실일을 한다:
    #   앞선 슬롯이 RDS 장애로 실패해 남은 백로그를 이 스캔이 줍는다 — 그게 창을 안 붙인
    #   이유이기도 하다(statemachine.tf 의 LoadInvestorIntraday 주석). True 로 두면 그 회수
    #   실행에 attempt 가 아예 안 붙고(wrapper 가 PLAN_SKIPPED 를 그냥 통과시킨다), 다시
    #   실패해도 원장에 **어느 작업이 실패했는지 자리조차 없다**. 화면엔 "휴장이라 안 했다"로
    #   남는데 실제로는 실패한 것이다(Rule 12). 공휴일 정상 런은 `already=N` 이 records_out
    #   으로 세어져 UNKNOWN 도 아니다.
    CatalogEntry(
        task_key="INVESTOR_INTRADAY_COLLECTION_KIS", stage="raw",
        dataset="investor_flow_intraday", required=True,
        cli_command=("ingest-raw-investor-estimate",),
        sfn_state_name="CollectKisInvestorEstimate",
        ecs_task_definition="kis", source_vendor="kis",
        deadline_offset_seconds=600, stalled_after_seconds=900,
        kr_trading_calendar=True,
        pipeline_type="investor-intraday",
    ),
    # 정제 의존은 세 선례와 같은 이유로 비운다 — raw 부분 실패는 뒤를 막지 않고(ADR-0030)
    # 정제는 빈 입력을 정상 성공으로 처리하므로, raw 를 선행으로 걸면 수집이 skip·실패한 런에서
    # **실제로 돌아 성공한 정제**가 BLOCKED 로 오귀속된다.
    CatalogEntry(
        task_key="NORMALIZE_INVESTOR_INTRADAY", stage="normalize",
        dataset="investor_flow_intraday", required=True,
        cli_command=("normalize-investor-estimate",),
        sfn_state_name="NormalizeInvestorEstimate",
        ecs_task_definition="bigkinds",
        deadline_offset_seconds=700, stalled_after_seconds=900,
        kr_trading_calendar=True,
        pipeline_type="investor-intraday",
    ),
    # 의존은 이 SFN 의 `InvestorIntradayNormalizeCheckResults` 게이트다 — 시장 레인 작업
    # (ENRICH_CORP_CODE 등)을 걸 수 없다. 그건 이 런에 존재하지 않아 영영 eligible 이 안 된다
    # (뉴스·공시 레인이 옛 시장 의존을 복사하지 않은 것과 같은 함정). 이 적재는 instrument 를
    # `instrument_id` 로 직접 해소하므로 corp_code 축과 무관하다.
    CatalogEntry(
        task_key="LOAD_INVESTOR_INTRADAY", stage="feature",
        dataset="investor_flow_intraday_load", required=True,
        cli_command=("load-investor-intraday",),
        sfn_state_name="LoadInvestorIntraday",
        ecs_task_definition="rds",
        depends_on=("NORMALIZE_INVESTOR_INTRADAY",),
        deadline_offset_seconds=800, stalled_after_seconds=900,
        kr_trading_calendar=False,  # 창 없는 전량 스캔이라 공휴일에도 실일을 한다 — 위 주석
        pipeline_type="investor-intraday",
    ),
)

CATALOG: dict[str, CatalogEntry] = {e.task_key: e for e in _ENTRIES}

PIPELINE_TYPE = "etf-daily"        # 시장/EOD 레인(기본)
NEWS_PIPELINE_TYPE = "news"        # 뉴스 레인(ALPHA-591)
# 장중 공시 레인(ALPHA-721). **아직 등록 작업이 0개다** — 배선(Planner 분기·Reconciler 슬롯)만
# 먼저 착지시키고 카탈로그 엔트리 이동은 컷오버 PR 이 한다. 순서가 이런 이유: 이미지 CD 와
# terraform-apply 가 독립 워크플로라 어느 쪽이 먼저 떠도 안전해야 하는데, 레인을 아는 코드가
# 먼저 떠 있으면 스케줄이 켜졌을 때 Planner 가 죽지 않고(모르는 레인 = SystemExit) 기대 0건으로
# 계획할 뿐이다. 반대 순서(엔트리를 먼저 옮김)는 시장 런이 자기 기대 밖 attempt 를 받아
# **resolve 경로 없는 LEDGER_GAP** 이 된다.
DISCLOSURE_PIPELINE_TYPE = "disclosure"
# 장중 수급 레인(ALPHA-769). 공시와 달리 **엔트리를 같은 PR 에서 등록한다** — 공시는 시장 SFN 이
# 이미 돌던 스텝의 소유 레인 이동이라 순서를 나눠야 했지만(엔트리를 먼저 옮기면 시장 런이 자기
# 기대 밖 attempt 를 받아 resolve 경로 없는 LEDGER_GAP 이 된다), 이 3작업은 **어느 레인도 소유한
# 적이 없는 신설**이라 뺏어올 기대가 없다. 배포 순서가 어긋나 terraform 이 먼저 떠도 Planner 가
# 이 레인을 모르는 이미지 위에서 fail-loud 로 죽을 뿐 원장을 오염시키지 않는다.
INVESTOR_INTRADAY_PIPELINE_TYPE = "investor-intraday"

# `--source` 미지정 시 run.py 가 쓰는 기본 벤더(`args.source or "fmp"`). 벤더 인자가 없는
# 카탈로그 엔트리는 이 벤더를 뜻한다 — 두 표현(`ingest-raw` 와 `ingest-raw --source fmp`)이
# 같은 작업으로 해소돼야 한다.
_DEFAULT_VENDOR = "fmp"


def entries(pipeline_type: str | None = None) -> tuple[CatalogEntry, ...]:
    """카탈로그 엔트리(정의 순서). pipeline_type 지정 시 그 레인만, None 이면 전 레인.

    Planner 는 자기 레인을 지정한다(다른 레인 작업을 기대에 섞으면 매 런 MISSED). Reconciler·
    `by_cli`·`by_sfn_state` 는 전 레인이다 — 증거는 런의 SFN history 에서만 오고 state 이름이
    레인 간 유일해 섞일 수 없다.
    """
    if pipeline_type is None:
        return _ENTRIES
    return tuple(e for e in _ENTRIES if e.pipeline_type == pipeline_type)


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
            "pipeline_type": e.pipeline_type,
            "contract_key": e.contract_key,
        }
        for e in sorted(_ENTRIES, key=lambda x: x.task_key)
    ]
    blob = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def version() -> str:
    """배포 Git SHA. 이미지 빌드가 env 로 주입한다(없으면 'unknown' — 로컬·미주입)."""
    return os.environ.get("OPS_CATALOG_VERSION") or os.environ.get("GIT_SHA") or "unknown"
