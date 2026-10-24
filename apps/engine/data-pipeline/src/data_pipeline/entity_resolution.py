"""엔티티 해소 — assertion argument 텍스트 → instrument entity_id (ALPHA-375·850).

tag-news 의 argument `text` 는 기사 원문 표현("삼성전자"·"005930")이고,
`assertion_argument.entity_id` 는 NOT NULL + FK 라 해소 없이는 한 건도 못 넣는다.

**규칙은 코드가 답한다(Rule 5) — LLM 재호출 금지.** 마스터 축 3개:
  (a) 티커(6자리, `instrument.ticker`)      → instrument_id
  (b) 회사 정식명(발행사 entity display_name) → 그 회사 보통주 instrument_id
  (c) 종목/ETF display_name                  → instrument_id

완전일치로 못 찾으면, 호출부가 허용할 때(`allow_aliases`) **표기 매칭**으로 한 번 더 찾는다
(ALPHA-850). 같은 회사를 다르게 쓴 표기만 푼다:
  - 별칭 명부 `instrument_aliases.tsv` — 정규화로 안 풀리는 같은 회사의 다른 이름(현대자동차→현대차).
  - 정규화 키 — NFKC, 법인 표기(`(주)`·`㈜`·`주식회사`)·`보통주`·공백·기호 제거, 영문 글자를 한글
    읽기로(`DB`→`디비`, `&`→`앤`). `DB하이텍`·`디비하이텍`·`DB 하이텍`이 같은 키가 된다.
  - 괄호 병기 — `한국항공우주산업(KAI)` 은 괄호 밖·안을 각각 찾고, 둘이 다른 종목이면 모호다.
  - 끝 조사 — 키에서 조사(`은`·`의`·`측` 등) 하나를 뗀 형태가 마스터 키일 때만.

**이 해소기의 결과는 instrument 엔티티다.** 문서의 종목 매칭과 기존 사건 ID 산식이
이 값을 사용한다. 사건 참여자는 저장 경계에서 `events.participants`가 Equity를
발행 Actor로 변환한다. 따라서 이 해소 결과를 사건 참여 대상의 최종 ID로 해석하지 않는다.

동명 충돌(한 키가 서로 다른 엔티티 2개)은 **미해소(ambiguous)** 다 — 아무거나 고르면
그 순간 조용히 틀린다. 정규화 키도 같다: 두 종목이 같은 키가 되면 그 키로는 둘 다 안 푼다.
부분일치·유사도·그룹/브랜드/자회사 귀속은 하지 않는다 — `삼성전자 노조`·`SK그룹`·`SK온`은
그 상장사가 아니다(틀린 회사에 붙이는 것보다 미해소가 낫다).
"""

from __future__ import annotations

import functools
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

# 충돌 표식 — dict 값이 None 이면 "그 키는 두 엔티티가 다퉜다"는 뜻이다.
_AMBIGUOUS = None

RESOLVED = "resolved"
ALIAS_RESOLVED = "alias_resolved"
UNRESOLVED = "unresolved"
AMBIGUOUS = "ambiguous"

_ALIAS_FILE = Path(__file__).with_name("instrument_aliases.tsv")


@dataclass(frozen=True)
class ResolutionIndex:
    """정규화 키 → instrument entity_id (None = 동명 충돌)."""

    by_key: dict[str, str | None]
    by_ticker: dict[str, str] = field(default_factory=dict)
    alias_ticker_by_key: dict[str, str] = field(default_factory=dict)
    alias_keys: frozenset[str] = frozenset()
    # 표기 매칭 키(`match_key`) → entity_id. 마스터 키와 별칭에서 만든다(ALPHA-850).
    by_match_key: dict[str, str | None] = field(default_factory=dict)


def _normalize(text: str) -> str:
    """완전일치 전 최소 정규화 — 앞뒤·내부 연속 공백만 접는다."""
    return " ".join(text.split())


