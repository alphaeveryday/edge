"""Task Catalog 테스트 (ALPHA-530) — 스펙 §9 시나리오 24(카탈로그↔ASL 매핑) 포함."""

from __future__ import annotations

import re
from pathlib import Path

from data_pipeline.ops import catalog

_TF_MODULE = Path(__file__).resolve().parents[5] / "infra/terraform/modules/data-pipeline"
_STATEMACHINE_TF = _TF_MODULE / "statemachine.tf"
_TASKS_TF = _TF_MODULE / "tasks.tf"
# 뉴스 SFN(ALPHA-553)의 직렬 2개(NewsLoadAssertions·NewsAssembleEvents)는 여기에만 있다 —
# 병렬 브랜치 4개는 statemachine.tf 잡 정의를 부분집합 필터로 재사용한다.
_NEWS_PIPELINE_TF = _TF_MODULE / "news_pipeline.tf"
# 공시 SFN(ALPHA-722)은 직렬 state 가 없어 지금은 여기서 잡히는 state 가 0개다. 그래도 함께
# 읽는다 — 나중에 직렬 꼬리가 붙으면 그때 **자동으로** 역방향 검사 대상이 되게(이 테스트의
# 존재 이유가 "새 SFN 잡이 아무도 모르게 미계측로 남는 것"을 막는 것이다).
_DISCLOSURE_PIPELINE_TF = _TF_MODULE / "disclosure_pipeline.tf"
# 장중 수급 SFN(ALPHA-769)도 공시와 같이 직렬 state 가 없어 지금은 여기서 잡히는 state 가 0개다.
# 그래도 함께 읽는 이유는 같다 — 나중에 직렬 꼬리가 붙으면 **자동으로** 역방향 검사 대상이 되게.
_INVESTOR_INTRADAY_PIPELINE_TF = _TF_MODULE / "investor_intraday_pipeline.tf"


def _combined_tf() -> str:
    return (_STATEMACHINE_TF.read_text(encoding="utf-8")
            + _NEWS_PIPELINE_TF.read_text(encoding="utf-8")
            + _DISCLOSURE_PIPELINE_TF.read_text(encoding="utf-8")
            + _INVESTOR_INTRADAY_PIPELINE_TF.read_text(encoding="utf-8"))


def test_content_hash_is_deterministic():
    assert catalog.content_hash() == catalog.content_hash()
    assert len(catalog.content_hash()) == 64  # sha256 hex


def test_by_sfn_state_maps_registered_only():
    assert catalog.by_sfn_state("CollectKisPrice").task_key == "PRICE_COLLECTION_KIS"
    assert catalog.by_sfn_state("NormalizePrice").task_key == "NORMALIZE_PRICE"
    assert catalog.by_sfn_state("LoadPriceDaily").task_key == "LOAD_PRICE_DAILY"
    assert catalog.by_sfn_state("SomeInternalChoice") is None


# ASL 의 ECS Task state 는 두 형태로만 나타난다: 페이즈 잡 맵의 `state = "X"` 와 인라인 직렬의
# `X = merge(local.ecs_run_task_base`. Choice·Succeed 같은 내부 state 는 태스크가 아니다.
_ASL_JOB_STATE = re.compile(r'^\s*state\s*=\s*"(\w+)"', re.M)
# ⚠️ 쉼표까지 요구한다 — `Parameters = merge(local.ecs_run_task_base.Parameters, {` 가 같은
# 접두를 써서, 안 그러면 `Parameters` 가 state 로 잡혀 개수가 하나 늘어난다.
_ASL_INLINE_STATE = re.compile(r'^\s*(\w+)\s*=\s*merge\(local\.ecs_run_task_base,', re.M)

# 계측하지 않는 ECS Task state 와 그 이유(카탈로그 docstring 의 표와 같은 근거).
# (뉴스 레인 4개는 ALPHA-591 원장 편입으로 목록에서 빠져 다시 등록됐다 — 자체 pipeline_type.)
_NOT_INSTRUMENTED = {
    "CollectFmpNews": "FMP bandwidth 한도 소진 → SFN 토글 off(ALPHA-558). 잡 정의만 남고 "
                      "브랜치에 없다 — 토글 복구 시 뉴스 레인으로 등록",
    "CollectFmpPrice": "FMP bandwidth 한도 소진 → SFN 토글 off",
    "CollectFmpFinancial": "FMP bandwidth 한도 소진 → SFN 토글 off",
    "CollectFmpEtf": "FMP bandwidth 한도 소진 → SFN 토글 off",
    "CollectDartFinancial": "하류 소비자 0(financial_statements 를 읽는 정제·적재·분석 없음) — "
                            "대응할 이유 없는 실패 경보가 되므로 등록 보류",
    # AnalyzeOne 은 ALPHA-806 에서 state 자체가 사라졌다(analyze 페이즈 제거) — 설명은
    # SFN 스텝이 아니라 분봉 트리거 큐 상주 소비자가 만든다. 제외 목록에서도 뺀다.
    #
    # ── 공시 4state: 1분 레인으로 소유 이동(ALPHA-875) ──
    # ALPHA-724 가 시장 SFN → 공시 SFN 으로 옮겼던 그 스텝들이, 이번엔 SFN 을 아예 떠나
    # **1분 세션**(`disclosure-worker`)이 소유한다. SFN 정의는 남기되 스케줄이 DISABLED 라
    # 아무도 시작하지 않는다 — 롤백 경로를 위해 남긴 것이고, 원장 기대는 옮겨갔다.
    # ⚠️ 등록을 남기면 **돌지도 않는 슬롯을 원장이 기대**해 매 거래일 10슬롯이 전건 MISSED 다.
    # 되살릴 때는 반드시 `disclosure_schedule_state` 와 **같은 apply** 로 되돌린다.
    "CollectDartDisclosure": "1분 레인이 소유(ALPHA-875) — SFN 스케줄 DISABLED, 원장 기대는 "
                             "minute_ingestion_window 로 이동",
    "NormalizeDisclosure": "1분 레인이 소유(ALPHA-875)",
    "NormalizeDisclosureSegment": "1분 레인이 소유(ALPHA-875)",
    "LoadDisclosure": "1분 레인이 소유(ALPHA-875)",
}


