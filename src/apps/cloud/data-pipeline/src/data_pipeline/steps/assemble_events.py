"""이벤트 조립·threading — v4 온톨로지 추출 체인 (ALPHA-412 이식 → ALPHA-545 v4 확장).

분석엔진 daily_pipeline 의 추출 구간(제목 분류 → document/assertion/source_event 계보
조립 → event_thread threading)을 feature 페이즈 스텝으로 옮긴 이식(ALPHA-412, ADR-0028)
위에, 실험실(event-ontology repo) normalize 의 v4 추출 계약을 포팅했다(ALPHA-545):

- **2콜 체인**: (a) 게이트+타입판별 콜(doc_class·event_type_code·primary_ticker) →
  (b) 타입별 추출 콜(predicate·stage·arguments[]·measures[]·confidence). 프롬프트
  메뉴(술어·역할·수량·단계)는 `edge_ontology` 뷰에서 파생해 검증과 같은 출처를 쓴다.
- **기록 확장**: source_event 에 predicate_code·confidence_level·completeness,
  event_argument 에 slot·mention_text·entity_kind·group_ord(참여자 전원 — 다중역할),
  event_measure 신규(surface 는 LLM, value/unit 은 결정적 KR 파서 — events/amounts.py).
- **stage 통제**: 타입 lifecycle 모델 메뉴 밖 값은 NULL + 카운터(자유텍스트 오염 차단 —
  구 43종 오염 결함의 수정).
- **novelty 세분화**: 동일 thread 내 stage 진행=FOLLOW_UP_STAGE, 정정 마커=CORRECTION,
  그 외 재보도=DUPLICATE_REBROADCAST (news_thread_contract §novelty).

⚠️ **PIPELINE_ID 는 엔진과 반드시 동일해야 한다**(db.PIPELINE_ID) — 결정적 ID 의 재료라,
다르면 같은 이벤트가 다른 source_event_id/thread_id 로 갈려 이행기(엔진이 아직 자체
조립을 하는 동안)의 멱등 수렴이 깨진다. 엔진 축소(PR D) 후에도 기존 행과의 수렴을
위해 유지한다.

tag-news 와의 관계: tag-news 는 다중 assertion·역할 추출로 feature 존을 만들고(문서/
assertion 사슬), 이 스텝은 v4 추출 체인(기사당 1건, 제목만)으로 **event 계보**를 만든다.
두 분류기의 단일화는 로직 소유자 결정 사안(후속) — 그동안 양쪽 다 자연키/결정적 ID
멱등이라 공존이 안전하다. document_assertion 컬럼 소유권(ALPHA-538): 이 스텝의
assertion INSERT 는 event_evidence FK 를 세우기 위한 **비계**라 공유·결정값만 싣는다 —
`confidence` 는 tag-news 체인(load-assertions) 소유, `lifecycle_stage` 는 event grain
(`source_event`) 소유. 그래서 두 스텝의 실행 순서가 이 테이블의 최종 행을 바꾸지 않는다.
"""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from functools import lru_cache

from edge_ontology import OntologyView, load_lifecycle_models, load_ontology_view

from ..config import DbConfig
from ..db import connect, stable_domain_id
from ..events.amounts import BASIS_VALUES, parse_amount, parse_basis
from ..lake import Storage, canonical_news_articles_partition, quality_log_key
from ..tagging.ontology import default_predicate, identity_roles, load_profiles
from .dart_values import match_dart_values

logger = logging.getLogger(__name__)

JOB_NAME = "assemble_events"
DATASET = "source_event"

# 추출기 버전 — provenance. 프롬프트·검증 계약이 바뀌면 올린다(tag-news 의 TAGGER_VERSION
# 관례). v4 = 2콜 체인 + 다중역할/수량 기록(ALPHA-545).
ASSEMBLER_VERSION = "assemble-v4"

CLASSIFY_BATCH = 40
# 추출 콜은 항목당 출력(arguments·measures JSON)이 게이트보다 훨씬 커서 배치를 줄인다
# (이식원 run_extraction batch_size=10 과 동일) — 40이면 응답이 max_tokens 에 잘릴 수 있다.
EXTRACT_BATCH = 10
TITLE_EVIDENCE_TYPE = "TITLE"
_KST = timezone(timedelta(hours=9))

# 분류 LLM 호출 병렬도(ALPHA-520). classify 는 40건배치×수십 배치가 완전 직렬이라 Assemble
# 런타임의 최대 병목이었다. 각 배치는 독립(자기 배치의 LLM 콜 + 검증만, article_id 도 배치 간
# 유일)이라 병렬 안전하고, complete_fn 은 상태없는 urllib 콜(스레드안전)이다. 배치별 결과를
# 취합 후 메인스레드에서 merge 한다. **threading(thread_events)은 병렬화 금지** — novelty 가
# available_at 정렬·prior 카운트에 의존해 순서가 결과를 바꾼다. DeepSeek 캡 500 안이라 100 까지 안전.
DEFAULT_CLASSIFY_CONCURRENCY = 32
MAX_CLASSIFY_CONCURRENCY = 100

# canonical 뉴스의 언어 파티션 축(벤더 고정: bigkinds=ko·fmp=en). 분류 대상은 전 언어지만
# 프롬프트가 한국어 제목 전제라 실질 판정은 ko 에서 나온다(엔진도 전 파티션을 읽었다).
LANGUAGES = ("ko", "en")

# 게이트 콜의 문서 성격 메뉴(이식원 v3 DOC_CLASSES) — EVENT 만 추출 콜로 넘어간다.
# tag-news 의 DOC_CLASSES(4클래스)와 별개 어휘다: 이 게이트는 적재되지 않는 내부 관문이라
# 이식원 계약을 그대로 쓰고, tag-news 체인은 불변이다.
GATE_DOC_CLASSES = ("EVENT", "MARKET_COMMENTARY", "OPINION_OR_ANALYSIS", "PROMOTIONAL", "LIST")

# event_argument.slot CHECK 어휘 — 추출 계약(llm-extract-v4)과 동일.
SLOT_VALUES = frozenset({"subject", "object", "qualifier"})

# 추출 콜 confidence(H/M/L) → source_event.confidence_level CHECK 어휘 사상.
_CONFIDENCE_LEVELS = {"H": "HIGH", "M": "MEDIUM", "L": "LOW"}

# 파서 단위 → 온톨로지 unit_family (환산 없음 — 소속 판정만). RATIO 는 파서 문법에 증명
# 단위가 없어 매핑이 없다 = RATIO 역할의 파스는 항상 미해결로 남는다(지어내지 않는다).
_UNIT_FAMILY = {
    "KRW": "CURRENCY", "USD": "CURRENCY",
    "PCT": "PERCENT",
    "DAYS": "DURATION_DAYS", "MONTHS": "DURATION_DAYS", "YEARS": "DURATION_DAYS",
    "COUNT": "COUNT",
}

# group_ord 는 SMALLINT — 범위 밖 정수를 그대로 INSERT 하면 Postgres range error 로 날짜
# 전체가 롤백된다(기형 LLM 필드 하나가 배치를 죽이면 안 된다 — 그 필드만 결측).
_SMALLINT_MAX = 32767


def _ordinal(value: object) -> int | None:
    """SMALLINT 범위의 음이 아닌 서수만 통과 — 밖이면 NULL(그 필드만 결측)."""
    if not isinstance(value, int) or isinstance(value, bool):
        return None
    return value if 0 <= value <= _SMALLINT_MAX else None


def _entity_kind(role: str | None, entity_id: str | None) -> str | None:
    """역할→엔티티 종별. 계약(entity_mapping_contract_v0_1.yaml)은 아직 종별별 `used_for`
    산문만 갖고 역할→종별 **표가 없다** — 코드가 표를 만들면 계약 밖 SSOT 가 생긴다
    (Rule 7·11). 그래서 이름이 종별과 같은 ISSUER 만 확정하고 나머지는 NULL 로 둔다:
    CUSTOMER·SUPPLIER·ACQUIRER·TARGET_COMPANY 는 계약상 COMPANY_ENTITY 후보인데 전원
    ISSUER 로 실으면 다자 딜 행이 발행사 행과 구분되지 않아 역할-종별 검증이 불가능해진다
    (Codex #255 P2). 표가 계약에 생기면 그때 여기서 읽는다."""
    if entity_id is None or role != "ISSUER":
        return None  # 미해소는 종별 근거가 없고, 계약에 표가 없는 역할은 지어내지 않는다
    return "ISSUER"


# event_measure.value_source CHECK 어휘 — 이 스텝은 뉴스 파싱만 하므로 DART 는 내지 않는다
# (DART 확정치 보강은 엔진 소비 확장의 소관).
VALUE_SOURCE_PARSED = "PARSED"
VALUE_SOURCE_UNRESOLVED = "UNRESOLVED"

