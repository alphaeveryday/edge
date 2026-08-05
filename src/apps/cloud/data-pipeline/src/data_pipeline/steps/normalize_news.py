"""뉴스 정제 Step2 — 정규화 + 필수필드 게이트 + canonical 멱등 병합 (ALPHA-131·132).

raw stock_news(FMP·BigKinds 두 벤더, 이형 스키마)를 읽어 **표준 뉴스 메타행으로
정규화**하고, 필수필드 게이트(quality/news.validate_news_meta)를 통과하는지 검사한다.
검증 결과는 `data_quality_logs` 로 남긴다 — 몇 건 읽고/통과/탈락(blocking)/경고했는지와
사유를 드러내, 분석에 못 쓰는 뉴스가 조용히 새거나 사라지지 않게 한다(AGENTS Rule 12).

게이트를 통과한 행은 `canonical/news/news_articles/language=…/published_date=…/` 에 **article_id
정체성 키로 멱등 병합**한다(ALPHA-132·352). 정체성 `article_id = url_hash(원문 URL)` 은 **소스
무관**이라(FMP `url`/BigKinds `PROVIDER_LINK_PAGE`) canonical 이 소스를 흡수한 **통합 구조**가 된다 —
source_vendor 는 파티션이 아니라 컬럼(provenance)이다. 파티션은 `language`(벤더 고정 파생)→
`published_date` 2단으로, 다운스트림 언어모델이 언어별로 분기/프루닝하게 한다(ALPHA-352). canonical 은
run_id 가 없어 같은 raw 를 몇 번 정제해도 결과가 같다. 같은 article_id 재적재는 최신 fetched_at 이
메타 대표를 이기되 mentions 는 union 한다. 다른 article_id 가 **같은 정규화 제목**이면 근접중복
신호로 로깅만 한다(URL 충돌은 곧 같은 id 라 자동 병합 → 신호 대상 아님). fuzzy 클러스터는
다운스트림(news_dedup_cluster) 소관이다.

정규화가 흡수하는 벤더 이형(raw 무변형으로 보존된 원본):
  - FMP: title/url/site/publishedDate(오프셋 없는 벽시계)/text/mentions[]
  - BigKinds: TITLE/PROVIDER_LINK_PAGE/PROVIDER/DATE·NEWS_ID(날짜 단위)/CONTENT/our_ticker
벤더 판별은 raw 키의 source= 파티션으로 한다(레코드 내용 아님 — 키가 규약의 SSOT).

**종목 매핑은 정규화의 일이다(ALPHA-416)**: BigKinds 행은 canonical ETF holdings 최신
스냅샷의 종목명 인덱스로 제목+리드에서 종목명을 탐지해 mentions 를 합성한다(구 raw 의
our_ticker provenance 와 union — 이행기 호환). 수집이 카테고리 주도(전체 경제 뉴스)로
전환돼도 mentions 가 유지되는 근거이며, 유니버스가 바뀌면 전체 백필 재정규화로 과거
기사에 소급된다. FMP 는 ingest 병합 mentions[] 그대로(영문 기사라 한글 이름 탐지 무의미).
"""

from __future__ import annotations

import json
import logging
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from ..lake import (
    Storage,
    canonical_etf_holdings_partition,
    canonical_news_articles_partition,
    is_raw_news_key,
    parse_raw_news_key,
    quality_log_key,
)
# BigKinds 발행 시각 파생(bigkinds_datetime)은 parse 의 벤더 SSOT — 날짜부가 ingest 의
# bigkinds_date(raw 파티션 published_date)와 항상 같다는 불변식을 그 함수가 보장한다.
from ..parse import bigkinds_datetime, news_article_id, normalize_url, parse_datetime, url_hash
from ..quality import BLOCKING_REASONS, validate_news_meta

logger = logging.getLogger(__name__)
_KST = timezone(timedelta(hours=9))


JOB_NAME = "normalize_news"
DATASET = "news_articles"