def _asl_task_states(tf: str) -> set[str]:
    return set(_ASL_JOB_STATE.findall(tf)) | set(_ASL_INLINE_STATE.findall(tf))


def _strip_hcl_comments(tf: str) -> str:
    """줄 주석·블록 주석을 걷는다.

    ⚠️ 이걸 안 하면 **주석 처리된 항목을 살아 있는 배선으로 센다.** 배선을 뗄 때 가장 흔한
    형태가 삭제가 아니라 주석 처리이고, 그때가 정확히 가드가 잡아야 할 순간이다(edge-review).
    `_taskdefs_with_db_env` 가 같은 이유로 같은 처리를 한다 — 여기서도 같은 규율을 쓴다.
    줄 **선두**만 보는 이유도 같다: 값 안의 `//`(`"s3://…"`)를 자르면 반대 방향 오탐이 난다.
    """
    lines = [ln for ln in tf.splitlines() if not ln.lstrip().startswith(("#", "//"))]
    return re.sub(r"/\*.*?\*/", "", "\n".join(lines), flags=re.S)


def _hcl_list(tf: str, name: str) -> list[str]:
    """`name = [ "a", "b" ]` 의 문자열 항목. 한 줄·여러 줄 모두 받는다(주석 제외)."""
    body = re.search(rf"^\s*{name}\s*=\s*\[(.*?)\]", _strip_hcl_comments(tf), re.M | re.S)
    assert body, f"{name} 를 statemachine.tf 에서 못 찾았다 — 파서가 낡았다"
    return re.findall(r'"(\w+)"', body.group(1))


def _hcl_number(tf: str, name: str) -> int:
    """`variable "name" { … default = N }` 의 기본값."""
    block = re.search(rf'variable\s+"{name}"\s*\{{(.*?)^\}}', _strip_hcl_comments(tf), re.M | re.S)
    assert block, f"variable {name} 을 못 찾았다 — 파서가 낡았다"
    value = re.search(r"^\s*default\s*=\s*(\d+)", block.group(1), re.M)
    assert value, f"variable {name} 에 숫자 default 가 없다"
    return int(value.group(1))


def lane_slot_interval_seconds(prefix: str = "disclosure") -> int:
    """다슬롯 레인의 cron 맵에서 **실제 최소 슬롯 간격**을 계산한다(분 단위 cron 을 KST 시각으로).

    테스트가 3600 을 하드코딩하면 cron 에 30분 슬롯을 하나 더해도 통과한다 — 그때 deadline·
    STALLED 임계는 다음 슬롯 뒤로 밀려 판정이 무의미해지는데 아무도 모른다(edge-review).

    `prefix` 로 레인을 고른다(ALPHA-769 에 장중 수급 레인이 붙으며 일반화). 복제하지 않는
    이유는 이 계산이 곧 계약이라서다 — 레인마다 베끼면 한쪽만 고쳐진 채 갈린다.
    """
    tf = _strip_hcl_comments((_TF_MODULE / "variables.tf").read_text(encoding="utf-8"))
    block = re.search(rf'variable\s+"{prefix}_schedule_expressions"\s*\{{(.*?)^\}}', tf,
                      re.M | re.S)
    assert block, f"{prefix}_schedule_expressions 를 못 찾았다 — 파서가 낡았다"
    minutes = sorted(int(h) * 60 + int(m)
                     for m, h in re.findall(r'"cron\((\d+) (\d+) ', block.group(1)))
    assert len(minutes) >= 2, f"슬롯이 {len(minutes)}개 — 간격을 잴 수 없다"
    return min(b - a for a, b in zip(minutes, minutes[1:])) * 60


def lane_sfn_timeout_seconds(prefix: str = "disclosure") -> int:
    return _hcl_number((_TF_MODULE / "variables.tf").read_text(encoding="utf-8"),
                       f"{prefix}_state_machine_timeout_seconds")


def reconcile_period_seconds() -> int:
    """Reconciler 주기(초). **판정 지연의 하한**이라 deadline 계약에 반드시 들어간다.

    deadline 은 "언제부터 결측인가"를 정하지만 그 판정을 실제로 찍는 건 주기 Reconciler 다 —
    deadline 직후가 아니라 최대 이 주기만큼 뒤에 찍힌다. 간격만 보고 `deadline < 간격` 으로
    두면 "다음 슬롯 예정 전에 판정된다"는 계약이 실제로는 안 지켜진다(edge-review).
    """
    tf = _strip_hcl_comments((_TF_MODULE / "variables.tf").read_text(encoding="utf-8"))
    block = re.search(r'variable\s+"reconcile_schedule_expression"\s*\{(.*?)^\}', tf, re.M | re.S)
    assert block, "reconcile_schedule_expression 을 못 찾았다 — 파서가 낡았다"
    rate = re.search(r'default\s*=\s*"rate\((\d+)\s+minutes?\)"', block.group(1))
    assert rate, "Reconciler 주기가 rate(N minutes) 형태가 아니다 — 계약을 다시 계산하라"
    return int(rate.group(1)) * 60