# 계약 correction_markers — news_thread_contract_v0_1.yaml 의 *id002 앵커로 53타입 전부가
# 같은 목록을 공유하므로 단일 상수가 계약과 동형이다. predicate/stage 문자열에 마커가
# 포함되면 정정 보도로 본다(예: predicate REVISE, stage CANCELLED).
CORRECTION_MARKERS = ("AMEND", "CORRECT", "CANCEL", "REVISE", "RESTATE")


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# 결정적 ID 산식은 db.stable_domain_id 가 소유한다(ALPHA-456) — load-assertions 와 **같은
# 함수**를 써야 같은 자연키에 같은 assertion_id 가 나온다. 지역 별칭만 남긴다(이 파일의
# _stable_id 호출 6곳을 치환하는 것보다 diff 가 작고 호출부 의미도 그대로다).
_stable_id = stable_domain_id


def _iso(value: object) -> str:
    if value is None:
        return _utcnow_iso()
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    return str(value)


def _read_parquet_rows(data: bytes) -> list[dict]:
    import io
    import pyarrow.parquet as pq

    return pq.read_table(io.BytesIO(data)).to_pylist()


def _partition_dates(storage: Storage, language: str) -> list[str]:
    marker = canonical_news_articles_partition(language, "")  # ".../published_date="
    dates: set[str] = set()
    for key in storage.list_keys(marker):
        date = key[len(marker):].split("/", 1)[0]
        if date:
            dates.add(date)
    return sorted(dates)


def _mention_tickers(mentions: object) -> list[str]:
    if not mentions:
        return []
    if isinstance(mentions, str):
        try:
            mentions = json.loads(mentions)
        except json.JSONDecodeError:
            return []
    out: list[str] = []
    for item in mentions or []:
        ticker = item.get("ticker") if isinstance(item, dict) else None
        if ticker:
            out.append(str(ticker))
    return out


def read_daily_news(storage: Storage, published_date: str) -> list[dict]:
    """해당 발행일의 canonical 뉴스(제목 축 필드만) — 엔진 read_daily_news 이식."""
    rows: list[dict] = []
    seen: set[str] = set()
    for language in LANGUAGES:
        prefix = canonical_news_articles_partition(language, published_date)
        for key in storage.list_keys(prefix + "/"):
            if not key.endswith(".parquet"):
                continue
            for rec in _read_parquet_rows(storage.get_bytes(key)):
                title = (rec.get("title") or "").strip()
                article_id = rec.get("article_id")
                if not title or not article_id:
                    continue
                article_id = str(article_id)
                if article_id in seen:
                    continue
                seen.add(article_id)
                rows.append({
                    "article_id": article_id,
                    "title": title,
                    "published_at": rec.get("published_at"),
                    "publisher": rec.get("publisher"),
                    "source_vendor": rec.get("source_vendor") or "bigkinds",
                    "language": language,  # 파티션 축 — document.language_code 로 실린다
                    "tickers": _mention_tickers(rec.get("mentions")),
                })
    return rows


# ── 분류·추출 (v4 2콜 체인 — 이식원 extract.py 의 게이트→타입별 추출 구조를 edge 관례로) ──
def _classify_system(view: OntologyView) -> str:
    """게이트+타입판별 콜(a)의 system — 판별만 하고 인자 추출은 타입별 콜(b)이 한다."""
    types = "\n".join(
        f"- {tid} | pred:{','.join(sorted(tv.predicates))} | req:{','.join(tv.required_roles)}"
        for tid, tv in sorted(view.types.items())
    )
    return (
        "너는 한국어 금융 뉴스 제목만 보고 문서 성격과 이벤트 타입을 판정하는 게이트다. 제목 외 정보는 없다.\n"
        "각 항목에 대해 아래 JSON 스키마의 오브젝트를 만든다.\n"
        '{"items":[{"id": <입력 id 그대로>, "doc_class": "' + "|".join(GATE_DOC_CLASSES) + '", '
        '"event_type_code": <doc_class=EVENT 면 아래 목록 중 하나, 아니면 "">, '
        '"primary_ticker": <입력 tickers 중 하나 또는 "">, "confidence": 0~1}]}\n'
        "doc_class 메뉴: EVENT=확정된 사실 행동/결과 보도(실적·수주·계약·인수·출시·공시·증설·판결·인사·가격변동). "
        "MARKET_COMMENTARY=시황·시세 동향 해설(가격·지수 등락 자체, 누적 회고·마일스톤 단독). "
        "OPINION_OR_ANALYSIS=전망·분석·칼럼·인터뷰 의견. PROMOTIONAL=보도자료·수상·신제품 홍보톤. "
        "LIST=단순 나열·목록·일정·시세표.\n"
        "규칙: event_type_code 는 반드시 아래 목록에서만 고른다. primary_ticker 는 입력 tickers 목록에서만 "
        '고른다(없으면 ""). 목록에 없는 값은 만들지 마라. 술어·인자 추출은 다음 단계가 한다 — 여기선 판별만 한다.\n'
        f"[이벤트 타입 목록]\n{types}"
    )


@lru_cache(maxsize=None)
def _stage_sequence(event_type_code: str) -> tuple[str, ...]:
    """타입 lifecycle 모델의 순서축(stages + terminal) — 프롬프트 메뉴·검증·novelty 공용.

    stage 어휘의 SSOT 는 edge_ontology 의 lifecycle_models 리소스다 — 프롬프트와 검증이
    같은 출처를 봐야 모델이 프롬프트를 지켜도 검증에서 떨어지는 모순이 없다(tagging 과 동일 원칙).
    """
    model = (load_profiles().get(event_type_code) or {}).get("lifecycle_model")
    spec = load_lifecycle_models().get(model) or {}
    return tuple(spec.get("stages") or []) + tuple(spec.get("terminal") or [])


def _extract_system(view: OntologyView, event_type_code: str) -> str:
    """타입별 추출 콜(b)의 system — 그 타입의 메뉴(술어·역할·수량·단계)만 싣는다.

    이식원 build_system_prompt 는 53타입 다이제스트를 한 콜에 다 실었지만, edge 는 게이트가
    타입을 이미 골랐으므로 타입당 메뉴만 실어 토큰·혼동을 줄인다. LLM 은 판정·원문 복사만
    하고 산술은 절대 하지 않는다(value/unit 은 events/amounts.py 가 계산 — Rule 5).
    """
    tv = view.types[event_type_code]
    argument_menu = sorted(
        (frozenset(tv.required_roles) | frozenset(tv.optional_roles)) - tv.quantity_roles)
    stages = _stage_sequence(event_type_code)
    stage_line = " < ".join(stages) if stages else "(없음 — stage 는 항상 null)"
    return (
        f"너는 한국어 금융 뉴스 제목에서 '{event_type_code}' 이벤트의 인자를 추출하는 구조화기다. "
        "제목 외 정보는 없다.\n"
        "각 항목에 대해 아래 JSON 스키마의 오브젝트를 만든다.\n"
        '{"items":[{"id": <입력 id 그대로>, "predicate": <술어 메뉴 중 하나 또는 null>, '
        '"stage": <단계 메뉴 중 하나 또는 null>, '
        '"arguments":[{"role": <참여자 역할 메뉴 중 하나>, "slot": "subject|object|qualifier", '
        '"mention": <제목 원문 그대로>, "ticker": <그 참여자가 입력 tickers 의 종목 자신일 때만 그 티커, '
        '아니면 "">, "group": <정수>}], '
        '"measures":[{"role": <수량 역할 메뉴 중 하나>, "surface": <제목 원문 표기 그대로>, '
        '"basis": "TOTAL|ANNUAL|UNKNOWN", "group": <정수>}], "confidence": "H|M|L"}]}\n'
        "규칙:\n"
        "- mention/surface 는 제목 원문 문자열 그대로 복사한다(정규화·자르기·번역 금지).\n"
        "- measures 의 surface 는 원문 표기 그대로(예: 2734억원). 숫자 계산·단위 환산 절대 금지 — 코드가 계산한다.\n"
        "- basis: 원문에 총액 명시=TOTAL, 연간 명시=ANNUAL, 그 외 UNKNOWN.\n"
        "- group: 같은 라인아이템(제품·계약 단위)끼리 같은 정수 서수, 단일 사안이면 0.\n"
        "- arguments=개체 역할만, measures=수량 역할만. 수량 역할을 arguments 에 넣지 않는다.\n"
        "- 메뉴에 없는 역할·술어·단계는 만들지 마라. 근거 없으면 null.\n"
        f"[술어 메뉴] {','.join(sorted(tv.predicates)) or '-'}\n"
        f"[참여자 역할 메뉴] {','.join(argument_menu) or '-'}\n"
        f"[수량 역할 메뉴] {','.join(sorted(tv.quantity_roles)) or '-'}\n"
        f"[단계 메뉴(순서축)] {stage_line}"
    )


def _complete_json(complete_fn, system: str, user: str) -> dict:
    """엔진 DeepSeekClient.complete_json 의 3회 재시도 계약 — 어댑터는 llm.py 주입물."""
    last: Exception | None = None
    for _attempt in range(3):
        try:
            result = json.loads(complete_fn(system, user))
        except Exception as exc:  # llm.py 는 실패를 예외로 올린다(조용한 폴백 금지)
            last = exc
            continue
        if isinstance(result, dict):
            return result
        # 최상위가 배열·스칼라면 재시도 — 통과시키면 하류 payload.get("items") 이 opaque
        # AttributeError 로 터져 날짜 전체가 롤백된다(Rule 12: 불명확 실패로 위장 금지).
        last = TypeError(f"LLM 응답 최상위가 객체가 아님: {type(result).__name__}")
    raise RuntimeError(f"분류 LLM 호출이 재시도 후에도 실패: {last}")


