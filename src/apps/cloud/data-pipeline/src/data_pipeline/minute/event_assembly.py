"""1분 뉴스 추출 결과 → event 계보 단건 조립 (ALPHA-727).

assemble-events(일 배치)와 **같은 함수·같은 결정적 ID 산식**으로 적재한다 — 두 writer
가 갈리면 같은 기사에 다른 계보가 선다. 이 모듈의 일은 형상 변환과 참여자 해소뿐이고,
검증(`_validate_extraction`)·적재(`persist_normalization`)·threading(`thread_events`)은
전부 assemble_events 의 것을 재사용한다(그 함수들은 conn 만 받는다 — LLM 결합 없음).

- **참여자 해소**: 추출 결과(`extract_assertions`)의 arguments 는 표면형뿐이다
  (`entity_id: None` 고정, ticker 없음). `entity_resolution.resolve` 로 instrument 를
  해소하고 ticker 로 역매핑해 `_validate_extraction` 의 입력 계약(ticker 축)에 맞춘다.
  authority·concept 해소는 그 함수 안에 이미 있다.
- **primary**: 해소된 instrument 참여자 중 첫 번째(assertion 서술 순). 없으면 그
  assertion 은 조립 불가다(`document_entity.entity_id` NOT NULL·evt id 재료) — 세어서
  로그로 드러낸다. 유니버스 밖 기사는 여기서 자연히 걸러진다(해소 인덱스가 마스터 축).
- **lifecycle_stage 는 항상 NULL** — 태깅 프롬프트가 stage 를 묻지 않는다(온톨로지
  결정, tagging/ontology.py). novelty 는 stage 세분 없이 판정된다(알려진 한계 — 태깅
  프롬프트 확장은 별건).
- **멱등 게이트 = document_entity 자국**(배치 `assembled_source_ids` 와 같은 축) —
  재태깅의 응답 순서 흔들림이 event_measure(measure_ord 자연키)에 다른 행을 남기는
  것을 막고, 배치·단건 두 writer 의 이중 조립도 막는다.
- threading 직렬화 락은 `thread_events` **안**에 있다 — 모든 호출자(배치·realtime·
  backfill 소비자, ALPHA-548 백필)가 같은 락을 지나므로 novelty 의 read-modify-write
  가 writer 수와 무관하게 안전하다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

import json

from ..config import DbConfig
from ..db import connect, stable_domain_id
from ..entity_resolution import load_resolution_index, resolve
from ..steps.assemble_events import (
    _validate_extraction,
    load_entity_index,
    persist_normalization,
    thread_events,
)
from .models import KST

logger = logging.getLogger(__name__)


@dataclass
class NewsEventAssembler:
    """추출 성공 1건을 event 계보로 조립한다 — news-consumer 의 적재 꼬리."""

    db: DbConfig

    def assemble(
        self, *, source_code: str, article_id: str, article: dict, result: dict
    ) -> dict:
        """조립하고 카운터를 반환한다. 예외는 전파한다 — 조립 실패를 삼키면 job 이
        SUCCEEDED 로 확정돼 이 기사의 event 가 영영 없다(재시도가 정답)."""
        assertions = result.get("assertions") or []
        if result.get("status") != "ok" or not assertions:
            return {"assembled": 0, "skipped": "no_assertions"}
        published_at = article.get("published_at")
        if not published_at:
            # event_date·available_at 의 축 — 없으면 assemble 의 `_iso(None)` 이
            # **현재시각**을 찍어 재실행마다 다른 값이 되고 결정성이 깨진다.
            logger.warning("published_at 없음 — 조립 불가: (%s, %s)",
                           source_code, article_id)
            return {"assembled": 0, "skipped": "no_published_at"}
        event_date = (
            datetime.fromisoformat(str(published_at)).astimezone(KST).date().isoformat()
        )

        with connect(self.db) as conn:
            doc_id = stable_domain_id("doc", source_code, article_id)
            with conn.cursor() as cur:
                # doc 단위 직렬화 — 멱등 게이트(아래 SELECT)와 적재가 한 검사-후-행동
                # 이라, 락 없이는 realtime·backfill 소비자가 같은 기사를 동시에 통과해
                # 이중 조립한다. doc 별 락이라 서로 다른 기사는 병렬 그대로다.
                # ⚠️ 배치(assemble)는 자국 조회 후 LLM 분류까지 긴 창이 있어 이 락으로
                # 못 막는다 — 배치 강등(후속 티켓)에서 배치 쪽 재확인으로 닫는다.
                cur.execute(
                    "SELECT pg_advisory_xact_lock(hashtext('edge-event-assembly:' || %s))",
                    (doc_id,),
                )
                # ponytail: 조립 커밋 직후·checksum 반환 직전에 프로세스가 죽으면 다음
                # claim(attempt+1)이 LLM 을 다시 불러 **다른 결과 artifact** 를 남기고,
                # 이 게이트는 조립을 건너뛴다 — job.result_checksum 과 event 계보가
                # 서로 다른(둘 다 유효한) 추출을 가리킬 수 있다. 좁은 crash 창 한정이고
                # 계보-artifact 대조는 EOD QC 축 — 내용 기반 재조립이 필요해지면 그때.
                cur.execute(
                    "SELECT 1 FROM document_entity WHERE document_id = %s LIMIT 1",
                    (doc_id,),
                )
                if cur.fetchone() is not None:
                    return {"assembled": 0, "skipped": "already_assembled"}

            view = _process_registry()
            entity_index = load_entity_index(conn)
            ticker_by_entity = {v: k for k, v in entity_index.items()}
            res_index = load_resolution_index(conn)
            row = {
                "article_id": article_id,
                "published_at": str(published_at),
                "source_vendor": source_code,
                "title": article.get("title"),
                "language": article.get("language_code"),
            }
            assembled = 0
            unresolved_primary = 0
            # evt id 로 dedup — 같은 (type, predicate, primary) 중복 assertion 은 같은
            # 결정적 source_event_id 를 낸다. 목록에 두 번 실으면 thread_events 가
            # 두 번째 항목에서 자기 자신을 prior 로 세어 첫 사건이
            # DUPLICATE_REBROADCAST 로 오염된다.
            new_events: dict[str, dict] = {}
            for assertion in assertions:
                cls = self._to_classification(
                    assertion, article_id=article_id, view=view,
                    entity_index=entity_index, ticker_by_entity=ticker_by_entity,
                    res_index=res_index,
                )
                if cls is None:
                    unresolved_primary += 1
                    continue
                created = persist_normalization(conn, [row], {article_id: cls}, event_date)
                assembled += len(created)
                for made in created:
                    new_events.setdefault(
                        made["source_event_id"],
                        _thread_event(made, cls, str(published_at)),
                    )
            if new_events:
                # **이번 기사 신규분만** 엮는다 — 배치처럼 날짜 전체 미연결·UNKNOWN 을
                # 재조회하면 UNKNOWN 이 쌓인 날 기사마다 그 전량을 재기록해 제곱형
                # DB 작업 + 전역 락 점유가 된다. UNKNOWN 재평가·미연결 잔여 회수는
                # 배치(fetch_unthreaded_events)의 일이다. 직렬화는 thread_events 안의 락.
                thread_events(conn, list(new_events.values()))
        if unresolved_primary:
            # 해소 실패는 유니버스 밖 기사(정상)와 마스터 결손(결함)이 같은 얼굴이다 —
            # 조용히 접지 않고 남긴다. 하루 단위 판정은 EOD QC 소관.
            logger.info("primary 미해소 assertion %d건 — 조립 생략: (%s, %s)",
                        unresolved_primary, source_code, article_id)
        return {"assembled": assembled, "unresolved_primary": unresolved_primary}

    def _to_classification(
        self, assertion: dict, *, article_id: str, view, entity_index: dict[str, str],
        ticker_by_entity: dict[str, str], res_index,
    ) -> dict | None:
        """추출 assertion 1건 → assemble 의 분류(cls) 형상. 불가면 None.

        `_validate_extraction` 의 raw item 계약(ticker 축·measures 분리)으로 되접는다 —
        slot·entity_kind·authority/concept 해소·수량 파싱·completeness 를 전부 그쪽
        코드로 재사용하기 위해서다(두 벌이 되면 writer 끼리 갈린다).
        """
        event_type = str(assertion.get("event_type_code") or "")
        tv = view.types.get(event_type)
        if tv is None:
            return None
        raw_arguments = assertion.get("arguments")
        arguments = []
        resolved_tickers: set[str] = set()
        primary_ticker: str | None = None
        for raw in (raw_arguments if isinstance(raw_arguments, list) else ()):
            if not isinstance(raw, dict):
                continue
            mention = raw.get("text")
            if not isinstance(mention, str) or not mention.strip():
                continue
            entity_id, _reason = resolve(res_index, mention)
            ticker = ticker_by_entity.get(entity_id) if entity_id else None
            if ticker:
                resolved_tickers.add(ticker)
                if primary_ticker is None:
                    primary_ticker = ticker
            arguments.append({
                "role": raw.get("role_code"),
                "mention": mention,
                "ticker": ticker or "",
                "group": None,
            })
        if primary_ticker is None:
            return None
        # 수량 역할은 arguments 에 섞여 온다(태깅 허용역할 = required ∪ optional) —
        # _validate_extraction 은 measures 를 별도 키로 기대하므로 여기서 가른다.
        measures = [
            {"role": a["role"], "surface": a["mention"], "basis": None, "group": None}
            for a in arguments if a["role"] in tv.quantity_roles
        ]
        participant_args = [a for a in arguments if a["role"] not in tv.quantity_roles]
        confidence = assertion.get("confidence")
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            confidence = None
        if confidence is not None and not 0.0 <= confidence <= 1.0:
            confidence = None
        gate_cls = {
            "article_id": article_id,
            "event_type_code": event_type,
            "primary_ticker": primary_ticker,
            "entity_id": entity_index[primary_ticker],
            "role_code": tv.required_roles[0] if tv.required_roles else "ISSUER",
            # 게이트와 같은 규약(assemble `_validate_gate`) — 다중 primary 타입은
            # 어느 역할인지 코드가 모르므로 조작하지 않는다.
            "anchor_role": tv.primary_roles[0] if len(tv.primary_roles) == 1 else None,
            "confidence": confidence,
        }
        item = {
            "id": article_id,
            "predicate": assertion.get("predicate_code"),
            "stage": None,  # 태깅은 stage 를 묻지 않는다 — NULL(모듈 독스트링)
            "confidence": None,  # H/M/L 문자열 축 — 태깅 float 와 축이 달라 싣지 않는다
            "arguments": participant_args,
            "measures": measures,
        }
        return _validate_extraction(item, view, gate_cls, entity_index, resolved_tickers)


def _thread_event(created: dict, cls: dict, published_at: str) -> dict:
    """persist 산출 1건 → `thread_events` 입력 형상.

    role_values 는 배치 `fetch_unthreaded_events` 와 같은 규약으로 접는다 — **해소된
    참여자만**(미해소는 값이 아니라 부재 — 접으면 서로 다른 사건이 한 thread_key 로
    뭉갠다), 한 역할 다값은 정렬 JSON 배열(결정적 순서). 규약이 갈리면 배치와 단건이
    같은 사건에 다른 thread_key 를 세운다.
    """
    role_lists: dict[str, list[str]] = {}
    for argument in cls["arguments"]:
        if argument["entity_id"] is None:
            continue
        role_lists.setdefault(argument["role_code"], []).append(str(argument["entity_id"]))
    # anchor 폴백 미러 — persist_normalization 은 primary 가 참여자에 없으면
    # anchor_role 로 event_argument 행을 하나 세운다. 여기서 같은 조건으로 넣지
    # 않으면 배치(fetch_unthreaded_events — DB 의 그 행을 읽음)와 단건의 role_values
    # 가 갈려 같은 이벤트가 서로 다른 thread_key(또는 UNKNOWN)를 얻는다.
    primary_entity = created["entity_id"]
    if cls["anchor_role"] and not any(
            a["entity_id"] == primary_entity for a in cls["arguments"]):
        role_lists.setdefault(cls["anchor_role"], []).append(str(primary_entity))
    return {
        "source_event_id": created["source_event_id"],
        "event_type_code": cls["event_type_code"],
        "available_at": created.get("available_at") or published_at,
        "lifecycle_stage": cls["lifecycle_stage"],
        "predicate_code": cls["predicate_code"],
        "role_values": {
            role: (values[0] if len(values) == 1
                   else json.dumps(sorted(values), ensure_ascii=False))
            for role, values in role_lists.items()
        },
    }


def _process_registry():
    """온톨로지 레지스트리 — assemble 과 같은 로더(lru_cache)를 그대로 쓴다."""
    from edge_ontology import load_process_registry

    return load_process_registry()
