"""뉴스 태깅 Step3 — canonical 뉴스 → assertion 추출 → feature 적재 (ALPHA-365).

ALPHA-138 이 태깅 **라이브러리**(`tagging/extract.py`)를 인도했지만 그걸 부르는 스텝이 없어
지금까지 기사가 한 건도 실제로 태깅되지 않았다. 이 스텝이 그 배선이다.

canonical 뉴스(`language=ko`)를 읽어 기사마다 `extract_assertions` 를 돌리고, 결과를
`feature/news/assertions/language=…/published_date=…/` 에 `article_id` 키로 멱등 병합한다.

**왜 canonical 이 아니라 feature 인가**: 여기 값은 LLM 추론 결과다 — 같은 입력에 다시 돌려도
같은 값이 나온다는 보장이 없고 호출마다 돈이 든다. canonical(raw 에서 언제든 무료로 재생성)과
라이프사이클이 다르므로 존을 가른다.

**재태깅하지 않는다**: 이미 태깅된 기사는 건너뛴다. LLM 이 비싸서만이 아니라, 같은 기사를 다시
돌리면 값이 흔들려 point-in-time 재현이 깨지기 때문이다. 다시 도는 건 그 판정이 더는 이 기사를
설명하지 못할 때뿐이고, 그 축은 셋이다 — `tagger_version`·`ontology_version`(다른 태거의 판정) ·
**입력 지문**(다른 텍스트에 대한 판정: normalize_news 가 같은 URL 재적재에서 최신 fetched_at 의
title·lead 를 대표로 삼아 **정정을 반영**하므로 canonical 텍스트는 바뀔 수 있다) · `llm_error`
status(판정이 아니라 '물어보지도 못했다'는 뜻).

**장중 레인의 판정도 여기서 본다(ALPHA-900)**: 1분 뉴스 Consumer 가 같은 파티션의
`minute/` 구역에 기사당 미러를 남기므로(`lake.feature_news_assertions_minute_key`),
이 스텝은 장중이 이미 태깅한 기사를 skip 하고 그 판정을 part 파일에 흡수한 뒤 조각을
지운다. 두 레인의 LLM 장부가 서로를 보는 유일한 자리다 — 그 전에는 배치가 canonical
날짜축만, 장중이 job 축만 보고 있어 같은 기사를 반드시 두 번 태웠다.

**ko 만 태깅한다**: 프롬프트가 한국 금융 뉴스 전용("너는 한국 금융 뉴스에서…")이다. 영어(FMP)
기사에 이 프롬프트를 씌우면 조용히 품질이 무너지므로, 언어 파티션에서 아예 고른다. 영어는 별도
프롬프트가 생길 때 대상에 넣는다(그때 이 상수만 늘린다).

**mentions 있는 기사만 태깅한다(ALPHA-416)**: 수집이 전체 경제 뉴스(카테고리 주도)로 전환되면
기사 수가 배수로 늘지만, 유니버스 종목이 안 잡힌 기사는 다운스트림(assemble-events 의
in_universe 필터)이 어차피 버린다 — 거기에 기사당 1 LLM 콜을 태우지 않는다. mentions 는
normalize_news 가 종목명 탐지로 합성하므로 이 게이트가 곧 '유니버스 관련 기사' 필터다.
건너뛴 수는 skipped_no_mention 으로 드러낸다(Rule 12).

entity_id 는 채우지 않는다 — 모델은 사내 식별자를 모르고, 엔티티 해소는 entity 마스터(RDB)를
읽어야 해서 로더(ALPHA-190)와 같은 소관이다. `text` 가 그 해소의 입력으로 남는다.

`complete_fn` 은 주입받는다(라이브러리와 같은 규약) — 이 스텝도 어느 LLM 벤더인지 모른다.
벤더 배선·env 읽기는 run.py 가 한다.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from ..lake import (
    Storage,
    canonical_news_articles_partition,
    feature_news_assertions_minute_prefix,
    feature_news_assertions_partition,
    quality_log_key,
)
from ..tagging.extract import PROMPT_LANGUAGES, TAGGER_VERSION, extract_assertions
from ..tagging.ontology import ontology_version

logger = logging.getLogger(__name__)

JOB_NAME = "tag_news"
DATASET = "news_assertions"

# 태깅 대상 언어. 정본은 프롬프트를 가진 모듈이다(`tagging.extract.PROMPT_LANGUAGES`) —
# 영어 프롬프트가 생기면 거기 한 곳만 늘린다.
TAGGED_LANGUAGES = PROMPT_LANGUAGES

# LLM 호출 병렬도(ALPHA-519). TagNews 는 기사당 LLM 1콜이 완전 직렬이라 런타임의 큰 몫이다.
# complete_fn 은 콜마다 독립 urllib 요청(상태없음=스레드안전)이고 블로킹 I/O 라 GIL 이 풀려
# 실병렬이 난다. DeepSeek v4-pro 동시성 캡 500 안이라 100 까지 안전 — 상한을 그 아래로 둔다.
# 기사별 status·격리·merged 병합은 결과 취합 후 메인스레드에서 해 경합을 피한다.
DEFAULT_TAG_CONCURRENCY = 32
MAX_TAG_CONCURRENCY = 100

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
    "input_fingerprint",
    "doc_class",
    "status",
    "assertions",
    "reasons",
    "ontology_version",
    "tagger_version",
    "tagged_at",
)


def _has_mentions(article: dict) -> bool:
    """canonical mentions(JSON 문자열)에 종목 mention 이 하나라도 있는가 — 태깅 비용 게이트.
    파싱 불가·비리스트·비객체 원소뿐이면 False(태깅 안 함) — mentions 가 없는 기사와 같은
    처지고, 게이트 건너뛴 수로 계측되므로 조용히 사라지지 않는다."""
    value = article.get("mentions")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return False
    return isinstance(value, list) and any(isinstance(m, dict) for m in value)


def _input_fingerprint(article: dict) -> str:
    """이 assertion 이 **어느 텍스트에서 나왔는지**의 지문 — 프롬프트에 들어가는 값만 해싱한다.

    canonical 은 같은 article_id 라도 title·lead_text 가 **바뀔 수 있다** — normalize_news 가
    같은 URL 재적재에서 최신 fetched_at 행의 스칼라를 대표로 삼아 정정을 반영하기 때문이다.
    버전만 보고 건너뛰면 제목이 정정돼도 옛 텍스트 기반 assertion 이 그대로 남아, 정제가 반영한
    정정이 태깅에서 조용히 사라진다.

    fetched_at 은 **일부러 넣지 않는다** — 재수집으로 fetched_at 만 갱신되고 텍스트가 같으면
    같은 답이 나올 게 뻔한데 LLM 을 다시 부르는 건 돈만 태운다. 지문은 내용에만 걸린다.
    """
    parts = [article.get("title"), article.get("lead_text"), article.get("published_at")]
    payload = "\x1f".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


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


def _is_current(row: object, fingerprint: str) -> bool:
    """이 feature 행이 **현재 태거·온톨로지가 현재 텍스트에 내린 유효한 판정**인가 — 그러면
    재태깅하지 않는다. 세 축이 전부 맞아야 현재다.

    비객체·결측은 현재 아님으로 본다(다시 태깅). 버전이 다르면 다른 태거의 판정이고, 지문이
    다르면 **다른 텍스트에 대한** 판정이라 이 기사의 현재 내용을 설명하지 못한다.

    status 가 RETRYABLE 이면 나머지가 같아도 현재 아님이다 — llm_error 는 '이 기사는 이렇다'는
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
        and row.get("input_fingerprint") == fingerprint
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