def _llm_items(chunk: list[dict], entity_index: dict[str, str]) -> tuple[list[dict], dict[str, set[str]]]:
    """LLM 입력 items(유니버스 교집합 tickers) + id별 허용 티커 집합."""
    items = [{"id": r["article_id"], "title": r["title"],
              "tickers": [t for t in r["tickers"] if t in entity_index]}
             for r in chunk]
    return items, {i["id"]: set(i["tickers"]) for i in items}


def _gate_batch(complete_fn, system: str, chunk: list[dict], view: OntologyView,
                entity_index: dict[str, str]) -> dict[str, dict]:
    """게이트 콜 40건배치 1개 → {article_id: 검증된 게이트 판정}. 배치 로컬 dict 만 반환
    (공유상태 미접근)해 스레드에서 안전하게 돈다 — merge 는 호출부가 메인스레드에서 한다."""
    items, allowed_by_id = _llm_items(chunk, entity_index)
    payload = _complete_json(complete_fn, system, json.dumps({"items": items}, ensure_ascii=False))
    out: dict[str, dict] = {}
    raw_items = payload.get("items")
    # items 컨테이너·항목이 비정형이어도 그 항목만 버린다(국소 실패) — 배치 전체 롤백 금지.
    for item in (raw_items if isinstance(raw_items, list) else ()):
        if not isinstance(item, dict):
            continue
        validated = _validate_gate(item, view, entity_index, allowed_by_id)
        if validated is not None:
            out[validated["article_id"]] = validated
    return out


def _validate_gate(item: dict, view: OntologyView, entity_index: dict[str, str],
                   allowed_by_id: dict[str, set[str]]) -> dict | None:
    article_id = item.get("id")
    if not article_id or item.get("doc_class") != "EVENT":
        return None
    event_type = str(item.get("event_type_code") or "")
    if event_type not in view.types:
        return None
    ticker = str(item.get("primary_ticker") or "")
    if ticker not in allowed_by_id.get(str(article_id), set()):
        # 프롬프트는 "입력 tickers 중에서만"을 이미 명시하지만 엔진 검증은 전역 유니버스만
        # 봤다 — 모델이 무관한 유니버스 종목을 반환하면 엉뚱한 회사에 이벤트·스레드가 선다
        # (Codex #137). 프롬프트 규칙의 코드 강제이지 분류 시맨틱 변경이 아니다.
        return None
    entity_id = entity_index.get(ticker)
    if entity_id is None:
        return None
    try:
        confidence = float(item.get("confidence"))
    except (TypeError, ValueError):
        confidence = None
    if confidence is not None and not 0.0 <= confidence <= 1.0:
        confidence = None
    tv = view.types[event_type]
    role_code = tv.required_roles[0] if tv.required_roles else "ISSUER"
    return {
        "article_id": str(article_id),
        "event_type_code": event_type,
        "primary_ticker": ticker,
        "entity_id": entity_id,
        "role_code": role_code,
        # 폴백 anchor 역할 — roles.primary 가 유일할 때만 존재한다. 다중 primary 타입
        # (CONTRACT.SIGNING: SUPPLIER|CUSTOMER)은 게이트가 고른 티커가 어느 역할인지 코드가
        # 모르므로 조작하면 없는 공급사 주장이 된다(Codex #255 P2). required_roles[0](=role_code,
        # assertion_argument 레거시 grain)와 다를 수 있다 — LEGAL.REGULATORY_ACTION 은
        # required[0]=AUTHORITY 지만 primary=[TARGET_COMPANY] 라, 기업 티커를 AUTHORITY 로
        # 실으면 규제당국 주장이 조작된다.
        "anchor_role": tv.primary_roles[0] if len(tv.primary_roles) == 1 else None,
        "confidence": confidence,
    }


def _extract_batch(complete_fn, system: str, event_type_code: str, chunk: list[dict],
                   gate: dict[str, dict], view: OntologyView,
                   entity_index: dict[str, str]) -> dict[str, dict]:
    """타입별 추출 콜 배치 1개 → {article_id: 게이트+추출 병합 분류}."""
    items, allowed_by_id = _llm_items(chunk, entity_index)
    payload = _complete_json(
        complete_fn, system,
        json.dumps({"event_type_code": event_type_code, "items": items}, ensure_ascii=False))
    out: dict[str, dict] = {}
    raw_items = payload.get("items")
    for item in (raw_items if isinstance(raw_items, list) else ()):
        if not isinstance(item, dict):
            continue  # 비객체 항목(null·스칼라)은 그 항목만 결측 취급 — 날짜 롤백 금지
        article_id = str(item.get("id") or "")
        cls = gate.get(article_id)
        if cls is None or cls["event_type_code"] != event_type_code:
            continue  # 게이트가 안 고른 id 를 지어내도 무시 — 판별은 게이트 소유
        validated = _validate_extraction(item, view, cls, entity_index,
                                         allowed_by_id.get(article_id, set()))
        if validated is not None:
            out[article_id] = validated
    return out


