"""assertion 적재 — feature 뉴스 assertion → document_assertion·assertion_argument (ALPHA-376).

"canonical → feature → DB" 사슬의 마지막 칸이다. tag-news(ALPHA-365)가 만든 feature
`news/assertions` 를 읽어 주장을 Cloud Event Store 에 세운다 — 분석이 feature 산출물만
읽는 최종 구조(ADR-0028)의 전제.

**엔티티 해소가 이 스텝의 본체다**: `assertion_argument.entity_id` 는 NOT NULL + FK 라
해소 없이는 한 건도 못 넣는다. 해소 축은 **역할이 정한다**(ALPHA-831) — 온톨로지의
`identity` 표를 `entity_resolution.plan_resolution` 이 읽어 셋으로 갈린다:
  NONE      instrument 완전일치(티커·종목명·발행사명). 못 붙으면 미해소
  REGISTRY  시드된 기관 명부 조회. 못 찾아도 **채번하지 않는다**
  MINT      멘션에서 결정적 채번 — `entity(CONCEPT)` + `concept` **마스터 행을 이 스텝이 만든다**
미해소·충돌은 **수치로 남기고 뺀다**(Rule 12). argument 가 전무 해소된 assertion 은
assertion 도 넣지 않는다 — 엔티티 연결 없는 주장은 event 조립 소비자에게 죽은 행이다.

⚠️ **쓰기 표면이 셋이다**: `document_assertion`·`assertion_argument` 에 더해 채번 경로가
`entity`·`concept` 를 쓴다. 채번 산식은 `entity_resolution.mint_concept` **하나**이고
assemble-events 도 그걸 부른다 — 갈리면 같은 개념에 ID 가 둘 생긴다(ALPHA-456 실패 양식).

**역할이 없는 argument 도 뺀다**(ALPHA-802). 예전엔 `ISSUER` 로 채웠는데, 적재 후에는
지어낸 역할과 모델이 뽑은 역할을 가릴 수단이 없다 — 이 테이블엔 출처 컬럼이 없고,
게다가 아래 `resolved_args` 가 `(역할, entity_id)` 로 접기 때문에 지어낸 ISSUER 가 같은
엔티티의 **진짜 ISSUER 와 한 행으로 합쳐진다** — 합쳐진 뒤에는 feature 원본과 대조해도
못 갈라진다.
그 argument 가 그 assertion 의 유일한 인자였다면 **assertion 자체가 안 들어간다** — 적재
행 집합이 달라지는 유일한 자리다(어휘가 깨져 런 전체가 exit 1 로 죽는 것은 별개다).
다만 추출단(`tagging/extract.py`)이 역할을 어휘 허용집합으로 이미 거르므로 현행
생산자로는 도달하지 않는다.

**멱등**: 논리 자연키 = `uq_document_assertion_natural (document_id, event_type_code,
predicate_code)` 에 ON CONFLICT DO NOTHING(원자적 — #130 교훈). 신규면 그 자연키에서
**결정적으로** 파생한 `asrt_<해시>`(`db.stable_domain_id`, ALPHA-456), 이미 있으면(분석엔진
선적재 포함) 그 행의 assertion_id 를 자연키로 읽어 arguments 만 union 한다
(`uq_assertion_argument` DO NOTHING). 같은 런 내 같은 키 주장은 arguments 를 접는다.

⚠️ **컬럼 소유권(ALPHA-538)** — 같은 자연키 행을 두 스텝(이 스텝·assemble-events)이 만들 수
있지만, 컬럼별 공급자는 하나다: `confidence` 는 **이 스텝 소유**(assertion-grain 판정,
행이 이미 있으면 UPDATE 로 확정 착지), `available_at` 은 **document 파생**(양쪽이 같은 값),
`lifecycle_stage` 는 event grain(`source_event`) 소유라 여기선 싣지 않는다. 그래서 두 스텝이
어느 순서로 돌아도 최종 행이 같다 — "먼저 쓴 쪽이 남는" 순서 의존은 ALPHA-538 로 제거됐다.

ADR-0027 대비: 도메인 ID 는 `<접두사>_<ULID>` 가 기본이지만 이 계열은 **hex 해시**라 시간
정렬이 안 된다. 불투명성(ID 를 파싱해 의미를 얻지 못함)은 유지되고, ADR 이 자연키 파생을
배격한 이유는 *가변 외부 식별자*(티커) 인코딩이었는데 여기 자연키는 전부 내부·불변값이라
그 위험이 없다. 시간 정렬이 필요한 소비자는 `available_at` 을 쓴다.

**document 연결**: feature 행에 source_vendor 가 없어 언어→벤더 고정(ko=bigkinds,
분석엔진과 같은 가정)으로 자연키 SELECT 해소. document 행이 없으면 결손으로 세고
건너뛴다. 정상 범위는 TagNews manifest로 고정되므로 LoadDocuments와의 일시 결손은 같은
`input_run_id`를 멱등 재실행해 회수한다(ALPHA-1033).

`modality_code` 는 비운다 — 어휘 미정의(ALPHA-361). 값을 발명하면 그게 계약이 된다.
`available_at` 은 **document 의 가용 시각**이다 — 주장의 PIT 기준은 원문 발행·수집이지
추출 프로세스 시각(tagged_at)이 아니다(ALPHA-538 로 tagged_at 사용 폐지).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

from edge_ontology import load_authority_registry, load_relations

from ..config import DbConfig
from ..db import connect, stable_domain_id
from ..entity_resolution import load_resolution_index, plan_resolution
from ..lake import (
    Storage,
    feature_run_manifest_key,
    feature_news_assertions_minute_prefix,
    feature_news_assertions_partition,
    quality_log_key,
)
from .tag_news import _merge_by_article

logger = logging.getLogger(__name__)

JOB_NAME = "load_assertions"
DATASET = "document_assertion"

# tag-news 의 태깅 대상과 같은 축(TAGGED_LANGUAGES=ko) — 언어는 벤더 고정이라 source_code 도
# 여기서 정해진다. 영어 태깅이 열리면 ("en", "fmp") 를 추가한다.
_SOURCE_CODE_BY_LANGUAGE = {"ko": "bigkinds"}

_CREATED_SAMPLE_LIMIT = 50

# 미해소 상위 표현을 몇 개까지 로그에 남길지. 20 이던 동안 롱테일 구성이 관측되지 않아
# "마스터를 넓혀야 하는가, 별칭을 넣어야 하는가"를 로그만 보고는 판단할 수 없었다(ALPHA-802).
_TOP_UNRESOLVED_LIMIT = 200


def _read_parquet_rows(data: bytes) -> list[dict]:
    import io
    import pyarrow.parquet as pq

    return pq.read_table(io.BytesIO(data)).to_pylist()


def _partition_dates(storage: Storage, language: str) -> list[str]:
    """이 언어의 feature published_date 목록(오름차순). 경로는 빌더로만 만든다(레이크 규약)."""
    marker = feature_news_assertions_partition(language, "")  # ".../published_date="
    dates: set[str] = set()
    for key in storage.list_keys(marker):
        date = key[len(marker):].split("/", 1)[0]
        if date:
            dates.add(date)
    return sorted(dates)


def _manifest_partitions(
    storage: Storage, input_run_id: str
) -> list[tuple[str, str, str, frozenset[str]]]:
    """TagNews가 증명한 직접 feature key와 현재 실행 article_id 범위."""
    key = feature_run_manifest_key("news_assertions", input_run_id)
    manifest = json.loads(storage.get_bytes(key).decode("utf-8"))
    if not isinstance(manifest, dict) or manifest.get("run_id") != input_run_id:
        raise ValueError(f"요청한 run_id의 manifest가 아니다: run_id={input_run_id}")
    if manifest.get("producer") != "tag_news" or manifest.get("feature_written") is not True:
        raise ValueError(f"완료된 tag-news manifest가 아니다: run_id={input_run_id}")
    raw = manifest.get("feature_partitions")
    if not isinstance(raw, list):
        raise ValueError(f"feature_partitions가 없는 구형 manifest다: run_id={input_run_id}")

    partitions: list[tuple[str, str, str, frozenset[str]]] = []
    seen_partitions: set[tuple[str, str]] = set()
    seen_article_ids: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError(f"feature_partitions 항목이 객체가 아니다: run_id={input_run_id}")
        language, published_date = item.get("language"), item.get("published_date")
        try:
            valid_date = (
                isinstance(published_date, str)
                and datetime.strptime(published_date, "%Y-%m-%d").strftime("%Y-%m-%d")
                == published_date
            )
        except ValueError:
            valid_date = False
        if language not in _SOURCE_CODE_BY_LANGUAGE or not valid_date:
            raise ValueError(f"feature_partitions 항목이 유효하지 않다: {item!r}")
        partition = (language, published_date)
        if partition in seen_partitions:
            raise ValueError(f"feature_partitions 항목이 중복됐다: {item!r}")
        seen_partitions.add(partition)

        parquet_key = item.get("key")
        expected_key = f"{feature_news_assertions_partition(language, published_date)}/part-00000.parquet"
        if parquet_key != expected_key:
            raise ValueError(f"feature 직접 키가 파티션과 일치하지 않는다: {item!r}")
        article_ids = item.get("article_ids")
        if (not isinstance(article_ids, list) or not article_ids
                or any(not isinstance(value, str) or not value.strip()
                       or value != value.strip() for value in article_ids)
                or article_ids != sorted(set(article_ids))):
            raise ValueError(f"article_ids가 유효한 정렬·고유 목록이 아니다: {item!r}")
        duplicated = seen_article_ids.intersection(article_ids)
        if duplicated:
            raise ValueError(f"article_id가 feature 파티션 사이에 중복됐다: {sorted(duplicated)!r}")
        seen_article_ids.update(article_ids)
        partitions.append((language, published_date, parquet_key, frozenset(article_ids)))
    return partitions


def _confidence(value: object) -> float | None:
    """CHECK(0~1) 를 어길 값은 NULL 로 — 게이트 통과값으로 강제하지 않는다(coerce 금지)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if 0.0 <= float(value) <= 1.0 else None


