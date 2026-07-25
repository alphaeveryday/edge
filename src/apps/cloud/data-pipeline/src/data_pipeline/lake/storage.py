"""레이크 스토리지 백엔드(local|s3) + 경로 빌더.

이 모듈이 s3://stock-ai-lake/ 파티션 규약의 SSOT 다 — 경로 문자열을
다른 곳에서 조립하지 말고 여기 빌더를 쓴다.

- raw:  run_id 별 append (재현성). 파티션 키는 소스별로 다르다 — 뉴스는 published_date,
        가격·재무는 ingest_date(수집일). 각 빌더 주석 참고.
- feature: canonical 에서 파생한 모델 산출물(LLM 태깅 등). canonical 과 마찬가지로 run_id 가
        없고 멱등이지만, canonical 이 **벤더 원본의 결정론적 정규화**인 반면 feature 는
        **비결정적·유료 추론의 결과**라 존을 가른다 — 재실행이 값을 바꿀 수 있으므로 한 번
        만든 것은 버전(tagger_version·ontology_version)이 같으면 다시 만들지 않는다.
- 로그: operations_archive/collection_logs/ 아래 run_id 별 1건.

백엔드는 설정(storage.backend)으로 고른다. MVP 개발은 local 스텁으로 돌리고,
배포는 s3 로 전환한다(같은 키 규약 — local 은 루트 디렉터리 아래 동일 상대경로).
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ..config import StorageConfig


# ── 경로 빌더 (파티션 규약 SSOT) ─────────────────────────
def raw_news_partition(
    source: str, market: str, published_date: str, run_id: str
) -> str:
    """raw 뉴스 파티션 프리픽스 (끝 슬래시 없음)."""
    return (
        f"raw/source={source}/dataset=stock_news/market={market}"
        f"/published_date={published_date}/run_id={run_id}"
    )


def raw_price_partition(
    source: str, market: str, ingest_date: str, run_id: str
) -> str:
    """raw 일봉(price_daily) 파티션 프리픽스 (끝 슬래시 없음).

    뉴스와 달리 파티션 키는 trade_date 가 아니라 ingest_date(수집일)다 — 가격 EOD
    응답은 한 심볼이 여러 trade_date 를 한 번에 주므로 원본을 수집일 기준으로
    보존한다(trade_date 별 분해는 후속 canonical/market_data 소관). SSOT: 사용자
    레이크 계층구조의 raw/source=fmp/dataset=price_daily/market=…/ingest_date=….
    """
    return (
        f"raw/source={source}/dataset=price_daily/market={market}"
        f"/ingest_date={ingest_date}/run_id={run_id}"
    )


def raw_investor_partition(
    source: str, market: str, ingest_date: str, run_id: str
) -> str:
    """raw 투자자 수급(investor_flow_daily) 파티션 프리픽스 (끝 슬래시 없음).

    가격(raw_price_partition)과 동형이다 — 투자자별 매매동향 응답은 한 종목이 여러 거래일을
    한 번에 주므로(콜당 30거래일) 원본을 수집일(ingest_date) 기준으로 run_id 별 append 한다.
    거래일(stck_bsop_date)은 각 레코드에 보존돼 canonical 이 쓴다. 날짜 윈도잉 백필로 같은
    거래일이 여러 run 에 걸쳐 들어오지만, 그 중복 제거는 canonical 소관이다(bronze 무변형).
    """
    return (
        f"raw/source={source}/dataset=investor_flow_daily/market={market}"
        f"/ingest_date={ingest_date}/run_id={run_id}"
    )


def raw_financial_partition(
    source: str, market: str, ingest_date: str, run_id: str
) -> str:
    """raw 재무제표 파티션 프리픽스 (끝 슬래시 없음).

    가격(raw_price_partition)과 동형이다 — bronze 통일 규약: 소스 불문 원본을 수집일
    (ingest_date) 기준으로 run_id 별 append 한다(전부 보존, dedup 없음). 재무 응답은 한
    (심볼·문서·주기) 질의가 여러 회계기간을 한 번에 주므로 원본을 수집일로 보존한다.

    재무는 드물게·비동기로 공시돼 매일 재폴링하면 같은 스냅샷이 날마다 쌓이지만, 그 중복
    제거·정정(SCD)·point-in-time 판정은 후속 canonical(silver) MERGE 소관이다 — raw 는
    받은 그대로 append 해 감사·재현성을 지킨다(정체성 판정을 raw 로 끌어올리지 않는다).
    statement_type·period_type·filing_date 등은 각 레코드에 그대로 보존돼 canonical 이 쓴다.
    """
    return (
        f"raw/source={source}/dataset=financial_statements/market={market}"
        f"/ingest_date={ingest_date}/run_id={run_id}"
    )


def raw_etf_partition(
    source: str, market: str, ingest_date: str, run_id: str
) -> str:
    """raw ETF 구성종목(etf_holdings) 파티션 프리픽스 (끝 슬래시 없음).

    가격·재무와 동형(bronze 통일) — ETF holdings 응답은 스냅샷이라 매 run 이 현재 PDF
    전량을 주므로 원본을 수집일(ingest_date) 기준으로 run_id 별 append 한다(전부 보존,
    dedup 없음). 벤더가 주는 기준일(as-of, FMP `updatedAt`)은 ingest_date 와 별개로 각
    레코드에 그대로 보존돼 canonical 이 쓴다 — 재무의 filing_date↔ingest_date 분리와 같다.
    같은 스냅샷 중복 제거·기준일 SCD·point-in-time 판정은 후속 canonical(silver) 소관이다.
    """
    return (
        f"raw/source={source}/dataset=etf_holdings/market={market}"
        f"/ingest_date={ingest_date}/run_id={run_id}"
    )


def raw_etf_nav_partition(
    source: str, market: str, ingest_date: str, run_id: str
) -> str:
    """raw ETF NAV(etf_nav) 파티션 프리픽스 (끝 슬래시 없음).

    가격(raw_price_partition)과 동형이다 — NAV 응답은 한 ETF 가 여러 거래일을 한 번에
    주므로(날짜창) 원본을 수집일(ingest_date) 기준으로 run_id 별 append 한다. 거래일
    (stck_bsop_date)은 각 레코드에 보존돼 canonical 이 쓴다(ALPHA-382). 날짜창 백필로
    같은 거래일이 여러 run 에 걸쳐 들어오지만, 그 중복 제거는 canonical 소관이다.
    """
    return (
        f"raw/source={source}/dataset=etf_nav/market={market}"
        f"/ingest_date={ingest_date}/run_id={run_id}"
    )


def raw_etf_inav_partition(
    source: str, market: str, ingest_date: str, run_id: str
) -> str:
    """raw ETF iNAV(etf_inav) 파티션 프리픽스 (끝 슬래시 없음).

    일별 NAV(raw_etf_nav_partition)와 dataset 을 나눈다 — 같은 벤더의 다른 축이다(거래일
    grain 종가 NAV vs 장중 시각 grain 추정 NAV). 한 dataset 에 섞으면 canonical 이 행마다
    grain 을 되짚어야 한다.

    장중 폴링이라 하루에 run 이 수십 개 들어온다. 소급 조회가 불가능해(ALPHA-555) 폴링
    창을 겹치게 잡으므로 같은 시각이 여러 run 에 중복 수집되는 것이 **정상**이다 — 겹침이
    유일한 갭 방어 수단이라 raw 는 전부 보존하고, 중복 제거는 canonical 소관이다.
    """
    return (
        f"raw/source={source}/dataset=etf_inav/market={market}"
        f"/ingest_date={ingest_date}/run_id={run_id}"
    )


def raw_etf_profile_partition(
    source: str, market: str, ingest_date: str, run_id: str
) -> str:
    """raw ETF 프로필(etf_profile) 파티션 프리픽스 (끝 슬래시 없음).

    구성종목·NAV 와 동형(bronze 통일) — 프로필은 스냅샷이라 매 run 이 현재 상품정보 전량을
    수집일(ingest_date) 기준으로 append 한다. 명칭 변경(개명)은 새 수집일의 새 스냅샷으로
    나타나고, 그 중 무엇이 현재인지 판정하는 건 canonical 소관이다.
    """
    return (
        f"raw/source={source}/dataset=etf_profile/market={market}"
        f"/ingest_date={ingest_date}/run_id={run_id}"
    )


def raw_disclosure_partition(
    source: str, market: str, ingest_date: str, run_id: str
) -> str:
    """raw 공시(disclosures) 메타 파티션 프리픽스 (끝 슬래시 없음).

    가격·재무와 동형(bronze 통일) — 공시목록(list.json) 행을 수집일(ingest_date) 기준으로
    run_id 별 append 한다(전부 보존, dedup 없음). 정체성 병합·corp_code↔ticker bridge·정정
    판정은 후속 canonical 소관이다. 각 행에 rcept_no(문서키)·corp_code·stock_code·source_url·
    document_raw_path 가 그대로 보존돼 canonical/파싱이 쓴다. 공시서류 원본 본문은 ndjson 에
    못 섞는 바이너리(euc-kr HTML ZIP)라 같은 파티션 아래 별도 객체로 둔다
    (raw_disclosure_document_key 참고).
    """
    return (
        f"raw/source={source}/dataset=disclosures/market={market}"
        f"/ingest_date={ingest_date}/run_id={run_id}"
    )


def raw_disclosure_document_key(
    source: str, market: str, ingest_date: str, run_id: str, rcept_no: str
) -> str:
    """raw 공시서류 원본 본문(document.xml ZIP) 객체 키.

    메타(raw_disclosure_partition ndjson)와 같은 파티션 아래 `documents/{rcept_no}.zip` 로
    받은 ZIP bytes 를 무변형 저장한다(bronze). 메타 행의 document_raw_path 가 이 키를 가리켜
    메타↔본문을 잇는다. rcept_no 는 14자리라 파일명이 유일하다.
    """
    return (
        f"{raw_disclosure_partition(source, market, ingest_date, run_id)}"
        f"/documents/{rcept_no}.zip"
    )


# ── raw price 스캔(정제 입력) ────────────────────────────
# 정제(normalize_price)는 raw price 를 벤더·시장·수집일에 걸쳐 읽어 (market,ticker,
# trade_date) 로 재그룹한다. raw 는 수집일(ingest_date)로 파티션되므로 한 trade_date 가
# 여러 ingest_date/run_id 에 흩어진다 — 프리픽스로 dataset 전체를 훑어야 한다. 경로
# 조립뿐 아니라 **경로 해석(파싱)도 이 모듈이 SSOT** 다(다른 곳에서 key 를 split 하지 않는다).
_RAW_PRICE_MARKER = "/dataset=price_daily/"


def is_raw_price_key(key: str) -> bool:
    """raw price_daily 데이터 파일 키인지. (part-*.ndjson 만, 프리픽스 디렉터리 아님.)"""
    return key.startswith("raw/") and _RAW_PRICE_MARKER in key and key.endswith(".ndjson")


def parse_raw_price_key(key: str) -> dict[str, str]:
    """raw price 키에서 파티션 값(source·market·ingest_date·run_id) 추출.

    경로 규약: raw/source=…/dataset=price_daily/market=…/ingest_date=…/run_id=…/part-*.ndjson.
    `key=value` 세그먼트만 취해 dict 로 — part 파일명(= 없음)은 자연히 빠진다.
    """
    segs = dict(seg.split("=", 1) for seg in key.split("/") if "=" in seg)
    return {
        "source": segs["source"],
        "market": segs["market"],
        "ingest_date": segs["ingest_date"],
        "run_id": segs["run_id"],
    }


# ── raw investor 스캔(정제 입력) ─────────────────────────
# 정제(normalize_investor)는 raw investor_flow_daily 를 벤더·시장·수집일에 걸쳐 읽어
# (market,ticker,trade_date) 로 재그룹한다. 가격(price_daily)과 동형이다 — 경로 해석도 이
# 모듈이 SSOT(다른 곳에서 key 를 split 하지 않는다).
_RAW_INVESTOR_MARKER = "/dataset=investor_flow_daily/"


def is_raw_investor_key(key: str) -> bool:
    """raw investor_flow_daily 데이터 파일 키인지. (part-*.ndjson 만, 프리픽스 디렉터리 아님.)"""
    return key.startswith("raw/") and _RAW_INVESTOR_MARKER in key and key.endswith(".ndjson")


def parse_raw_investor_key(key: str) -> dict[str, str]:
    """raw investor 키에서 파티션 값(source·market·ingest_date·run_id) 추출.

    경로 규약: raw/source=…/dataset=investor_flow_daily/market=…/ingest_date=…/run_id=…/part-*.ndjson.
    `key=value` 세그먼트만 취해 dict 로 — part 파일명(= 없음)은 자연히 빠진다.
    """
    segs = dict(seg.split("=", 1) for seg in key.split("/") if "=" in seg)
    return {
        "source": segs["source"],
        "market": segs["market"],
        "ingest_date": segs["ingest_date"],
        "run_id": segs["run_id"],
    }


def canonical_investor_flow_partition(market: str, trade_date: str) -> str:
    """canonical 투자자 수급 파티션 프리픽스 (끝 슬래시 없음).

    일봉(canonical_price_daily_partition)과 동형 — 투자자 순매수는 거래일 시계열이라 시간축이
    trade_date 이고 market_data 존에 둔다. run_id·source_vendor 파티션 없이 (market,trade_date)
    로 가른다(멱등). 정체성 키 (market,ticker,trade_date) 중 market·trade_date 가 파티션,
    ticker 가 파티션 내 행 키다. 벤더(kis)는 시장이 가르므로 컬럼(provenance)이지 파티션이 아니다.
    """
    return f"canonical/market_data/investor_flow_daily/market={market}/trade_date={trade_date}"


# ── raw news 스캔(정제 입력) ─────────────────────────────
# 정제(normalize_news)는 raw stock_news 를 벤더·시장·발행일에 걸쳐 읽어 표준 메타행으로
# 정규화한다. 경로 조립뿐 아니라 **경로 해석(파싱)도 이 모듈이 SSOT** 다(다른 곳에서 key 를
# split 하지 않는다 — is_raw_price_key/parse_raw_price_key 와 동형).
_RAW_NEWS_MARKER = "/dataset=stock_news/"


def is_raw_news_key(key: str) -> bool:
    """raw stock_news 데이터 파일 키인지. (part-*.ndjson 만, 프리픽스 디렉터리 아님.)"""
    return key.startswith("raw/") and _RAW_NEWS_MARKER in key and key.endswith(".ndjson")


def parse_raw_news_key(key: str) -> dict[str, str]:
    """raw news 키에서 파티션 값(source·market·published_date·run_id) 추출.

    경로 규약: raw/source=…/dataset=stock_news/market=…/published_date=…/run_id=…/part-*.ndjson.
    `key=value` 세그먼트만 취해 dict 로 — part 파일명(= 없음)은 자연히 빠진다.
    """
    segs = dict(seg.split("=", 1) for seg in key.split("/") if "=" in seg)
    return {
        "source": segs["source"],
        "market": segs["market"],
        "published_date": segs["published_date"],
        "run_id": segs["run_id"],
    }


# ── raw disclosure 스캔(정제 입력) ───────────────────────
# 정제(normalize_disclosure)는 raw disclosures 메타 ndjson 을 수집일에 걸쳐 읽어 본문을
# 파싱·조인한다. 메타는 part-*.ndjson, 본문은 같은 파티션 아래 documents/*.zip 라 **메타만**
# 매칭한다(is_raw_price_key/is_raw_news_key 와 동형 — .ndjson 만). 경로 해석도 이 모듈이 SSOT.
_RAW_DISCLOSURE_MARKER = "/dataset=disclosures/"


def is_raw_disclosure_key(key: str) -> bool:
    """raw disclosures 메타 파일 키인지. (part-*.ndjson 만 — documents/*.zip 본문은 제외.)"""
    return key.startswith("raw/") and _RAW_DISCLOSURE_MARKER in key and key.endswith(".ndjson")


def parse_raw_disclosure_key(key: str) -> dict[str, str]:
    """raw disclosure 메타 키에서 파티션 값(source·market·ingest_date·run_id) 추출.

    경로 규약: raw/source=…/dataset=disclosures/market=…/ingest_date=…/run_id=…/part-*.ndjson.
    `key=value` 세그먼트만 취해 dict 로 — part 파일명(= 없음)은 자연히 빠진다.
    """
    segs = dict(seg.split("=", 1) for seg in key.split("/") if "=" in seg)
    return {
        "source": segs["source"],
        "market": segs["market"],
        "ingest_date": segs["ingest_date"],
        "run_id": segs["run_id"],
    }


# ── raw etf 스캔(정제 입력) ──────────────────────────────
# 정제(normalize_etf)는 raw etf_holdings 를 벤더·시장·수집일에 걸쳐 읽어 (market,etf_id,
# 구성종목,as_of_date) 공통 스키마로 정규화한다. 경로 해석도 이 모듈이 SSOT(다른 곳에서 key 를
# split 하지 않는다 — is_raw_price_key/parse_raw_price_key 와 동형).
_RAW_ETF_MARKER = "/dataset=etf_holdings/"


def is_raw_etf_key(key: str) -> bool:
    """raw etf_holdings 데이터 파일 키인지. (part-*.ndjson 만, 프리픽스 디렉터리 아님.)"""
    return key.startswith("raw/") and _RAW_ETF_MARKER in key and key.endswith(".ndjson")


def parse_raw_etf_key(key: str) -> dict[str, str]:
    """raw etf 키에서 파티션 값(source·market·ingest_date·run_id) 추출.

    경로 규약: raw/source=…/dataset=etf_holdings/market=…/ingest_date=…/run_id=…/part-*.ndjson.
    `key=value` 세그먼트만 취해 dict 로 — part 파일명(= 없음)은 자연히 빠진다.
    """
    segs = dict(seg.split("=", 1) for seg in key.split("/") if "=" in seg)
    return {
        "source": segs["source"],
        "market": segs["market"],
        "ingest_date": segs["ingest_date"],
        "run_id": segs["run_id"],
    }


_RAW_ETF_NAV_MARKER = "/dataset=etf_nav/"


def is_raw_etf_nav_key(key: str) -> bool:
    """raw etf_nav 데이터 파일 키인지. (part-*.ndjson 만, 프리픽스 디렉터리 아님.)

    마커가 `/dataset=etf_nav/` 라 구성종목(`/dataset=etf_holdings/`)과 정확히 갈린다 —
    is_raw_etf_key 가 NAV 를 집거나 그 반대가 되지 않는다.
    """
    return key.startswith("raw/") and _RAW_ETF_NAV_MARKER in key and key.endswith(".ndjson")


def parse_raw_etf_nav_key(key: str) -> dict[str, str]:
    """raw etf_nav 키에서 파티션 값(source·market·ingest_date·run_id) 추출.

    경로 규약: raw/source=…/dataset=etf_nav/market=…/ingest_date=…/run_id=…/part-*.ndjson.
    """
    segs = dict(seg.split("=", 1) for seg in key.split("/") if "=" in seg)
    return {
        "source": segs["source"],
        "market": segs["market"],
        "ingest_date": segs["ingest_date"],
        "run_id": segs["run_id"],
    }


def canonical_etf_nav_partition(market: str, trade_date: str) -> str:
    """canonical ETF NAV 파티션 프리픽스 (끝 슬래시 없음).

    holdings(스냅샷·as_of_date)가 아니라 **일봉(canonical_price_daily_partition)과 동형**이다 —
    NAV 는 거래일 시계열이고 마트 etf_nav_daily 의 grain 도 (etf, trade_date)라 시간축이
    trade_date 다. 그래서 market_data 존에 둔다. 정체성 키 (market,etf_id,trade_date) 중
    market·trade_date 가 파티션, etf_id 가 파티션 내 행 키다.
    """
    return f"canonical/market_data/etf_nav/market={market}/trade_date={trade_date}"


_RAW_ETF_PROFILE_MARKER = "/dataset=etf_profile/"


def is_raw_etf_profile_key(key: str) -> bool:
    """raw etf_profile 데이터 파일 키인지. (part-*.ndjson 만.)"""
    return key.startswith("raw/") and _RAW_ETF_PROFILE_MARKER in key and key.endswith(".ndjson")


def parse_raw_etf_profile_key(key: str) -> dict[str, str]:
    """raw etf_profile 키에서 파티션 값(source·market·ingest_date·run_id) 추출."""
    segs = dict(seg.split("=", 1) for seg in key.split("/") if "=" in seg)
    return {
        "source": segs["source"],
        "market": segs["market"],
        "ingest_date": segs["ingest_date"],
        "run_id": segs["run_id"],
    }


def canonical_etf_profile_partition(market: str, as_of_date: str) -> str:
    """canonical ETF 프로필 파티션 프리픽스 (끝 슬래시 없음).

    시세(market_data)가 아니라 **참조 데이터**라 reference 존에 둔다 — 거래일 시계열이 아니고
    상품 식별·명칭이라 값이 거의 안 바뀐다. 시간축은 수집 기준일(as_of_date)이다: 개명이
    일어나면 새 기준일의 행이 최신을 말하고, 마스터 로더는 **최신 기준일 스냅샷**을 읽는다
    (구성종목 canonical 과 같은 모델).
    """
    return f"canonical/reference/etf_profile/market={market}/as_of_date={as_of_date}"


def canonical_etf_holdings_partition(market: str, as_of_date: str) -> str:
    """canonical ETF 구성종목 파티션 프리픽스 (끝 슬래시 없음).

    가격(canonical_price_daily_partition)과 동형 — run_id·source_vendor 파티션 없이
    (market, as_of_date) 로 가른다(멱등). 정체성 키 (market,etf_id,constituent_ticker,
    as_of_date) 중 market·as_of_date 가 파티션, (etf_id,constituent_ticker)가 파티션 내 행
    키다. 벤더는 시장이 가른다(US=fmp, KR=krx)라 source_vendor 는 파티션이 아니라 컬럼
    (provenance)이고, market-스코프 파티션이라 한 파티션엔 한 벤더만 온다(교차 충돌 불가).
    """
    return f"canonical/holdings/etf_holdings/market={market}/as_of_date={as_of_date}"


def canonical_price_daily_partition(market: str, trade_date: str) -> str:
    """canonical 일봉 파티션 프리픽스 (끝 슬래시 없음).

    raw 와 달리 run_id·source_vendor 파티션이 없다 — canonical 은 멱등이라 같은 raw 를
    몇 번 정제해도 결과가 같아야 하고, 벤더는 시장이 가른다(US=fmp, KR=kis). 정체성 키
    (market,ticker,trade_date) 중 market·trade_date 가 파티션, ticker 는 파티션 내 행 키다.
    """
    return f"canonical/market_data/price_daily/market={market}/trade_date={trade_date}"


def canonical_news_articles_partition(language: str, published_date: str) -> str:
    """canonical 뉴스 메타 파티션 프리픽스 (끝 슬래시 없음).

    파티션은 `language`(ko·en) → `published_date` 2단이다(ALPHA-352). 다운스트림 언어모델이
    언어별로 프루닝/분기하도록 언어를 파티션 최상단에 둔다 — 언어는 **벤더 고정**으로 파생한다
    (bigkinds=ko·fmp=en). `market` 을 언어 프록시로 쓰면 틀린다(FMP 는 KR 기업 ADR 의 영어
    기사를 market=KR 로 낸다) — 그래서 언어 파티션은 market 이 아니라 벤더에서 온다.

    source_vendor·market 은 파티션이 아니라 **컬럼**(provenance)이라 한 언어 파티션 안에 (벤더별로)
    섞인다 — 다만 현재 두 벤더가 언어와 1:1이라 언어 파티션이 곧 벤더 파티션이다. run_id 는 없다
    (멱등). 정체성 키는 `article_id`(=원문 URL 해시, 소스 무관)로 파티션 내 행 키다. 같은 원문 URL
    이 두 언어 파티션에 각각 오면 서로 다른 파티션이라 병합되지 않는다(교차언어 병합은 실무상
    드물고 — FMP 영어·BigKinds 국문 — 다운스트림 dedup 소관)."""
    return f"canonical/news/news_articles/language={language}/published_date={published_date}"


def feature_news_assertions_partition(language: str, published_date: str) -> str:
    """feature 뉴스 assertion 파티션 프리픽스 (끝 슬래시 없음).

    입력 canonical 뉴스와 **같은 파티션 축**(language → published_date)이다 — 한 canonical
    파티션이 한 feature 파티션으로 대응해 태깅 대상을 프루닝으로 고를 수 있다(비용이 LLM 호출에
    비례하므로 날짜창 프루닝이 곧 비용 통제다). 정체성 키는 canonical 과 같은 `article_id`.

    canonical 이 아니라 feature 인 이유: 여기 값은 벤더 원본의 결정론적 정규화가 아니라 **LLM
    추론 결과**다. 같은 입력에 다시 돌려도 같은 값이 나온다는 보장이 없고 호출마다 돈이 든다.
    그래서 canonical(언제든 raw 에서 재생성 가능·무료)과 라이프사이클이 다르다 — 이 존은 한 번
    만든 걸 보존하고, `tagger_version`·`ontology_version` 이 바뀔 때만 다시 만든다.

    행은 **기사 1건 = 1행**이다(assertion 1건 = 1행이 아니다). 한 기사가 사건을 0..N 개 주장하고
    status·reasons 는 기사 단위 사실이라, assertion 을 펼치면 사건 0건인 기사(전체의 다수 — 시황·
    논평)가 행 자체를 잃어 '태깅했는데 사건이 없었다'와 '태깅한 적 없다'가 구분되지 않는다.
    assertions 는 JSON 문자열 컬럼이다(canonical 뉴스의 mentions 와 같은 관례).
    """
    return f"feature/news/assertions/language={language}/published_date={published_date}"


def canonical_supply_contract_fact_partition(report_date: str) -> str:
    """canonical 공급계약 fact 파티션 프리픽스 (끝 슬래시 없음).

    raw 와 달리 run_id·source_vendor 파티션이 없다 — canonical 은 멱등이라 같은 raw 를 몇 번
    정제해도 결과가 같아야 한다. 파티션은 `report_date`(rcept_dt, 공시 접수일) 하나(가격의
    trade_date·뉴스의 published_date 파티션과 동형 — 프루닝·라이프사이클). 정체성 키는
    `rcept_no`(14자리 접수번호=문서키)로 파티션 내 행 키다. source_vendor(dart)는 현재 KR·DART
    단독이라 컬럼(provenance)이지 파티션이 아니다.
    """
    return f"canonical/disclosures/supply_contract_fact/report_date={report_date}"


def canonical_business_segment_fact_partition(report_date: str) -> str:
    """canonical 사업부문 fact 파티션 프리픽스 (끝 슬래시 없음).

    공급계약 fact(canonical_supply_contract_fact_partition)와 동형 — run_id·source_vendor
    파티션 없이 `report_date`(rcept_dt) 하나로 가른다(멱등). 정체성 키는 파티션 내
    `rcept_no + segment_ordinal`(파스 순서) — 한 사업보고서에 사업부문 여러 행이고, segment_name
    은 유일하지 않아(제품/용역 sub-row) 순서 인덱스로 키를 잡는다. source_vendor(dart)는 컬럼.
    """
    return f"canonical/disclosures/business_segment_fact/report_date={report_date}"


def quality_log_key(dataset: str, checked_date: str, run_id: str) -> str:
    """정제 품질 로그(검증 실행당 1건) 키.

    canonical 자체는 run_id 가 없지만(멱등), '이 검증 실행이 무엇을 몇 건 걸렀나'는
    실행 단위 감사라 run_id 로 남긴다. collection_log(수집)와 분리된 정제 단계 로그다.
    """
    return (
        f"operations_archive/data_quality_logs/dataset={dataset}"
        f"/checked_date={checked_date}/run_id={run_id}/log.json"
    )


def quality_log_prefix(dataset: str) -> str:
    """그 dataset 의 품질 로그가 사는 프리픽스(날짜 이하 전부). 관측이 run_id 로 훑을 때 쓴다.

    날짜 세그먼트를 부르는 쪽이 알 수 없어(런 시작 UTC 날짜라 자정 넘긴 런에서 어긋난다)
    프리픽스가 필요하다 — 경로 조립은 여기(경로 규약 SSOT)에 둔다.
    """
    return f"operations_archive/data_quality_logs/dataset={dataset}/"


def collection_log_prefix(source: str, dataset: str) -> str:
    """그 (source, dataset) 수집 로그가 사는 프리픽스. quality_log_prefix 와 같은 이유."""
    return f"operations_archive/collection_logs/source={source}/dataset={dataset}/"


def collection_log_key(source: str, dataset: str, started_date: str, run_id: str) -> str:
    """수집 실행 로그(런당 1건) 키.

    source 뿐 아니라 dataset 으로도 가른다 — 같은 벤더(source=fmp)의 뉴스(stock_news)·
    가격(price_daily) 수집이 같은 run_id 를 공유해도(오케스트레이션 백필 등) 로그가
    서로 덮어쓰지 않게. (뉴스만 있던 시절엔 dataset 없이 source 로만 갈랐다.)
    """
    return (
        f"operations_archive/collection_logs/source={source}/dataset={dataset}"
        f"/started_date={started_date}/run_id={run_id}/log.json"
    )


# ── 백엔드 ──────────────────────────────────────────────
class Storage(Protocol):
    """레이크 키-바이트 저장 계약. 키는 위 빌더가 만든 상대경로."""

    def put_bytes(self, key: str, data: bytes) -> None: ...

    def get_bytes(self, key: str) -> bytes: ...

    def list_keys(self, prefix: str) -> list[str]: ...


class LocalStorage:
    """로컬 파일 스텁 — 키를 루트 아래 동일 상대경로 파일로 저장한다."""

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        return self.root / key

    def put_bytes(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def get_bytes(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def list_keys(self, prefix: str) -> list[str]:
        # S3 처럼 '문자열 prefix' 매칭 — prefix 를 디렉터리로 취급하면 백엔드 간
        # 동작이 갈린다(컴포넌트 중간을 자르는 prefix·전체 키 전달 등). 두 백엔드의
        # 키 규약을 일치시켜 로컬 통과·배포 S3 불일치를 막는다.
        if not self.root.exists():
            return []
        # as_posix(): Windows 에서 str() 은 백슬래시 키를 내놓아 빌더의 '/' prefix 와
        # 영영 안 맞는다 — 키 규약은 OS 무관 forward-slash 다(S3 와 동형).
        keys = (
            p.relative_to(self.root).as_posix()
            for p in self.root.rglob("*")
            if p.is_file()
        )
        return sorted(k for k in keys if k.startswith(prefix))


class S3Storage:
    """S3 백엔드. boto3 는 지연 import — 단위테스트는 boto3 없이 모듈을 import 한다."""

    def __init__(self, bucket: str):
        self.bucket = bucket
        self._client = None

    @property
    def client(self):  # pragma: no cover - 통합(실 S3)
        if self._client is None:
            import boto3

            self._client = boto3.client("s3")
        return self._client

    def put_bytes(self, key: str, data: bytes) -> None:  # pragma: no cover - 통합
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data)

    def get_bytes(self, key: str) -> bytes:  # pragma: no cover - 통합
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def list_keys(self, prefix: str) -> list[str]:  # pragma: no cover - 통합
        paginator = self.client.get_paginator("list_objects_v2")
        keys: list[str] = []
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            keys.extend(obj["Key"] for obj in page.get("Contents", []))
        return sorted(keys)


def make_storage(config: StorageConfig) -> Storage:
    """설정에 따라 백엔드를 고른다. 잘못된 조합은 StorageConfig 검증이 이미 막았다."""
    if config.backend == "s3":
        return S3Storage(bucket=config.bucket)  # type: ignore[arg-type]
    return LocalStorage(root=config.local_root)
