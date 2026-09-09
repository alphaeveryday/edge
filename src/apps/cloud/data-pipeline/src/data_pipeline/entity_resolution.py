"""엔티티 해소 — assertion argument 텍스트 → instrument entity_id (ALPHA-375).

tag-news 의 argument `text` 는 기사 원문 표현("삼성전자"·"005930")이고,
`assertion_argument.entity_id` 는 NOT NULL + FK 라 해소 없이는 한 건도 못 넣는다.

**규칙은 코드가 답한다(Rule 5) — LLM 재호출 금지.** 마스터 축 3개와 실측 별칭 축:
  (a) 티커(6자리, `instrument.ticker`)      → instrument_id
  (b) 회사 정식명(발행사 entity display_name) → 그 회사 보통주 instrument_id
  (c) 종목/ETF display_name                  → instrument_id

**해소 결과는 항상 instrument 엔티티다** — 다운스트림 `event_argument ⋈ instrument`
조인과 분석엔진 entity_index(ticker→instrument_id) 관례에 맞춘다. 회사명이 와도
회사(actor)가 아니라 그 발행사의 주식으로 해소한다.

동명 충돌(한 키가 서로 다른 엔티티 2개)은 **미해소(ambiguous)** 다 — 아무거나 고르면
그 순간 조용히 틀린다. 별칭은 정상 런에서 확인된 같은 상장사의 정식명 변형만 canonical
마스터가 실제 존재할 때 붙인다. 유사도·그룹/브랜드 귀속은 하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# 충돌 표식 — dict 값이 None 이면 "그 키는 두 엔티티가 다퉜다"는 뜻이다.
_AMBIGUOUS = None

RESOLVED = "resolved"
ALIAS_RESOLVED = "alias_resolved"
UNRESOLVED = "unresolved"
AMBIGUOUS = "ambiguous"

# 2026-09-07 dev 정상 런의 상위 미해소 중, 같은 상장사를 가리키는 것이 결정적인 정식명·
# 상호 표기만 둔다(ALPHA-1067). 그룹명·브랜드·비상장 자회사처럼 상장 instrument 귀속이
# 해석인 표현(삼성·포스코·CJ온스타일·GS25)은 넣지 않는다.
_INSTRUMENT_ALIASES = {
    "IBK기업은행": "기업은행",
    "중소기업은행": "기업은행",
    "현대자동차": "현대차",
    "LS일렉트릭": "LS ELECTRIC",
    "한국전력공사": "한국전력",
}


@dataclass(frozen=True)
class ResolutionIndex:
    """정규화 키 → instrument entity_id (None = 동명 충돌)."""

    by_key: dict[str, str | None]
    by_ticker: dict[str, str] = field(default_factory=dict)
    alias_ticker_by_key: dict[str, str] = field(default_factory=dict)
    alias_keys: frozenset[str] = frozenset()


def _normalize(text: str) -> str:
    """완전일치 전 최소 정규화 — 앞뒤·내부 연속 공백만 접는다."""
    return " ".join(text.split())


def load_resolution_index(conn) -> ResolutionIndex:
    """엔티티 마스터 1쿼리로 해소 인덱스를 만든다.

    키 공간: instrument.ticker · 발행사 entity.display_name · instrument entity.display_name.
    같은 키가 서로 다른 instrument_id 로 두 번 오면 충돌 표식으로 바꾼다 — 나중 행이
    조용히 이기게 두면 어느 종목으로 해소됐는지가 적재 순서에 달리게 된다.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT i.instrument_id, i.ticker, ie.display_name, ae.display_name,"
            " ep.share_class_code"
            " FROM instrument i"
            " JOIN entity ie ON ie.entity_id = i.instrument_id"
            " LEFT JOIN equity_profile ep ON ep.instrument_id = i.instrument_id"
            " LEFT JOIN entity ae ON ae.entity_id = ep.issuer_actor_id"
        )
        rows = cur.fetchall()

    by_key: dict[str, str | None] = {}
    by_ticker: dict[str, str] = {}
    for instrument_id, ticker, instrument_name, issuer_name, share_class in rows:
        by_ticker[str(ticker)] = str(instrument_id)
        # 회사명 키는 보통주에만 건다 — 우선주가 있는 발행사에서 회사명이 두 종목으로
        # 갈려 ambiguous 가 되면 "회사명 → 그 회사 보통주" 약속이 깨진다. 우선주는
        # 자기 티커·종목명으로는 여전히 해소된다.
        for raw in (ticker, instrument_name, issuer_name if share_class == "COMMON" else None):
            if not raw:
                continue
            key = _normalize(str(raw))
            if not key:
                continue
            if key not in by_key:
                by_key[key] = str(instrument_id)
            elif by_key[key] != str(instrument_id):
                by_key[key] = _AMBIGUOUS

    alias_ticker_by_key: dict[str, str] = {}
    alias_keys: set[str] = set()
    ticker_by_entity = {entity_id: ticker for ticker, entity_id in by_ticker.items()}
    for raw_alias, raw_target in _INSTRUMENT_ALIASES.items():
        alias, target = _normalize(raw_alias), _normalize(raw_target)
        entity_id = by_key.get(target)
        # 타깃이 없거나 충돌이면 별칭도 미해소다. 정적 별칭 때문에 없는 마스터를 지어내거나
        # 충돌을 숨기지 않는다.
        if entity_id is None:
            continue
        existing = by_key.get(alias)
        if alias in by_key and existing != entity_id:
            by_key[alias] = _AMBIGUOUS
            continue
        if alias in by_key:
            # 실제 마스터 이름이면 별칭 전용 경로로 강등하지 않는다. 1분 조립은 기사 종목
            # 범위를 받지 않아 curated alias를 쓰지 않지만, 마스터 완전일치는 계속 해소해야 한다.
            # 배치 조립은 기사 허용 ticker를 검증하므로 그 역매핑은 별도로 보존한다.
            ticker = ticker_by_entity.get(entity_id)
            if ticker is not None:
                alias_ticker_by_key[alias] = ticker
            continue
        by_key[alias] = entity_id
        alias_keys.add(alias)
        ticker = ticker_by_entity.get(entity_id)
        if ticker is not None:
            alias_ticker_by_key[alias] = ticker
    return ResolutionIndex(
        by_key=by_key,
        by_ticker=by_ticker,
        alias_ticker_by_key=alias_ticker_by_key,
        alias_keys=frozenset(alias_keys),
    )