# 언어는 **벤더 고정**으로 파생한다(ALPHA-352) — code answers, 모델 불필요(Rule 5). BigKinds 는
# 국내(한국언론진흥재단) 아카이브라 전량 한국어, FMP /stable/news/stock 는 미국 금융 언론이라 영어
# (KR 기업 ADR 기사도 영어). market 은 언어가 아니다(FMP 가 market=KR 영어행을 낸다) → 벤더가 SSOT.
# 신규 뉴스 벤더는 여기 매핑만 늘린다. 미등록 벤더는 위쪽 unsupported_vendor 게이트가 먼저 막는다.
_LANGUAGE_BY_VENDOR = {"bigkinds": "ko", "fmp": "en"}

# 발행일 상한 여유 — 검증 실행일 기준 이 일수까지의 미래 발행일은 허용(수집 지연·TZ 여유).
_FUTURE_SLACK_DAYS = 2

# 종목명 탐지(ALPHA-416) 대상 시장 — BigKinds(ko)만 탐지하므로 KR holdings 만 인덱싱한다.
# FMP(en)는 ingest 가 병합한 mentions[] 가 이미 있고 영문 기사라 한글 종목명이 안 잡힌다.
_DETECT_MARKET = "KR"
# 이 길이 미만의 종목명은 인덱스에서 뺀다 — 한 글자 이름은 substring 오탐이 사실상 전부다.
_MIN_NAME_LEN = 2


def _text(record: dict, key: str) -> str | None:
    """문자열 필드 안전 추출 — 비문자열(int·list 등)은 None 으로 정리한다. 정규화 다운스트림
    (strip·normalize_url·parse_datetime)이 비str 에서 크래시하는 걸 막고(crash-before-gate),
    결측은 게이트가 사유로 잡게 한다(Rule 12)."""
    value = record.get(key)
    return value if isinstance(value, str) else None


def _mentions(record: dict, market: object) -> list[dict]:
    """canonical 에 보존할 종목 mention 목록(다운스트림 엔티티 링크 씨앗). FMP 는 ingest 가
    병합한 mentions[] 를, BigKinds 는 단일 our_ticker 를 [{market,ticker}] 로 합성한다.
    dict 원소만 취해 오염을 막는다(비객체 mention 은 버린다). BigKinds 는 여기에 더해
    run() 이 종목명 탐지(detect_mentions) 결과를 union 한다(ALPHA-416)."""
    existing = record.get("mentions")
    if isinstance(existing, list):
        return [m for m in existing if isinstance(m, dict)]
    ticker = record.get("our_ticker")
    if isinstance(ticker, str) and ticker.strip() and isinstance(market, str):
        # strip 해 저장 — 탐지 경로(detect_mentions)와 union dedup 키((market,ticker))를 맞춘다.
        return [{"market": market, "ticker": ticker.strip()}]
    return []


def _match_text(text: str) -> str:
    """부분문자열 매칭용 정규화 — NFKC 후 공백 축약. 인덱스의 종목명과 기사 텍스트에 **양쪽 다**
    적용해야 한다(한쪽만 하면 매칭이 조용히 죽는다).

    NFKC 는 저장소 관례다(`normalize_disclosure`·`parse_dart_*` — 실측 텍스트에 부분일치를 쓰는
    코드는 전부 먼저 정규화한다). 여기서 특히 필요한 이유(ALPHA-448): 같은 이름의 NFC/NFD 표기가
    서로 다른 키로 남으면 동명이 판정이 새어나가 **둘 다 '단일 ticker'로 인덱스에 들어간다** —
    막으려던 오매핑이 유니코드 형태 차이로 되살아난다."""
    return " ".join(unicodedata.normalize("NFKC", text).split())


