"""사건 유형 라벨 완전성 게이트 (ALPHA-942).

이 테스트가 곧 라벨 자산의 유지 계약이다: 상류 스냅샷 통째 교체로 새 타입이
들어오면 여기서 깨져 라벨 추가를 강제한다(fail-loud). 라벨이 registry 보다
많아도(고아) 깨진다 — 죽은 라벨이 어휘를 오염시키지 않게.
"""
from __future__ import annotations

from edge_ontology import event_type_label_ko, event_type_labels_ko, load_process_registry


def test_every_registry_type_has_an_exact_korean_label():
    registry = load_process_registry()
    labels = event_type_labels_ko()

    missing = sorted(set(registry.types) - set(labels))
    orphans = sorted(set(labels) - set(registry.types))
    # family 폴백 키도 registry 의 실제 family 집합과 대조한다 - 키가 오타·개명으로
    # 어긋나면 그 계열의 새 타입이 계열 폴백 대신 일반어로 조용히 퇴행한다.
    from edge_ontology.labels import _payload
    families = set(_payload()[1])
    registry_families = {t.family for t in registry.types.values()}
    assert families == registry_families, (
        sorted(families - registry_families), sorted(registry_families - families))

    assert not missing, (
        f"라벨 없는 사건 유형 {len(missing)}종 — 상류 스냅샷 교체로 새 타입이 왔다면 "
        f"resources/labels/event_type_labels_ko.yaml 에 라벨을 추가하라: {missing}")
    assert not orphans, f"registry 에 없는 고아 라벨: {orphans}"


def test_labels_are_prose_safe_noun_phrases():
    """라벨은 "과거에 {라벨} 소식이 있었던" 자리에 들어간다 — 허용 문자셋을
    **한글 음절·공백·가운뎃점**으로 닫는다(화이트리스트). 영문·한자·숫자·특수문자
    금지 목록 방식은 우회가 남는다 — 코드 원문·통계 어휘가 섞이면 산문 개편
    (ALPHA-943)의 목적이 무너진다."""
    import re

    from edge_ontology.labels import _payload

    allowed = re.compile(r"^[가-힣·]+(?:[ ][가-힣·]+)*$")
    types, families = _payload()
    # family 라벨도 폴백으로 같은 산문 자리에 노출된다 - 같은 잣대로 검사한다.
    for source in (types, families):
        for type_id, label in source.items():
            assert allowed.fullmatch(label), (type_id, label, "한글 명사구만 허용")
            assert len(label) <= 14, (type_id, label, "명사구가 너무 길다")


def test_lookup_falls_back_visibly_not_silently():
    exact = event_type_label_ko("COMPANY.WORKFORCE.LAYOFF")
    assert exact == ("인력 감축", True)

    family = event_type_label_ko("COMPANY.SOMETHING.BRAND_NEW")
    assert family == ("기업 관련", False)

    unknown = event_type_label_ko("NEW_FAMILY.THING")
    assert unknown.exact is False and unknown.text