def _market_normalize_task_keys() -> set[str]:
    """시장 SFN 이 실제로 도는 정제 state → 카탈로그 task_key.

    `normalize_jobs`(전체) − `market_excluded_states` 가 곧 ASL `NormalizeCheckResults` 의
    멤버다. 원장의 `LOAD_INSTRUMENTS.depends_on` 은 그 게이트를 그린 것이라 **같아야 한다** —
    어긋나면 게이트가 닫힌 런에서 원인이 오귀속되거나(BLOCKED↔MISSED), 안 도는 작업을 기다려
    영영 미충족이 된다. 두 사실이 코드와 terraform 에 나뉘어 있으므로 여기서 잇는다.
    """
    tf = _STATEMACHINE_TF.read_text(encoding="utf-8")
    block = re.search(r"^\s*normalize_jobs\s*=\s*\[(.*?)^\s{2}\]", tf, re.M | re.S)
    assert block, "normalize_jobs 블록을 못 찾았다 — 파서가 낡았다"
    states = set(_ASL_JOB_STATE.findall(block.group(1))) - set(
        _hcl_list(tf, "market_excluded_states"))
    assert states, "시장 정제 잡이 0개로 나왔다 — 파서가 깨졌다"
    keys = set()
    for state in states:
        entry = catalog.by_sfn_state(state)
        assert entry is not None, f"시장 SFN 이 도는 정제 state 인데 미등록: {state}"
        keys.add(entry.task_key)
    return keys


def test_catalog_and_asl_task_states_match_both_ways():
    """시나리오 24 확장 — 카탈로그↔ASL 를 **양방향** 정확 대조한다(ALPHA-181).

    WHY: 기존 검사는 `entry.sfn_state_name in tf`(부분 문자열)라 사실상 항상 통과하는 공허한
    절이었고, 역방향이 없어 **새 SFN 잡이 추가돼도 아무도 모르게 미계측**으로 남았다. 역방향에
    화이트리스트를 두면 잡을 추가한 사람이 "등록할지 제외할지"를 CI 에서 마주친다.
    """
    asl_states = _asl_task_states(_combined_tf())
    # 31 → 33(ALPHA-591): 뉴스 SFN 직렬 2(NewsLoadAssertions·NewsAssembleEvents)를 포함해
    # 두 SFN 파일을 함께 센다 — 뉴스 잡 4개의 `state = "…"` 정의는 statemachine.tf 에 있다.
    # 33 → 36(ALPHA-769): 장중 수급 3잡 신설. **ALPHA-724 와 성격이 다르다** — 공시는 소유
    # 레인만 옮긴 것이라 이 수가 그대로였지만, 여기선 statemachine.tf 잡 리스트에 없던 스텝이
    # 셋 늘었다(ALPHA-767·768 이 만든 층에 배선이 처음 붙는다).
    # 36 → 35(ALPHA-806): analyze 페이즈 제거로 AnalyzeOne 이 사라졌다. 설명은 SFN 스텝이
    # 아니라 분봉 트리거 큐 상주 소비자가 만든다.
    assert len(asl_states) == 35, f"ECS Task state 수가 바뀌었다: {len(asl_states)}"

    registered = {e.sfn_state_name for e in catalog.entries()}
    assert registered <= asl_states, f"ASL 에 없는 state 등록: {registered - asl_states}"
    uncovered = asl_states - registered - set(_NOT_INSTRUMENTED)
    assert not uncovered, f"등록도 제외도 안 된 state: {uncovered} — 카탈로그에 넣거나 이유를 달아라"
    assert registered.isdisjoint(_NOT_INSTRUMENTED), "제외 목록과 등록이 겹친다"
    # 21 → 27(ALPHA-591). ALPHA-724 는 공시 4작업의 **소유 레인만** 옮겨 총계가 그대로다 —
    # 커버리지를 숫자로 고정해 조용한 축소를 막는 절이다(Rule 12). 레인별 몫을 함께 고정하는
    # 이유가 여기 있다: 총계만 보면 레인 이동과 "한 레인이 통째로 사라짐"이 구분되지 않는다.
    # 27 → 30(ALPHA-769): 장중 수급 3작업 신설. 시장 17 은 그대로다 — 이 셋은 시장 SFN 이 돌던
    # 것을 뺏어온 게 아니라 배선이 0이던 신설이라, 레인 이동이었다면 반드시 줄었어야 할 숫자가
    # 안 줄어야 맞다(그 구분을 이 절이 든다).
    # 30 → 26(ALPHA-875): 공시 4작업이 **SFN 원장을 떠났다**(1분 세션이 소유). 위 두 사례와
    # 성질이 다르다 — 724 는 레인 간 이동이라 총계가 그대로였고, 769 는 신설이라 늘었다.
    # 이번엔 줄어야 맞다. ⚠️ 줄어드는 변경이 가장 위험한 종류라(조용한 커버리지 축소) 이
    # 숫자를 고쳐야만 통과하게 두는 것이 이 절의 목적이다: 공시 레인이 0 인 것은
    # **의도된 상태**이고 그 근거는 `_NOT_INSTRUMENTED` 의 공시 4항목에 적었다.
    assert len(registered) == 26
    assert len(catalog.entries("etf-daily")) == 17
    assert len(catalog.entries("news")) == 6
    # 공시 레인은 비었다 — 되살리려면 `disclosure_schedule_state` 와 같은 apply 여야 한다.
    assert len(catalog.entries("disclosure")) == 0
    assert len(catalog.entries("investor-intraday")) == 3
    # 자기 기록이 불가능한 등록 작업은 이제 **0개**다(ALPHA-596 이 krx·dart, ALPHA-610 이
    # TAG_NEWS 를 배선과 함께 승격). 빈 집합을 단언하는 이유: 미계측으로 되돌리는 변경은 그
    # 작업의 유실 신호가 exit code 로 납작해진다는 뜻이라(ALPHA-578) 조용히 지나가면 안 된다.
    # FMP 를 되살릴 때처럼 정당한 미계측이 다시 생기면 여기서 명시적으로 다시 연다.
    assert {e.task_key for e in catalog.entries() if not e.instrumented} == set()


