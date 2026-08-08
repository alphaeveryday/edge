"""엔티티 해소 — assertion argument 텍스트 → instrument entity_id (ALPHA-375).

tag-news 의 argument `text` 는 기사 원문 표현("삼성전자"·"005930")이고,
`assertion_argument.entity_id` 는 NOT NULL + FK 라 해소 없이는 한 건도 못 넣는다.

**규칙은 코드가 답한다(Rule 5) — LLM 재호출 금지.** 완전일치 축 3개:
  (a) 티커(6자리, `instrument.ticker`)      → instrument_id
  (b) 회사 정식명(발행사 entity display_name) → 그 회사 보통주 instrument_id
  (c) 종목/ETF display_name                  → instrument_id

**해소 결과는 항상 instrument 엔티티다** — 다운스트림 `event_argument ⋈ instrument`
조인과 분석엔진 entity_index(ticker→instrument_id) 관례에 맞춘다. 회사명이 와도
회사(actor)가 아니라 그 발행사의 주식으로 해소한다.

동명 충돌(한 키가 서로 다른 엔티티 2개)은 **미해소(ambiguous)** 다 — 아무거나 고르면
그 순간 조용히 틀린다. 별칭·유사도 매칭은 완전일치 해소율 실측 후 별건(티켓 범위).
"""

from __future__ import annotations

from dataclasses import dataclass

# 충돌 표식 — dict 값이 None 이면 "그 키는 두 엔티티가 다퉜다"는 뜻이다.
_AMBIGUOUS = None

RESOLVED = "resolved"
UNRESOLVED = "unresolved"
AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class ResolutionIndex:
    """정규화 키 → instrument entity_id (None = 동명 충돌)."""

    by_key: dict[str, str | None]


def _normalize(text: str) -> str:
    """완전일치 전 최소 정규화 — 앞뒤·내부 연속 공백만 접는다. 그 이상(법인 접미사
    제거·대소문자 등)은 별칭 축이라 해소율 실측 후 판단한다."""
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
    for instrument_id, ticker, instrument_name, issuer_name, share_class in rows:
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
    return ResolutionIndex(by_key=by_key)


def resolve(index: ResolutionIndex, text: object) -> tuple[str | None, str]:
    """argument 텍스트 1건을 **instrument 인덱스로** 해소 — (entity_id | None, 사유).

    **이 함수는 역할을 모른다** — 텍스트를 instrument 인덱스에 대볼 뿐이다. 역할별 축
    분기는 `plan_resolution` 이 하고, 배치 적재(`load_assertions`)는 그쪽을 쓴다.
    ⚠️ 다만 이 함수가 티커 축 전용인 것은 **아니다**: 1분 실시간 레인
    (`minute/event_assembly`)은 아직 역할과 무관하게 여기로 온다 — 그쪽 전환은 ALPHA-852.

    사유는 호출부(로더)가 quality log 에 분포로 남긴다(Rule 12 — 미해소는 침묵하지
    않는다). 비문자열·공백뿐 텍스트는 unresolved 다.
    """
    if not isinstance(text, str):
        return None, UNRESOLVED
    key = _normalize(text)
    if not key or key not in index.by_key:
        return None, UNRESOLVED
    entity_id = index.by_key[key]
    if entity_id is _AMBIGUOUS:
        return None, AMBIGUOUS
    return entity_id, RESOLVED