def _holdings_name_index(storage: Storage) -> tuple[str | None, dict[str, dict], list[str]]:
    """canonical ETF holdings **최신 스냅샷(KR)** 에서 `종목명 → {market,ticker}` 인덱스를 만든다
    (ALPHA-416). 이름 출처를 holdings 로 두는 이유 — normalize 는 레이크만 읽는 설계 전제라
    DB(entity 마스터)를 붙일 수 없고, holdings 유니버스가 곧 분석 유니버스(load-instruments
    시딩 원천)라 탐지 범위가 다운스트림 in_universe 필터와 정합한다.

    스냅샷이 없으면 (None, {}, []) — 탐지는 no-op 이 되고 구 raw 의 our_ticker 경로만 남는다
    (신규 레이크에서 정상). 반환: (as_of_date, index, 동명이로 제외된 이름들)."""
    marker = canonical_etf_holdings_partition(_DETECT_MARKET, "")  # ".../as_of_date="
    dates = {key[len(marker):].split("/", 1)[0] for key in storage.list_keys(marker)}
    dates.discard("")
    if not dates:
        return None, {}, []
    as_of_date = max(dates)
    tickers_by_name: dict[str, set[str]] = {}
    prefix = canonical_etf_holdings_partition(_DETECT_MARKET, as_of_date)
    for key in storage.list_keys(prefix + "/"):
        if not key.endswith(".parquet"):
            continue
        for row in _read_parquet_rows(storage.get_bytes(key)):
            name, ticker = row.get("constituent_name"), row.get("constituent_ticker")
            if not (isinstance(name, str) and isinstance(ticker, str) and ticker.strip()):
                continue
            name = _match_text(name)  # 기사 텍스트와 같은 정규화라야 substring 이 맞는다
            if len(name) >= _MIN_NAME_LEN:
                tickers_by_name.setdefault(name, set()).add(ticker.strip())
    # 동명이(같은 이름, 다른 ticker)는 **어느 쪽도 고르지 않는다**(ALPHA-448). 이름을 키로 덮어쓰면
    # 파일 나열 순서가 승자를 정해 mention 이 비결정적으로 틀린다 — 틀린 ticker 가 canonical 에
    # 들어가면 다운스트림에서 되돌릴 근거가 없다. entity_resolution 의 동명이 ambiguous 와 같은
    # 판단이고, 탐지 누락은 quality log 의 제외 이름으로 드러난다(Rule 12).
    index = {name: {"market": _DETECT_MARKET, "ticker": next(iter(tickers))}
             for name, tickers in tickers_by_name.items() if len(tickers) == 1}
    ambiguous = sorted(name for name, tickers in tickers_by_name.items() if len(tickers) > 1)
    return as_of_date, index, ambiguous


def detect_mentions(text: str, name_index: dict[str, dict]) -> dict[str, dict]:
    """제목+리드 텍스트에서 종목명 substring 탐지 → {이름: mention}. entity_resolution 의
    완전일치(필드=이름)와 다른 축이다 — 여기는 자유 텍스트 안의 부분 문자열을 찾는다.
    이름을 키로 돌려줘 호출부가 '어느 이름이 얼마나 잡히는지'를 계측한다(오탐 감시).
    # ponytail: naive substring O(기사×이름) — 유니버스 수백 종목 규모까진 충분, 커지면 aho-corasick
    """
    return {name: mention for name, mention in name_index.items() if name in text}