def _matches_partition_date(value: object, published_date: str) -> bool:
    """feature 시각이 유효한 timezone-aware ISO이고 manifest 파티션 날짜와 같은가."""
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.date().isoformat() == published_date


def run(
    storage: Storage,
    run_id: str,
    *,
    db: DbConfig,
    input_run_id: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
) -> int:
    """manifest 정상 범위 또는 명시 복구 범위의 feature assertion을 DB에 적재한다."""
    started_at = datetime.now(timezone.utc)
    physical_rows_read = rows_read = rows_not_ok = rows_no_assertion = rows_malformed = 0
    rows_superseded = 0   # 같은 기사의 더 낡은 판정이라 안 실은 행 (ALPHA-900)
    mirrors_unabsorbed = 0  # 아직 흡수 안 된 장중 미러 조각이라 안 읽은 객체 (ALPHA-900)
    folded = missing_document = skipped_incomplete = skipped_partial = 0
    skipped_no_resolved_argument = 0
    created = already = arguments_inserted = 0
    args_total = resolved_any = 0
    args_by_reason: dict[str, int] = {"resolved": 0, "unresolved": 0, "ambiguous": 0}
    # 역할 종별 분포 — 해소율 분모(`args_total`)는 entity 만 센다. 나머지는 분모 밖에서
    # 따로 보여야 "왜 분모가 argument 총수와 다른가"가 로그만으로 설명된다(Rule 12).
    args_by_role_kind: dict[str, int] = {"entity": 0, "non_entity": 0, "out_of_vocabulary": 0}
    args_role_missing = 0
    non_entity_resolved = 0
    unknown_roles: dict[str, int] = {}
    # 이 런이 채번한 개념: entity_id → (표시명, concept_type). 적재 직전에 한 번에
    # 세운다 — argument 마다 INSERT 하면 같은 개념에 왕복이 반복된다.
    pending_concepts: dict[str, tuple[str, str]] = {}
    concepts_minted = 0
    # 미해소 표현 → 빈도. 별칭 축 도입 판단의 근거다(ALPHA-802).
    unresolved_texts: dict[str, int] = {}
    created_sample: list[dict] = []
    failures: list[dict] = []
    exit_code = 0
    # manifest GET·검증도 아래 실패 경계 안이다. 그 전에 깨져도 quality log가 0건 범위를
    # 기록할 수 있도록 후보 컨테이너는 경계 밖에서 만든다(ALPHA-1033 fail-loud).
    candidates: dict[tuple[str, str, str, str], dict] = {}

    try:
        if input_run_id is not None and (from_date is not None or to_date is not None):
            raise ValueError("input_run_id와 from/to는 함께 쓸 수 없다")
        manifest_partitions = (
            _manifest_partitions(storage, input_run_id) if input_run_id is not None else None
        )
        # (source_code, article_id, event_type, predicate) → {assertion 스칼라 + arguments set}
        for language, source_code in _SOURCE_CODE_BY_LANGUAGE.items():
            scopes = (
                [(date, key, ids) for lang, date, key, ids in manifest_partitions if lang == language]
                if manifest_partitions is not None else
                [(date, None, None) for date in _partition_dates(storage, language)
                 if (from_date is None or date >= from_date)
                 and (to_date is None or date <= to_date)]
            )
            for date, direct_key, article_ids in scopes:
                prefix = feature_news_assertions_partition(language, date)
                mirror_marker = feature_news_assertions_minute_prefix(language, date)
                partition_rows: list[dict] = []
                keys = [direct_key] if direct_key is not None else storage.list_keys(prefix + "/")
                for parquet_key in keys:
                    if not parquet_key.endswith(".parquet"):
                        continue
                    if parquet_key.startswith(mirror_marker):
                        # ⚠️ **흡수 전 장중 미러는 아직 확정이 아니다**(ALPHA-900). 이 파티션의
                        # 정본은 `tag_news` 가 되쓴 part 파일이고, 미러는 그 스텝이 읽어
                        # 병합하기 전까지의 임시 조각이다. 여기서 바로 읽으면 `tag_news` 가
                        # 거는 게이트(canonical mentions 판정 — 배치가 일부러 feature 집합
                        # 밖에 두는 기사)를 **통째로 우회한다**: SFN 은 TagNews 뒤에
                        # LoadAssertions 를 돌리는데, 그 사이에 도착한 미러는 게이트를 한 번도
                        # 안 거친 채 DB 로 간다.
                        # 건너뛰어도 지연이 없다 — 같은 SFN 런의 TagNews 가 흡수한 미러는 이미
                        # part 파일에 있고, 그 뒤 도착분은 다음 런이 흡수해 싣는다.
                        mirrors_unabsorbed += 1
                        continue
                    batch = _read_parquet_rows(storage.get_bytes(parquet_key))
                    physical_rows_read += len(batch)
                    partition_rows.extend(batch)
                # ⚠️ **한 기사에 판정이 둘 이상 있을 수 있다**(ALPHA-900). 배치가 쓴 part
                # 파일과 장중이 남긴 미러가 한 파티션에 공존하는 창이 있고, 그 둘이 같은
                # 기사의 **다른 본문**에 대한 판정이면 사건 자연키가 갈려 둘 다 적재된다 —
                # 정정 전 사건이 DB 에 영구히 남는다(`ON CONFLICT DO NOTHING` 이라 나중에
                # 덮이지도 않는다). 그래서 `tag_news` 의 압축과 **같은 규칙**으로 기사마다
                # 최신 판정만 남긴다(규칙이 갈리면 두 소비자가 다른 사실을 본다).
                # article_id 없는 행은 접지 않고 그대로 흘린다 — 접으면 여러 결손 행이
                # 하나로 뭉쳐 `rows_no_assertion` 이 실제보다 작게 보고된다(Rule 12).
                if article_ids is not None:
                    selected = [r for r in partition_rows if r.get("article_id") in article_ids]
                    keyed = _merge_by_article(selected)
                    missing = article_ids - keyed.keys()
                    if missing:
                        raise ValueError(
                            f"manifest article_id가 feature에 없다: key={direct_key}, "
                            f"article_ids={sorted(missing)!r}"
                        )
                    for article_id, row in keyed.items():
                        if not _matches_partition_date(row.get("published_at"), date):
                            raise ValueError(
                                "manifest article_id의 feature 날짜가 파티션과 다르다: "
                                f"key={direct_key}, article_id={article_id}"
                            )
                    unkeyed: list[dict] = []
                    rows_superseded += len(selected) - len(keyed)
                else:
                    unkeyed = [r for r in partition_rows if not r.get("article_id")]
                    keyed = _merge_by_article([r for r in partition_rows if r.get("article_id")])
                    rows_superseded += len(partition_rows) - len(unkeyed) - len(keyed)
                for row in [*keyed.values(), *unkeyed]:
                    rows_read += 1
                    if row.get("status") != "ok":
                        rows_not_ok += 1
                        continue
                    article_id = row.get("article_id")
                    try:
                        assertions = json.loads(row.get("assertions") or "[]")
                        if not isinstance(assertions, list):
                            raise ValueError("assertions 가 리스트가 아님")
                    except (ValueError, TypeError):
                        # 행 단위 격리 — 한 이상치가 잡을 무너뜨리지 않는다.
                        rows_malformed += 1
                        continue
                    if not article_id or not assertions:
                        rows_no_assertion += 1
                        continue
                    for assertion in assertions:
                        if not isinstance(assertion, dict):
                            rows_malformed += 1
                            continue
                        event_type = assertion.get("event_type_code")
                        predicate = assertion.get("predicate_code")
                        arguments = assertion.get("arguments")
                        if not event_type or not predicate or not isinstance(arguments, list):
                            # 자연키 결손 주장 — 넣으면 NOT NULL 위반이거나 멱등 축이 사라진다.
                            skipped_incomplete += 1
                            continue
                        if assertion.get("completeness") != "complete":
                            # 추출기가 필수 역할 결손을 표시한 주장(partial) — 스키마에
                            # 완결성 컬럼이 없어 실으면 확정 주장과 구분 불가가 된다.
                            # feature 존에 원본이 남으니 어휘/컬럼 합의 후 재적재한다.
                            skipped_partial += 1
                            continue
                        nk = (source_code, article_id, str(event_type), str(predicate))
                        entry = candidates.get(nk)
                        if entry is None:
                            entry = candidates[nk] = {
                                "confidence": _confidence(assertion.get("confidence")),
                                "arguments": [],
                            }
                        else:
                            # 같은 문서·사건유형·서술의 재주장 — 자연키가 하나면 주장도
                            # 하나다. 스칼라는 첫 주장이 대표, arguments 는 union.
                            folded += 1
                        entry["arguments"].extend(a for a in arguments if isinstance(a, dict))

        # 역할→종별 계약(`role_bindings_v0_1.yaml`). assemble-events 가 이미 읽는 그 표다
        # — 같은 계보의 두 writer 가 다른 계약을 보고 있었다(ALPHA-802). 어휘가 깨져 있으면
        # 여기서 죽는데, 커넥션·트랜잭션을 열기 **전**이라 롤백할 것이 없다.
        relations = load_relations()
        # 명부(`authority_registry_v0_1.yaml`)도 **여기서** 읽는다. REGISTRY 축이 트랜잭션
        # 안에서 처음 건드리면 위 보장이 두 자원 중 하나에만 참이 된다 — 깨진 명부가
        # 열린 트랜잭션 한복판의 일반 `load_error` 로 도착한다. 값은 캐시가 들고 있으니
        # 이 호출의 몫은 **검증을 경계 밖으로 끌어내는 것**이다.
        load_authority_registry()

        with connect(db) as conn:
            index = load_resolution_index(conn)

            article_ids_by_source: dict[str, set[str]] = {}
            for source_code, article_id, _e, _p in candidates:
                article_ids_by_source.setdefault(source_code, set()).add(article_id)
            doc_by_key: dict[tuple[str, str], tuple[str, object]] = {}
            with conn.cursor() as cur:
                for source_code, ids in article_ids_by_source.items():
                    cur.execute(
                        "SELECT source_document_id, document_id, available_at FROM document"
                        " WHERE source_code = %s AND source_document_id = ANY(%s)",
                        (source_code, sorted(ids)),
                    )
                    for sdi, did, avail in cur.fetchall():
                        doc_by_key[(source_code, sdi)] = (did, avail)

            # ⭐개념 행을 **assertion_argument 보다 먼저** 세운다 — entity(CONCEPT) →
            # concept → assertion_argument 순서가 FK 다. 순서가 뒤집히면 마지막 INSERT 가
            # 없는 부모를 가리켜 터진다.
            #
            # 이 루프가 candidates 전체를 한 번 돌며 채번 계획을 먼저 모은다: argument 를
            # 보는 것은 아래 적재 루프와 같지만, 개념은 **전량을 모아 한 번에** 세워야
            # executemany 한 쌍으로 끝난다(assertion 마다 왕복하면 2,400회가 된다).
            for (source_code, article_id, _e, _p), entry in candidates.items():
                # 아래 적재 루프와 **같은 조건**으로 거른다. 문서 행이 없으면 그
                # 주장은 안 실리는데(missing_document — 정상 경로다), 여기서
                # 채번하면 참조 없는 개념 마스터만 남는다.
                if (source_code, article_id) not in doc_by_key:
                    continue
                for argument in entry["arguments"]:
                    role_code = argument.get("role_code")
                    if not role_code:
                        continue
                    eid, _reason, minted = plan_resolution(
                        index, str(role_code), argument.get("text"))
                    if minted is not None and eid is not None:
                        pending_concepts.setdefault(eid, minted)
            if pending_concepts:
                with conn.cursor() as cur:
                    # ON CONFLICT DO NOTHING — assemble-events 가 같은 개념을 이미 세웠을 수
                    # 있다. 같은 산식(concept_key+stable_domain_id)을 쓰므로 **같은 행**이고,
                    # 그게 두 writer 가 한 개념을 공유하는 방식이다.
                    cur.executemany(
                        "INSERT INTO entity (entity_id, entity_type, display_name, status)"
                        " VALUES (%s,'CONCEPT',%s,'ACTIVE') ON CONFLICT (entity_id) DO NOTHING",
                        [(cid, name) for cid, (name, _k) in sorted(pending_concepts.items())],
                    )
                    cur.executemany(
                        # concept_type 은 엔티티 종별 그대로 — 제품과 위치가 한 통에 섞이면
                        # 소비자가 구분할 수 없다(assemble-events 와 같은 규약).
                        "INSERT INTO concept (concept_id, concept_type) VALUES (%s,%s)"
                        " ON CONFLICT (concept_id) DO NOTHING",
                        [(cid, kind) for cid, (_n, kind) in sorted(pending_concepts.items())],
                    )
                concepts_minted = len(pending_concepts)

            for (source_code, article_id, event_type, predicate), entry in sorted(candidates.items()):
                doc_row = doc_by_key.get((source_code, article_id))
                if doc_row is None:
                    missing_document += 1
                    continue
                document_id, doc_available_at = doc_row

                resolved_args: dict[tuple[str, str], float | None] = {}
                for argument in entry["arguments"]:
                    role_code = argument.get("role_code")
                    if not role_code:
                        # 여기서 `or "ISSUER"` 로 채우던 자리다(ALPHA-802). 지어낸 역할은
                        # 적재 후 모델이 뽑은 값과 구분이 안 된다 — 출처 컬럼이 없고,
                        # 아래 setdefault 가 같은 엔티티의 진짜 ISSUER 와 한 행으로 접는다.
                        # 채우지 말고 탈락시킨다(모듈 독스트링).
                        args_role_missing += 1
                        continue
                    role_code = str(role_code)
                    relation = relations.get(role_code)
                    kind = ("entity" if relation is not None and relation.is_entity else
                            "non_entity" if relation is not None else "out_of_vocabulary")
                    args_by_role_kind[kind] += 1
                    if relation is None:
                        # 어휘 밖 = 추출단과 온톨로지가 갈렸다는 신호다. 개수만 세면 못 고친다
                        # — 어느 역할인지 이름을 남긴다(실측 0건이라 목록은 짧다).
                        unknown_roles[role_code] = unknown_roles.get(role_code, 0) + 1

                    # ⭐역할별 해소 — 온톨로지의 identity 표가 축을 정한다(ALPHA-831).
                    # 예전엔 역할과 무관하게 instrument 인덱스 하나에 때려서, 티커가 아닌
                    # 축(명부·채번)은 **구조적으로** 못 붙었다. 채번된 개념은 아래에서
                    # entity → concept 순서로 세운다(FK).
                    entity_id, reason, minted = plan_resolution(
                        index, role_code, argument.get("text"))
                    if kind == "entity":
                        # 해소율 분모는 **실체 역할만** 센다. non_entity(TIME·VALUE·TEXT)는
                        # 애초에 실체를 가리키지 않는 자리라, 미해소로 세면 분모가 부풀어
                        # 뒤따르는 마스터 확대의 효과를 잴 수 없다. ⚠️ 온톨로지가 "적재하지
                        # 않는다"고 정한 대상은 `event_argument` 다 — 여기선 분모에서만 뺀다.
                        args_total += 1
                        args_by_reason[reason] = args_by_reason.get(reason, 0) + 1
                        if entity_id is not None:
                            resolved_any += 1
                        if entity_id is None:
                            text = argument.get("text")
                            if isinstance(text, str) and text.strip():
                                text = text.strip()
                                # 미해소 상위 표현이 별칭 축 도입 판단의 근거다(티켓 완료 조건).
                                # ⚠️ 한때 정책 제외(척도·문장꼴)를 빼고 셌다(ALPHA-857).
                                # 그 정책 자체가 온톨로지 근거 없이 이 writer 에만 걸려
                                # 있어 되돌렸으므로(ALPHA-861), 뺄 것도 없어졌다 — 지금
                                # 안 붙은 것은 **전부 못 붙인 것**이다.
                                unresolved_texts[text] = unresolved_texts.get(text, 0) + 1
                    elif kind == "non_entity" and entity_id is not None:
                        # ⚠️ **이제 도달하지 않는다.** 역할별 분기(ALPHA-831)가 비실체를
                        # 해소 전에 걸러서 `entity_id` 가 항상 None 이다. 카운터를 남겨 두는
                        # 것은 0 이 "이번 런엔 없었다"가 아니라 **"그 경로가 닫혔다"**를
                        # 뜻하기 때문이다 — 0 이 아니게 되면 분기가 새는 것이다.
                        non_entity_resolved += 1
                    if entity_id is None:
                        continue
                    resolved_args.setdefault((role_code, entity_id), entry["confidence"])

                if not resolved_args:
                    skipped_no_resolved_argument += 1
                    continue

                with conn.cursor() as cur:
                    # 자연키에서 결정적으로 뽑는다(ALPHA-456) — 랜덤 ULID 였을 때는 이 테이블의
                    # 다른 writer(assemble-events)와 값이 갈렸고, 먼저 도는 이 스텝의 랜덤값이
                    # 남아 그걸 재료로 쓰는 source_event_id 까지 랜덤을 상속했다. 산식은
                    # assemble-events 와 **같은 함수**여야 한다(각자 구현하면 salt·구분자
                    # 하나에 다시 갈린다).
                    assertion_id = stable_domain_id(
                        "asrt", document_id, event_type, predicate)
                    cur.execute(
                        "INSERT INTO document_assertion (assertion_id, document_id,"
                        " event_type_code, predicate_code, confidence, available_at)"
                        " VALUES (%s, %s, %s, %s, %s, %s)"
                        " ON CONFLICT (document_id, event_type_code, predicate_code) DO NOTHING",
                        (assertion_id, document_id, event_type, predicate,
                         entry["confidence"], doc_available_at),
                    )
                    if cur.rowcount == 0:
                        # 이미 있다(assemble-events 의 FK 비계 선생성 포함) — 소유 컬럼
                        # confidence 를 UPDATE 로 확정 착지시키고 그 행의 ID 로 arguments 를
                        # union 한다. 행 생성 경주에서 져도 이 스텝의 판정이 유실되지 않는다.
                        already += 1
                        cur.execute(
                            "UPDATE document_assertion SET confidence = %s"
                            " WHERE document_id = %s AND event_type_code = %s"
                            " AND predicate_code = %s RETURNING assertion_id",
                            (entry["confidence"], document_id, event_type, predicate),
                        )
                        assertion_id = cur.fetchone()[0]
                    else:
                        created += 1
                        if len(created_sample) < _CREATED_SAMPLE_LIMIT:
                            created_sample.append({"assertion_id": assertion_id,
                                                   "document_id": document_id,
                                                   "event_type_code": event_type,
                                                   "predicate_code": predicate})
                    for (role_code, entity_id), confidence in sorted(resolved_args.items()):
                        cur.execute(
                            "INSERT INTO assertion_argument (assertion_id, role_code,"
                            " entity_id, confidence) VALUES (%s, %s, %s, %s)"
                            " ON CONFLICT (assertion_id, role_code, entity_id) DO NOTHING",
                            (assertion_id, role_code, entity_id, confidence),
                        )
                        arguments_inserted += cur.rowcount
    except Exception as exc:
        # 커밋 경계는 런 전체 — connect() 가 예외면 롤백이라 부분 적재가 없다(Rule 12).
        logger.exception("assertion 적재 실패(롤백)")
        failures.append({"reasons": ["load_error"], "error": str(exc)})
        created = already = arguments_inserted = 0
        # 채번도 같은 트랜잭션이라 롤백된다 — 되돌리지 않으면 로그가 만들지
        # 않은 개념 마스터를 만들었다고 주장한다(ALPHA-830 에서 겪은 것과 같은 자리).
        concepts_minted = 0
        created_sample = []
        exit_code = 1

    resolution_denominator = args_total
    # ⭐분자는 **붙은 것 전부**다 — 티커(resolved)·명부(registry_hit)·채번(minted).
    # 예전엔 `resolved` 하나만 셌는데, 역할별 축이 생기면서 명부·채번으로 붙은 argument 가
    # 분모엔 들어가고 분자엔 안 들어갔다. 그러면 **이 티켓이 성공할수록 해소율이 떨어진다** —
    # 회수를 재려고 만든 지표가 회수를 반대로 보고한다(ALPHA-831).
    # `resolved_any` 는 위 루프가 **붙은 것을 직접 센 값**이다. 예전엔 사유 허용목록
    # (`_RESOLVED_REASONS`)으로 더했는데, 축이 늘 때 그 목록에 빠뜨리면 조용히 미해소로
    # 집계된다 — F1 이 정확히 그 사고였다. 목록은 사람이 유지하지만 `entity_id is not None`
    # 은 드리프트하지 않는다(Rule 5: 코드가 답할 수 있으면 코드가 답한다).
    resolution_rate = (resolved_any / resolution_denominator
                       if resolution_denominator else None)
    log = {
        "job": JOB_NAME, "run_id": run_id, "dataset": DATASET,
        "ops_attempt_id": os.environ.get("OPS_LEDGER_ATTEMPT_ID"),
        "started_at": started_at.isoformat(), "finished_at": datetime.now(timezone.utc).isoformat(),
        "languages": list(_SOURCE_CODE_BY_LANGUAGE), "input_run_id": input_run_id,
        "from_date": from_date, "to_date": to_date,
        "physical_rows_read": physical_rows_read, "logical_rows_read": rows_read,
        "rows_read": rows_read, "rows_not_ok": rows_not_ok,
        # 같은 기사의 낡은 판정이라 안 실은 행 — 유실이 아니라 **대체**다(ALPHA-900).
        # 0 이 아니면 배치 part 파일과 장중 미러가 겹친 창을 지났다는 뜻이다.
        "rows_superseded": rows_superseded,
        # 아직 흡수 안 돼 이번 런이 안 읽은 미러 조각 수(ALPHA-900). 유실이 아니라 **대기**다
        # — 다음 tag_news 가 게이트를 거쳐 part 파일로 흡수하면 그때 실린다.
        "minute_mirrors_unabsorbed": mirrors_unabsorbed,
        "rows_no_assertion": rows_no_assertion, "rows_malformed": rows_malformed,
        "assertions_considered": len(candidates), "assertions_folded": folded,
        "missing_document": missing_document, "skipped_incomplete": skipped_incomplete,
        "skipped_partial": skipped_partial,
        "skipped_no_resolved_argument": skipped_no_resolved_argument,
        "created": created, "already_present": already,
        "arguments_inserted": arguments_inserted,
        # 해소율 실측(ALPHA-375 완료 조건) — 분모·분자·사유 분포 + 미해소 상위 표현.
        # ⚠️ `total` 은 argument 총수가 아니라 **실체 역할 argument 수**다(ALPHA-802).
        # 나머지 축은 `role_kinds`·`role_missing` 이 따로 말한다 — 넷을 더하면 총수가 된다.
        # 역할별 해소 축의 성적(ALPHA-831). `**args_by_reason` 에 minted·registry_hit·
        # registry_miss 등이 사유로 섞여 들어온다 — 그게 무엇을 회수했고 무엇을 남겼는지
        # 보는 유일한 창이다.
        "concepts_minted": concepts_minted,
        "argument_resolution": {
            # 분모 정의를 로그 자신이 들고 있어야 한다 — 이 필드가 없으면 ALPHA-802 이전에
            # 찍힌 로그(분모=argument 총수)와 이후 로그를 나란히 놓고 비교할 때 정의가
            # 바뀐 것을 알 방법이 없다. 회수 효과와 분모 변화가 같은 숫자에서 섞인다.
            "denominator": "entity_roles_only",
            "total": args_total, **args_by_reason, "rate": resolution_rate,
            # rate 의 분자에 무엇이 들었는지 — 축이 늘면 값이 바뀌므로 로그가 스스로 말한다
            # 분자는 **붙은 것을 직접 센 수**다(사유 허용목록이 아니다). 어느 사유로
            # 붙었는지는 위 `**args_by_reason` 분포가 그대로 말한다.
            "resolved_any": resolved_any,
            # ⚠️ 이름이 `top_unresolved` 로 **환원됐다**(ALPHA-861). ALPHA-857 이
            # `_recoverable` 을 붙인 이유는 정책 제외분을 뺐기 때문인데, 그 정책을
            # 되돌려 뺄 것이 없어졌다 — 이름을 그대로 두면 그게 거짓이 된다.
            "top_unresolved": sorted(unresolved_texts.items(),
                                     key=lambda kv: -kv[1])[:_TOP_UNRESOLVED_LIMIT],
            "role_kinds": args_by_role_kind,
            "role_missing": args_role_missing,
            "out_of_vocabulary_roles": unknown_roles,
            # 비실체가 인덱스로 해소된 수. **0 이 정상이고 계약이다**(ALPHA-831 이
            # 그 경로를 닫았다) — 0 이 아니면 역할별 분기가 샌 것이다.
            "non_entity_resolved": non_entity_resolved,
        },
        "created_rows_sample": created_sample,
        "failures": failures, "exit_code": exit_code,
        # 원장 관측용 공통 봉투(ALPHA-181). ⚠️ `rows_no_assertion` 은 유실이 아니다 — 모든 기사가
        # 주장을 내지는 않는다(1:1 이 아니다). 유실은 **주장이 있었는데 못 실은 것**이다:
        # malformed·문서 미적재·불완전/부분·해소 인자 없음.
        "ops": {
            "records_out": created + already,
            "failed_records": (len(failures) + rows_malformed + missing_document
                               + skipped_incomplete + skipped_partial
                               + skipped_no_resolved_argument),
            # 성공해 원장에 실린 시도의 해소율만 추이로 쓴다. 실패 시도는 top-level 품질 로그에
            # 진단값을 남기되 저장 pair는 NULL로 보내 앞 시도의 성공값을 지운다(ALPHA-1000).
            "entity_resolution_arguments_total": args_total if exit_code == 0 else None,
            "entity_resolution_arguments_resolved": resolved_any if exit_code == 0 else None,
        },
    }
    try:
        storage.put_bytes(quality_log_key(DATASET, started_at.isoformat()[:10], run_id),
                          json.dumps(log, ensure_ascii=False, indent=2).encode("utf-8"))
    except Exception:
        logger.exception("적재 로그 기록 실패")
        exit_code = 1

    # 아래 둘은 추출단 검증(`tagging/extract.py` 의 허용집합 필터)이 막고 있어야 하는 값이라
    # 실측 0건이다. 발화했다면 그 계약이 깨진 것이고, 둘 다 분모 밖으로 빠져 **해소율을
    # 대표성 없는 수로 만든다**. ⚠️ 어느 방향으로 틀리는지는 로그로 알 수 없다 — 빠진 쪽이
    # 남은 쪽보다 잘 해소되면 해소율이 내려가고 아니면 올라간다. 방향을 못 쓰니 발화 사실
    # 자체를 띄운다: 로그 파일을 열어야 보이는 수치로 두지 않는다(Rule 12).
    if unknown_roles:
        logger.warning("load_assertions: 어휘 밖 역할 %d종 — 추출단과 온톨로지가 갈렸다. 그 자리는 적재하지 않았다: %s",
                       len(unknown_roles), sorted(unknown_roles))
    if non_entity_resolved:
        # 0 이 계약이다(ALPHA-831 이 이 경로를 닫았다) — 0 이 아니면 역할별 분기가 샌 것이라
        # 품질 로그를 열어야 보이는 수치로 두면 안 된다(Rule 12, 아래 둘과 같은 이유).
        logger.warning("load_assertions: 비실체가 인덱스로 해소됨 %d건 — 역할별 분기가 샌다",
                       non_entity_resolved)
    if args_role_missing:
        logger.warning("load_assertions: 역할 없는 argument %d건 — 그 자리는 적재하지 않았다",
                       args_role_missing)
    logger.info(
        "load_assertions: physical_rows=%d logical_rows=%d considered=%d missing_doc=%d no_resolved=%d created=%d"
        " already=%d args_inserted=%d resolution=%s",
        physical_rows_read, rows_read, len(candidates), missing_document, skipped_no_resolved_argument,
        created, already, arguments_inserted,
        f"{resolution_rate:.3f}" if resolution_rate is not None else "n/a",
    )
    return exit_code
