"""Task Catalog 테스트 (ALPHA-530) — 스펙 §9 시나리오 24(카탈로그↔ASL 매핑) 포함."""

from __future__ import annotations

import re
from pathlib import Path

from data_pipeline.ops import catalog

_STATEMACHINE_TF = (
    Path(__file__).resolve().parents[5]
    / "infra/terraform/modules/data-pipeline/statemachine.tf"
)


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
_NOT_INSTRUMENTED = {
    "CollectFmpNews": "fmp task-def 에 DB env 없음(부분 주입 불가 — 시크릿 경계 변경 필요)",
    "CollectFmpPrice": "fmp task-def 에 DB env 없음",
    "CollectFmpFinancial": "fmp task-def 에 DB env 없음",
    "CollectFmpEtf": "fmp task-def 에 DB env 없음",
    "CollectDartFinancial": "dart task-def 에 DB env 없음",
    "CollectDartDisclosure": "dart task-def 에 DB env 없음",
    "CollectKrxEtf": "krx task-def 에 DB env 없음",
    "AnalyzeOne": "다른 이미지(run.py 미경유)·Map 팬아웃 31종이 한 state 로 뭉쳐 거짓 초록",
}


def _asl_task_states(tf: str) -> set[str]:
    return set(_ASL_JOB_STATE.findall(tf)) | set(_ASL_INLINE_STATE.findall(tf))


def test_catalog_and_asl_task_states_match_both_ways():
    """시나리오 24 확장 — 카탈로그↔ASL 를 **양방향** 정확 대조한다(ALPHA-181).

    WHY: 기존 검사는 `entry.sfn_state_name in tf`(부분 문자열)라 사실상 항상 통과하는 공허한
    절이었고, 역방향이 없어 **새 SFN 잡이 추가돼도 아무도 모르게 미계측**으로 남았다. 역방향에
    화이트리스트를 두면 잡을 추가한 사람이 "등록할지 제외할지"를 CI 에서 마주친다.
    """
    tf = _STATEMACHINE_TF.read_text(encoding="utf-8")
    asl_states = _asl_task_states(tf)
    assert len(asl_states) == 33, f"ECS Task state 수가 바뀌었다: {len(asl_states)}"

    registered = {e.sfn_state_name for e in catalog.entries()}
    assert registered <= asl_states, f"ASL 에 없는 state 등록: {registered - asl_states}"
    uncovered = asl_states - registered - set(_NOT_INSTRUMENTED)
    assert not uncovered, f"등록도 제외도 안 된 state: {uncovered} — 카탈로그에 넣거나 이유를 달아라"
    assert registered.isdisjoint(_NOT_INSTRUMENTED), "제외 목록과 등록이 겹친다"
    assert len(registered) == 25  # 커버리지를 숫자로 고정 — 조용한 축소 금지(Rule 12)
    # 자기 기록이 불가능한데도 등록한 것은 **게이트 멤버**뿐이다 — 빠지면 의존 판정이 거짓이 된다.
    assert {e.task_key for e in catalog.entries() if not e.instrumented} == {"TAG_NEWS"}


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
    tf = _STATEMACHINE_TF.read_text(encoding="utf-8")
    asl = {m.group("state"): (m.group("taskdef"), _asl_command_args(m.group("cmd")))
           for m in _ASL_JOB_TRIPLE.finditer(tf)}
    asl.update({m.group("state"): (m.group("taskdef"), _asl_command_args(m.group("cmd")))
                for m in _ASL_INLINE_CMD.finditer(tf)})
    assert len(asl) >= 32, f"ASL 삼중항 파싱 실패: {len(asl)}"

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
    # TagNews 는 등록됐지만 자기 기록이 불가능하다(instrumented=False) — 해소는 되고, 그
    # 컨테이너엔 원장 설정이 없어 wrapper 가 투명 통과한다.
    assert ops_entry.task_key_for("tag-news", None) == "TAG_NEWS"
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
    # WHY: 인라인 직렬 4작업은 페이즈 빌더 밖이라 `OPS_SFN_STATE_NAME` 주입이 빠져 있었다.
    #      없으면 그 attempt 의 sfn_state_name·실행 ARN 이 NULL 로 남아 attempt↔SFN 계보가
    #      끊긴다(Reconciler 가 backfill 때 일부러 채우는 그 계보).
    tf = _STATEMACHINE_TF.read_text(encoding="utf-8")
    for state in ("LoadInstruments", "EnrichCorpCode", "LoadAssertions", "AssembleEvents"):
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
    # feature 7개는 같은 게이트(EnrichCorpCode 직렬 뒤) 아래에 있다 — 하나만 다르면 안 된다.
    gate = {"LOAD_PRICE_DAILY", "LOAD_PRICE_TRIGGERS", "LOAD_ETF_NAV", "LOAD_ETF_HOLDINGS",
            "LOAD_ETF_FLOW", "LOAD_DOCUMENTS", "LOAD_DISCLOSURE"}
    for key in gate:
        assert catalog.get(key).depends_on == ("ENRICH_CORP_CODE",), key
    # FeatureCheckResults 게이트는 로더 7개 + **TagNews** 다. TagNews 를 빼면 그것만 죽은 런에서
    # 의존이 전부 충족된 것으로 보여 BLOCKED 여야 할 것이 MISSED 로 찍힌다(Codex #273 P1).
    assert set(catalog.get("LOAD_ASSERTIONS").depends_on) == gate | {"TAG_NEWS"}
    assert catalog.get("ASSEMBLE_EVENTS").depends_on == ("LOAD_ASSERTIONS",)
    assert catalog.get("ENRICH_CORP_CODE").depends_on == ("LOAD_INSTRUMENTS",)
    assert len(catalog.get("LOAD_INSTRUMENTS").depends_on) == 8   # 정제 전량 성공 게이트
    # 참조 무결성 — 없는 task_key 를 가리키면 그 작업은 영원히 eligible 이 안 된다.
    keys = {e.task_key for e in catalog.entries()}
    for e in catalog.entries():
        assert set(e.depends_on) <= keys, f"{e.task_key}: 미등록 의존 {set(e.depends_on) - keys}"