# **배선이 플래그보다 한 배포 앞선** 작업(task_key). 비어 있는 것이 정상 상태다.
#
# WHY: 이미지 CD(deploy-data-pipeline.yml)와 terraform-apply.yml 은 같은 dev push 에서 **독립
# 실행**돼 순서 보장이 없고, task-def 는 mutable `data-pipeline-latest` 를 참조한다. 배선과
# 플래그를 한 PR 에 묶으면 이미지가 먼저 뜨는 순서에서 새 카탈로그(instrumented=True)가 DB env
# 없는 옛 task revision 위에서 돌고, Reconciler 가 그 attempt 결측에 **LEDGER_GAP 을 연다**
# (reconciler.py — resolve 경로가 없어 영구 OPEN 이다). 그래서 ALPHA-596(#359→#362)처럼 배선을
# 한 배포 앞세운다.
#
# 이 유예는 그 한 배포 동안만 유효하다 — 플래그를 올리는 PR 이 여기서 지운다. 잊어도 조용히
# 넘어가지 않게 **만료 단언**을 함께 둔다(아래 `stale`): 플래그가 올라가는 순간 이 목록이
# 실패의 원인이 된다. task-def 가 아니라 task_key 로 잡는 이유는 같은 task-def 를 쓰는 다른
# 작업까지 덩달아 면제되지 않게 하기 위해서다.
# ALPHA-610 이 #379(배선)→#(이 PR, 플래그)로 실제로 밟은 경로이고, 지금은 비어 있는 것이 맞다.
_WIRING_AHEAD_OF_FLAG: set[str] = set()


def _taskdefs_with_db_env() -> set[str]:
    """`tasks.tf` 에서 DB 접속 env 를 **완전히** 받는 task-def 키. host(`db_env`)와 password 둘 다
    있어야 한다 — `DbConfig` 는 부분 주입이면 `load_settings()` 단계에서 통째로 터진다.
    """
    # ⚠️ 주석을 먼저 걷어낸다. 배선을 뗄 때 가장 흔한 형태가 **삭제가 아니라 주석 처리**인데,
    # 원문을 그대로 훑으면 `# DATA_PIPELINE_DB__PASSWORD = …` 를 배선으로 세어 **가드가 정확히
    # 놓쳐야 할 상황에서 통과**한다. 이 파일은 주석이 설명의 대부분이라 실제로 밟는 경로다.
    # HCL 주석은 `#`·`//`·`/* */` 세 형태다 — 하나만 걷으면 나머지로 그대로 우회된다.
    # 줄 **선두**만 보는 이유: 값 안에 `//` 가 정상적으로 들어간다(`"s3://..."`). 줄 중간까지
    # 자르면 배선이 멀쩡한데 가드가 실패하는 **반대 방향 오류**가 나고, 잘린 `}` 가 아래 블록
    # 매칭까지 어긋내 오탐이 엉뚱한 곳에서 터진다. 주석 처리는 실제로 줄 선두에서 일어난다.
    # 순서가 중요하다: **줄 주석을 먼저** 걷고 그다음 블록이다. 거꾸로 하면 `#` 주석 안의
    # `sources/*.py`(tasks.tf:24 에 실제로 있다) 가 유령 블록을 열어 그 뒤 150여 줄을 통째로
    # 먹고, 추출 집합이 비어 엉뚱한 곳에서 터진다(아래 sanity assert 가 잡긴 한다).
    lines = _TASKS_TF.read_text(encoding="utf-8").splitlines()
    tf = "\n".join(ln for ln in lines if not ln.lstrip().startswith(("#", "//")))
    tf = re.sub(r"/\*.*?\*/", "", tf, flags=re.S)
    # env_sets 는 `key = local.db_env` 또는 `key = merge(local.db_env, {...})`.
    host = set(re.findall(r"^\s*(\w+)\s*=\s*(?:merge\(\s*)?local\.db_env\b", tf, re.M))
    # secret_sets 의 password 는 블록 안에 있으니 블록 단위로 본다.
    password = {
        m.group("key")
        for m in re.finditer(r"^\s{4}(?P<key>\w+)\s*=\s*\{(?P<body>.*?)^\s{4}\}", tf, re.M | re.S)
        if "DATA_PIPELINE_DB__PASSWORD" in m.group("body")
    }
    return host & password


