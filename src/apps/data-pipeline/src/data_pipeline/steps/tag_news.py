"""뉴스 태깅 Step3 — canonical 뉴스 → assertion 추출 → feature 적재 (ALPHA-365).

ALPHA-138 이 태깅 **라이브러리**(`tagging/extract.py`)를 인도했지만 그걸 부르는 스텝이 없어
지금까지 기사가 한 건도 실제로 태깅되지 않았다. 이 스텝이 그 배선이다.

canonical 뉴스(`language=ko`)를 읽어 기사마다 `extract_assertions` 를 돌리고, 결과를
`feature/news/assertions/language=…/published_date=…/` 에 `article_id` 키로 멱등 병합한다.

**왜 canonical 이 아니라 feature 인가**: 여기 값은 LLM 추론 결과다 — 같은 입력에 다시 돌려도
같은 값이 나온다는 보장이 없고 호출마다 돈이 든다. canonical(raw 에서 언제든 무료로 재생성)과
라이프사이클이 다르므로 존을 가른다.

**재태깅하지 않는다**: 이미 태깅된 기사는 건너뛴다. LLM 이 비싸서만이 아니라, 같은 기사를 다시
돌리면 값이 흔들려 point-in-time 재현이 깨지기 때문이다. `tagger_version`·`ontology_version` 이
바뀌면 그때만 다시 돈다 — 그건 '다른 태거의 판정'이라 새로 만드는 게 맞다.

**ko 만 태깅한다**: 프롬프트가 한국 금융 뉴스 전용("너는 한국 금융 뉴스에서…")이다. 영어(FMP)
기사에 이 프롬프트를 씌우면 조용히 품질이 무너지므로, 언어 파티션에서 아예 고른다. 영어는 별도
프롬프트가 생길 때 대상에 넣는다(그때 이 상수만 늘린다).

entity_id 는 채우지 않는다 — 모델은 사내 식별자를 모르고, 엔티티 해소는 entity 마스터(RDB)를
읽어야 해서 로더(ALPHA-190)와 같은 소관이다. `text` 가 그 해소의 입력으로 남는다.

`complete_fn` 은 주입받는다(라이브러리와 같은 규약) — 이 스텝도 어느 LLM 벤더인지 모른다.
벤더 배선·env 읽기는 run.py 가 한다.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import datetime, timezone

from ..lake import Storage, canonical_news_articles_partition, feature_news_assertions_partition, quality_log_key
from ..tagging.extract import TAGGER_VERSION, extract_assertions
from ..tagging.ontology import ontology_version

logger = logging.getLogger(__name__)

JOB_NAME = "tag_news"
DATASET = "news_assertions"

# 태깅 대상 언어. 프롬프트가 한국어 전용이라 ko 만 — 영어 프롬프트가 생기면 여기 늘린다.
TAGGED_LANGUAGES = ("ko",)

# 다음 런이 **다시 시도해야 하는** status. llm_error 는 호출 자체가 실패한 것(네트워크·레이트
# 리밋·5xx)이라 기사에 대한 판정이 아니다 — 이걸 '태깅 완료'로 캐시하면 일시적 장애 한 번에
# 그 기사가 영구히 태깅되지 않는다.
#
# llm_unparseable·bad_doc_class 는 넣지 않는다. 그건 호출은 됐고 **모델이 그렇게 답한** 것이라
# temperature=0 에선 재시도해도 같은 답이 올 공산이 크다 — 매 런 돈만 태운다. 프롬프트·모델이
# 바뀌면 tagger_version 이 올라가 그때 자연히 재태깅된다.
RETRYABLE_STATUSES = frozenset({"llm_error"})

_FEATURE_COLUMNS = (
    "article_id",
    "published_at",
    "title",
    "doc_class",
    "status",
    "assertions",
    "reasons",
    "ontology_version",
    "tagger_version",
    "tagged_at",
)


def _feature_schema():
    import pyarrow as pa

    # 전부 문자열이다 — assertions·reasons 는 JSON 문자열(canonical 뉴스 mentions 와 같은 관례).
    # 중첩 구조를 parquet 스키마로 굳히면 온톨로지가 바뀔 때마다 스키마 마이그레이션이 따라오는데,
    # 이 존의 소비자는 로더 하나뿐이고 그쪽이 어차피 JSON 을 다시 조립한다.
    return pa.schema([(c, pa.string()) for c in _FEATURE_COLUMNS])


def _read_parquet_rows(data: bytes) -> list[dict]:
    import io
    import pyarrow.parquet as pq

    return pq.read_table(io.BytesIO(data)).to_pylist()


def _write_parquet_rows(rows: list[dict]) -> bytes:
    import io
    import pyarrow as pa
    import pyarrow.parquet as pq

    table = pa.Table.from_pylist(
        [{c: r.get(c) for c in _FEATURE_COLUMNS} for r in rows], schema=_feature_schema()
    )
    buf = io.BytesIO()
    pq.write_table(table, buf)
    return buf.getvalue()


def _is_current(row: object) -> bool:
    """이 feature 행이 **현재 태거·온톨로지의 유효한 판정**인가 — 그러면 재태깅하지 않는다.

    비객체·결측은 현재 아님으로 본다(다시 태깅). 버전이 다르면 다른 태거의 판정이라 새로 만든다.

    status 가 RETRYABLE 이면 버전이 같아도 현재 아님이다 — llm_error 는 '이 기사는 이렇다'는
    판정이 아니라 '물어보지도 못했다'는 뜻이라, 그걸 완료로 캐시하면 일시적 장애 한 번이 그
    기사를 영구히 태깅 대상에서 지운다.
    """
    if not isinstance(row, dict):
        return False
    if row.get("status") in RETRYABLE_STATUSES:
        return False
    return (
        row.get("tagger_version") == TAGGER_VERSION
        and row.get("ontology_version") == ontology_version()
    )


def _partition_dates(storage: Storage, language: str, from_date: str | None, to_date: str | None) -> list[str]:
    """태깅 대상 canonical 파티션의 published_date 목록 — 날짜창으로 좁힌다.

    비용이 LLM 호출 수에 비례하므로 날짜창 프루닝이 곧 비용 통제다. 키에서 날짜를 읽되
    빌더가 만든 프리픽스를 기준으로 파싱해 경로 규약을 이 스텝이 재조립하지 않는다.
    """
    # language 파티션까지는 빌더로 만들고(규약 SSOT), 그 아래 published_date= 만 열거한다.
    marker = canonical_news_articles_partition(language, "")  # ".../published_date="
    dates: set[str] = set()
    for key in storage.list_keys(marker):
        rest = key[len(marker):]
        date = rest.split("/", 1)[0]
        if not date:
            continue
        if from_date is not None and date < from_date:
            continue
        if to_date is not None and date > to_date:
            continue
        dates.add(date)
    return sorted(dates)


def _read_canonical(storage: Storage, language: str, published_date: str) -> list[dict]:
    rows: list[dict] = []
    prefix = canonical_news_articles_partition(language, published_date)
    for key in storage.list_keys(prefix + "/"):
        if key.endswith(".parquet"):
            rows.extend(_read_parquet_rows(storage.get_bytes(key)))
    return rows


def _read_feature(storage: Storage, language: str, published_date: str) -> list[dict]:
    rows: list[dict] = []
    prefix = feature_news_assertions_partition(language, published_date)
    for key in storage.list_keys(prefix + "/"):
        if key.endswith(".parquet"):
            rows.extend(_read_parquet_rows(storage.get_bytes(key)))
    return rows


def _feature_row(article: dict, result: dict, tagged_at: str) -> dict:
    return {
        "article_id": article.get("article_id"),
        "published_at": article.get("published_at"),
        "title": article.get("title"),
        "doc_class": result.get("doc_class"),
        "status": result.get("status"),
        "assertions": json.dumps(result.get("assertions") or [], ensure_ascii=False),
        "reasons": json.dumps(result.get("reasons") or [], ensure_ascii=False),
        "ontology_version": result.get("ontology_version"),
        "tagger_version": result.get("tagger_version"),
        "tagged_at": tagged_at,
    }


def run(
    storage: Storage,
    run_id: str,
    *,
    complete_fn,
    from_date: str | None = None,
    to_date: str | None = None,
    limit: int | None = None,
) -> int:
    """canonical 뉴스(ko) → 태깅 → feature 멱등 병합 + quality_log. 성공 0, 장애 시 비0.

    limit 은 **이번 런에서 새로 LLM 을 부를 기사 수 상한**이다(이미 태깅된 건 세지 않는다).
    미지정이면 대상 전부를 태깅한다 — 실수로 큰 비용이 나가는 걸 호출부가 막을 수 있게 둔다.
    """
    started_at = datetime.now(timezone.utc)
    tagged_at = started_at.isoformat()
    checked_date = tagged_at[:10]

    read = 0            # canonical 에서 본 기사 수
    skipped = 0         # 이미 현재 버전으로 태깅돼 건너뛴 수 (LLM 미호출)
    tagged = 0          # 이번 런에서 LLM 을 부른 수
    limited = 0         # limit 에 걸려 안 부른 수
    status_counts: Counter = Counter()
    reason_counts: Counter = Counter()
    failures: list[dict] = []
    parts_written = rows_written = 0
    exit_code = 0

    for language in TAGGED_LANGUAGES:
        for published_date in _partition_dates(storage, language, from_date, to_date):
            try:
                articles = _read_canonical(storage, language, published_date)
                existing = _read_feature(storage, language, published_date)
            except Exception as exc:
                # 한 파티션의 읽기 실패가 나머지 파티션을 죽이지 않게 격리하되, 조용히 넘기지
                # 않는다(Rule 12) — exit 을 비0으로 올려 런이 성공으로 위장되지 않게.
                logger.exception("파티션 읽기 실패(격리): %s/%s", language, published_date)
                failures.append({"language": language, "published_date": published_date,
                                 "reasons": ["partition_read_error"], "error": str(exc)})
                exit_code = 1
                continue

            by_id = {r.get("article_id"): r for r in existing if isinstance(r, dict)}
            merged = dict(by_id)
            changed = False

            for article in articles:
                if not isinstance(article, dict):
                    # canonical 은 이 스텝이 안 쓰지만, 비객체 행이 섞이면 .get 에서 파티션이
                    # 통째로 죽는다 — 행 단위로 격리하고 사유를 남긴다.
                    failures.append({"language": language, "published_date": published_date,
                                     "reasons": ["non_object_article"]})
                    continue
                read += 1
                article_id = article.get("article_id")
                if _is_current(by_id.get(article_id)):
                    skipped += 1
                    continue
                if limit is not None and tagged >= limit:
                    limited += 1
                    continue

                result = extract_assertions(article, complete_fn=complete_fn)
                tagged += 1
                status_counts[result.get("status")] += 1
                for reason in result.get("reasons") or []:
                    reason_counts[str(reason)] += 1
                merged[article_id] = _feature_row(article, result, tagged_at)
                changed = True

            if not changed:
                continue
            try:
                prefix = feature_news_assertions_partition(language, published_date)
                rows = [merged[a] for a in sorted(merged, key=lambda x: (x is None, x))]
                storage.put_bytes(f"{prefix}/part-00000.parquet", _write_parquet_rows(rows))
                parts_written += 1
                rows_written += len(rows)
            except Exception as exc:
                logger.exception("feature 적재 실패: %s/%s", language, published_date)
                failures.append({"language": language, "published_date": published_date,
                                 "reasons": ["feature_write_error"], "error": str(exc)})
                exit_code = 1

    if limited:
        # 상한에 걸려 남긴 건 실패가 아니지만 조용히 두면 '전부 태깅됐다'로 오독된다(Rule 12).
        logger.warning("limit=%s 로 %d건을 이번 런에서 태깅하지 않았다 — 재실행하면 이어서 태깅된다",
                       limit, limited)

    log = {
        "job": JOB_NAME, "run_id": run_id, "dataset": DATASET,
        "started_at": tagged_at, "finished_at": datetime.now(timezone.utc).isoformat(),
        "languages": list(TAGGED_LANGUAGES),
        "tagger_version": TAGGER_VERSION, "ontology_version": ontology_version(),
        "articles_read": read, "articles_tagged": tagged,
        "articles_skipped_already_tagged": skipped, "articles_left_by_limit": limited,
        "status_counts": dict(status_counts), "reason_counts": dict(reason_counts),
        "partitions_written": parts_written, "rows_written": rows_written,
        "failures": failures, "exit_code": exit_code,
    }
    try:
        storage.put_bytes(quality_log_key(DATASET, checked_date, run_id),
                          json.dumps(log, ensure_ascii=False, indent=2).encode("utf-8"))
    except Exception:
        # 로그를 못 남기면 이 런이 뭘 했는지 사후에 알 수 없다 — 결과를 성공으로 두지 않는다.
        logger.exception("quality_log 적재 실패")
        exit_code = 1

    logger.info(
        "tag_news: read=%d tagged=%d skipped=%d limited=%d status=%s parts=%d rows=%d",
        read, tagged, skipped, limited, dict(status_counts), parts_written, rows_written,
    )
    return exit_code