def _validate_extraction(item: dict, view: OntologyView, gate_cls: dict,
                         entity_index: dict[str, str], allowed_tickers: set[str]) -> dict | None:
    """추출 콜 항목 1건 검증 — 라벨이 메뉴에 드는지는 코드가 판정한다(Rule 5).

    불량 부분(메뉴 밖 역할·빈 mention·범위 밖 slot)은 그 부분만 떨어뜨리고 이벤트는 살린다
    (tagging 과 동일 — 한 역할의 환각이 사건 전체를 버리게 하지 않는다). 미해소 참여자는
    entity_id 없이 보존한다 — completeness 판정에 쓰이고, 적재도 표면형과 함께 남는다
    (ALPHA-563).
    """
    event_type = gate_cls["event_type_code"]
    tv = view.types[event_type]

    # 술어: 모델 값이 메뉴 안이면 그대로, 없거나 밖이면 온톨로지 기본값(tagging 과 같은 규약 —
    # 기본값은 지어낸 게 아니라 그 타입 자신의 allowed_predicates[0]이다). document_assertion
    # 자연키가 predicate NOT NULL 이라 비울 수 없다.
    predicate = item.get("predicate")
    if not isinstance(predicate, str) or predicate not in tv.predicates:
        predicate = default_predicate(event_type)
    if predicate is None:
        # 타입에 허용 술어가 하나도 없다 = 온톨로지 리소스 이상 — 조용히 넘기면 적재에서
        # NOT NULL 위반으로 터진다. 사유를 남기고 이 기사는 버린다(다음 런 재시도).
        logger.warning("허용 술어가 없는 타입 — 추출 폐기: article=%s type=%s",
                       gate_cls["article_id"], event_type)
        return None

    # stage 통제: lifecycle 모델 메뉴 안 값만 실린다. 밖이면 NULL + 카운터 — 자유텍스트가
    # source_event.lifecycle_stage 를 오염시키던 결함(43종)의 수정이다.
    raw_stage = item.get("stage")
    if not isinstance(raw_stage, str):
        raw_stage = None  # 비스칼라 라벨은 결측과 동급 — frozenset 멤버십 TypeError 로 런을 굴리지 않는다
    stage = raw_stage if raw_stage and raw_stage in tv.stages else None
    stage_rejected = bool(raw_stage) and stage is None
    if stage_rejected:
        logger.warning("stage 메뉴 밖 값 → NULL: article=%s type=%s stage=%r",
                       gate_cls["article_id"], event_type, raw_stage)

    raw_confidence = item.get("confidence")
    confidence_level = (_CONFIDENCE_LEVELS.get(raw_confidence)
                        if isinstance(raw_confidence, str) else None)

    argument_menu = (frozenset(tv.required_roles) | frozenset(tv.optional_roles)) - tv.quantity_roles
    arguments: list[dict] = []
    # primary 의 역할 충족은 폴백 anchor 행이 실제로 설 때만 센다(아래 루프 뒤에서 판정).
    covered_roles: set[str] = set()
    raw_arguments = item.get("arguments")
    # 컨테이너 자체가 비리스트(스칼라 등)면 결측과 동급 — TypeError 로 날짜 전체를 굴리지 않는다.
    for raw in (raw_arguments if isinstance(raw_arguments, list) else ()):
        if not isinstance(raw, dict):
            continue
        role = str(raw.get("role") or "")
        if role not in argument_menu:
            continue  # 메뉴 밖 역할 = 모델이 라벨을 발명 — 그 역할만 버린다
        mention = raw.get("mention")
        if not isinstance(mention, str) or not mention.strip():
            continue  # 역할만 있고 원문 근거가 없으면 안 채운 것과 같다
        slot = raw.get("slot")
        group = raw.get("group")
        ticker = str(raw.get("ticker") or "")
        entity_id = entity_index.get(ticker) if ticker in allowed_tickers else None
        covered_roles.add(role)
        arguments.append({
            "role_code": role,
            "slot": slot if isinstance(slot, str) and slot in SLOT_VALUES else None,
            "mention_text": mention.strip(),
            "entity_id": entity_id,
            "entity_kind": _entity_kind(role, entity_id),
            "group_ord": _ordinal(group),
        })

    # 폴백 anchor 행이 설 때만 그 역할을 충족으로 센다 — 추출이 primary 를 다른 유효 역할로
    # 냈는데 required_roles[0](SUPPLIER)을 미리 덮으면 기사에 없는 공급사를 '충족'으로
    # 위장해 completeness 가 추출 품질을 과대평가한다(Codex #255 P2, 폴백 조건과 동형).
    primary_entity = entity_index.get(str(gate_cls.get("primary_ticker") or ""))
    if (gate_cls["anchor_role"]
            and (primary_entity is None
                 or not any(a["entity_id"] == primary_entity for a in arguments))):
        covered_roles.add(gate_cls["anchor_role"])

    measures: list[dict] = []
    raw_measures = item.get("measures")
    for raw in (raw_measures if isinstance(raw_measures, list) else ()):
        if not isinstance(raw, dict):
            continue
        role = str(raw.get("role") or "")
        if role not in tv.quantity_roles:
            continue  # 수량 메뉴 밖 역할 = 발명된 라벨 — 참여자와 같은 규약으로 그 항목만 버린다
        surface = raw.get("surface")
        if not isinstance(surface, str) or not surface.strip():
            continue  # surface 없는 수량은 파서 입력이 없다 — 지어내지 않는다
        surface = surface.strip()
        parsed = parse_amount(surface)
        expected_family = tv.quantity_unit_families.get(role)
        if (parsed.value is not None and expected_family
                and _UNIT_FAMILY.get(parsed.unit) != expected_family):
            # 수량 역할의 unit_family 와 파서 단위 소속이 다르면(CONTRACT_VALUE+5%,
            # CONTRACT_DURATION+%) 값을 지어내느니 미해결로 남긴다 — USD 등 통화는 보존,
            # 파서가 증명 못 하는 family(RATIO)도 미해결이다(Codex #255 P2 일반화).
            parsed = replace(parsed, value=None, unit=None, parse_flag="unit_mismatch")
        basis = raw.get("basis")
        group = raw.get("group")
        if parsed.value is not None:
            # 수량은 '숫자'가 목적이라 파싱 실패(대규모·5%)는 그 측정이 없는 것과 같다 —
            # 참여자와 비대칭인 이유: 참여자는 보도가 역할을 말했으면 충족이고 entity 해소
            # 실패는 링킹 문제지만, 수량의 주장 자체가 값이다(Codex #255 P2). 행은 UNRESOLVED
            # 로 남아 surface 는 보존된다 — 소비자는 value_source 로 재판정할 수 있다.
            covered_roles.add(role)
        measures.append({
            "role_code": role,
            "surface": surface,
            "value": parsed.value,
            "unit": parsed.unit,
            # basis 는 모델 명시가 메뉴 안이면 그 값, 아니면 surface 의 결정적 판정(총/연간).
            "basis": (basis if isinstance(basis, str) and basis in BASIS_VALUES
                      else parse_basis(surface)),
            "value_source": (VALUE_SOURCE_PARSED if parsed.value is not None
                             else VALUE_SOURCE_UNRESOLVED),
            "parse_flag": parsed.parse_flag,
            "group_ord": _ordinal(group),
        })

    # completeness: required 역할 ∪ required 수량이 다 채워졌는가. 참여자는 **해소 여부 무관**
    # (보도가 역할을 말했으면 충족 — 해소 실패는 적재 가능 여부의 문제다), 수량은 **파싱 성공만**
    # (숫자가 주장 자체라 UNRESOLVED 는 측정 부재와 같다). 수량 required 를 빼면 completeness
    # 가 추출 품질을 과대평가하고, UNRESOLVED 를 세면 쓸 수 없는 값을 완비로 위장한다.
    required_for_completeness = frozenset(tv.required_roles) | tv.required_quantity_roles
    completeness = "complete" if required_for_completeness <= covered_roles else "partial"

    return {
        **gate_cls,
        "predicate_code": predicate,
        "lifecycle_stage": stage,
        "stage_rejected": stage_rejected,
        "confidence_level": confidence_level,
        "completeness": completeness,
        "arguments": arguments,
        "measures": measures,
    }


def classify_titles(complete_fn, rows: list[dict], view: OntologyView,
                    entity_index: dict[str, str],
                    concurrency: int = DEFAULT_CLASSIFY_CONCURRENCY) -> dict[str, dict]:
    """article_id → 검증된 v4 추출(게이트 EVENT + 해소 가능한 primary + 타입별 인자).

    2콜 체인: (a) 게이트 배치(40건)가 doc_class·타입·primary 를 판별하고, (b) 게이트를 통과한
    기사만 타입별로 묶어 추출 배치(10건)가 인자를 뽑는다. 각 단계의 배치별 LLM 콜을 병렬
    실행하고(각 배치 독립·complete_fn 스레드안전) 결과 병합은 취합 뒤 메인스레드에서 한다 —
    순차 실행과 결과 동일(article_id 는 배치 간 유일이라 순서 무관). 추출 콜이 누락한 기사는
    적재하지 않는다(이식원 규약: 누락=추출 실패, 비이벤트 아님) — 자국이 안 남아 다음 런에서
    재시도된다.
    """
    concurrency = max(1, min(concurrency, MAX_CLASSIFY_CONCURRENCY))
    batches = [rows[start:start + CLASSIFY_BATCH] for start in range(0, len(rows), CLASSIFY_BATCH)]
    if not batches:
        return {}
    gate_system = _classify_system(view)
    gate: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=min(concurrency, len(batches))) as pool:
        for partial in pool.map(
                lambda chunk: _gate_batch(complete_fn, gate_system, chunk, view, entity_index),
                batches):
            gate.update(partial)  # 메인스레드 순차 병합(경합 없음)
    if not gate:
        return {}

    rows_by_id = {r["article_id"]: r for r in rows}
    by_type: dict[str, list[dict]] = {}
    for article_id, cls in gate.items():
        by_type.setdefault(cls["event_type_code"], []).append(rows_by_id[article_id])
    systems = {t: _extract_system(view, t) for t in by_type}
    jobs = [(type_id, typed_rows[start:start + EXTRACT_BATCH])
            for type_id, typed_rows in sorted(by_type.items())
            for start in range(0, len(typed_rows), EXTRACT_BATCH)]
    results: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=min(concurrency, len(jobs))) as pool:
        for partial in pool.map(
                lambda job: _extract_batch(complete_fn, systems[job[0]], job[0], job[1],
                                           gate, view, entity_index),
                jobs):
            results.update(partial)

    missing = set(gate) - set(results)
    if missing:
        # 조용한 누락 금지(Rule 12) — 게이트는 EVENT 라 했는데 추출 응답에 없는 기사.
        logger.warning("추출 콜 누락 %d건(다음 런 재시도): %s",
                       len(missing), sorted(missing)[:10])
    return results


def load_entity_index(conn) -> dict[str, str]:
    with conn.cursor() as cur:
        cur.execute("SELECT ticker, instrument_id FROM instrument")
        return {str(t): str(i) for t, i in cur.fetchall()}


def assembled_source_ids(conn, source_ids: list[str]) -> set[str]:
    """이미 **조립된** 기사의 article_id 집합 — 증분(재분류 방지)의 근거.

    엔진의 원래 규칙(document 존재=정규화됨)은 엔진이 document 의 유일한 생산자일 때만
    성립했다. 통합 SFN 에선 LoadDocuments 가 이 스텝보다 먼저 **모든** 기사에 document
    를 깔아 주므로, 그 기준이면 todo 가 항상 비어 이벤트가 영영 안 생긴다(Codex #137).
    조립만 남기는 자국인 document_entity(persist_normalization 전용 산출)로 판정한다 —
    비이벤트 기사는 자국이 없어 재실행 시 재분류되는데, 이는 엔진 원 동작과 같다.
    """
    if not source_ids:
        return set()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT d.source_document_id FROM document d"
            " JOIN document_entity de ON de.document_id = d.document_id"
            " WHERE d.source_document_id = ANY(%s)",
            (source_ids,),
        )
        return {str(r[0]) for r in cur.fetchall()}