def test_instrumented_entries_have_db_env_in_taskdef():
    """WHY: `instrumented=True` 는 "이 컨테이너가 자기 원장을 쓸 수 있다"는 **주장**인데 그 근거는
    코드가 아니라 terraform 에 있다. 어긋나면 조용히 실패한다 — wrapper 가 `settings.db is None`
    으로 투명 통과해(`entry.ledger_from_settings`) 작업은 exit 0 인데 원장엔 PENDING 행만 남고,
    화면은 그걸 "대기"로 굳힌다(ALPHA-596 이 고친 바로 그 상태). 게다가 Reconciler 는 이제
    `instrumented=True` 인 attempt 결측을 LEDGER_GAP 으로 여니 거짓 이슈까지 쌓인다.
    테스트가 없으면 다음 사람이 플래그만 뒤집고 배선을 잊어도 전부 초록이다.
    """
    wired = _taskdefs_with_db_env()
    assert "kis" in wired and "rds" in wired, f"파서가 깨졌다 — 추출된 task-def: {wired}"

    missing = {
        e.task_key: e.ecs_task_definition
        for e in catalog.entries()
        if e.instrumented and e.ecs_task_definition not in wired
    }
    assert not missing, (
        f"DB env 없는 task-def 인데 instrumented=True: {missing} — "
        f"tasks.tf 에 db_env+DATA_PIPELINE_DB__PASSWORD 를 주거나 instrumented=False 로 내려라"
    )

    # 역방향도 잠근다. DB env 가 배선된 task-def 인데 `instrumented=False` 면 그건 **반대 방향의
    # 거짓말**이다: 컨테이너는 attempt 를 쓸 수 있는데 Reconciler 는 그 결측을 정상으로 보고
    # LEDGER_GAP 을 안 연다 — wrapper 가 실제로 죽어도 원장이 조용히 넘어간다. 위 단언만 두면
    # 누가 플래그를 False 로 되돌려도 조건에서 빠져 전부 초록이라(ALPHA-596 이 지운 상태로 복귀),
    # 이 PR 이 세운 "등록 = 직접 계측" 불변식이 테스트로 지켜지지 않는다.
    # 미배선 task-def 의 False 는 여전히 정상이다(FMP 를 되살릴 때의 문) — 그건 위 집합 밖이다.
    lying = {
        e.task_key: e.ecs_task_definition
        for e in catalog.entries()
        if not e.instrumented and e.ecs_task_definition in wired
    }
    # 유예는 **스스로 만료한다**. 주석으로 "다음 PR 에서 지워라"만 적으면 안 지워도 초록이고,
    # 그 task_key 는 역방향 가드에서 영구 제외된다 — 플래그가 False 로 회귀해도 아무도 모른다.
    # 그래서 유예 항목이 실제로 위반 상태일 때만 유효하다고 단언한다: 플래그를 올리는 순간
    # 그 키가 `lying` 에서 빠져 **유예 자체가 실패로 드러난다**(제거를 강제하는 코드다).
    stale = _WIRING_AHEAD_OF_FLAG - set(lying)
    assert not stale, (
        f"만료된 유예: {stale} — 배선·플래그가 이미 일치한다. "
        f"_WIRING_AHEAD_OF_FLAG 에서 지워 역방향 가드를 되살려라"
    )
    lying = {k: v for k, v in lying.items() if k not in _WIRING_AHEAD_OF_FLAG}
    assert not lying, (
        f"DB env 가 배선됐는데 instrumented=False: {lying} — "
        f"계측 가능한 작업의 attempt 결측이 LEDGER_GAP 없이 묻힌다"
    )


# 페이즈 잡 맵의 삼중항(state·taskdef_key·command_expr). 인라인 직렬은 별도로 판다.
_ASL_JOB_TRIPLE = re.compile(
    r'state\s*=\s*"(?P<state>\w+)"\s*\n\s*taskdef_key\s*=\s*"(?P<taskdef>\w+)"\s*\n'
    r'\s*command_expr\s*=\s*"States\.Array\((?P<cmd>[^)]*)\)"', re.M)
_ASL_INLINE_CMD = re.compile(
    r'^\s*(?P<state>\w+)\s*=\s*merge\(local\.ecs_run_task_base,.*?'
    r'aws_ecs_task_definition\.this\["(?P<taskdef>\w+)"\].*?'
    r'"Command\.\$"\s*=\s*"States\.Array\((?P<cmd>[^)]*)\)"', re.M | re.S)


def _asl_command_args(cmd: str) -> list[str]:
    """`'ingest-raw', '--source', 'fmp', '--run-id', $.run_id` → 리터럴 인자만."""
    return [m for m in re.findall(r"'([^']*)'", cmd)]