def _mirror_dates(
    storage: Storage, language: str, from_date: str | None, to_date: str | None
) -> set[str]:
    """장중 미러가 남아 있는 feature 파티션의 날짜 — **태깅과 같은 창으로 거른다** (ALPHA-900).

    canonical 파티션이 없는 날짜에도 미러는 생긴다 — 기사 정본은 PG 이고 canonical 은 다음
    `normalize_news` 에 오므로, 장중만 본 기사의 발행일이 아직 canonical 에 없을 수 있다.
    `_partition_dates` 는 canonical 만 열거하니 그런 날짜는 루프에 안 들어오고, 그러면
    미러가 **영영 흡수되지 않는다** — 소비자(`load_assertions`)는 흡수 전 미러를 안 읽으므로
    그 장중 판정이 DB 에 영영 안 실린다(이 티켓이 노리는 값 하나가 통째로 사라진다).

    ⚠️ **창은 그대로 적용한다.** 창을 무시하고 합치면 둘이 깨진다 — ①창 밖 canonical 기사가
    태깅 대상이 되어 전역 `limit` 을 먼저 소진하고(창이 지키기로 한 비용 범위가 무너진다)
    ②명시 범위 백필이 자기 범위 밖 파티션의 `part-00000.parquet` 을 쓰게 되어, 그 파티션을
    동시에 쓰는 정규 런과 lost update 가 난다(운영자가 범위를 갈라 피하던 경합이다).

    ⚠️ 대가는 **창 밖 조각이 다음 풀스캔까지 남는 것**이다(창 미지정이면 여기도 전량이다).
    발생이 드물고 `minute_mirrors_absorbed` 로 드러나므로 수용한다.
    """
    marker = feature_news_assertions_partition(language, "")  # ".../published_date="
    dates: set[str] = set()
    for key in storage.list_keys(marker):
        rest = key[len(marker):]
        date, _, tail = rest.partition("/")
        if not date or not tail.startswith("minute/"):
            continue
        if from_date is not None and date < from_date:
            continue
        if to_date is not None and date > to_date:
            continue
        dates.add(date)
    return dates