# ── 계보 조립 (엔진 persist_normalization 이식 — 양방향 자연키 브리지 포함) ──
def persist_normalization(conn, rows: list[dict], classifications: dict[str, dict],
                          event_date: str) -> list[dict]:
    """document + canonical-event 계보 적재; 생성한 source event 목록 반환.

    엔진과 같은 자연키 브리지 2개를 그대로 유지한다(ALPHA-409·#133): document 와
    document_assertion 의 실제 행 ID 를 자연키로 다시 읽어 종속 계보를 그 ID 로 건다 —
    load-documents/load-assertions 가 먼저 적재한 행과도 FK 가 산다.
    psycopg2 execute_values → psycopg3 executemany 는 실행부 어댑트일 뿐 SQL 시맨틱 동일.

    엔진과 다른 지점 하나: source_code 를 'bigkinds' 로 하드코딩하지 않고 **기사 행의
    source_vendor** 를 쓴다 — LoadDocuments 가 (fmp, article_id)로 적재한 en 기사를
    bigkinds 키로 다시 넣으면 벤더 어긋난 중복 document 가 생긴다(Codex #137). 엔진은
    실질 ko/bigkinds 만 다뤄 잠복했던 결함이라, 정본 로직이 아니라 실행부 결함 수정이다.

    v4 확장(ALPHA-545): source_event 에 predicate_code·confidence_level·completeness,
    event_argument 에 slot·mention_text·entity_kind·group_ord, event_measure 신규(추출
    순서 = measure_ord). **미해소 참여자도 싣는다**(ALPHA-563) — entity_id=NULL +
    mention_text 로 남아 접지 질의에서만 빠진다. primary 엔티티가 참여자로 해소되지
    않았으면 구 단일역할 행(required_roles[0])을 폴백으로 실어 thread identity 연속성을
    지킨다.
    """
    created: list[dict] = []
    documents: list[tuple] = []
    news_docs: list[tuple] = []
    doc_entities: list[tuple] = []
    assertions: list[tuple] = []
    assertion_args: list[tuple] = []
    source_events: list[tuple] = []
    event_args: list[tuple] = []
    event_measures: list[tuple] = []
    evidences: list[tuple] = []

    by_id = {r["article_id"]: r for r in rows}
    pending: list[tuple[str, dict, dict, str, str]] = []
    for article_id, cls in classifications.items():
        row = by_id.get(article_id)
        if row is None:
            continue
        available_at = _iso(row["published_at"])
        source_code = row["source_vendor"]
        documents.append((
            _stable_id("doc", source_code, article_id), "NEWS", source_code, article_id,
            row["title"], row.get("language") or "ko", available_at, available_at,
        ))
        pending.append((article_id, row, cls, available_at, source_code))

    if not documents:
        return created

    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO document (document_id, document_type, source_code, source_document_id,"
            " title, language_code, published_at, available_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)"
            " ON CONFLICT (source_code, source_document_id) DO NOTHING",
            documents,
        )
        article_ids_by_source: dict[str, list[str]] = {}
        for article_id, _r, _c, _at, source_code in pending:
            article_ids_by_source.setdefault(source_code, []).append(article_id)
        # 문서 행의 available_at 까지 함께 읽는다 — 행이 load-documents 선적재분이면
        # available_at 이 fetched 기반이라, 지역 published 값을 비계에 실으면 두 스텝의
        # 실행 순서가 document_assertion.available_at 을 가른다(Codex #243 P2 수용).
        # 비계는 항상 **문서 행 값**을 실어 load-assertions 와 같은 출처를 공유한다.
        doc_by_key: dict[tuple[str, str], tuple[str, str]] = {}
        for source_code, ids in sorted(article_ids_by_source.items()):
            cur.execute(
                "SELECT source_document_id, document_id, available_at FROM document"
                " WHERE source_code = %s AND source_document_id = ANY(%s)",
                (source_code, ids),
            )
            for sdi, did, avail in cur.fetchall():
                doc_by_key[(source_code, sdi)] = (did, avail)

    for article_id, row, cls, available_at, source_code in pending:
        document_id, doc_available_at = doc_by_key[(source_code, article_id)]
        news_docs.append((document_id,))
        doc_entities.append((document_id, cls["entity_id"], row["title"], "mention",
                             cls["confidence"]))
        assertions.append((
            _stable_id("asrt", document_id, cls["event_type_code"], cls["predicate_code"]),
            document_id, cls["event_type_code"], cls["predicate_code"], doc_available_at,
        ))

    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO news_document (document_id) VALUES (%s)"
            " ON CONFLICT (document_id) DO NOTHING",
            news_docs,
        )
        cur.executemany(
            "INSERT INTO document_entity (document_id, entity_id, matched_text, link_method,"
            " confidence) VALUES (%s,%s,%s,%s,%s) ON CONFLICT (document_id, entity_id) DO NOTHING",
            doc_entities,
        )
        cur.executemany(
            # FK 비계 — 소유권 계약(모듈 독스트링)상 confidence·lifecycle_stage 는 안 싣는다.
            "INSERT INTO document_assertion (assertion_id, document_id, event_type_code,"
            " predicate_code, available_at) VALUES (%s,%s,%s,%s,%s)"
            " ON CONFLICT (document_id, event_type_code, predicate_code) DO NOTHING",
            assertions,
        )
        cur.execute(
            "SELECT document_id, event_type_code, predicate_code, assertion_id"
            " FROM document_assertion WHERE document_id = ANY(%s)",
            ([doc_by_key[(sc, a)][0] for a, _r, _c, _at, sc in pending],),
        )
        asrt_id_by_key = {(d, e, p): a for d, e, p, a in cur.fetchall()}

    for article_id, row, cls, available_at, source_code in pending:
        document_id = doc_by_key[(source_code, article_id)][0]
        entity_id = cls["entity_id"]
        assertion_id = asrt_id_by_key[(document_id, cls["event_type_code"], cls["predicate_code"])]
        assertion_args.append((assertion_id, cls["role_code"], entity_id, cls["confidence"]))

        source_event_id = _stable_id("evt", assertion_id, entity_id)
        source_events.append((
            source_event_id, "NEWS", cls["event_type_code"], event_date,
            cls["lifecycle_stage"], "ACTIVE", available_at,
            cls["predicate_code"], cls["confidence_level"], cls["completeness"],
        ))
        # 참여자 전원(다중역할) — **미해소 포함**(ALPHA-563). 참여자는 추출된 사실이고
        # 엔티티 해소는 별도 레인이라(runbook 레인 B), 마스터에 없다는 이유로 사실을 지우지
        # 않는다. 미해소는 entity_id=NULL + mention_text 로 남아 접지 질의에서만 빠진다.
        # 중복은 (role, 접지 또는 표면형)으로 접는다 — 같은 역할·같은 대상을 두 번 주장해도
        # 참여자는 하나다. 자연키 ON CONFLICT 는 접지된 행만 막아 주므로(UNIQUE 안의 NULL 은
        # 서로 구분된다) 미해소의 재실행 중복은 이 접기 + **기사 단위 멱등**이 막는다:
        # 자국(document_entity)과 이 행들은 run 전체를 감싼 한 트랜잭션에서 함께 커밋되고,
        # 자국이 있는 기사는 assembled_source_ids 가 애초에 걸러 여기 오지 않는다.
        seen_args: set[tuple[str, str]] = set()
        primary_present = False
        for part in cls["arguments"]:
            identity = part["entity_id"] or part["mention_text"]
            if identity is None:
                continue  # 접지도 표면형도 없으면 근거 0 — ck_event_argument_grounding 과 같은 판정
            key = (part["role_code"], identity)
            if key in seen_args:
                continue
            seen_args.add(key)
            if part["entity_id"] == entity_id:
                # primary 가 어떤 유효 역할로든 실렸으면 폴백 불필요 — 다중 identity 타입
                # (SUPPLIER/CUSTOMER)에서 identity 역할을 지어내면 기사에 없는 공급사
                # 주장이 생긴다(Codex #255 P2). anchor 부재는 unknown_thread 로 정직 강등.
                primary_present = True
            event_args.append((source_event_id, part["role_code"], part["entity_id"],
                               cls["confidence"], part["slot"], part["mention_text"],
                               part["entity_kind"], part["group_ord"]))
        if not primary_present and cls["anchor_role"]:
            # 구 단일역할 경로와 동형의 폴백 — 추출이 primary 를 아무 역할로도 안 냈고
            # (빈 응답 포함) anchor 역할이 유일할 때만 이벤트의 anchor 행을 세운다.
            event_args.append((source_event_id, cls["anchor_role"], entity_id, cls["confidence"],
                               None, None, _entity_kind(cls["anchor_role"], entity_id), None))
        for measure_ord, measure in enumerate(cls["measures"]):
            # measure_ord = 추출 순서(0..n) — 자연키. dart_rcept_no 는 뉴스 경로에선 없다.
            event_measures.append((source_event_id, measure_ord, measure["role_code"],
                                   measure["surface"], measure["value"], measure["unit"],
                                   measure["basis"], measure["value_source"],
                                   measure["parse_flag"], measure["group_ord"], None))
        evidence_id = _stable_id("evd", source_event_id, assertion_id, TITLE_EVIDENCE_TYPE)
        evidences.append((evidence_id, source_event_id, assertion_id, TITLE_EVIDENCE_TYPE,
                          row["title"], cls["confidence"]))
        created.append({
            "source_event_id": source_event_id,
            "evidence_id": evidence_id,
            "event_type_code": cls["event_type_code"],
            "entity_id": entity_id,
            "ticker": cls["primary_ticker"],
            "available_at": available_at,
            "title": row["title"],
            "confidence": cls["confidence"],
        })

    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO assertion_argument (assertion_id, role_code, entity_id, confidence)"
            " VALUES (%s,%s,%s,%s) ON CONFLICT (assertion_id, role_code, entity_id) DO NOTHING",
            assertion_args,
        )
        cur.executemany(
            "INSERT INTO source_event (source_event_id, source_class, event_type_code, event_date,"
            " lifecycle_stage, event_status, available_at, predicate_code, confidence_level,"
            " completeness) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
            " ON CONFLICT (source_event_id) DO NOTHING",
            source_events,
        )
        cur.executemany(
            "INSERT INTO event_argument (source_event_id, role_code, entity_id, confidence,"
            " slot, mention_text, entity_kind, group_ord) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)"
            " ON CONFLICT (source_event_id, role_code, entity_id) DO NOTHING",
            event_args,
        )
        cur.executemany(
            "INSERT INTO event_measure (source_event_id, measure_ord, role_code, surface,"
            " value, unit, basis, value_source, parse_flag, group_ord, dart_rcept_no)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
            " ON CONFLICT (source_event_id, measure_ord) DO NOTHING",
            event_measures,
        )
        cur.executemany(
            "INSERT INTO event_evidence (evidence_id, source_event_id, assertion_id, evidence_type,"
            " evidence_text, link_confidence) VALUES (%s,%s,%s,%s,%s,%s)"
            " ON CONFLICT (evidence_id) DO NOTHING",
            evidences,
        )
    return created