# ── 표기 매칭(ALPHA-850) ────────────────────────────────────────────────
# 영문 글자의 한글 읽기. 뉴스는 같은 회사를 `DB하이텍`·`디비하이텍`, `LG`·`엘지`로 섞어 쓴다.
# 글자 단위라 영단어(`ELECTRIC`)는 뜻 있는 읽기가 안 되지만 양쪽에 같은 변환이 걸려 일관된다 —
# 영단어 사명의 한글 표기(`LS일렉트릭`)는 별칭 명부가 맡는다.
_LETTER_READING = {
    "a": "에이", "b": "비", "c": "씨", "d": "디", "e": "이", "f": "에프", "g": "지", "h": "에이치",
    "i": "아이", "j": "제이", "k": "케이", "l": "엘", "m": "엠", "n": "엔", "o": "오", "p": "피",
    "q": "큐", "r": "알", "s": "에스", "t": "티", "u": "유", "v": "브이", "w": "더블유", "x": "엑스",
    "y": "와이", "z": "제트", "&": "앤", "+": "플러스",
}
# NFKC 뒤의 법인 표기(`㈜` 는 NFKC 로 `(주)` 가 된다).
_LEGAL_FORM = re.compile(r"\(주\)|\(유\)|주식회사")
_DROP = re.compile(r"[\s·ㆍ・\-_.,'\"‘’“”`]")
# 표기 끝에 붙어 오는 조사·지시어. 뗀 나머지가 마스터 키일 때만 쓴다(완전일치가 먼저다).
_PARTICLES = tuple(sorted({
    "은", "는", "이", "가", "을", "를", "의", "에", "와", "과", "도", "만", "로", "으로", "이나",
    "랑", "이랑", "까지", "부터", "처럼", "보다", "에서", "에게", "께서", "이며", "이자", "에도", "에는",
    "와의", "과의", "로는", "으로는", "에서는", "에서도", "측", "측은", "측이", "측의", "측도", "측에",
    "등", "등이", "등은", "등의", "쪽",
}, key=len, reverse=True))
# 사명 끝 글자로도 흔한 조사(마스터 2,918종 중 `…이` 183·`…로` 32·`…가`·`…도` 6). 떼고 남은 키가 짧으면
# 다른 회사 이름의 앞부분일 수 있다(`레몬도`→`레몬`, 해외 기업). 3글자 이상 남을 때만 뗀다 — 2면 실제 기사
# 개발셋에서 틀린 연결이 생기고, 4면 `네패스가` 같은 짧은 사명을 놓친다(ALPHA-850 측정).
_NAME_FINAL_PARTICLES = frozenset({"이", "가", "도", "로", "과", "을"})
_MIN_STEM_AFTER_NAME_FINAL = 3
# 끝에 붙은 괄호 병기 하나: `밖(안)` 뒤에는 조사만 올 수 있다 — `KGM(옛 쌍용차)그룹` 처럼 괄호 뒤에
# 다른 말이 이어지면 그 표기는 괄호 안의 회사가 아니다.
_PAREN = re.compile(r"^(?P<out>[^()\[\]]+)[(\[](?P<inner>[^()\[\]]+)[)\]](?P<tail>[^()\[\]]*)$")


def match_key(text: str) -> str:
    """표기 매칭 키 — 같은 회사를 다르게 쓴 표기가 같은 문자열이 되게 한다."""
    t = _LEGAL_FORM.sub("", unicodedata.normalize("NFKC", text))
    t = _DROP.sub("", t).casefold()
    t = t.removesuffix("보통주")
    # C 는 `씨`·`시` 둘 다로 읽힌다(ISC → 아이에스씨·아이에스시) — 키에서는 하나로 접는다.
    return "".join(_LETTER_READING.get(ch, ch) for ch in t).replace("씨", "시")


@functools.lru_cache(maxsize=1)
def _curated_aliases() -> tuple[tuple[str, str], ...]:
    """별칭 명부(alias, ticker). 주석·머리행은 건너뛴다."""
    out = []
    for line in _ALIAS_FILE.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        alias, ticker = line.split("\t")[:2]
        if alias == "alias":
            continue
        out.append((alias.strip(), ticker.strip()))
    return tuple(out)


def _put(table: dict[str, str | None], key: str, entity_id: str | None) -> None:
    """충돌이면 None 으로 — 나중 행이 조용히 이기지 않게."""
    if key not in table:
        table[key] = entity_id
    elif table[key] != entity_id:
        table[key] = _AMBIGUOUS