def test_catalog_matches_asl_command_and_taskdef_per_state():
    """WHY: state 이름 집합만 맞춰 보면 두 엔트리의 `cli_command`·`ecs_task_definition` 을 서로
    **바꿔 놔도 전부 통과**한다 — 배포 후 wrapper 가 실행된 CLI 를 다른 task_key 로 해소하거나,
    그 expected_task 가 영원히 FULFILLED 되지 못한다. state 별로 삼중항을 통째로 대조한다.
    """
    # ⚠️ 파일별로 따로 파싱한다 — 인라인 패턴은 DOTALL 비탐욕이라, 삼중항이 안 갖춰진 state
    # 에서 시작한 매치가 이어붙인 다음 파일의 삼중항까지 건너가 삼키면 뉴스 직렬 2개가
    # 파싱에서 사라진다.
    asl: dict[str, tuple[str, list[str]]] = {}
    for path in (_STATEMACHINE_TF, _NEWS_PIPELINE_TF):
        tf = path.read_text(encoding="utf-8")
        asl.update({m.group("state"): (m.group("taskdef"), _asl_command_args(m.group("cmd")))
                    for m in _ASL_JOB_TRIPLE.finditer(tf)})
        asl.update({m.group("state"): (m.group("taskdef"), _asl_command_args(m.group("cmd")))
                    for m in _ASL_INLINE_CMD.finditer(tf)})
    assert len(asl) >= 27, f"ASL 삼중항 파싱 실패: {len(asl)}"

    for entry in catalog.entries():
        taskdef, args = asl[entry.sfn_state_name]
        assert entry.ecs_task_definition == taskdef, \
            f"{entry.task_key}: task-def 가 ASL({taskdef})과 다르다"
        assert args[0] == entry.cli_command[0], \
            f"{entry.task_key}: CLI 스텝이 ASL({args[0]})과 다르다"
        # 벤더가 갈리는 스텝은 `--source` 까지 같아야 남의 벤더 결과를 그 작업으로 안 기록한다.
        asl_vendor = args[args.index("--source") + 1] if "--source" in args else None
        entry_vendor = (entry.cli_command[entry.cli_command.index("--source") + 1]
                        if "--source" in entry.cli_command else None)
        assert asl_vendor == entry_vendor, f"{entry.task_key}: 벤더가 ASL({asl_vendor})과 다르다"


def test_catalog_cli_commands_are_real_steps():
    """WHY: 존재하지 않는 CLI 를 가리키는 엔트리는 **영원히 FULFILLED 될 수 없다**(매 런 MISSED).
    argparse choices 와 대조해 오타·개명을 CI 에서 잡는다."""
    import inspect

    from data_pipeline import run as run_module

    source = inspect.getsource(run_module.main)
    for entry in catalog.entries():
        assert f'"{entry.cli_command[0]}"' in source, f"{entry.cli_command[0]} 가 CLI 에 없다"


def test_by_cli_resolves_vendor_split_steps():
    # WHY: 같은 CLI 스텝이 `--source` 로 갈린다(`ingest-price-raw` → FMP vs KIS). 벤더를 섞어
    #      해소하면 원장이 **남의 벤더 결과를 그 작업으로** 기록한다 — 미등록 벤더는 None 이어야
    #      계측 없이 지나간다. 미지정=fmp 는 run.py 의 `args.source or "fmp"` 규칙과 같은 축이다.
    assert catalog.by_cli("ingest-price-raw", "kis").task_key == "PRICE_COLLECTION_KIS"
    assert catalog.by_cli("ingest-price-raw", None) is None      # FMP 가격은 미등록
    assert catalog.by_cli("ingest-price-raw", "fmp") is None
    # ALPHA-578: KRX ETF 수집이 등록되면서 이 스텝도 벤더로 갈린다. FMP 짝(CollectFmpEtf)은
    # 토글 off 로 여전히 미등록이라, 벤더 미지정(=fmp)은 None 이어야 한다 — 폴백하면 US 수집
    # 결과가 KR 작업으로 기록된다.
    assert catalog.by_cli("ingest-raw-etf", "krx").task_key == "ETF_HOLDINGS_COLLECTION_KRX"
    assert catalog.by_cli("ingest-raw-etf", None) is None
    # 재무는 양쪽 다 미등록(FMP=토글 off, DART=하류 소비자 0) — 계측 없이 지나간다.
    assert catalog.by_cli("ingest-raw-financial", "dart") is None
    assert catalog.by_cli("ingest-raw-financial", None) is None
    # 공시는 벤더 축이 없다(DART 단일) — --source 없이 해소된다. 레인이 바뀌어도(ALPHA-724)
    # `by_cli` 는 전 레인 검색이라 그대로다: 컨테이너는 자기 레인을 모르고 CLI 가 정체성이다.
    # 공시는 이제 ops 원장이 소유하지 않는다(ALPHA-875) — **None 이 정답이다.** 엔트리를
    # 남기면 1분 레인이 부른 스텝이 이 레인 task_key 로 귀속돼 슬롯이 전건 MISSED 가 된다.
    # (워커는 CLI 가 아니라 스텝 함수를 부르므로 이 조회 자체가 안 일어나지만, 어휘가 비어
    # 있어야 "누가 소유하나"가 한 곳에서만 답해진다.)
    assert catalog.by_cli("ingest-raw-disclosure") is None
    # 벤더 축이 없는 스텝은 --source 없이 해소된다.
    assert catalog.by_cli("normalize-price").task_key == "NORMALIZE_PRICE"
    assert catalog.by_cli("load-price-daily").task_key == "LOAD_PRICE_DAILY"
    assert catalog.by_cli("no-such-step") is None
    # 벤더 축이 없는 스텝에 --source 가 딸려와도 계측이 끊기면 안 된다 — dispatch 는 source 를
    # 안 보고 정상 실행하는데 원장만 비면 그 런이 통째로 안 보인다(수동 회수에서 흔한 입력).
    assert catalog.by_cli("normalize-price", "kis").task_key == "NORMALIZE_PRICE"


