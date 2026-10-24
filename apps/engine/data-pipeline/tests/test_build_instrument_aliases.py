"""build_instrument_aliases 스크립트 테스트 (ALPHA-850).

WHY: 별칭 명부는 이 스크립트가 만든 후보로 커진다 — 후보 이름이 잘못 잘리면 실제 사명이 아닌 표기가
그 종목으로 연결된다(검사는 다른 종목과의 충돌만 본다).
"""

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "build_instrument_aliases",
    Path(__file__).resolve().parents[1] / "scripts" / "build_instrument_aliases.py",
)
bia = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bia)


def test_english_legal_tail_is_cut_only_at_a_word_boundary():
    """`KISCO`·`KOREA ZINC` 의 끝(`CO`·`INC`)은 사명의 일부다 — 떼면 `KIS`·`KOREA Z` 라는 없는 이름이 생긴다."""
    def english(name):
        return bia.krx_names({"english_name": name})

    assert english("KISCO") == ["KISCO"]
    assert english("KOREA ZINC") == ["KOREA ZINC"]
    assert english("SeA Mechanics Co., LTD.") == ["SeA Mechanics"]
    assert english("Hwaseung Corporation") == ["Hwaseung"]


def test_korean_legal_name_drops_share_class_and_company_form():
    """법적 명칭 `…보통주`·`주식회사` 는 뉴스가 쓰지 않는 꼬리다 — 떼야 뉴스 표기와 같아진다."""
    assert bia.krx_names({"display_name": "SK하이닉스", "legal_name": "에스케이하이닉스보통주"}) == [
        "SK하이닉스", "에스케이하이닉스"]
    assert bia.krx_names({"legal_name": "해태제과식품주식회사"}) == ["해태제과식품"]