def resolve(
    index: ResolutionIndex, text: object, *, allow_aliases: bool = False,
) -> tuple[str | None, str]:
    """argument 텍스트 1건을 **instrument 인덱스로** 해소 — (entity_id | None, 사유).

    **이 함수는 역할을 모른다** — 텍스트를 instrument 인덱스에 대볼 뿐이다. 역할별 축
    분기는 `plan_resolution` 이 하고, 배치 적재(`load_assertions`)는 그쪽을 쓴다.
    curated alias는 호출부가 명시적으로 허용할 때만 쓴다. 1분 실시간 레인은 기사별 허용
    ticker를 받지 않으므로 기본값(False)을 유지해 다른 종목 기사의 비교 대상을 계보에
    붙이지 않는다. 배치 조립은 `resolve_alias_ticker`로 허용 ticker를 별도 검증한다.

    사유는 호출부(로더)가 quality log 에 분포로 남긴다(Rule 12 — 미해소는 침묵하지
    않는다). 비문자열·공백뿐 텍스트는 unresolved 다.
    """
    if not isinstance(text, str):
        return None, UNRESOLVED
    key = _normalize(text)
    if not key or key not in index.by_key:
        return None, UNRESOLVED
    if key in index.alias_keys and not allow_aliases:
        return None, UNRESOLVED
    entity_id = index.by_key[key]
    if entity_id is _AMBIGUOUS:
        return None, AMBIGUOUS
    return entity_id, ALIAS_RESOLVED if key in index.alias_keys else RESOLVED


def resolve_alias_ticker(index: ResolutionIndex, text: object) -> str | None:
    """별칭의 canonical ticker. assemble-events가 기사 허용 ticker 안에서만 쓸 수 있게 분리한다."""
    if not isinstance(text, str):
        return None
    return index.alias_ticker_by_key.get(_normalize(text))


# 해소 계획 사유 — quality log 의 축이 된다.
MINTED = "minted"
REGISTRY_HIT = "registry_hit"
REGISTRY_MISS = "registry_miss"
NOT_RESOLVABLE = "not_resolvable"
CONCEPT_REJECTED = "concept_rejected"


def mint_concept(role_code: str, mention: str) -> tuple[str, str] | None:
    """멘션 → (concept entity_id, 정규화 키). 채번 대상이 아니면 None.

    ⭐**두 writer 의 유일한 채번 지점이다**(`load_assertions`·`assemble_events`). 산식을
    각자 조립하면 — 같은 `concept_key`·`stable_domain_id` 를 쓰더라도 — 접두사나 인자
    하나가 갈리는 순간 같은 개념에 ID 가 둘 생기고 조인이 조용히 끊긴다. ALPHA-456 이
    `assertion_id` 에서 겪은 그 일이라, "같은 함수를 쓴다"를 **호출부 합의가 아니라
    한 함수**로 강제한다(ALPHA-831).

    ⚠️ **정책을 호출부에 두지 마라.** 한때 "무엇을 개념으로 볼 것인가는 호출부마다 다를
    수 있다"는 근거로 척도 제외·길이 상한을 `plan_resolution` 에 뒀는데, `assemble_events`
    에는 안 걸려 **같은 멘션이 입력 경로에 따라 갈렸다**(ALPHA-861 이 되돌렸다). 정책이
    필요하면 두 writer 를 한 번에 덮는 `concept_key`(온톨로지)에 둔다 — 그 판단은
    ALPHA-859. 여기는 산식만 소유한다.
    """
    from edge_ontology import concept_key

    from .db import stable_domain_id

    key = concept_key(role_code, mention)
    if not key:
        return None
    return stable_domain_id("concept", key), key