def test_task_key_resolves_from_the_cli_regardless_of_env(monkeypatch):
    # WHY: 원장에 남길 것은 이 컨테이너가 **한 일**이지 오케스트레이터가 의도한 일이 아니다.
    #      env 를 정본으로 두면 둘이 어긋날 때 하지도 않은 작업이 FULFILLED 로 남고, 벤더까지
    #      맞춰 검증해야 해서 `by_cli` 의 규칙이 두 곳에 복제된다. 또 env 없이 도는 수동 회수
    #      (`--run-id <원래 run_id>`)의 계측이 끊기면 안 된다.
    from data_pipeline.ops import entry as ops_entry

    monkeypatch.setenv("OPS_SFN_STATE_NAME", "NormalizePrice")
    assert ops_entry.task_key_for("normalize-price", None) == "NORMALIZE_PRICE"
    # 미등록 state(뉴스 레인처럼 이름만 있는 경우 포함)여도 CLI 해소는 그대로다.
    monkeypatch.setenv("OPS_SFN_STATE_NAME", "CollectFmpNews")
    assert ops_entry.task_key_for("ingest-price-raw", "kis") == "PRICE_COLLECTION_KIS"
    monkeypatch.delenv("OPS_SFN_STATE_NAME")
    assert ops_entry.task_key_for("ingest-price-raw", "kis") == "PRICE_COLLECTION_KIS"
    # tag-news 는 뉴스 레인 원장 편입(ALPHA-591)으로 재등록 → ALPHA-610 이 직접 계측으로 승격.
    # 이제 attempt 결측은 정상이 아니라 LEDGER_GAP 이다(Reconciler backfill 은 백스톱).
    assert ops_entry.task_key_for("tag-news", None) == "TAG_NEWS"
    # KRX·공시 수집은 등록·**직접 계측** 대상이다(ALPHA-578 등록 → ALPHA-596 계측).
    assert ops_entry.task_key_for("ingest-raw-etf", "krx") == "ETF_HOLDINGS_COLLECTION_KRX"
    # 공시는 ops 원장 밖이다(ALPHA-875) — by_cli 와 같은 축으로 None 이 정답이다.
    assert ops_entry.task_key_for("ingest-raw-disclosure", None) is None
    assert ops_entry.task_key_for("ingest-raw-financial", "dart") is None   # 미등록 = 통과


def test_env_state_that_contradicts_the_step_is_not_trusted(monkeypatch, caplog):
    # WHY: env 를 무조건 믿으면 이 컨테이너가 한 일을 **다른 작업의 attempt 로** 기록하고,
    #      성공 시 그 작업을 FULFILLED 로 만든다 — 실행되지 않은 작업이 완료로 남아 원장이
    #      오염된다. 정상 SFN 경로에선 command·env 가 같이 주입돼 안 갈리므로, 갈렸다면
    #      수동 override·드리프트다. 조용히 따르지 말고 드러낸 뒤 CLI 로 해소한다(Rule 12).
    import logging

    from data_pipeline.ops import entry as ops_entry

    monkeypatch.setenv("OPS_SFN_STATE_NAME", "NormalizePrice")
    with caplog.at_level(logging.WARNING):
        resolved = ops_entry.task_key_for("load-price-daily", None)
    assert resolved == "LOAD_PRICE_DAILY"      # 실제로 돌린 작업으로 해소
    assert "불일치" in caplog.text

    # state 이름만 바뀌고 카탈로그가 안 따라온 경우도 드리프트다 — 한쪽만 검사하면 알람이
    # 반쪽이라 "덮고 있다"는 착각만 준다. 정상 경로(둘 다 같은 작업)는 조용해야 한다.
    caplog.clear()
    monkeypatch.setenv("OPS_SFN_STATE_NAME", "NormalizePriceV2")   # 카탈로그에 없는 이름
    with caplog.at_level(logging.WARNING):
        assert ops_entry.task_key_for("normalize-price", None) == "NORMALIZE_PRICE"
    assert "불일치" in caplog.text

    caplog.clear()
    monkeypatch.setenv("OPS_SFN_STATE_NAME", "NormalizePrice")
    with caplog.at_level(logging.WARNING):
        assert ops_entry.task_key_for("normalize-price", None) == "NORMALIZE_PRICE"
    assert caplog.text == ""


def test_serial_states_inject_ops_env():
    # WHY: 인라인 직렬 작업은 페이즈 빌더 밖이라 `OPS_SFN_STATE_NAME` 주입이 빠져 있었다.
    #      없으면 그 attempt 의 sfn_state_name·실행 ARN 이 NULL 로 남아 attempt↔SFN 계보가
    #      끊긴다(Reconciler 가 backfill 때 일부러 채우는 그 계보). 뉴스 SFN 의 직렬 2개도
    #      같은 축이다(ALPHA-591 — LOAD_ASSERTIONS·ASSEMBLE_EVENTS 재등록으로 실질화).
    tf = _combined_tf()
    for state in ("LoadInstruments", "EnrichCorpCode", "NewsLoadAssertions", "NewsAssembleEvents"):
        assert f'{{ Name = "OPS_SFN_STATE_NAME", Value = "{state}" }}' in tf, \
            f"{state} 에 OPS_SFN_STATE_NAME 주입이 없다"