def _read_canonical(storage: Storage, language: str, published_date: str) -> list[dict]:
    rows: list[dict] = []
    prefix = canonical_news_articles_partition(language, published_date)
    for key in storage.list_keys(prefix + "/"):
        if key.endswith(".parquet"):
            rows.extend(_read_parquet_rows(storage.get_bytes(key)))
    return rows


def _read_feature(
    storage: Storage, language: str, published_date: str
) -> tuple[list[dict], list[str]]:
    """(파티션의 feature 행 전량, **흡수 대상 장중 미러 키**) — ALPHA-900.

    미러 키를 함께 돌려주는 이유는 이 스텝이 그걸 지워야 하기 때문이다. 장중 레인은
    `(기사, 그 기사의 어느 텍스트)` 마다 객체를 남기는데(writer 가 동시다발이라 part
    파일을 공유할 수 없다), 그대로 두면 이 함수가 파티션마다 수천 GET 을 하게 된다. 병합해 part 파일에 실은 뒤 지워
    **다음 배치 런까지만** 존재하게 한다(`feature_news_assertions_minute_prefix`).
    """
    rows: list[dict] = []
    mirror_keys: list[str] = []
    prefix = feature_news_assertions_partition(language, published_date)
    mirror_marker = feature_news_assertions_minute_prefix(language, published_date)
    for key in storage.list_keys(prefix + "/"):
        if not key.endswith(".parquet"):
            continue
        rows.extend(_read_parquet_rows(storage.get_bytes(key)))
        if key.startswith(mirror_marker):
            mirror_keys.append(key)
    return rows, mirror_keys