def _normalize(vendor: str, record: dict) -> dict:
    """벤더 raw 뉴스행 → 표준 메타행. 비문자열/결측은 None 으로 정리(게이트가 사유로 잡음).

    게이트가 보는 필드(title·normalized_url·published_at·publisher)를 벤더 무관 표준행으로
    수렴시킨다 — 정제의 존재 이유(FMP·BigKinds 이형 흡수). canonical 적재용 필드
    (normalized_url_hash·mentions·fetched_at)도 함께 채운다.
    """
    is_bigkinds = vendor == "bigkinds"
    if is_bigkinds:
        title = _text(record, "TITLE")
        url = _text(record, "PROVIDER_LINK_PAGE")
        publisher = _text(record, "PROVIDER")
        market = "KR"
        # NEWS_ID 는 KST 벽시계다. UTC 로 라벨하면 사건 τ가 9시간 밀려 장중 기사가
        # 마감 후로 분류된다.
        published_at = parse_datetime(bigkinds_datetime(record), naive_tz=_KST)
        lead = _text(record, "CONTENT")
    else:  # fmp
        title = _text(record, "title")
        url = _text(record, "url")
        publisher = _text(record, "site")
        market = _text(record, "market")
        published_at = parse_datetime(_text(record, "publishedDate"))
        lead = _text(record, "text")

    # article_id 는 raw 의 stamp 를 **신뢰하지 않고 항상 재계산**한다(parse.news_article_id) —
    # canonical 정체성은 canonical 단계가 불변 raw 내용에서 파생해야, 정체성 로직이 바뀌어도
    # (예: NEWS_ID 우선 → 원문 URL 우선) 이미 수집된 구 raw 까지 통합이 적용된다. stamp 를
    # 신뢰하면 구 raw 의 옛 id 가 남아 같은 원문 URL 인 FMP·BigKinds 가 안 합쳐진다(Codex P2).
    article_id = news_article_id(record)

    return {
        "article_id": article_id,
        "source_vendor": vendor,
        "language": _LANGUAGE_BY_VENDOR[vendor],  # 파티션 키(벤더 고정) — canonical 경로만 쓰고 컬럼엔 안 넣음
        "market": market,
        "title": " ".join(title.split()) if title else None,  # 공백 정규화(제목 dedup 안정)
        "url": url,
        "normalized_url": normalize_url(url),
        "normalized_url_hash": url_hash(url),
        "published_at": published_at,
        "publisher": publisher.strip() if publisher else None,
        # 기사 앞부분(리드) — 다운스트림 태깅의 입력이다. 제목만으론 역할(공급자·고객사·금액)이
        # 안 나오는 기사가 많다. BigKinds CONTENT 는 200~256자 스니펫이고 FMP text 는 더 길 수
        # 있는데, 여기선 자르지 않고 온 만큼 보존한다(절단 기준은 소비처가 정한다). 본문 전문
        # 크롤은 여전히 범위 밖 — 리드는 이미 raw 에 있어 공짜다.
        "lead_text": " ".join(lead.split()) if lead else None,
        "mentions": _mentions(record, market),
        "fetched_at": _text(record, "fetched_at"),
    }


# ── canonical 적재(ALPHA-132) ────────────────────────────
# 명시 스키마로 고정(전 컬럼 string) — pyarrow 추론에 맡기면 all-None 컬럼이 null 타입으로
# 잡혀 기존 파티션과 병합 시 스키마가 충돌한다. mentions 는 JSON 문자열로 직렬화해 저장한다
# (parquet list-of-struct 의 스키마 복잡성·병합 충돌을 피한다 — 다운스트림이 파싱).
_CANONICAL_COLUMNS = (
    "article_id", "source_vendor", "market", "title", "url", "normalized_url",
    "normalized_url_hash", "published_at", "publisher", "lead_text", "mentions", "fetched_at",
)

_OLDEST = datetime.min.replace(tzinfo=timezone.utc)


def _canonical_schema():
    import pyarrow as pa

    return pa.schema([(c, pa.string()) for c in _CANONICAL_COLUMNS])


def _canonical_row(row: dict) -> dict:
    """표준 메타행 → canonical 직렬화 행. mentions(list)를 **정규화(dedup+정렬) JSON 문자열**로
    고정한다 — 단일 적재도 병합 경로(_union_mentions)와 **같은 결정적 표현**을 써야 멱등 재실행
    바이트가 안정된다(단일 적재만 raw 순서로 쓰면 첫 런 vs 병합 재런의 바이트가 어긋난다)."""
    out = {c: row.get(c) for c in _CANONICAL_COLUMNS}
    out["mentions"] = _union_mentions(row.get("mentions"))
    return out


def _union_mentions(*mentions_values: object) -> str:
    """여러 행의 mentions(JSON 문자열 또는 list)를 (market,ticker) 기준 union → **결정적** JSON
    문자열(정렬). BigKinds 는 종목별 질의라 같은 기사(NEWS_ID)가 여러 추적 종목에 걸려 각기 단일
    mention 으로 온다(ingest 가 mention 병합 안 함) — 병합이 통째 교체하면 종목↔기사 링크가
    유실되므로 union 한다. 정렬로 멱등 재실행이 같은 바이트를 낸다."""
    by_key: dict[tuple, dict] = {}
    for value in mentions_values:
        if isinstance(value, str):
            try:
                items = json.loads(value)
            except ValueError:
                items = []
        else:
            items = value or []
        if not isinstance(items, list):
            continue
        for mention in items:
            if isinstance(mention, dict):
                by_key[(str(mention.get("market")), str(mention.get("ticker")))] = mention
    return json.dumps([by_key[k] for k in sorted(by_key)], ensure_ascii=False)