def load_resolution_index(conn, aliases=None) -> ResolutionIndex:
    """엔티티 마스터 1쿼리 + 별칭 명부로 해소 인덱스를 만든다.

    `aliases` 는 (alias, ticker) 목록 — 없으면 `instrument_aliases.tsv`. 명부 생성 스크립트가
    빈 목록으로 마스터만의 인덱스를 만들어 후보를 검사할 때 쓴다.

    키 공간: instrument.ticker · 발행사 entity.display_name · instrument entity.display_name ·
    별칭. 같은 키가 서로 다른 instrument_id 로 두 번 오면 충돌 표식으로 바꾼다 — 나중 행이
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
            if key:
                _put(by_key, key, str(instrument_id))

    alias_ticker_by_key: dict[str, str] = {}
    alias_keys: set[str] = set()
    for raw_alias, ticker in (_curated_aliases() if aliases is None else aliases):
        alias, entity_id = _normalize(raw_alias), by_ticker.get(ticker)
        # 대상 종목이 마스터에 없으면 별칭도 없다 — 정적 명부가 없는 마스터를 지어내지 않는다.
        if not alias or entity_id is None:
            continue
        if alias in by_key and by_key[alias] != entity_id:
            by_key[alias] = _AMBIGUOUS
            continue
        alias_ticker_by_key[alias] = ticker
        if alias in by_key:
            # 실제 마스터 이름이면 별칭 전용 경로로 강등하지 않는다(완전일치는 기본 경로에서도 푼다).
            continue
        by_key[alias] = entity_id
        alias_keys.add(alias)

    by_match_key: dict[str, str | None] = {}
    for key, entity_id in by_key.items():
        mk = match_key(key)
        if mk:
            _put(by_match_key, mk, entity_id)
    return ResolutionIndex(
        by_key=by_key,
        by_ticker=by_ticker,
        alias_ticker_by_key=alias_ticker_by_key,
        alias_keys=frozenset(alias_keys),
        by_match_key=by_match_key,
    )


def _lookup(index: ResolutionIndex, text: str) -> tuple[bool, str | None]:
    """정규화 키(그대로, 끝 조사 하나 뗀 것) → (찾았나, entity_id | None=충돌)."""
    key = match_key(text)
    if key in index.by_match_key:
        return True, index.by_match_key[key]
    # 떼는 방법이 여럿일 수 있다(`액트로는` = `액트로`+`는` 또는 `액트`+`로는`). 맞는 종목이 갈리면 모호다.
    hits = set()
    for particle in _PARTICLES:
        stem = key.removesuffix(particle)
        min_stem = _MIN_STEM_AFTER_NAME_FINAL if particle in _NAME_FINAL_PARTICLES else 2
        if stem != key and len(stem) >= min_stem and stem in index.by_match_key:
            hits.add(index.by_match_key[stem])
    if not hits:
        return False, None
    return True, hits.pop() if len(hits) == 1 else _AMBIGUOUS


def _match(index: ResolutionIndex, text: str) -> tuple[str | None, str]:
    """표기 매칭 — 전체 표기, 안 되면 끝 괄호 병기의 밖·안. 둘이 다른 종목이면 모호."""
    found, entity_id = _lookup(index, text)
    if found:
        return (entity_id, ALIAS_RESOLVED) if entity_id is not _AMBIGUOUS else (None, AMBIGUOUS)
    paren = _PAREN.match(unicodedata.normalize("NFKC", text).strip())
    if paren is None or (paren["tail"].strip() and paren["tail"].strip() not in _PARTICLES):
        return None, UNRESOLVED
    hits = [_lookup(index, part) for part in (paren["out"], paren["inner"])]
    ids = {entity_id for found, entity_id in hits if found}
    if not ids:
        return None, UNRESOLVED
    if len(ids) > 1 or _AMBIGUOUS in ids:
        return None, AMBIGUOUS
    return ids.pop(), ALIAS_RESOLVED


def resolve(
    index: ResolutionIndex, text: object, *, allow_aliases: bool = False,
) -> tuple[str | None, str]:
    """argument 텍스트 1건을 **instrument 인덱스로** 해소 — (entity_id | None, 사유).

    **이 함수는 역할을 모른다** — 텍스트를 instrument 인덱스에 대볼 뿐이다. 역할별 축
    분기는 `plan_resolution` 이 하고, 배치 적재(`load_assertions`)는 그쪽을 쓴다.
    별칭·표기 매칭은 호출부가 허용할 때만(`allow_aliases`) 쓰고, 그 결과는 `alias_resolved` 다.
    기본값(False)은 마스터 완전일치만 푼다.

    사유는 호출부(로더)가 quality log 에 분포로 남긴다(Rule 12 — 미해소는 침묵하지
    않는다). 비문자열·공백뿐 텍스트는 unresolved 다.
    """
    if not isinstance(text, str):
        return None, UNRESOLVED
    key = _normalize(text)
    if not key:
        return None, UNRESOLVED
    if key in index.by_key and (allow_aliases or key not in index.alias_keys):
        entity_id = index.by_key[key]
        if entity_id is _AMBIGUOUS:
            return None, AMBIGUOUS
        return entity_id, ALIAS_RESOLVED if key in index.alias_keys else RESOLVED
    if not allow_aliases:
        return None, UNRESOLVED
    return _match(index, key)


def resolve_alias_ticker(index: ResolutionIndex, text: object) -> str | None:
    """별칭·표기 매칭으로 찾은 종목의 ticker. assemble-events(DeepSeek 경로)가 기사 허용 ticker
    안에서만 쓸 수 있게 분리한다."""
    if not isinstance(text, str):
        return None
    key = _normalize(text)
    ticker = index.alias_ticker_by_key.get(key)
    if ticker is not None or not key:
        return ticker
    entity_id, _reason = _match(index, key)
    if entity_id is None:
        return None
    return next((t for t, e in index.by_ticker.items() if e == entity_id), None)


def check_alias_candidates(
    master: ResolutionIndex, candidates,
) -> tuple[list[tuple[str, str, str]], list[tuple[str, str, str, str]]]:
    """별칭 후보 검사 — 명부(`instrument_aliases.tsv`)에 넣을 것과 뺄 것을 가른다(ALPHA-850).

    `master` 는 별칭 없이 만든 인덱스(`load_resolution_index(conn, aliases=())`), `candidates` 는
    (alias, ticker, source) 순서열이다. 판정은 **운영 해소기 그 자체**로 한다:
      unknown_ticker  마스터에 없는 종목
      redundant       별칭 없이도 이미 그 종목으로 풀린다(정규화가 맡는다)
      conflict        다른 종목으로 풀리거나 모호하다 — 넣으면 그 종목의 이름을 망가뜨린다
      clash           후보끼리 같은 매칭 키에 다른 종목 — 어느 쪽도 넣지 않는다
    반환: (accepted [(alias, ticker, source)], rejected [(alias, ticker, source, 사유)]).
    """
    accepted: dict[str, tuple[str, str, str]] = {}
    rejected: list[tuple[str, str, str, str]] = []
    clashed: set[str] = set()
    for raw, ticker, source in candidates:
        alias = _normalize(raw)
        target = master.by_ticker.get(ticker)
        if not alias or target is None:
            rejected.append((alias, ticker, source, "unknown_ticker"))
            continue
        entity_id, reason = resolve(master, alias, allow_aliases=True)
        if entity_id == target:
            rejected.append((alias, ticker, source, "redundant"))
            continue
        if reason != UNRESOLVED:
            rejected.append((alias, ticker, source, "conflict"))
            continue
        key = match_key(alias)
        if key in clashed:
            rejected.append((alias, ticker, source, "clash"))
        elif key in accepted and accepted[key][1] != ticker:
            clashed.add(key)
            rejected.append((alias, ticker, source, "clash"))
            rejected.append((*accepted.pop(key), "clash"))
        elif key not in accepted:
            accepted[key] = (alias, ticker, source)
        # 같은 키·같은 종목의 두 번째 표기는 정규화가 같은 키로 푼다 — 하나만 둔다.
    return sorted(accepted.values(), key=lambda r: (r[1], r[0])), rejected


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
      NONE      instrument 인덱스(`resolve`) 우선. 미해소일 때만 역할에 선언된 기관 명부
                폴백을 허용한다. 충돌은 보존하며 새 실체를 채번하지 않는다.

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
        entity_id, reason = resolve(index, text, allow_aliases=True)
        if reason == UNRESOLVED and mention and relation.registry_fallback_sections:
            authority_id = resolve_authority(role_code, mention)
            if authority_id:
                return authority_id, REGISTRY_HIT, None
        return entity_id, reason, None

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