def plan_resolution(
    index: ResolutionIndex, role_code: str, text: object
) -> tuple[str | None, str, tuple[str, str] | None]:
    """역할 하나를 계약대로 해소한다 — (entity_id | None, 사유, 채번할 개념 | None).

    ⭐**새 규칙을 만들지 않는다.** 어느 축으로 갈지는 온톨로지가 이미 정해 뒀고
    (`role_bindings_v0_1.yaml` 의 `identity`), 이 함수는 그 표를 읽어 갈래를 탈 뿐이다:

      REGISTRY  `resolve_authority(role, mention)` — 시드된 명부 조회. **못 찾으면 채번하지
                않는다**(온톨로지 근거: 지어내면 같은 기관이 표기마다 다른 엔티티가 된다).
                단 `mint_fallback` 이 선 역할(EXCHANGE·MARKET)은 아래 채번으로 내려간다.
      MINT      `mint_concept` — **assemble-events 와 같은 그 함수**를 부른다. "같은 원시
                함수를 각자 조립"으로는 부족했다: 접두사 하나만 달라도 산식이 갈려 같은
                개념에 ID 가 둘 생기고 조인이 조용히 끊긴다(ALPHA-456 이 겪은 실패 양식).
      NONE      instrument 인덱스(`resolve`). 못 붙으면 미해소 — 채번하면 상장사가 유령으로
                갈린다는 게 온톨로지가 NONE 을 고른 이유다.

    세 번째 반환값은 **채번한 개념**이다 `(display_name, concept_type)`. 호출부가
    entity(CONCEPT) → concept → assertion_argument 순서로 FK 를 세운다 — 이 함수는 DB 를
    모른다(순수 함수라 테스트가 쉽고, 트랜잭션 경계는 호출부 소유다).
    """
    from edge_ontology import load_relations, resolve_authority

    relation = load_relations().get(role_code)
    if relation is None or not relation.is_entity:
        # 비실체·어휘 밖 — 애초에 해소 대상이 아니다(ALPHA-802 가 계측에서 분리해 둔다).
        return None, NOT_RESOLVABLE, None

    # ⚠️ `strip()` 이다 — `assemble_events` 가 채번 개념의 display_name 으로 쓰는 것과
    # **같은 식**이어야 한다. 둘 다 같은 entity_id 에 ON CONFLICT DO NOTHING 으로 쓰므로,
    # 식이 다르면 먼저 쓴 쪽이 이겨 display_name 이 실행 순서를 탄다(ALPHA-538 이
    # document_assertion 에서 없앤 바로 그 의존). 해시 키는 concept_key 가 따로 정규화한다.
    mention = text.strip() if isinstance(text, str) else ""

    # ── REGISTRY: 명부에서 찾기만 한다(새 엔티티를 만들지 않는다)
    if load_relations().sections_for(role_code):
        entity_id = resolve_authority(role_code, mention) if mention else None
        if entity_id:
            return entity_id, REGISTRY_HIT, None
        if not load_relations().can_mint(role_code):
            # mint_fallback 이 없는 순수 명부 역할 — 미등재거나 '당국' 같은 모호어다.
            return None, REGISTRY_MISS, None
        # EXCHANGE·MARKET 은 미등재 해외 거래소를 위해 채번까지 간다(온톨로지 명시).

    # ── NONE: 티커 축
    if not load_relations().can_mint(role_code):
        return (*resolve(index, text, allow_aliases=True), None)

    # ── MINT: 채번. **온톨로지가 정한 것만 판단한다** — 어떤 멘션을 개념으로 볼지는
    # `concept_key` 소관이고(하한·숫자만 배제), 이 함수는 그 판정을 그대로 따른다.
    # ⚠️ 한때 여기에 척도 역할 제외와 길이 상한이 있었다(ALPHA-831). 온톨로지에 근거가
    # 없었고, `assemble_events` 에는 안 걸려 **같은 멘션이 입력 경로에 따라 갈렸다** —
    # 정책이 필요한지는 ALPHA-859 가 판단하고, 필요하면 두 writer 를 한 번에 덮도록
    # `concept_key` 에 둔다. 여기(호출부)에 두면 그 갈림이 그대로 재발한다(ALPHA-861).
    coined = mint_concept(role_code, mention)
    if coined is None:
        return None, CONCEPT_REJECTED, None
    entity_id, _key = coined
    return entity_id, MINTED, (mention, relation.entity_kind)