def _read_parquet_rows(data: bytes) -> list[dict]:
    import io
    import pyarrow.parquet as pq

    return pq.read_table(io.BytesIO(data)).to_pylist()


def _write_parquet_rows(rows: list[dict]) -> bytes:
    import io
    import pyarrow as pa
    import pyarrow.parquet as pq

    table = pa.Table.from_pylist(
        [{c: r.get(c) for c in _CANONICAL_COLUMNS} for r in rows], schema=_canonical_schema()
    )
    buf = io.BytesIO()
    pq.write_table(table, buf)
    return buf.getvalue()


def _fetched_at(row: dict) -> datetime:
    """'최신 우선' 정렬 키 — 실제 시각으로 비교한다(문자열 비교는 오프셋이 다르면 어긋난다).
    파싱 불가·결측·naive 는 각각 가장 오래된 것/UTC 로 안전 처리(가격 정제와 동형)."""
    text = row.get("fetched_at")
    if not isinstance(text, str):
        return _OLDEST
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return _OLDEST
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _merge_partition(existing: list[dict], new_rows: list[dict]) -> list[dict]:
    """한 **published_date 파티션**(통합 — 벤더가 섞임)을 `article_id`(=원문 URL 해시) 키로 멱등
    병합. 같은 article_id 재적재는 최신 fetched_at 이 이기고(정정 반영), 동률이면 신규(멱등 재실행).

    가격의 fail-loud(벤더 교차 통화 오염) 분기는 없다 — 뉴스는 **정체성이 원문 URL 이라 교차벤더
    같은 id = 같은 실기사**이고, 오염될 공유 수치 진실이 없다. 교차벤더 같은 URL 이 병합될 때는
    최신 fetched_at 행의 스칼라 메타(title·url·market·source_vendor·publisher)가 대표가 되고 **패자
    쪽 스칼라는 버려진다** — 단 mentions 는 양쪽 union 한다(BigKinds 는 같은 기사가 여러 종목
    질의로 각기 단일 mention 으로 와, 통째 교체하면 종목↔기사 링크가 유실되므로). 그래서 행
    레벨 `market`/`source_vendor` 는 '대표(승자) 프로비넌스'일 뿐이고, **종목별 권위 있는 market
    은 self-describing 한 mentions[].market 에 있다**(다운스트림은 행 market 이 아니라 mention 을
    쓴다). 교차벤더 같은 URL 은 실무상 드묾(FMP 영문·US vs BigKinds 국문·KR)."""
    acc: dict[str, dict] = {}
    for row in [*existing, *new_rows]:
        article_id = row["article_id"]
        prev = acc.get(article_id)
        if prev is None:
            acc[article_id] = row
            continue
        winner = row if _fetched_at(row) >= _fetched_at(prev) else prev
        merged = dict(winner)
        merged["mentions"] = _union_mentions(prev.get("mentions"), row.get("mentions"))
        acc[article_id] = merged
    return [acc[a] for a in sorted(acc)]