def _thread_key(event_type_code: str, role_values: dict[str, str]) -> tuple[str | None, list[str]]:
    """계약 thread_key(§2) — `event_type_id=X||required:ROLE=value||...`(identity_roles 순).

    (key, missing_roles) 를 돌려준다. identity 역할이 하나라도 비면 key=None —
    계약 novelty 0단계(required identity 결측 → UNKNOWN)이자 불변식 5
    (missing_identity_policy=EMIT_UNKNOWN_LINK_ONLY): synthetic thread 를 억지로 만들지 않는다.

    엔진 `_thread_key`(alphamale threading.py) 대비 두 가지를 뺀다:
    `thread_scope`(엔진 실물 코드도 안 넣는다 — 계약 문서에만 있는 유령)와 optional
    discriminator(edge 추출은 역할당 값 하나뿐이라 optional 값이 존재할 수 없다, YAGNI).
    값은 엔진의 리치 스칼라(ticker/concept_id) 대신 edge 의 결정적 `entity_id` 다 — edge 는
    개념 역할을 해소하지 않아 identity 를 채울 수 있는 건 entity 역할뿐이다(그 밖은
    위에서 UNKNOWN 으로 빠진다). 엔진 thread_id 와의 byte 수렴은 값·해시가 달라 애초에
    불가라 추구하지 않는다(edge-native, 엔진 축소 후 edge 가 정본). v4 다중역할 기록으로
    CUSTOMER 등 entity identity 역할이 채워지는 만큼 UNKNOWN 이 줄어든다(ALPHA-545).
    """
    roles = identity_roles(event_type_code)
    # falsy(None·빈 문자열) 값은 결측으로 본다 — 계약 _identity_scalar 이 falsy 를 값 없음으로
    # 판정하므로(edge-review), 키 존재만 보면 `required:CUSTOMER=None` 같은 헛 스레드가 서서
    # 다른 계약이 다시 뭉갠다. `not role_values.get(r)` 로 결측·None·빈값을 한 번에 건다.
    missing = [r for r in roles if not role_values.get(r)]
    if not roles or missing:
        return None, (missing or ["<contract: no identity roles>"])
    parts = [f"event_type_id={event_type_code}"]
    parts += [f"required:{r}={role_values[r]}" for r in roles]
    return "||".join(parts), []


def _stage_rank(event_type_code: str, stage: str | None) -> int | None:
    """타입 lifecycle 순서축에서의 위치 — 메뉴 밖(구 오염 행 포함)·결측은 None(정보 없음)."""
    if not stage:
        return None
    seq = _stage_sequence(event_type_code)
    try:
        return seq.index(stage)
    except ValueError:
        return None


def _novelty(event: dict, prior: int, thread_stage: str | None) -> str:
    """계약 novelty 판정(news_thread_contract) — DB CHECK 5종 안의 값만 낸다.

    prior=0 → FIRST_IN_THREAD. 그 뒤로는: 정정 마커(predicate/stage 에 correction_markers
    포함, 예: REVISE·CANCELLED) → CORRECTION, stage 가 스레드의 현 단계보다 **진행**(순서축
    전진 또는 첫 단계 정보) → FOLLOW_UP_STAGE, 그 외(단계 정보 없음·동일·후퇴) →
    DUPLICATE_REBROADCAST. 구 구현은 prior>0 을 전부 FOLLOW_UP_STAGE 로 뭉쳐 재보도와
    진행을 구분 못 했다(ALPHA-545).
    """
    if prior == 0:
        return "FIRST_IN_THREAD"
    predicate = str(event.get("predicate_code") or "").upper()
    stage = event.get("lifecycle_stage")
    stage_upper = str(stage or "").upper()
    if any(marker in predicate or marker in stage_upper for marker in CORRECTION_MARKERS):
        return "CORRECTION"
    rank = _stage_rank(event["event_type_code"], stage)
    if rank is not None:
        prior_rank = _stage_rank(event["event_type_code"], thread_stage)
        if prior_rank is None or rank > prior_rank:
            return "FOLLOW_UP_STAGE"
    return "DUPLICATE_REBROADCAST"


def thread_events(conn, events: list[dict]) -> int:
    """event_thread 계보 — 계약 identity_roles 기반 thread_key + novelty(ALPHA-457·545).

    thread_key 는 그 타입의 `identity_roles` 값들로 구성한다(엔진 정본 §2). identity 를 못
    채우는 이벤트(개념·날짜 역할 등 미해소)는 thread 를 만들지 않고
    `novelty_status='UNKNOWN'`·`thread_id=NULL`·`unknown_reason` 으로 link/snapshot 만
    남긴다(불변식 5). **UNKNOWN 처리 건수를 반환한다** — 조용히 삼키지 않고 run 로그에
    드러내기 위함(Rule 12).

    novelty 는 `_novelty` 가 계약 5종으로 세분한다(정정=CORRECTION·stage 진행=FOLLOW_UP_STAGE·
    재보도=DUPLICATE_REBROADCAST). 스레드의 현 단계는 event_thread.current_stage 에서 시드하고
    배치 내에선 순서축 전진일 때만 갱신한다(메뉴 밖 오염 값은 rank=None 이라 헤더를 못 건드린다).

    알려진 천장(엔진 정본 그대로): prior 판정이 기존 링크 **총수** 기준이라, 이미 처리된
    날짜보다 **오래된** 날짜를 나중에 백필하면 novelty 가 역전된다(옛 이벤트가 FOLLOW_UP).
    런 내부는 available_at 정렬이라 안전 — 런 간 역순 백필만 해당. 회피는 운영 지침
    (백필은 과거→현재 순)이고, 시각 기준 novelty 재판정은 threading 로직 소유자 안건.
    current_stage 도 같은 천장을 공유한다(역순 백필이 단계를 되돌릴 수 있다).
    """
    if not events:
        return 0
    threads: dict[str, tuple] = {}
    links: list[tuple] = []
    snapshots: list[tuple] = []
    evaluated_at = _utcnow_iso()

    keyed: list[tuple[dict, str]] = []          # identity 충족 — 스레드 대상
    unknown: list[tuple[dict, list[str]]] = []  # identity 결측 — UNKNOWN
    for event in events:
        thread_key, missing = _thread_key(event["event_type_code"], event["role_values"])
        (unknown.append((event, missing)) if thread_key is None
         else keyed.append((event, thread_key)))

    prior_counts = _thread_prior_counts(conn, [k for _e, k in keyed])
    thread_stages = _thread_current_stages(conn, [k for _e, k in keyed])
    per_thread_seen: dict[str, int] = {}
    for event, thread_key in sorted(keyed, key=lambda ek: ek[0]["available_at"]):
        thread_id = _stable_id("thr", thread_key)
        prior = prior_counts.get(thread_key, 0) + per_thread_seen.get(thread_key, 0)
        novelty = _novelty(event, prior, thread_stages.get(thread_key))
        per_thread_seen[thread_key] = per_thread_seen.get(thread_key, 0) + 1
        # current_stage 는 순서축 **전진**일 때만 갱신 — 재보도·후퇴·메뉴 밖 값이 스레드
        # 상태를 되돌리거나 오염시키지 못한다.
        stage = event.get("lifecycle_stage")
        rank = _stage_rank(event["event_type_code"], stage)
        if rank is not None:
            current_rank = _stage_rank(event["event_type_code"], thread_stages.get(thread_key))
            if current_rank is None or rank > current_rank:
                thread_stages[thread_key] = stage
        # 같은 배치에 같은 스레드 이벤트가 여럿이면 opened_at 은 **첫**(가장 이른) 이벤트,
        # last_state_at 만 갱신 — 마지막 대입으로 덮으면 opened_at 이 멤버보다 늦어진다.
        prev_row = threads.get(thread_key)
        opened_at = prev_row[4] if prev_row else event["available_at"]
        threads[thread_key] = (thread_id, thread_key, event["event_type_code"],
                               thread_stages.get(thread_key), opened_at, event["available_at"])
        links.append((event["source_event_id"], thread_id, "NEWS", novelty, "TITLE_EVENT",
                      evaluated_at, None))
        snapshots.append((event["source_event_id"], thread_id, prior, None, prior == 0,
                          None, evaluated_at))

    for event, missing in unknown:
        reason = "missing required identity roles: " + ", ".join(missing)
        links.append((event["source_event_id"], None, "NEWS", "UNKNOWN", "TITLE_EVENT",
                      evaluated_at, reason))
        snapshots.append((event["source_event_id"], None, None, None, None, reason, evaluated_at))

    with conn.cursor() as cur:
        if threads:
            # 백필(--from/--to)이 기존 스레드보다 **오래된** 이벤트를 넣을 수 있다 — 단순
            # 대입이면 last_state_at 이 역행하고, opened_at 보다 앞서면 ck_event_thread_time
            # 위반으로 백필 전체가 롤백된다. 시각은 단조로 유지한다(엔진은 '오늘'만 돌아
            # 이 경로가 없었다 — 백필 능력이 생기며 필요해진 실행부 보강). current_stage 는
            # 배치 계산이 DB 시드 기준 단조라 COALESCE 병합으로 충분하다(NULL 로 되돌리지 않음).
            cur.executemany(
                "INSERT INTO event_thread (thread_id, thread_key, event_type_code, current_stage,"
                " opened_at, last_state_at) VALUES (%s,%s,%s,%s,%s,%s)"
                " ON CONFLICT (thread_key) DO UPDATE SET"
                " opened_at = LEAST(event_thread.opened_at, EXCLUDED.opened_at),"
                " last_state_at = GREATEST(event_thread.last_state_at, EXCLUDED.last_state_at),"
                " current_stage = COALESCE(EXCLUDED.current_stage, event_thread.current_stage)",
                list(threads.values()),
            )
        # UNKNOWN↔thread_id NULL 커플링 CHECK 는 각 행의 EXCLUDED 값이 자기정합이라 통과한다.
        # 재threading 이 이전 판정을 뒤집을 수 있어(UNKNOWN↔fillable) thread_id·novelty·
        # unknown_reason 을 함께 갱신한다.
        cur.executemany(
            "INSERT INTO event_thread_link (source_event_id, thread_id, source_class,"
            " novelty_status, link_type, evaluated_at, unknown_reason) VALUES (%s,%s,%s,%s,%s,%s,%s)"
            " ON CONFLICT (source_event_id) DO UPDATE SET thread_id = EXCLUDED.thread_id,"
            " novelty_status = EXCLUDED.novelty_status, evaluated_at = EXCLUDED.evaluated_at,"
            " unknown_reason = EXCLUDED.unknown_reason",
            links,
        )
        cur.executemany(
            "INSERT INTO thread_discovery_snapshot (source_event_id, thread_id, prior_event_count,"
            " days_since_previous_stage, is_novel, unknown_reason, evaluated_at)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s)"
            " ON CONFLICT (source_event_id) DO UPDATE SET thread_id = EXCLUDED.thread_id,"
            " prior_event_count = EXCLUDED.prior_event_count, is_novel = EXCLUDED.is_novel,"
            " unknown_reason = EXCLUDED.unknown_reason, evaluated_at = EXCLUDED.evaluated_at",
            snapshots,
        )
    return len(unknown)