def _tagged_at(row: object) -> str:
    """병합 승자 판정 축 — 결측·비문자열은 가장 오래된 것으로 본다.

    두 writer 가 같은 ISO-8601 UTC(`+00:00` 오프셋)를 쓰므로 문자열 비교가 곧 시각
    비교다. 오프셋이 갈리면 이 비교가 조용히 틀리니, 미러를 쓰는 쪽도 같은 형식을
    유지해야 한다(`mirror_row_bytes`).

    ⚠️ **두 writer 의 시각 의미가 정확히 같지는 않다.** 배치 행은 런 **시작** 시각을
    한 런의 모든 행에 균일하게 찍고(`tagged_at = started_at`), 미러는 자기가 쓰인 시각을
    찍는다. 그래서 이 스텝이 리스팅한 **뒤에** 도착한 미러는 삭제를 피하고, 다음 런의
    병합에서 같은 런 후반에 나온 배치 판정보다 최신으로 잡힐 수 있다.

    ⭐ **그 자리에서 미러가 이기는 것이 맞다.** 두 판정의 입력이 갈리는 경우는 정정뿐인데,
    정정은 PG 정본에 먼저 반영되고(1분 `CanonicalWriter`) 레이크 canonical 에는 다음
    `normalize_news` 에 온다 — 즉 미러가 **더 새 본문**을 보고 판정한 쪽이다. 입력이 같으면
    지문도 같아 어느 행이 남든 `_is_current` 판정이 동일하다(LLM 비결정성의 두 표본).
    배치 행에 행별 판정 시각을 찍는 쪽으로 바꿔도 이 결론은 안 바뀌므로 그대로 둔다.
    """
    value = row.get("tagged_at") if isinstance(row, dict) else None
    return value if isinstance(value, str) else ""


def _merge_by_article(rows: list[dict]) -> dict:
    """한 파티션의 feature 행을 `article_id` 키로 접는다 — **최신 판정이 이긴다**.

    ⚠️ 리스팅 순서에 맡기면 **틀린 쪽이 이긴다.** 키 정렬로는 `minute/…` 가 앞이고
    `part-00000.parquet` 이 뒤라 나중 것이 덮는데, 정정 기사에서는 그게 거꾸로다 —
    배치가 옛 본문으로 내린 판정(part 파일)이 장중이 정정 본문으로 내린 판정(미러)을
    이기면, 다음 런의 지문 대조가 어긋나 **재태깅이 그대로 난다**. 이 스텝이 미러를
    두는 목적 자체가 사라지므로 시각으로 명시한다.
    """
    by_id: dict = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        article_id = row.get("article_id")
        current = by_id.get(article_id)
        if current is None or _tagged_at(row) >= _tagged_at(current):
            by_id[article_id] = row
    return by_id


def mirror_row_bytes(article: dict, result: dict, tagged_at: str) -> tuple[str, bytes]:
    """장중 추출 결과 1건 → 배치와 **같은 형식**의 feature 행 바이트 (ALPHA-900).

    장중 레인(`minute/news_consumer`)이 부른다. 여기 있는 이유는 형식의 정본이 하나여야
    하기 때문이다 — `_is_current` 가 보는 축 셋(`input_fingerprint`·`tagger_version`·
    `ontology_version`)의 형식이 조금이라도 갈리면 skip 이 안 걸려 미러가 헛돈다.
    행 조립·지문 산식을 저쪽에서 재구현하면 그 어긋남이 조용히 생긴다.

    지문을 함께 돌려주는 것은 그 값이 **미러 키에도 들어가기** 때문이다
    (`lake.feature_news_assertions_minute_key` — 같은 기사의 다른 판정이 서로를 덮으면
    배치의 압축 창에서 최신 판정이 유실된다). 호출부가 따로 계산하면 두 값이 갈릴 자리가
    생기므로 한 번만 계산해 함께 준다.

    ⚠️ `article` 은 **canonical 과 같은 표현**이어야 한다. 특히 `published_at` 은
    문자열이고 벤더 선언 tz 의 오프셋을 달고 있어야 한다 — 같은 순간이라도 표기가 다르면
    지문이 다른 값이 된다(`PgArticleReader` 가 그 복원을 진다).
    """
    fingerprint = _input_fingerprint(article)
    return fingerprint, _write_parquet_rows(
        [_feature_row(article, result, tagged_at, fingerprint)]
    )