def test_dependencies_encode_the_asl_gates():
    """WHY: 의존은 "언제 실행 가능해졌나"의 SSOT 이고, 그게 MISSED 와 BLOCKED 를 가른다.
    ASL 게이트와 어긋나면 게이트가 닫힌 런에서 원인을 오귀속한다(같은 원인이 작업마다 다른
    라벨로 남는다). 정제는 의존을 비워 둔다 — raw 부분실패는 뒤를 막지 않는다(ADR-0030)."""
    assert catalog.get("PRICE_COLLECTION_KIS").depends_on == ()
    for e in catalog.entries():
        if e.stage == "normalize":
            assert e.depends_on == () or e.task_key == "NORMALIZE_PRICE", \
                f"{e.task_key}: 정제에 raw 의존을 걸면 수집 실패 런에서 실제로 성공한 정제가 BLOCKED 다"
    # 시장 feature 로더 5개는 같은 게이트(EnrichCorpCode 직렬 뒤) 아래에 있다 — 하나만
    # 다르면 안 된다. LOAD_DISCLOSURE 는 ALPHA-724 로 공시 레인이라 여기 없다(그 레인의
    # 자체 게이트를 따른다 — 시장 EnrichCorpCode 의존은 레인 밖이라 복사할 수 없다).
    gate = {"LOAD_PRICE_DAILY", "LOAD_PRICE_TRIGGERS", "LOAD_ETF_NAV", "LOAD_ETF_HOLDINGS",
            "LOAD_ETF_FLOW"}
    for key in gate:
        assert catalog.get(key).depends_on == ("ENRICH_CORP_CODE",), key
    assert catalog.get("ENRICH_CORP_CODE").depends_on == ("LOAD_INSTRUMENTS",)
    # 정제 전량 성공 게이트(ASL `NormalizeCheckResults`) — **개수가 아니라 정확한 집합**을
    # terraform 에서 도출해 대조한다(edge-review). 길이만 보면 게이트 멤버가 **바뀌어도**
    # 통과한다: 예컨대 `market_excluded_states` 에서 공시 정제 대신 투자자 정제를 빼면 시장
    # SFN 은 투자자를 안 돌리는데 원장은 여전히 NORMALIZE_INVESTOR 를 기다려, 그 런이 영영
    # 미충족으로 BLOCKED 다. 그 어긋남이 이 테스트가 막으려는 바로 그 종류의 결함이다.
    assert set(catalog.get("LOAD_INSTRUMENTS").depends_on) == _market_normalize_task_keys()
    # 공시 레인 게이트(ASL `DisclosureNormalizeCheckResults`) — 같은 축을 자기 레인으로 그린다.
    # LOAD_DISCLOSURE 의 의존은 이제 카탈로그가 아니라 **Worker 의 체인 순서**가 진다
    # (ALPHA-875 — 한 window 가 collect→normalize×2→load→assemble 을 순차로 돈다). 엔트리가 없으므로
    # 여기서 볼 것도 없다: 그 순서가 깨지는지는 `test_disclosure_worker` 가 본다.
    assert catalog.get("LOAD_DISCLOSURE") is None
    # 뉴스 레인(ALPHA-591)의 의존은 **뉴스 SFN 의 게이트 축**이다 — 옛 시장 의존(LOAD_ASSERTIONS
    # ← feature 7개, LOAD_DOCUMENTS ← ENRICH_CORP_CODE)을 복사하면 뉴스 런에 존재하지 않는
    # 작업을 기다려 영영 eligible 이 안 되고, hard deadline 뒤 전부 BLOCKED 로 오귀속된다.
    assert catalog.get("TAG_NEWS").depends_on == ("NORMALIZE_NEWS",)          # NewsNormalizeCheck
    assert catalog.get("LOAD_DOCUMENTS").depends_on == ("NORMALIZE_NEWS",)
    assert catalog.get("LOAD_ASSERTIONS").depends_on == ("TAG_NEWS", "LOAD_DOCUMENTS")
    assert catalog.get("ASSEMBLE_EVENTS").depends_on == ("LOAD_ASSERTIONS",)  # 직렬 꼬리
    # 레인 무결성 — 의존은 자기 레인 안에서만 성립한다. 레인을 넘으면 그 선행은 이 런의
    # expected_task 에 없어 영영 미충족이다(evidence 도 다른 SFN 실행에 있다).
    for e in catalog.entries():
        for dep in e.depends_on:
            assert catalog.get(dep).pipeline_type == e.pipeline_type, \
                f"{e.task_key}: 레인 밖 의존 {dep}"
    # 뉴스 레인은 전부 비거래일에도 DUE 다 — 뉴스 SFN 은 공휴일에도 돈다(ALPHA-181 함정 방지).
    for e in catalog.entries("news"):
        assert e.kr_trading_calendar is False, e.task_key
    # 참조 무결성 — 없는 task_key 를 가리키면 그 작업은 영원히 eligible 이 안 된다.
    keys = {e.task_key for e in catalog.entries()}
    for e in catalog.entries():
        assert set(e.depends_on) <= keys, f"{e.task_key}: 미등록 의존 {set(e.depends_on) - keys}"