def fetch_unthreaded_events(conn, event_date: str) -> list[dict]:
    """그 event_date 의 아직 event_thread_link 가 없는 NEWS source_event 전체.

    threading 대상을 '이번 run 신규분(created)'이 아니라 '미연결 전체'로 잡는다 — 분류는
    비싸 이미 조립된 기사를 건너뛰지만(assembled_source_ids), threading 은 싸고 멱등이라
    재실행마다 미연결분을 채워야 한다. 안 그러면 배포 전 KODEX-only 로 조립돼 미연결로 남은
    과거 이벤트가 영영 계보 없이 남고, _thread_prior_counts 가 그걸 못 세 같은 스레드 새
    이벤트의 novelty 까지 오염된다(ALPHA-468 edge-review). 이미 엮인 이벤트는 제외라
    prior_count 와 겹치지 않아 novelty 판정이 안전하다(단 UNKNOWN 링크는 재평가 대상으로
    다시 포함한다 — 아래 SQL 주석). source_event 는 in-universe 로만 조립되므로 별도 유니버스
    필터가 필요 없다.

    이벤트마다 **역할별 값 맵**(`role_values`: role_code → entity_id)을 싣는다 — thread_key 가
    identity_roles 전 역할 값을 필요로 하기 때문(ALPHA-457). 그래서 DISTINCT ON 단일 entity
    가 아니라 event_argument 전 행을 모아 role 별로 접는다. 한 역할에 값이 여럿이면 정렬된
    JSON 배열로 축약한다(계약 `_collect_role_values` 규약 — 결정적 순서).
    lifecycle_stage·predicate_code 도 함께 싣는다 — novelty 세분(_novelty)의 입력이다(ALPHA-545).
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT se.source_event_id, se.event_type_code, se.available_at,"
            " se.lifecycle_stage, se.predicate_code, ea.role_code, ea.entity_id"
            " FROM source_event se"
            # LEFT JOIN — 아규먼트 0건 이벤트(다중 primary 타입에서 anchor 조작을 안 한 건)도
            # 스레딩에 넘겨 계약대로 UNKNOWN 링크를 받게 한다. INNER 면 그 이벤트가 조회에서
            # 빠져 영구 미연결로 남고 unknown_thread 지표에서도 사라진다(Codex #255 P2).
            " LEFT JOIN event_argument ea ON ea.source_event_id = se.source_event_id"
            " LEFT JOIN event_thread_link etl ON etl.source_event_id = se.source_event_id"
            # event_status='ACTIVE' 는 엔진 read 경로(fetch_kodex_events)와 같은 필터 — 비활성
            # (REJECTED 등) 이벤트를 엮으면 prior_count 가 그걸 세 같은 스레드 첫 ACTIVE 를
            # 잘못 FOLLOW_UP 으로 판정한다(edge-review). 설명이 읽는 집합과 threading 집합을 맞춘다.
            # 미연결(link 없음) + **기존 UNKNOWN 링크**를 대상으로 한다 — 계약상 UNKNOWN 은
            # 재평가 가능한 상태라(identity 가 나중에 채워지면 승격), 링크 있다고 영구 제외하면
            # UNKNOWN 에 갇힌다(edge-review). UNKNOWN 링크는 thread_id NULL 이라 _thread_prior_counts
            # (thread_id 로 셈)에 안 잡혀, 재조회해도 novelty 판정이 안전하다.
            " WHERE se.event_date = %s AND se.source_class = 'NEWS'"
            " AND se.event_status = 'ACTIVE'"
            " AND (etl.source_event_id IS NULL OR etl.novelty_status = 'UNKNOWN')"
            " ORDER BY se.source_event_id, ea.role_code, ea.entity_id",
            (event_date,),
        )
        rows = cur.fetchall()
    events: dict[str, dict] = {}
    role_lists: dict[str, dict[str, list[str]]] = {}
    for sid, event_type_code, available_at, stage, predicate, role_code, entity_id in rows:
        sid = str(sid)
        event = events.get(sid)
        if event is None:
            event = events[sid] = {"source_event_id": sid, "event_type_code": event_type_code,
                                   "available_at": _iso(available_at),
                                   "lifecycle_stage": stage, "predicate_code": predicate,
                                   "role_values": {}}
            role_lists[sid] = {}
        if role_code is None or entity_id is None:
            # 아규먼트 0건, 또는 **미해소 참여자**(ALPHA-563) — 둘 다 identity 재료가 아니다.
            # 미해소를 str(None)="None" 으로 실으면 서로 다른 사건(각자 다른 미해소 고객사)이
            # 같은 thread_key 로 접혀 한 계보가 되고, 나중에 마스터가 채워져 해소되는 순간
            # key 가 바뀌어 계보가 갈린다. 미해소는 값이 아니라 **부재**라, role_values 에서
            # 빼 identity 미충족 → UNKNOWN 링크로 정직하게 강등한다.
            continue
        role_lists[sid].setdefault(role_code, []).append(str(entity_id))
    for sid, roles in role_lists.items():
        events[sid]["role_values"] = {
            role: (values[0] if len(values) == 1
                   else json.dumps(sorted(values), ensure_ascii=False))
            for role, values in roles.items()
        }
    return list(events.values())


def _thread_prior_counts(conn, thread_keys: list[str]) -> dict[str, int]:
    if not thread_keys:
        return {}
    thread_ids = {_stable_id("thr", tk): tk for tk in thread_keys}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT thread_id, COUNT(*) FROM event_thread_link WHERE thread_id = ANY(%s)"
            " GROUP BY thread_id",
            (list(thread_ids),),
        )
        counts = {str(tid): int(n) for tid, n in cur.fetchall()}
    return {thread_ids[tid]: n for tid, n in counts.items()}


def _thread_current_stages(conn, thread_keys: list[str]) -> dict[str, str]:
    """thread_key → 기존 event_thread.current_stage(있는 것만) — novelty 판정의 시드."""
    if not thread_keys:
        return {}
    thread_ids = {_stable_id("thr", tk): tk for tk in thread_keys}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT thread_id, current_stage FROM event_thread WHERE thread_id = ANY(%s)",
            (list(thread_ids),),
        )
        stages = {str(tid): stage for tid, stage in cur.fetchall()}
    return {thread_ids[tid]: stage for tid, stage in stages.items() if stage}


def run(
    storage: Storage,
    run_id: str,
    *,
    db: DbConfig,
    complete_fn,
    from_date: str | None = None,
    to_date: str | None = None,
    window_days: int | None = None,
    concurrency: int = DEFAULT_CLASSIFY_CONCURRENCY,
) -> int:
    """canonical 뉴스 → v4 2콜 추출 → event 계보 조립 → threading. 성공 0, 장애 시 비0.

    창(from/to) 미지정이면 **오늘(Asia/Seoul) 하루**다 — 엔진의 일일 시맨틱을 따른다
    (tag-news 류의 전체 스캔 기본과 다름: 분류 LLM 비용이 기사 수에 비례한다). 과거
    구간은 창으로 백필한다. 이미 정규화된 기사(document 존재)는 건너뛰므로 멱등이다.

    window_days(ALPHA-592)는 그 기준일에서 N일 소급해 창을 겹친다(from/to 명시가 우선).
    뉴스 SFN 23:50 슬롯은 체인 소요(9~14분)가 자정을 넘겨 assemble 이 다음 날짜로 도는 게
    기본 경로라(2026-07-28 00:03 read=0 라이브 실측), 겹침 없이는 그날 늦저녁 기사가 영영
    조립되지 않는다. 멱등(document-exists skip)이라 겹침 비용은 스캔뿐이다.
    """
    started_at = datetime.now(timezone.utc)
    news_read = in_universe_count = already_normalized = 0
    classified = events_created = threaded = unknown_thread = 0
    stage_rejected = arguments_unresolved = anchorless_events = 0
    dart_matched = dart_ambiguous = 0
    failures: list[dict] = []
    exit_code = 0

    if from_date is None and to_date is None:
        today = datetime.now(_KST).date()
        from_date = (today - timedelta(days=window_days or 0)).isoformat()
        to_date = today.isoformat()

    try:
        view = load_ontology_view()
        all_dates = sorted(set().union(*[set(_partition_dates(storage, lang))
                                         for lang in LANGUAGES]) if LANGUAGES else set())
        targets = [d for d in all_dates
                   if (from_date is None or d >= from_date) and (to_date is None or d <= to_date)]

        with connect(db) as conn:
            entity_index = load_entity_index(conn)
            for date in targets:
                news = read_daily_news(storage, date)
                news_read += len(news)
                in_universe = [n for n in news if any(t in entity_index for t in n["tickers"])]
                in_universe_count += len(in_universe)
                already = assembled_source_ids(conn, [n["article_id"] for n in in_universe])
                todo = [n for n in in_universe if n["article_id"] not in already]
                already_normalized += len(in_universe) - len(todo)
                classifications = (classify_titles(complete_fn, todo, view, entity_index,
                                                   concurrency=concurrency)
                                   if todo else {})
                classified += len(classifications)
                # 품질 카운터(Rule 12) — stage 오염 차단·엔티티 해소 실패를 로그로 드러낸다.
                stage_rejected += sum(
                    1 for c in classifications.values() if c.get("stage_rejected"))
                arguments_unresolved += sum(
                    1 for c in classifications.values()
                    for p in c.get("arguments", ()) if p["entity_id"] is None)
                # **접지** 참여자 0건 이벤트 — 해소된 참여자도 없고 anchor 역할도 유일하지
                # 않아(다중 primary) 폴백을 조작하지 않은 건. 행 자체는 남지만(ALPHA-563
                # 미해소 보존) 접지가 없어 thread identity 와 엔진 선별(EXISTS JOIN
                # instrument)에서 빠지므로 카운터로 드러낸다(Rule 12).
                anchorless_events += sum(
                    1 for c in classifications.values()
                    if not c.get("anchor_role")
                    and not any(p["entity_id"] is not None for p in c.get("arguments", ())))
                created = persist_normalization(conn, todo, classifications, date)
                events_created += len(created)
                # 유니버스(entity_index=holdings 파생 마스터) 전 구성종목 이벤트를 threading 한다
                # (ALPHA-468). 과거 KODEX 9종 한정은 엔진이 KODEX 반도체만 설명하던 잔재였고,
                # 다중 ETF 설명(ALPHA-465·467)은 계보·신규성이 유니버스 전체에 필요하다.
                # in_universe/entity 해소가 이미 유니버스 필터라 별도 파생이 없다. 대상은
                # created 가 아니라 그 날짜의 미연결 전체 — 재실행이 과거 미연결분을 self-heal.
                to_thread = fetch_unthreaded_events(conn, date)
                unknown = thread_events(conn, to_thread)
                # threaded = 실제로 스레드가 선 것, unknown_thread = identity 결측으로 UNKNOWN
                # (thread_id NULL). 둘을 갈라 로그에 남긴다 — UNKNOWN 을 threaded 로 뭉치면
                # 계약상 스레드가 안 선 사실이 묻힌다(ALPHA-457, Rule 12).
                threaded += len(to_thread) - unknown
                unknown_thread += unknown

            # DART 값 승격(ALPHA-547) — 같은 런·같은 트랜잭션에서 PARSED 금액을 공시 사실과
            # 대조해 승격한다. event_measure 의 INSERT writer 는 조립뿐이고, 이 호출은
            # value_source·dart_rcept_no 두 컬럼만 UPDATE 한다(컬럼 소유 분리, ALPHA-538 규약).
            if targets:
                dart_matched, dart_ambiguous = match_dart_values(conn, targets[0], targets[-1])
    except Exception as exc:
        # 커밋 경계는 런 전체 — connect() 가 예외면 롤백이라 부분 적재가 없다(Rule 12).
        logger.exception("이벤트 조립 실패(롤백)")
        failures.append({"reasons": ["assemble_error"], "error": str(exc)})
        events_created = threaded = unknown_thread = 0
        stage_rejected = arguments_unresolved = anchorless_events = 0
        dart_matched = dart_ambiguous = 0
        exit_code = 1

    log = {
        "job": JOB_NAME, "run_id": run_id, "dataset": DATASET,
        "assembler_version": ASSEMBLER_VERSION,
        "started_at": started_at.isoformat(), "finished_at": datetime.now(timezone.utc).isoformat(),
        "from_date": from_date, "to_date": to_date, "languages": list(LANGUAGES),
        "news_read": news_read, "in_universe": in_universe_count,
        "already_normalized": already_normalized, "classified": classified,
        "events_created": events_created, "threaded": threaded,
        "unknown_thread": unknown_thread,
        "stage_rejected": stage_rejected,
        "arguments_unresolved": arguments_unresolved,
        "anchorless_events": anchorless_events,
        "dart_matched": dart_matched, "dart_ambiguous": dart_ambiguous,
        "failures": failures, "exit_code": exit_code,
        # 원장 관측용 공통 봉투(ALPHA-181). ⚠️ 유실이 아닌 것: `already_normalized`(멱등)·
        # 유니버스 밖 기사. 유실은 **분류했는데 이벤트가 쓸모없게 된 것** — stage 오염으로 거절된
        # 건과 아규먼트가 통째로 빈 건(event_argument 가 비면 threading·엔진 소비에서 빠진다).
        # `arguments_unresolved`(일부 인자 미해소)는 이벤트 자체는 남으므로 세지 않는다.
        # 봉투는 **이 런이 실제로 판정한 범위**만 말한다. `already_normalized` 를 산출에 더하면
        # 안 된다 — 그 기사들은 분류를 다시 하지 않으므로 옛 stage_rejected·anchorless 가
        # 실패 카운터에서 빠진 채 산출로만 잡혀 결함 이벤트가 정상으로 뒤집힌다.
        # 멱등 no-op 재실행이 0건 → UNKNOWN 인 것은 정직한 결과다(상태 기반 완전성은 ALPHA-490).
        "ops": {
            "records_out": events_created,
            "failed_records": len(failures) + stage_rejected + anchorless_events,
        },
    }
    try:
        storage.put_bytes(quality_log_key(DATASET, started_at.isoformat()[:10], run_id),
                          json.dumps(log, ensure_ascii=False, indent=2).encode("utf-8"))
    except Exception:
        logger.exception("적재 로그 기록 실패")
        exit_code = 1

    logger.info(
        "assemble_events: read=%d in_universe=%d already=%d classified=%d created=%d"
        " threaded=%d unknown_thread=%d stage_rejected=%d unresolved=%d anchorless=%d"
        " dart_matched=%d dart_ambiguous=%d failures=%d",
        news_read, in_universe_count, already_normalized, classified, events_created,
        threaded, unknown_thread, stage_rejected, arguments_unresolved, anchorless_events,
        dart_matched, dart_ambiguous, len(failures),
    )
    return exit_code
