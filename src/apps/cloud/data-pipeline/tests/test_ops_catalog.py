"""Task Catalog 테스트 (ALPHA-530) — 스펙 §9 시나리오 24(카탈로그↔ASL 매핑) 포함."""

from __future__ import annotations

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


def test_catalog_sfn_states_exist_in_real_asl():
    """시나리오 24 — 세 작업의 sfn_state_name 이 실제 ASL 정의에 존재한다(드리프트 방지)."""
    tf = _STATEMACHINE_TF.read_text(encoding="utf-8")
    for entry in catalog.entries():
        assert f'"{entry.sfn_state_name}"' in tf or f"= \"{entry.sfn_state_name}\"" in tf \
            or entry.sfn_state_name in tf, f"{entry.sfn_state_name} 가 ASL 에 없다"


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
    assert ops_entry.task_key_for("normalize-investor", None) is None   # 미등록 = 통과


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


def test_dependencies_form_the_price_chain():
    assert catalog.get("PRICE_COLLECTION_KIS").depends_on == ()
    assert catalog.get("NORMALIZE_PRICE").depends_on == ("PRICE_COLLECTION_KIS",)
    assert catalog.get("LOAD_PRICE_DAILY").depends_on == ("NORMALIZE_PRICE",)