def _duplicate_signals(rows: list[dict], published_date: str) -> list[dict]:
    """파티션 내 근접중복 신호 — **다른 article_id** 가 같은 **정규화 제목**을 가지면 로깅한다
    (exact 병합은 안 함 — 서로 다른 기사를 붕괴시키면 유실이므로 신호만). URL 은 이제 정체성이라
    같은 URL→같은 article_id→이미 병합이므로 다른 id 로 갈릴 수 없다 → 제목 근접중복만 신호 대상.
    호출은 (language, published_date) 파티션 단위라 **같은 언어 안의** 제목 충돌만 감지한다 —
    언어 파티션이 곧 벤더 파티션(1:1)이라 교차언어/교차벤더 near-dup 은 여기서 안 잡히고 다운스트림
    dedup 소관이다(ALPHA-352 언어분리의 귀결). 판정은 **공백정규화 제목의 정확일치**다
    (대소문자·문장부호 미폴딩) — 넓은 fuzzy 클러스터링은
    다운스트림 news_dedup_cluster 소관이라 여기선 좁은 exact-title 신호만 낸다."""
    signals: list[dict] = []
    groups: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        title = row.get("title")
        if isinstance(title, str) and title.strip():
            groups[title].append(row["article_id"])
    for title, article_ids in groups.items():
        distinct = sorted(set(article_ids))
        if len(distinct) > 1:
            signals.append({
                "published_date": published_date, "basis": "normalized_title",
                "title": title, "article_ids": distinct,
            })
    return signals


def _write_canonical(storage: Storage, passing: list[dict], signals: list[dict]) -> tuple[int, int]:
    """통과 행을 (language, published_date) 파티션별로 기존 canonical 과 article_id(원문 URL 해시)
    키로 멱등 병합해 쓴다. language 는 벤더 고정 파생(파티션 키, 컬럼 아님). 근접중복 신호는 각
    파티션 내에서만 감지된다 — 언어가 다르면 파티션이 갈려 교차언어 near-dup 은 안 잡힌다(의도:
    다운스트림 언어분기 대비). 신호는 signals 에 append(호출부가 quality_log 에 반영).
    반환: (쓴 파티션 수, 행 수)."""
    by_partition: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in passing:
        # 게이트 통과행은 published_at 이 유효(결측·범위밖 아님)라 [:10] 파티션이 결정적(멱등).
        published_date = row["published_at"][:10]
        by_partition[(row["language"], published_date)].append(_canonical_row(row))

    parts_written = rows_written = 0
    for (language, published_date), new_rows in sorted(by_partition.items()):
        prefix = canonical_news_articles_partition(language, published_date)
        # 파티션의 기존 parquet 을 전부 읽어 병합한다. 이 스텝은 항상 part-00000 하나로 되써
        # 멱등을 지킨다(canonical 은 이 스텝만 쓰므로 part-00000 만 존재).
        existing: list[dict] = []
        for key in storage.list_keys(prefix + "/"):
            if key.endswith(".parquet"):
                existing.extend(_read_parquet_rows(storage.get_bytes(key)))
        merged = _merge_partition(existing, new_rows)
        signals.extend(_duplicate_signals(merged, published_date))
        storage.put_bytes(f"{prefix}/part-00000.parquet", _write_parquet_rows(merged))
        parts_written += 1
        rows_written += len(merged)
    return parts_written, rows_written