# 채번할 개념 이름의 길이 상한(정규화 후 문자 수). 온톨로지에 하한(MIN_CONCEPT_CHARS=2)은
# 있는데 상한이 없다 — 짧은 이름엔 무해하지만 긴 값에선 두 가지가 깨진다(ALPHA-831 실측):
#
# **개념이 아니라 사건 인스턴스가 된다.** "차세대 모빌리티 개발 및 해외시장 진출 활성화를
# 위한 상생 금융지원 업무협약" 은 그 협약 한 건이지 재사용되는 개념이 아니다. MINT 축
# 37,229건 중 **30자 초과는 3.3%(1,210건)뿐**이고 그 구간 고유율이 87%다 — 거의 전부 한 번
# 쓰이고 만다.
#
# ⚠️ **이 상한이 사는 것은 "이 스텝의 argument 가 그 행을 만들지 않는다"까지다.** 채번 함수
# (`mint_concept`)에는 상한이 없고 assemble-events 도 안 건다 — 문장꼴 멘션은 event 조립
# 경로로는 여전히 `entity`/`concept` 에 들어온다. 마스터 자체를 지키려면 상한이 채번 함수나
# 온톨로지로 올라가야 하고, 그건 이 티켓 밖이다(ALPHA-831 은 argument 회수가 범위다).
#
# ⚠️ **이 값은 "무엇을 개념으로 볼 것인가"의 선이라 온톨로지 소관이다.** 여기서 30 을 고른
# 것은 실측 꼬리(3.3%)만 자르는 보수적 값이라서고, 상한에 걸린 건 **미해소로 남긴다** —
# 되돌리기가 싸다(채번해 버리면 참조까지 정리해야 한다). 로그에 잘린 수와 표본을 남기니
# 온톨로지 쪽이 그 근거로 조정하면 된다.
MAX_CONCEPT_CHARS = 30

# 채번하지 않는 역할 — **척도**다. 온톨로지가 이들을 `PRODUCT_OR_CONCEPT` 종에 매핑해
# `kind_default: MINT` 로 흘려보내지만, **그 종 자신의 정의에 척도가 없다**
# (`entity_kinds_v0_1.yaml` 의 `used_for`: "product launches, demand/supply/technology
# standards, commodity/product spillover"). `concept` 테이블 주석("제품·산업·사업부문·
# 테마·매크로")도 마찬가지다.
#
# `영업이익`은 삼성전자의 영업이익이지 그 자체로 서 있는 개체가 아니다 — 측정 축을 개체로
# 세우는 건 모델링 결정이고 계약이 **명시적으로 하지 않았다**(종 매핑의 부수 효과다).
# 그래서 여기선 미해소로 남기고 사유를 카운트한다. 결정은 온톨로지 소관(ALPHA-831 코멘트).
# ⚠️ 넷 다 `PRODUCT_OR_CONCEPT`(실체·MINT)인 것을 어휘에서 확인하고 넣었다. `RATE` 는
# 비실체(VALUE)라 위 is_entity 게이트에서 이미 걸러지므로 여기 넣어도 도달하지 않는다.
MEASURE_ROLES = frozenset({
    "METRIC", "INDICATOR", "POLICY_RATE", "CURRENCY_PAIR",
})

# 해소 계획 사유 — quality log 의 축이 된다.
MINTED = "minted"
REGISTRY_HIT = "registry_hit"
REGISTRY_MISS = "registry_miss"
MEASURE_SKIPPED = "measure_skipped"
TOO_LONG = "concept_too_long"
NOT_RESOLVABLE = "not_resolvable"


def mint_concept(role_code: str, mention: str) -> tuple[str, str] | None:
    """멘션 → (concept entity_id, 정규화 키). 채번 대상이 아니면 None.

    ⭐**두 writer 의 유일한 채번 지점이다**(`load_assertions`·`assemble_events`). 산식을
    각자 조립하면 — 같은 `concept_key`·`stable_domain_id` 를 쓰더라도 — 접두사나 인자
    하나가 갈리는 순간 같은 개념에 ID 가 둘 생기고 조인이 조용히 끊긴다. ALPHA-456 이
    `assertion_id` 에서 겪은 그 일이라, "같은 함수를 쓴다"를 **호출부 합의가 아니라
    한 함수**로 강제한다(ALPHA-831).

    길이 상한은 여기 두지 않는다 — 그건 "무엇을 개념으로 볼 것인가"의 정책이고 호출부마다
    다를 수 있다. 여기는 산식만 소유한다.
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
        return (*resolve(index, text), None)

    # ── MINT: 채번
    if role_code in MEASURE_ROLES:
        return None, MEASURE_SKIPPED, None
    coined = mint_concept(role_code, mention)
    if coined is None:
        return None, UNRESOLVED, None
    entity_id, key = coined
    if len(key) > MAX_CONCEPT_CHARS:
        return None, TOO_LONG, None
    return entity_id, MINTED, (mention, relation.entity_kind)
