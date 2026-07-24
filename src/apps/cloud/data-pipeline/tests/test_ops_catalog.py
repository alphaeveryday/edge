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


def test_dependencies_form_the_price_chain():
    assert catalog.get("PRICE_COLLECTION_KIS").depends_on == ()
    assert catalog.get("NORMALIZE_PRICE").depends_on == ("PRICE_COLLECTION_KIS",)
    assert catalog.get("LOAD_PRICE_DAILY").depends_on == ("NORMALIZE_PRICE",)