def _feature_row(article: dict, result: dict, tagged_at: str, fingerprint: str) -> dict:
    return {
        "article_id": article.get("article_id"),
        "published_at": article.get("published_at"),
        "title": article.get("title"),
        "input_fingerprint": fingerprint,
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
    concurrency: int = DEFAULT_TAG_CONCURRENCY,
) -> int:
    """canonical 뉴스(ko) → 태깅 → feature 멱등 병합 + quality_log. 성공 0, 장애 시 비0.

    limit 은 **이번 런에서 새로 LLM 을 부를 기사 수 상한**이다(이미 태깅된 건 세지 않는다).
    미지정이면 대상 전부를 태깅한다 — 실수로 큰 비용이 나가는 걸 호출부가 막을 수 있게 둔다.

    concurrency 는 LLM 호출 병렬도다(파티션 안에서 기사별 extract 를 동시 실행). 카운터·격리·
    merged 병합은 결과 취합 뒤 메인스레드에서 순차로 해 경합을 없앤다 — 순차 실행과 결과 동일.
    """
    concurrency = max(1, min(concurrency, MAX_TAG_CONCURRENCY))
    started_at = datetime.now(timezone.utc)
    tagged_at = started_at.isoformat()
    checked_date = tagged_at[:10]

    read = 0            # canonical 에서 본 기사 수
    skipped = 0         # 이미 현재 버전으로 태깅돼 건너뛴 수 (LLM 미호출)
    skipped_no_mention = 0  # mentions 없어 태깅 대상이 아닌 수 (LLM 미호출, ALPHA-416)
    tagged = 0          # 이번 런에서 LLM 을 부른 수
    limited = 0         # limit 에 걸려 안 부른 수
    # ⚠️ 아래 둘은 **"흡수됐다"의 증거가 아니다** — 이름보다 넓게 센다. 권한이 없어 삭제가
    # 늘 실패하던 동안 `mirrors_absorbed` 는 구조적으로 0 이라 이 어긋남이 안 드러났다.
    mirrors_absorbed = 0    # 지운 장중 미러 조각 수 — mentions 게이트로 part 에 **안 실린**
                            # 조각도 함께 지워지므로 여기 포함된다(part 적재분 ≠ 이 값). (ALPHA-900)
    mirrors_dropped_no_mention = 0  # 되쓰기에서 빠진 행 수 — 미러 유래가 대부분이지만 **예전에
                            # 태깅돼 part 에 있던 행**이 지금 mentions 를 잃어도 여기 들어간다.
                            # 두 레인의 대상 집합 차이를 재려면 그만큼 상한이다. (ALPHA-900/416)
    status_counts: Counter = Counter()
    reason_counts: Counter = Counter()
    failures: list[dict] = []
    parts_written = rows_written = 0
    exit_code = 0

    for language in TAGGED_LANGUAGES:
        taggable = set(_partition_dates(storage, language, from_date, to_date))
        # 미러가 남은 날짜 — canonical 이 아직 없는 날짜를 여기서 집는다. 창은 같이 걸어
        # 창 밖 기사가 태깅되거나 범위 밖 파티션을 쓰는 일이 없게 한다(`_mirror_dates`).
        # 새로 들어오는 날짜에는 canonical 이 없으므로 아래 루프가 읽어도 0건이다.
        mirror_dates = _mirror_dates(storage, language, from_date, to_date)
        for published_date in sorted(taggable | mirror_dates):
            try:
                articles = _read_canonical(storage, language, published_date)
                existing, mirror_keys = _read_feature(storage, language, published_date)
            except Exception as exc:
                # 한 파티션의 읽기 실패가 나머지 파티션을 죽이지 않게 격리하되, 조용히 넘기지
                # 않는다(Rule 12) — exit 을 비0으로 올려 런이 성공으로 위장되지 않게.
                logger.exception("파티션 읽기 실패(격리): %s/%s", language, published_date)
                failures.append({"language": language, "published_date": published_date,
                                 "reasons": ["partition_read_error"], "error": str(exc)})
                exit_code = 1
                continue

            by_id = _merge_by_article(existing)
            merged = dict(by_id)
            changed = False

            # 1) 선택(순차·LLM 미호출): 비-LLM 게이트로 태깅 대상만 고른다. limit 은 전 파티션에
            #    걸친 상한이라 확정 tagged + 이번에 고른 수로 판정(순차 tagged>=limit 와 동치).
            to_tag: list[tuple[object, dict, str]] = []  # (article_id, article, fingerprint)
            no_mention_ids: set = set()   # 이 파티션에서 mentions 게이트가 배제한 기사
            for article in articles:
                if not isinstance(article, dict):
                    # canonical 은 이 스텝이 안 쓰지만, 비객체 행이 섞이면 .get 에서 파티션이
                    # 통째로 죽는다 — 행 단위로 격리하고 사유를 남긴다.
                    failures.append({"language": language, "published_date": published_date,
                                     "reasons": ["non_object_article"]})
                    continue
                read += 1
                if not _has_mentions(article):
                    # 유니버스 종목이 안 잡힌 기사 — 다운스트림이 버릴 기사에 LLM 을 안 태운다.
                    skipped_no_mention += 1
                    no_mention_ids.add(article.get("article_id"))
                    continue
                article_id = article.get("article_id")
                fingerprint = _input_fingerprint(article)
                if _is_current(by_id.get(article_id), fingerprint):
                    skipped += 1
                    continue
                if limit is not None and tagged + len(to_tag) >= limit:
                    limited += 1
                    continue
                to_tag.append((article_id, article, fingerprint))

            # 2) 실행(병렬): LLM 콜만 스레드풀에 던진다. map 은 순서를 보존해 결과를 대상에 맞춘다.
            #    extract 는 실패를 status=llm_error 로 격리하므로(부수효과 없는 순수 함수) 워커가
            #    예외를 던지지 않는다 — 순차판과 같은 크래시 계약(격리 밖 예외는 그대로 전파).
            if to_tag:
                workers = min(concurrency, len(to_tag))
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    results = list(pool.map(
                        lambda item: extract_assertions(item[1], complete_fn=complete_fn), to_tag))
            else:
                results = []

            # 3) 병합(순차·메인스레드): 카운터·merged 갱신을 여기서 해 경합을 없앤다.
            for (article_id, article, fingerprint), result in zip(to_tag, results):
                tagged += 1
                status_counts[result.get("status")] += 1
                for reason in result.get("reasons") or []:
                    reason_counts[str(reason)] += 1
                merged[article_id] = _feature_row(article, result, tagged_at, fingerprint)
                changed = True

            # 미러가 있으면 이번 런에 새 태깅이 없어도 되쓴다 — 그게 압축이다(ALPHA-900).
            # 안 그러면 태깅할 게 없는 파티션의 미러가 영영 안 지워져 매 런 GET 을 늘린다.
            if not changed and not mirror_keys:
                continue
            try:
                prefix = feature_news_assertions_partition(language, published_date)
                # ⚠️ **mentions 게이트가 배제한 기사는 미러가 있어도 안 싣는다**(ALPHA-900).
                # 1분 레인에는 아직 그 게이트가 없어서(ALPHA-690) 유니버스 무관 기사의 미러가
                # 온다. 그대로 흡수하면 `load_assertions` 는 mentions 를 안 보므로, **배치가
                # 일부러 feature 집합 밖에 둔 기사**의 assertion 이 DB 에 착지한다 — 두 레인의
                # 대상 집합이 갈린다(Rule 7). 판정 근거는 job 축 artifact 에 영구히 남으므로
                # 여기서 빼도 증거가 사라지지는 않는다.
                rows = [merged[a] for a in sorted(merged, key=lambda x: (x is None, x))
                        if a not in no_mention_ids]
                mirrors_dropped_no_mention += len(merged) - len(rows)
                storage.put_bytes(f"{prefix}/part-00000.parquet", _write_parquet_rows(rows))
                parts_written += 1
                rows_written += len(rows)
                if mirror_keys:
                    # **쓰기가 끝난 뒤에** 지운다 — 순서가 뒤집히면 되쓰기가 실패한 파티션의
                    # 장중 판정이 통째로 사라진다. 지우는 대상은 위에서 읽어 병합한 키뿐이다.
                    # ⚠️ 그게 안전한 것은 **미러 키에 입력 지문이 들어가기 때문**이다 —
                    # 읽고 지우는 사이에 도착한 정정 판정은 본문이 달라 다른 키라, 여기서
                    # 안 지워지고 다음 런이 흡수한다. 키가 `article_id` 뿐이면 그 정정이
                    # 같은 키를 덮고 곧바로 지워져, **아무도 안 읽은 최신 판정이 유실된다**.
                    storage.delete_keys(mirror_keys)
                    mirrors_absorbed += len(mirror_keys)
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
        "articles_skipped_no_mention": skipped_no_mention,
        # 지운 장중 미러 조각 수(ALPHA-900) — **part 에 실린 수가 아니다**(선언부 주석 참조).
        # ⚠️ **0 을 "미러가 안 떨어졌다"로 읽지 마라.** 삭제만 실패해도 0 이 된다 — 카운터가
        # `delete_keys` **뒤에** 오르기 때문이다. 그 경우 병합·되쓰기는 이미 끝났는데 0 으로
        # 보인다(2026-08-12 dev 실측: 미러 1,034·part 적재 325행인데 이 값 0, 원인은 IAM).
        # 판별자는 같은 로그의 `exit_code`·`failures[].reasons` 이고, 그 다음이 미러 구역에
        # 조각이 남아 있는지다. `articles_skipped_already_tagged` 와 나란히 봐야 교차 dedup 이
        # 실제로 걸렸는지 판별되는 것은 그 셋을 통과한 뒤다.
        "minute_mirrors_absorbed": mirrors_absorbed,
        # 되쓰기에서 빠진 행 수(ALPHA-900/416) — mentions 게이트가 배제한 기사다.
        # 1분 레인에 같은 게이트가 서면(ALPHA-690) 이 값이 0 으로 수렴한다. 그때까지 두 레인의
        # 대상 집합 차이를 드러내되 **차이 자체가 아니라 그 하한**이다: ①canonical 에 없는
        # 기사(장중만 본 기사)는 `no_mention_ids` 에 못 들어가 게이트를 아예 안 거치고
        # ②예전에 태깅돼 part 에 있던 행이 지금 mentions 를 잃은 경우도 여기 섞인다.
        "minute_mirrors_dropped_no_mention": mirrors_dropped_no_mention,
        "status_counts": dict(status_counts), "reason_counts": dict(reason_counts),
        "partitions_written": parts_written, "rows_written": rows_written,
        "failures": failures, "exit_code": exit_code,
        # 원장 관측용 공통 봉투(ALPHA-181). ⚠️ 유실이 아닌 것: `already_tagged`(멱등 재실행)·
        # `no_mention`(유니버스 무관 기사)·`left_by_limit`(다음 런이 집는 백로그). 유실은
        # **태깅을 시도했는데 결과가 없는 것** — LLM 오류·파싱 실패·제목 결측·문서분류 이상.
        # 봉투는 **이 런이 실제로 판정한 범위**만 말한다(기사 단위). `skipped`(이미 태깅됨)를
        # 산출에 더하면 안 된다 — 그 기사들은 재판정하지 않으므로 옛 실패(`llm_unparseable` 등
        # 비재시도 상태)가 산출로 뒤집힌다. `rows_written` 도 못 쓴다: 파티션 재작성 행수라
        # 한 건만 바뀌어도 과거 행까지 부풀린다. 멱등 no-op 재실행은 0건 → UNKNOWN 이 정직하다
        # (상태 기반 완전성은 ALPHA-490 소관).
        "ops": {
            "records_out": status_counts.get("ok", 0),
            "failed_records": len(failures) + sum(
                status_counts.get(s, 0)
                for s in ("llm_error", "llm_unparseable", "no_title", "bad_doc_class")),
        },
    }
    try:
        storage.put_bytes(quality_log_key(DATASET, checked_date, run_id),
                          json.dumps(log, ensure_ascii=False, indent=2).encode("utf-8"))
    except Exception:
        # 로그를 못 남기면 이 런이 뭘 했는지 사후에 알 수 없다 — 결과를 성공으로 두지 않는다.
        logger.exception("quality_log 적재 실패")
        exit_code = 1

    logger.info(
        "tag_news: read=%d tagged=%d skipped=%d no_mention=%d limited=%d mirrors=%d "
        "status=%s parts=%d rows=%d",
        read, tagged, skipped, skipped_no_mention, limited, mirrors_absorbed,
        dict(status_counts), parts_written, rows_written,
    )
    return exit_code