def run(storage: Storage, run_id: str, input_run_id: str | None = None) -> int:
    """raw stock_news → 정규화 → 게이트 → canonical 멱등 병합 + quality_log. 성공 0, 장애 시 비0.

    input_run_id 지정 시 **그 수집 런의 raw 만** 읽어 canonical 을 멱등 적재한다(ALPHA-389 —
    SFN 이 이 경로로 돈다. 정제 비용이 여태 쌓인 raw 전체가 아니라 이번 런에 비례한다).
    미지정이면 raw news 전체를 읽는다 — **백필·복구 수단**이다(실패한 런의 raw 를 나중에
    주워오거나 정체성 로직 변경을 구 raw 에 소급할 때).
    """
    started_at = datetime.now(timezone.utc)
    checked_date = started_at.isoformat()[:10]
    # 발행일 상한 = 실행일 + 여유. 파싱되지만 범위 밖인 미래 날짜를 게이트가 잡는 기준.
    max_published_date = (started_at.date() + timedelta(days=_FUTURE_SLACK_DAYS)).isoformat()

    raw_keys = [k for k in storage.list_keys("raw/") if is_raw_news_key(k)]
    if input_run_id is not None:
        raw_keys = [k for k in raw_keys if f"/run_id={input_run_id}/" in k]

    # 종목명 탐지 인덱스(ALPHA-416). 로드 실패해도 정규화는 계속한다(구 our_ticker 경로는
    # 살아 있다) — 단 성공으로 위장하지 않는다(Rule 12): 전체 수집 전환 후엔 인덱스가 mentions
    # 의 유일한 공급원이라, exit 0 이면 'mentions 전량 소실'이 정상 완료로 오독된다. canonical
    # 적재 실패와 같은 계약으로 비0 종료한다. 스냅샷 부재(신규 레이크)는 실패가 아니다.
    name_index_error: str | None = None
    ambiguous_names: list[str] = []
    try:
        holdings_as_of, name_index, ambiguous_names = _holdings_name_index(storage)
    except Exception as exc:
        logger.exception("holdings 이름 인덱스 로드 실패 — 이번 런은 탐지 없이 정규화")
        holdings_as_of, name_index, name_index_error = None, {}, str(exc)
    detected_name_counts: Counter = Counter()

    read = 0
    failures: list[dict] = []  # blocking — canonical 제외 대상
    warnings: list[dict] = []  # non-blocking — 통과하되 결측을 로깅(url·publisher)
    passing: list[dict] = []  # 게이트 통과 행 — 루프 뒤 canonical 로 멱등 병합
    exit_code = 1 if name_index_error else 0  # 인덱스 로드 실패는 fail-loud(위 주석)

    for raw_key in raw_keys:
        try:
            # 키 파싱도 try 안에 둔다 — 규약 밖 키(source= 누락 등)의 KeyError 가 런 전체를
            # 죽이지 않고 이 파티션만 격리되게(가격 정제와 동일한 격리 의도).
            vendor = parse_raw_news_key(raw_key)["source"]
            lines = storage.get_bytes(raw_key).decode("utf-8").splitlines()
        except Exception as exc:
            logger.exception("raw 읽기/키 파싱 실패: %s", raw_key)
            failures.append({"raw_key": raw_key, "reasons": ["raw_read_error"], "error": str(exc)})
            exit_code = 1
            continue
        for line in lines:
            if not line.strip():
                continue
            read += 1
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                failures.append({"raw_key": raw_key, "reasons": ["unparseable_json"]})
                continue
            if not isinstance(record, dict):
                # 유효 JSON 이지만 객체가 아닌 행(null·배열·스칼라)은 _normalize 의 record.get 에서
                # 런 전체를 죽인다 — 행 단위로 격리해 나머지 검증이 완료되게(격리≠은폐, Rule 12).
                failures.append({"raw_key": raw_key, "reasons": ["non_object_row"]})
                continue
            if vendor not in ("fmp", "bigkinds"):
                # 알 수 없는 뉴스 벤더 — 조용히 통과시키지 않고 사유로 드러낸다(Rule 12).
                failures.append({"raw_key": raw_key, "source_vendor": vendor,
                                 "reasons": ["unsupported_vendor"]})
                continue
            try:
                row = _normalize(vendor, record)
                reasons = validate_news_meta(row, max_published_date=max_published_date)
            except Exception as exc:
                # 예기치 못한 행 단위 크래시도 배치를 죽이지 않게 격리한다(항상 quality_log — Rule 12).
                logger.exception("행 정규화 실패(격리): %s", raw_key)
                failures.append({"raw_key": raw_key, "reasons": ["row_error"], "error": str(exc)})
                continue

            ref = {"article_id": row["article_id"], "source_vendor": vendor,
                   "published_at": row["published_at"], "raw_key": raw_key}
            blocking = [r for r in reasons if r in BLOCKING_REASONS]
            if blocking:
                # blocking 이 있으면 canonical 제외 대상 — 경고까지 포함한 전체 사유를 남겨
                # 소스 품질 문제를 한 번에 파악하게 한다.
                failures.append({**ref, "reasons": reasons})
                continue
            if vendor == "bigkinds" and name_index:
                # 종목명 탐지 합성(ALPHA-416) — mentions = our_ticker(구 raw provenance) ∪ 탐지.
                # 전체 경제 뉴스 수집(카테고리 주도)으로 전환하면 our_ticker 가 사라지므로 이
                # 탐지가 mentions 의 유일한 공급원이 된다. dedup 은 _canonical_row 의
                # _union_mentions 가 (market,ticker) 키로 결정적으로 처리한다.
                text = _match_text(" ".join(p for p in (row["title"], row["lead_text"]) if p))
                hits = detect_mentions(text, name_index)
                if hits:
                    row["mentions"] = [*row["mentions"], *hits.values()]
                    detected_name_counts.update(hits.keys())
            passing.append(row)
            warn = [r for r in reasons if r not in BLOCKING_REASONS]
            if warn:
                # 통과했지만 url·publisher 결측 — canonical 진입은 시키되 provenance 손실을 드러낸다.
                warnings.append({**ref, "reasons": warn})

    # 통과 행을 canonical 로 멱등 병합 — **스코프든 전체 런이든 쓴다**(ALPHA-389).
    # 예전엔 전체 런만 썼고 근거를 "스코프가 부분 파티션을 덮어써 멱등성을 흔든다"로 적었으나
    # 그건 사실이 아니었다 — _write_canonical 은 파티션의 기존 parquet 을 **전부 읽어**
    # _merge_partition 으로 합치지(덮어쓰지) 않는다. 스코프 런이 자기 런의 행만 병합해도
    # 기존 행은 그대로 남는다.
    duplicate_signals: list[dict] = []
    parts_written = canonical_rows = 0
    canonical_written = True  # quality_log 계약 유지(스코프 여부와 무관하게 이제 항상 쓴다)
    try:
        parts_written, canonical_rows = _write_canonical(storage, passing, duplicate_signals)
    except Exception:
        logger.exception("canonical 적재 실패")
        # 감사 로그가 거짓말하지 않게 내린다 — 적재가 터졌는데 canonical_written=true 로
        # 남으면 나중에 백필 판단이 "적재는 됐고 0행이었다"로 오독한다(Rule 12).
        canonical_written = False
        exit_code = 1

    try:
        storage.put_bytes(
            quality_log_key(DATASET, checked_date, run_id),
            json.dumps({
                "run_id": run_id,
                "job_name": JOB_NAME,
                "dataset": DATASET,
                "input_run_id": input_run_id,
                "raw_files": len(raw_keys),
                "records_read": read,
                "records_passed": len(passing),
                "records_failed": len(failures),
                # 원장 관측용 공통 봉투(ALPHA-181) — 통과 행이 산출, 탈락 행이 유실이다.
                "ops": {"records_out": len(passing), "failed_records": len(failures)},
                "records_warned": len(warnings),
                "failures": failures,
                "warnings": warnings,
                # 종목명 탐지 계측(ALPHA-416) — 어느 이름이 얼마나 잡혔는지로 오탐을 감시한다.
                "mention_index_as_of_date": holdings_as_of,
                "mention_index_names": len(name_index),
                # 동명이로 인덱스에서 빠진 이름 — 탐지 누락의 사유이자 유니버스 품질 신호다.
                "mention_index_ambiguous_names": ambiguous_names,
                "mention_index_error": name_index_error,
                "detected_name_counts": dict(detected_name_counts),
                "canonical_written": canonical_written,
                "canonical_partitions_written": parts_written,
                "canonical_rows_written": canonical_rows,
                "duplicate_signals": duplicate_signals,
                "started_at": started_at.isoformat(),
                "finished_at": datetime.now(timezone.utc).isoformat(),
            }, ensure_ascii=False).encode("utf-8"),
        )
    except Exception:
        # 품질 로그마저 못 남기면 검증 결과가 통째로 유실된다 — 최소한 비0 종료로 알린다.
        logger.exception("quality_log 기록 실패 — 검증 결과 유실")
        exit_code = exit_code or 1

    logger.info(
        "normalize_news 완료: raw_files=%d read=%d passed=%d failed=%d warned=%d "
        "canonical_parts=%d canonical_rows=%d dup_signals=%d",
        len(raw_keys), read, len(passing), len(failures), len(warnings),
        parts_written, canonical_rows, len(duplicate_signals),
    )
    return exit_code
