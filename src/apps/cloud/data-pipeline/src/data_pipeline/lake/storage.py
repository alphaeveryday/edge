"""레이크 스토리지 백엔드(local|s3) + 경로 빌더.

이 모듈이 s3://stock-ai-lake/ 파티션 규약의 SSOT 다 — 경로 문자열을
다른 곳에서 조립하지 말고 여기 빌더를 쓴다.

- raw:  run_id 별 append (재현성). 파티션 키는 소스별로 다르다 — 뉴스는 published_date,
        가격·재무는 ingest_date(수집일). 각 빌더 주석 참고. **예외: 1분 파이프라인의
        minute artifact 는 run_id 없는 결정적·불변 키다**(v0.7 9절 — 재실행 no-op 전제,
        canonical_price_minute_artifact_key 주석 참고 — 분봉은 canonical 존이 정본이다,
        ALPHA-701·705). 스캐너·보존 정책이 run_id= 존재를 전제하면 안 된다.
- feature: canonical 에서 파생한 모델 산출물(LLM 태깅 등). canonical 과 마찬가지로 run_id 가
        없고 멱등이지만, canonical 이 **벤더 원본의 결정론적 정규화**인 반면 feature 는
        **비결정적·유료 추론의 결과**라 존을 가른다 — 재실행이 값을 바꿀 수 있으므로 한 번
        만든 것은 버전(tagger_version·ontology_version)이 같으면 다시 만들지 않는다.
- 로그: operations_archive/collection_logs/ 아래 run_id 별 1건.

백엔드는 설정(storage.backend)으로 고른다. MVP 개발은 local 스텁으로 돌리고,
배포는 s3 로 전환한다(같은 키 규약 — local 은 루트 디렉터리 아래 동일 상대경로).
"""

from __future__ import annotations

import hashlib
import threading
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


def raw_price_5min_partition(
    source: str, market: str, ingest_date: str, run_id: str
) -> str:
    """raw 5분봉(price_5min) 파티션 프리픽스 (끝 슬래시 없음).

    raw_price_partition(일봉)과 동형이나 파일 포맷은 ndjson 이 아니라 **parquet**(티커당 1개,
    part-*.ndjson 관례 아님) — 벤더 원본이 이미 parquet 이고 종목당 수만 행(3~4년치 5분봉)이라
    ndjson 재직렬화는 순수 비용이다. 그래서 is_raw_price_key 류 스캐너 대상이 아니다(마커가
    dataset=price_daily 로 고정) — 이 데이터셋을 읽는 정제(normalize) 스텝은 아직 없다(후속).
    """
    return (
        f"raw/source={source}/dataset=price_5min/market={market}"
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


def raw_investor_estimate_partition(
    source: str, market: str, ingest_date: str, run_id: str
) -> str:
    """raw 장중 투자자 추정(investor_flow_intraday) 파티션 프리픽스 (끝 슬래시 없음).

    `raw_investor_partition`(EOD 확정)과 **다른 dataset** 이다 — 값이 가집계 추정이고 시간축이
    거래일이 아니라 그날의 슬롯(`bsop_hour_gb`)이라, 한 데이터셋에 담으면 소비자가 잠정과
    확정을 구분할 수 없다(`.dev/etf-flow-collection-plan.md` §2.5).

    파티션 축은 EOD 와 같다(수집일 기준 run_id 별 append) — 하루에 여러 슬롯 런이 각자
    run_id 로 쌓이고, 슬롯 간 중복(응답이 그날 슬롯을 누적해 주는 성질)의 정리는 canonical
    소관이다(bronze 무변형).
    """
    return (
        f"raw/source={source}/dataset=investor_flow_intraday/market={market}"
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


def raw_report_partition(
    source: str, market: str, ingest_date: str, run_id: str
) -> str:
    """raw 보고서(reports) 파티션 프리픽스 (끝 슬래시 없음).

    가격·재무와 동형(bronze 통일) — 원본을 수집일(ingest_date) 기준으로 run_id 별 append
    한다. **발표일(published_date)로 파티션하지 않는 이유**: 한 번의 날짜 범위 백필이 여러
    발표일을 한꺼번에 주고(뉴스처럼 발표일당 한 번 도는 구조가 아니다), 같은 발표일이 여러
    백필 run 에 걸쳐 다시 들어온다. 발표일은 각 레코드에 보존돼 canonical 이 쓴다.

    `market` 은 시장이 아니라 **지리 범위**를 담는다(KR·US·GLOBAL) — 보고서는 거래소에
    속하지 않는다. 파티션 키 이름을 새로 만들지 않고 기존 규약을 재사용하는 편이, 스캐너·
    로그 키·승격 절차를 하나로 유지한다.

    광의의 보고서를 한 데이터셋에 담는다. 종류(basic·current·estimative·warning)와 출처
    등급(신뢰도)은 레코드 컬럼이다 - raw 에서 종류별로 갈라 두면 분류 규칙이 바뀔 때
    이미 쌓인 파티션을 옮겨야 한다. 종류로 테이블을 가르는 것은 canonical 소관이다.
    """
    return (
        f"raw/source={source}/dataset=reports/market={market}"
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


def raw_instrument_profile_partition(
    source: str, market: str, ingest_date: str, run_id: str
) -> str:
    """raw 종목기본정보(instrument_profile) 파티션 프리픽스 (끝 슬래시 없음).

    ETF 프로필과 동형(bronze 통일) — 상장 종목의 식별·명칭이라 스냅샷이고, 매 run 이 그
    기준일의 전종목을 수집일(ingest_date) 기준으로 append 한다. **벤더 기준일(basDd)은
    수집일과 별개로 각 레코드에 보존**한다(ALPHA-829): KRX 가 당일 조회를 막아 basDd 는
    직전 거래일이므로, 수집일을 기준일로 대신 읽으면 마스터가 하루 앞선 날짜를 주장한다.

    ⚠️ 두 날짜가 **항상 다르지는 않다**. ingest_date 는 UTC, basDd 는 KST 파생이라
    08:00~09:00 KST(= 전날 23:00~24:00 UTC) 실행에서는 우연히 같은 값이 된다. 그래서
    "다르니까 구분된다"에 기대지 말고 **각자 자기 축을 쓴다** — canonical 은 행의 basDd 만
    본다(`canonical_instrument_profile_partition`).
    """
    return (
        f"raw/source={source}/dataset=instrument_profile/market={market}"
        f"/ingest_date={ingest_date}/run_id={run_id}"
    )


_RAW_INSTRUMENT_PROFILE_MARKER = "/dataset=instrument_profile/"


def is_raw_instrument_profile_key(key: str) -> bool:
    """raw instrument_profile 데이터 파일 키인지. (part-*.ndjson 만.)"""
    return (key.startswith("raw/") and _RAW_INSTRUMENT_PROFILE_MARKER in key
            and key.endswith(".ndjson"))


def parse_raw_instrument_profile_key(key: str) -> dict[str, str]:
    """raw instrument_profile 키에서 파티션 값(source·market·ingest_date·run_id) 추출.

    ⚠️ 여기엔 **기준일이 없다**. canonical 의 as_of_date 는 키가 아니라 **행의 `bas_dd`**
    에서 온다 — ingest_date 로 대신 읽으면 안 된다(파티션 빌더 주석 참조).
    """
    segs = dict(seg.split("=", 1) for seg in key.split("/") if "=" in seg)
    return {
        "source": segs["source"],
        "market": segs["market"],
        "ingest_date": segs["ingest_date"],
        "run_id": segs["run_id"],
    }


def canonical_instrument_profile_partition(market: str, as_of_date: str) -> str:
    """canonical 종목기본정보 파티션 프리픽스 (끝 슬래시 없음).

    `canonical_etf_profile_partition` 과 같은 모델이다 — 시세가 아니라 **참조 데이터**라
    reference 존이고, 시간축은 벤더 기준일(as_of_date = KRX `basDd`)이다. 종목 마스터
    로더는 **최신 기준일 스냅샷**을 읽는다(ETF 프로필·구성종목과 동형).

    ⚠️ as_of_date 는 수집일이 아니다 — raw 빌더 주석 참조.
    """
    return f"canonical/reference/instrument_profile/market={market}/as_of_date={as_of_date}"


def raw_disclosure_day_prefix(source: str, market: str, ingest_date: str) -> str:
    """raw 공시 **수집일 전체**(run_id 무관) 프리픽스 (끝 슬래시 포함).

    한 수집일에는 run_id 파티션이 여럿 있다 — 장중 레인은 같은 날 여러 슬롯이 돈다. 그
    날 이미 받아 둔 본문(document.xml ZIP)을 찾으려면 run_id 아래가 아니라 **수집일 전체**를
    훑어야 한다(ALPHA-720). run_id 까지 붙은 프리픽스가 필요하면 raw_disclosure_partition 이다.

    끝 슬래시를 포함하는 이유: 프리픽스는 문자열 매칭이라(list_keys 규약) 슬래시가 없으면
    `ingest_date=2026-08-0` 가 `…-03`·`…-04` 를 함께 잡는다.
    """
    return f"raw/source={source}/dataset=disclosures/market={market}/ingest_date={ingest_date}/"


def raw_disclosure_partition(
    source: str, market: str, ingest_date: str, run_id: str
) -> str:
    """raw 공시(disclosures) 메타 파티션 프리픽스 (끝 슬래시 없음).

    가격·재무와 동형(bronze 통일) — 공시목록(list.json) 행을 수집일(ingest_date) 기준으로
    run_id 별 append 한다(전부 보존). 정체성 병합·corp_code↔ticker bridge·정정 판정은 후속
    canonical 소관이다 — **서로 다른 관측을 접는 일은 여기서 하지 않는다.** 단 소스가 한
    순회 안에서 **내용이 완전히 같은 행**은 접는다(페이지 경계 이동 중복 — 접지 않으면 같은
    문서를 두 번 내려받는다). 그래서 collection_log 의 `list_rows_seen`(벤더가 건넨 행 수)과
    이 파티션의 행 수는 다를 수 있다 — 서로 다른 질문에 대한 답이고 둘 다 참이라, 그 차이를
    유실로 대사하면 안 된다. 각 행에 rcept_no(문서키)·corp_code·stock_code·source_url 이
    그대로 보존돼 canonical/파싱이 쓴다. `document_raw_path` 는 **대상 유형 행만** 값을
    갖는다(ALPHA-865) — 파티션에는 유니버스 행이 전량 들어오고 유형은 `is_target` 플래그로
    실리는데, 본문은 대상만 받기 때문이다. 비대상 행은 `document_raw_path`·`body_format` 이
    명시적 None 이다(키 부재가 아니라). 공시서류 원본 본문은 ndjson 에
    못 섞는 바이너리(euc-kr HTML ZIP)라 같은 파티션 아래 별도 객체로 둔다
    (raw_disclosure_document_key 참고).
    """
    return f"{raw_disclosure_day_prefix(source, market, ingest_date)}run_id={run_id}"


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


# ── raw 장중 투자자 추정 스캔(정제 입력) ────────────────
# EOD 와 **다른 dataset** 이라 마커만 다르다. 키 파싱은 `parse_raw_investor_key` 를 그대로 쓴다 —
# 경로 규약(source·market·ingest_date·run_id)이 같고, 그 함수는 `key=value` 세그먼트만 취하므로
# dataset 에 무관하다(복제하면 두 벌이 따로 낡는다).
_RAW_INVESTOR_ESTIMATE_MARKER = "/dataset=investor_flow_intraday/"


def is_raw_investor_estimate_key(key: str) -> bool:
    """raw investor_flow_intraday 데이터 파일 키인지. (part-*.ndjson 만, 프리픽스 디렉터리 아님.)"""
    return (
        key.startswith("raw/")
        and _RAW_INVESTOR_ESTIMATE_MARKER in key
        and key.endswith(".ndjson")
    )


def canonical_investor_flow_partition(market: str, trade_date: str) -> str:
    """canonical 투자자 수급 파티션 프리픽스 (끝 슬래시 없음).

    일봉(canonical_price_daily_partition)과 동형 — 투자자 순매수는 거래일 시계열이라 시간축이
    trade_date 이고 market_data 존에 둔다. run_id·source_vendor 파티션 없이 (market,trade_date)
    로 가른다(멱등). 정체성 키 (market,ticker,trade_date) 중 market·trade_date 가 파티션,
    ticker 가 파티션 내 행 키다. 벤더(kis)는 시장이 가르므로 컬럼(provenance)이지 파티션이 아니다.
    """
    return f"canonical/market_data/investor_flow_daily/market={market}/trade_date={trade_date}"


def canonical_investor_flow_intraday_partition(market: str, trade_date: str) -> str:
    """canonical 장중 투자자 추정 파티션 프리픽스 (끝 슬래시 없음).

    EOD(`canonical_investor_flow_partition`)와 파티션 축은 같지만(market·trade_date) **다른
    데이터셋**이다 — 값이 가집계 추정이라 확정치와 섞이면 소비자가 어느 쪽을 본 것인지 사후에
    구분할 수 없다(`.dev/etf-flow-collection-plan.md` §2.5).

    정체성 키는 **한 축 많다**: (market, ticker, trade_date, asof_slot). 하루 4~5 슬롯이 한
    종목·한 날짜에 공존하므로 파티션 안의 행 키가 ticker 단독이 아니라 (ticker, asof_slot) 이다.
    """
    return f"canonical/market_data/investor_flow_intraday/market={market}/trade_date={trade_date}"


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

    🔴 **이 파티션의 etf_id 집합은 분석 유니버스가 아니다.** 읽는 쪽은 유니버스 뿌리로
    한 번 걸러라 — `ingest_price_raw._krx_expected_etfs(settings)` 가 그 정본이고,
    data-pipeline 의 소비자는 전부 그걸 통해 읽는다(`expected_etfs` 인자 — 다만 **어느
    파티션을 보는지는 소비자마다 다르다**: `_kr_holdings_universe` 는 ETF 별 최신 스냅샷의
    소급 합집합, `_holdings_name_index` 는 `max(as_of)` 하나다. 뿌리 ETF 수집이 부분 실패한
    날은 그래서 두 집합이 갈린다).
    ⚠️ analysis-engine 은 아직 아니다 — `adapters/lake.py` 는 요청 ETF 단건 스코프라
    오늘은 무해하고, `statics/duck.py` 는 프리픽스 전체를 뷰로 등록한다(뿌리 검사 없음).

    두 가지가 여기 섞여 들어온다. (1) **폐지·제외된 ETF** 의 옛 행 — 파티션은 지워지지
    않으므로 config 에서 뺀 ETF 가 소급 상한만큼 살아 있다. (2) **참조 계열 ETF** — 층
    분해의 겹침 게이트가 명부를 필요로 해서 받는 계열로, 판정·적재·탐지 축이 아니다.
    1분 레인은 이미 축을 갈라 뒀고(`[minute_universe].sector_etf_ids`, ALPHA-842) 일배치
    수집 축의 편입은 ALPHA-855 다 — 그때 이 파티션에 47종이 들어온다. 안 거르면 마스터에
    없는 ETF 가 매 런 `failed_records` 로 잡혀 원장이 **영구 INCOMPLETE** 가 되거나
    (`load_etf_holdings`·`load_price_triggers`), 분석 유니버스 밖 종목이 멘션 사전에
    들어간다(`normalize_news`).
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


def feature_news_assertions_minute_prefix(language: str, published_date: str) -> str:
    """위 파티션 **안**의 장중 미러 구역 (끝 슬래시 포함) — ALPHA-900.

    장중 레인이 추출 결과를 배치와 같은 날짜축에 남기는 자리다. 그래야 배치
    `tag_news` 의 재태깅 skip 이 걸리고(`_is_current` 의 지문·버전 축), `load_assertions`
    가 그 판정을 주워 `confidence` 를 DB 에 착지시킨다 — 두 소비자 모두 이 파티션만 본다.

    ⚠️ **파티션 part 파일과 한 구역에 섞지 않는 이유는 writer 수다.** 배치는 파티션을
    통째로 되쓰는 writer 하나지만(`part-00000.parquet`), 장중은 job 을 `max_concurrency`
    로 병렬 처리하고 realtime·backfill 두 서비스로 뜬다. 같은 part 파일을 read-merge-write
    하면 lost update 가 상시로 나서 미러가 절감으로 이어지지 않는다. 그래서 기사당 객체다.

    ⚠️ **누적을 그대로 두면 안 된다.** `tag_news._read_feature` 는 파티션 아래 `.parquet`
    을 전부 GET 하므로, 미러가 쌓이면 파티션마다 수천 왕복이 된다. 흡수한 미러는 그 스텝이
    지운다(`Storage.delete_keys`) — 이 구역은 **다음 배치 런까지의 임시 자리**이지
    영구 보관소가 아니다. 정본은 흡수 뒤의 part 파일이다.
    """
    return f"{feature_news_assertions_partition(language, published_date)}/minute/"


def feature_news_assertions_minute_key(
    language: str, published_date: str, article_id: str, input_fingerprint: str
) -> str:
    """장중 미러 1건의 키 — `(기사, 그 기사의 어느 텍스트)` 가 객체 하나다 (ALPHA-900).

    **같은 텍스트의 재추출은 자기 자신을 덮는다**(자가 dedup) — 재시도·redrive 는 본문이
    같아 지문도 같으므로 조각이 안 쌓인다. job 축 결과 키(`news_extraction_result_key`)와
    달리 attempt·generation 이 없는 것은 그래서다: 저기는 "어느 시도가 무엇을 냈나"의
    기록이고, 여기는 "이 텍스트에 대한 판정" 하나다.

    ⚠️ **지문이 키에 있는 이유는 배치의 압축과 겹치는 창 때문이다.** `article_id` 만
    키로 쓰면, 배치가 미러를 읽어 part 파일에 병합하고 **지우는 사이에** 장중이 같은
    기사의 정정 판정을 같은 키로 덮어쓸 수 있다 — 배치는 자기가 읽은 것을 지운다고
    믿지만 실제로 지워지는 것은 **아직 아무도 안 읽은 최신 판정**이다. 지문이 갈리면
    키도 갈려 그 판정이 살아남고, 병합 승자 규칙(`tag_news._merge_by_article`)이
    다음 런에 최신을 고른다.
    """
    return (
        f"{feature_news_assertions_minute_prefix(language, published_date)}"
        f"{article_id}.{input_fingerprint}.parquet"
    )


def news_extraction_result_key(
    job_id: str, redrive_generation: int, attempt: int
) -> str:
    """1분 뉴스 추출 job 한 번의 실행 결과 키 (ALPHA-689).

    존은 배치 태깅과 같은 **feature** 다 — 값이 LLM 추론 결과라는 성질이 같다
    (`feature_news_assertions_partition` 주석의 근거가 그대로 적용된다). 파티션 축만
    다르다: 배치는 날짜창을 프루닝해 훑지만, Consumer 는 job 하나를 처리하고 그 결과를
    DB `news_extraction_job.result_checksum` 으로 지목한다 — 즉 **DB 가 색인**이라
    키에 날짜 축이 필요 없다.

    ⚠️ 축이 (job_id, redrive_generation, attempt) **셋 다**인 이유: LLM 출력은
    비결정적이라 같은 job 의 재시도가 다른 바이트를 낸다. attempt 를 빼면 "PUT 성공 →
    DB 기록 전 사망 → 재시도" 가 같은 key 에 다른 바이트를 써서 `put_immutable` 이
    거부하고, 그 job 은 영구히 못 끝난다. redrive 는 attempt_count 를 0 으로 되돌리므로
    generation 을 빼면 새 세대의 첫 시도가 같은 함정을 그대로 밟는다.

    실패한 시도의 결과도 남는다(끝난 job 이 지목하지 않는 키) — 그 정리는 orphan 검출·
    EOD QC 소관이다(PR 8). 지우는 쪽을 여기에 두면 근거가 사라진다.
    """
    return (
        f"feature/news_extraction/job={job_id}"
        f"/generation={redrive_generation}/attempt={attempt}/result.json"
    )


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


def latest_good_pointer_key(dataset: str, market: str) -> str:
    """latest-good 파티션 포인터 alias — 데이터셋·시장마다 하나인 mutable CAS key."""
    return (
        "operations_archive/latest_good_partition_pointers/"
        f"dataset={dataset}/market={market}/pointer.json"
    )


def latest_good_artifact_key(
    dataset: str, market: str, as_of_date: str, run_id: str,
) -> str:
    """latest-good가 가리키는 run-scoped 불변 Parquet snapshot key."""
    return (
        "operations_archive/latest_good_partition_artifacts/"
        f"dataset={dataset}/market={market}/as_of_date={as_of_date}"
        f"/run_id={run_id}/part-00000.parquet"
    )


def canonical_run_manifest_key(dataset: str, run_id: str) -> str:
    """정제 실행이 실제로 갱신한 canonical 파티션 manifest의 직접 조회 키.

    quality log는 UTC checked_date 축이라 run_id만 아는 하류가 찾으려면 날짜 전체를 LIST해야 한다.
    실행 계보 소비자는 이 키를 GET해 파티션 수와 무관한 비용으로 정확한 입력 범위를 얻는다.
    """
    return (
        f"operations_archive/canonical_run_manifests/dataset={dataset}"
        f"/run_id={run_id}/manifest.json"
    )


def canonical_run_partition_key(dataset: str, run_id: str, report_date: str) -> str:
    """manifest 소비용 run-scoped immutable canonical parquet 키."""
    return (
        f"operations_archive/canonical_run_artifacts/dataset={dataset}"
        f"/run_id={run_id}/report_date={report_date}/part-00000.parquet"
    )


def raw_run_manifest_key(dataset: str, run_id: str) -> str:
    """배치 raw producer가 확정한 실행별 직접 입력 key manifest."""
    return (
        f"operations_archive/raw_run_manifests/dataset={dataset}"
        f"/run_id={run_id}/manifest.json"
    )


def feature_run_manifest_key(dataset: str, run_id: str) -> str:
    """feature producer가 현재 실행에서 바꾼 직접 파티션·논리 ID manifest 키."""
    return (
        f"operations_archive/feature_run_manifests/dataset={dataset}"
        f"/run_id={run_id}/manifest.json"
    )


# manifest 계보의 루트 — 소비 마커 탐색이 이 프리픽스를 LIST 한다(아래 unconsumed_run_ids).
_MANIFEST_ROOT = {
    "canonical": "operations_archive/canonical_run_manifests",
    "feature": "operations_archive/feature_run_manifests",
}


def run_manifest_consumed_key(kind: str, dataset: str, run_id: str, consumer: str) -> str:
    """그 manifest 를 **누가** 소비 완료했는지 남기는 마커 키 (ALPHA-1052).

    manifest 는 생산자가 쓰고 소비자가 읽는데, 소비가 성공했다는 사실은 지금까지 어디에도
    안 남았다 — 소비 스텝이 실패하면 그 범위는 다음 런 manifest 에 안 들어오고(이미 생산돼
    `changed` 가 아니다) 영구 유실됐다. 마커가 있으면 "생산됐으나 미소비"가 LIST 하나로
    계산돼 다음 런이 이어 싣는다.

    ⚠️ **소비자별로 판다.** 한 manifest 에 소비자가 둘 이상인 계보가 있다 — `normalize_news`
    의 canonical manifest 는 `tag_news` 와 `load_documents` 가 각각 읽는다. 한 마커를 공유하면
    먼저 끝난 쪽이 상대의 미소비를 지운다.

    ⚠️ **마커의 단위는 run_id 다 — manifest 의 세대가 아니다.** 생산자가 같은 run_id 로
    manifest 를 다시 써 범위를 넓히면(`tag_news._existing_feature_partitions` 가 그 재시도를
    지원한다) 자동 회수는 그 2세대를 미소비로 못 본다. 자동 경로에서는 도달하지 않는다:
    뉴스 SFN 에 `Retry` 가 없고(2026-08-28 정의 확인) run_id 는 실행마다 새로 나므로, 한
    소비 성공과 다음 생산 사이가 벌어지지 않는다. 남는 것은 **사람이 같은 run_id 로
    tag-news 를 다시 돌린 경우**뿐이고, 그때는 같은 손이 `--input-run-id` 로 이 스텝도
    재실행한다(그 경로는 마커와 무관하게 자기 manifest 를 항상 읽는다). 세대를 보려면
    manifest 지문을 마커에 넣고 대조해야 하는데, 그러면 LIST 한 번이던 탐색이 run 마다
    GET 둘로 바뀐다 — 도달 불가한 경로에 그 비용을 내지 않는다.

    ⚠️ 마커는 **소비자가 자기 성공을 증명한 뒤에만** 쓴다. 원장(ops_task_attempt)으로 대신할
    수 없다 — 원장 기록은 본 작업을 막지 않으려 예외를 삼키므로(ledger.py 스펙 §3.4) 기록이
    없는 것과 실패한 것이 구분되지 않는다. 그 축으로 회수를 판정하면 **관대한 방향으로**
    틀린다(누락된 attempt = 회수 없음 = 조용한 유실).
    """
    return f"{_MANIFEST_ROOT[kind]}/dataset={dataset}/run_id={run_id}/consumed/{consumer}.json"


def run_manifest_skipped_key(kind: str, dataset: str, run_id: str, consumer: str) -> str:
    """그 manifest 를 이 소비자가 **싣지 않기로 끝냈다**는 종결 마커 키 (ALPHA-1052).

    소비 마커와 축이 다르다 — 저건 "실었다", 이건 "안 싣고 닫았다"다. 한 이름으로 합치면
    로그·조회가 둘을 구분 못 해 "회수됐다"가 거짓이 된다.

    이게 없으면 **탐색이 수렴하지 않는다**: 되돌아보기 창 밖으로 밀린 manifest 는 영원히
    소비되지 않으므로 매 런이 그걸 다시 GET 하고 다시 센다. 조건이 단조(시간은 한 방향)라
    한 번 창 밖이면 영원히 창 밖이니, 그 판정을 한 번만 하고 닫는 것이 맞다.

    ⚠️ **미완료 manifest(`*_written=false`)에는 쓰지 않는다.** 생산자가 같은 run_id 로
    끝맺을 수 있어 조건이 단조가 아니다 — 닫아 버리면 완성된 범위를 영영 안 싣는다.
    그건 창 밖으로 밀릴 때 이 마커를 받는다.
    """
    return f"{_MANIFEST_ROOT[kind]}/dataset={dataset}/run_id={run_id}/skipped/{consumer}.json"


def unconsumed_run_ids(
    storage, kind: str, dataset: str, consumer: str, *, exclude: str | None = None
) -> list[str]:
    """`manifest.json` 은 있는데 이 소비자의 consumed 마커가 없는 run_id (사전순, ALPHA-1052).

    LIST 는 이 manifest 계보 프리픽스 **하나**다 — 파티션 데이터가 아니라 실행 메타라
    ALPHA-1033 이 막은 데이터 풀스캔과 다른 축이다. 키 수는 런당 2~3개로 자라므로(뉴스 기준
    연 ~1,500) 페이지 수는 년당 1~2다. 반환은 **정렬**이라 호출자가 같은 순서로 처리한다.

    "미소비"는 **두 종결 마커가 모두 없는 것**이다 — `consumed`(실었다)와 `skipped`(안 싣고
    닫았다). skipped 가 없으면 영원히 못 싣는 manifest 를 매 런 다시 GET 해 탐색이 수렴하지
    않는다.

    ⚠️ `feature_written`·`canonical_written` 이 false 인 반쪽 manifest 도 여기 섞여 나온다 —
    생산자가 시작 시 false 로 쓰고 끝에 true 로 덮기 때문이다. 생산자가 죽으면 그 false
    manifest 가 영영 남으므로, **호출자가 GET 해서 완료 여부를 보고 걸러야 한다**(여기서
    거르려면 run 마다 GET 이 필요해 LIST 한 번의 이점이 사라진다).
    """
    prefix = f"{_MANIFEST_ROOT[kind]}/dataset={dataset}/"
    produced: set[str] = set()
    consumed: set[str] = set()
    # 종결은 둘이다 — 실었거나(consumed), 안 싣고 닫았거나(skipped). 둘 다 빼야 탐색이 수렴한다.
    marker_suffixes = (f"/consumed/{consumer}.json", f"/skipped/{consumer}.json")
    for key in storage.list_keys(prefix):
        rest = key[len(prefix):]
        if not rest.startswith("run_id="):
            continue
        run_id, _, tail = rest[len("run_id="):].partition("/")
        if not run_id:
            continue
        if tail == "manifest.json":
            produced.add(run_id)
        elif key.endswith(marker_suffixes):
            consumed.add(run_id)
    return sorted(produced - consumed - ({exclude} if exclude else set()))


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


def canonical_price_minute_artifact_key(
    market: str, session_date: str, window_start_hhmm: str, generation: int
) -> str:
    """1분 가격 window artifact 의 **결정적·불변** 키 (v0.7 9절, ALPHA-665·705).

    이 artifact 가 분봉 canonical 의 **단일 정본**이다(ALPHA-701 — DB 에 넣지 않는다).
    그래서 존이 raw 가 아니라 canonical 이고, 벤더(source)는 파티션 축이 아니라
    **레코드 컬럼**이다 — canonical 소비자는 벤더로 갈라 읽지 않는다(레포 키 규약).

    다른 파티션과 달리 run_id 가 없다 — 같은 window 재실행은 같은 key 에 같은
    바이트를 다시 PUT 하는 no-op 이어야 "S3 PUT 후 DB commit 전 종료 → artifact
    재사용" 복구가 성립한다. 불변성은 generation 이 진다: correction 은 새 generation
    → 새 key 라 기존 artifact 를 덮지 않는다.
    """
    return (
        f"{canonical_price_minute_prefix(market, session_date)}window={window_start_hhmm}"
        f"/generation={generation}/bars.ndjson"
    )


def canonical_price_minute_prefix(market: str, session_date: str) -> str:
    """그 (market, session_date) 분봉 canonical 이 사는 프리픽스 — orphan 스캔 축.

    artifact key 와 같은 조립을 두 곳에 두면 한쪽만 옮겨져 스캐너가 없는 prefix 를
    훑고 빈 목록을 clean 으로 확정한다(경로 규약 SSOT 는 이 모듈이다).
    """
    return (
        f"canonical/market_data/price_minute/market={market}"
        f"/session_date={session_date}/"
    )


def canonical_etf_inav_minute_artifact_key(
    market: str, session_date: str, window_start_hhmm: str, generation: int
) -> str:
    """장중 iNAV window artifact 의 **결정적·불변** 키 (ALPHA-851).

    `canonical_price_minute_artifact_key` 와 같은 규약이다 — run_id 없음(같은 window
    재실행 = 같은 key 에 같은 바이트 PUT 하는 no-op), 불변성은 generation 이 진다,
    벤더는 파티션 축이 아니라 레코드 컬럼.

    분봉과 **dataset 을 나눈다**. 같은 (market, session_date, window) 라도 담는 것이
    다르고(캔들 OHLCV vs NAV·괴리), 무엇보다 **완전성 기대 집합이 다르다** — 구성종목에는
    NAV 가 없다. 한 artifact 에 섞으면 그 window 가 "봉은 다 왔는데 NAV 는 ETF 것만
    왔다"를 표현할 수 없어 매 window INCOMPLETE 가 된다.

    ⚠️ orphan 스캐너(`minute/commit.py`)는 아직 price prefix 를 하드코딩하고 `eod.py` 는
    비-price dataset 을 정직하게 건너뛴다 — **이 prefix 는 아직 아무도 훑지 않는다**.
    규약이 같다고 스캐너가 따라오지 않는다.
    """
    return (
        f"{canonical_etf_inav_minute_prefix(market, session_date)}"
        f"window={window_start_hhmm}/generation={generation}/inav.ndjson"
    )


def canonical_etf_inav_minute_prefix(market: str, session_date: str) -> str:
    """그 (market, session_date) iNAV canonical 이 사는 프리픽스."""
    return (
        f"canonical/market_data/etf_inav_minute/market={market}"
        f"/session_date={session_date}/"
    )


def canonical_sector_index_minute_artifact_key(
    market: str, session_date: str, window_start_hhmm: str, generation: int
) -> str:
    """업종지수 1분봉 window artifact 의 **결정적·불변** 키 (ALPHA-887).

    분봉·iNAV 와 같은 규약이다 — run_id 없음, 불변성은 generation 이 진다, 벤더는 파티션
    축이 아니라 레코드 컬럼.

    `price_minute` 와 **dataset 을 나눈다**. 담는 것이 종목 봉이 아니라 업종지수 봉이고,
    무엇보다 **완전성 기대 집합이 다르다** — 지수 45종은 universe 에 없다(ETF 명부에도
    구성종목에도 없다). 한 artifact 에 섞으면 그 window 가 "종목은 다 왔는데 지수는 안
    왔다"를 표현할 수 없어 매 window INCOMPLETE 가 된다(iNAV 와 같은 논거).

    ⚠️ iNAV 와 마찬가지로 **이 prefix 는 아직 아무도 훑지 않는다** — orphan 스캐너
    (`minute/commit.py`)는 price prefix 를 하드코딩하고 `eod.py` 는 비-price dataset 을
    정직하게 건너뛴다. 규약이 같다고 스캐너가 따라오지 않는다.
    """
    return (
        f"{canonical_sector_index_minute_prefix(market, session_date)}"
        f"window={window_start_hhmm}/generation={generation}/bars.ndjson"
    )


def canonical_sector_index_minute_prefix(market: str, session_date: str) -> str:
    """그 (market, session_date) 업종지수 분봉 canonical 이 사는 프리픽스."""
    return (
        f"canonical/market_data/sector_index_minute/market={market}"
        f"/session_date={session_date}/"
    )


def canonical_intraday_5m_prefix(market: str, trade_date: str) -> str:
    """그 거래일 5분봉 파티션이 사는 프리픽스 — **구멍 판정의 스캔 축** (ALPHA-839).

    키 조립을 두 곳에 두면 한쪽만 옮겨져 스캐너가 없는 prefix 를 훑고 빈 목록을
    "구멍 없음"으로 확정한다(`canonical_price_minute_prefix` 와 같은 축).

    ⚠️ 파티션에 파일이 `part-0.parquet` 하나뿐이라고 가정하면 안 된다 — 과거 백필이
    같은 파티션에 `part-<vendor>-backfill.parquet` 로 벤더마다 따로 쓰고(ALPHA-828
    토스 · ALPHA-856 KIS), 소비자는
    파티션을 `*.parquet` 글롭으로 읽는다. `part-0` 부재를 구멍으로 읽으면 다른
    파일명이 채운 거래일(실측 2026-08-03)을 영영 결손으로 보고한다.
    """
    return f"canonical/market_data/intraday_5m/market={market}/trade_date={trade_date}/"


def canonical_intraday_5m_key(market: str, trade_date: str) -> str:
    """5분봉 canonical(dataset=intraday_5m)의 **가격** 거래일 파일 키 (ALPHA-750).

    분석엔진(analysis-engine)이 소비하는 기존 데이터셋이다 — FMP 백필
    (source_vendor='fmp', ~2026-07-31)이 이미 이 형상으로 살고, 그 뒤를 벤더 백필과
    1분 롤업이 나눠 쓴다. 소유 판정은 **축이 둘**이다 — ①백필이냐 롤업이냐는
    `minute.rollup.writer_owns()`(기본은 `WRITER_SINCE` 경계 + 경계 앞 예외 집합)가 정하고,
    백필과 롤업이 **파티션을 통째로 나눠 소유한다** ②롤업 소유일 안에서 **어느 세션이냐**는
    `minute.rollup.OWNER_SOURCE_GROUP` 이 정한다(ALPHA-847 — 이 키에 source_group 축이
    없는데 1분 세션은 source_group 으로 갈려서, 그날 세션이 둘이면 얇은 쪽의 재집계가 두꺼운
    산출을 덮는다). 날짜를 여기 박아 두면 경계가 움직일 때 이 문장만 거짓이 된다
    (ALPHA-836 에서 실제로 그랬다).

    한 파티션에 두 writer 의 파일이 공존할 수 있다(`part-0.parquet` + 벤더 백필 파일).
    소비자는 파티션을 `*.parquet` 글롭으로 읽으므로 **행이 서로소여야 한다** — 백필이
    쓰기 전에 `part-0` 의 티커를 빼는 것이 그 보장이고, 롤업 쪽은 자기 소유 구간에서
    낯선 파일을 만나면 산출을 멈춘다(`rollup._rollup_day` 의 foreign 가드).

    ⚠️ 롤업 자신도 이 파티션에 **파일을 둘** 쓴다(ALPHA-941 — 업종지수는
    `canonical_intraday_5m_sector_key`). "롤업 파일 = `part-0` 하나"로 읽으면 그 가드가
    자기 파일을 남의 것으로 본다 — 소유 목록의 정본은 `canonical_intraday_5m_writer_keys`
    하나다.

    스키마(dev S3 2026-07-31 part-0.parquet 실측 — 이 컬럼·타입 그대로, 여분 컬럼
    없음: 소비자 스키마 검증과의 충돌을 피하는 게 우선이라 bars_count 류는 넣지
    않고 결손은 로그로 남긴다):
    - ticker: string (bare 6자리, 예 "004990")
    - source_symbol: string (벤더 심볼 — fmp 는 "004990.KS", 1분 롤업은 bare 그대로)
    - ts: timestamp[us] **naive KST** — **구간 시작** 라벨(09:00 행 = 09:00~09:04)
    - open/high/low/close: double
    - volume: int64
    - source_vendor: string (원본·파생·레인을 가르는 필터 축 — `fmp` 원본 / 1분 롤업의
      `1m_rollup`(가격) · `1m_rollup_sector`(업종지수). ⚠️ 이 파티션은 파일 합집합으로
      읽히고 **두 심볼 어휘가 공존**하므로, 파티션 전체를 훑는 소비자는 이 축으로 갈라야
      한다 — `rollup.SOURCE_VENDOR*` 가 정본)
    - available_at: timestamp[us] naive KST = ts + 5분(구간 끝)

    ⚠️ **"거래일당 1파일"이 아니다**(2026-08-07 정정). 이 키는 1분 롤업 writer 의 파일
    이고, 같은 파티션에 다른 writer 가 다른 파일명으로 쓴다(과거 백필이 벤더마다
    `part-<vendor>-backfill.parquet`, ALPHA-828 토스·ALPHA-856 KIS). 소비자는 파티션을 `*.parquet` 글롭으로
    읽으므로 **파티션 = 여러 파일의 합집합**이고, 겹치는 (ticker, ts) 는 두 번 세어진다
    — 비겹침을 보장하는 주체는 각 writer 다(`rollup.writer_owns()`, 롤업의 타 writer 가드).

    generation 축 없음 — 파생물이라 원장이 확정한 1분
    세대에서 언제든 같은 규칙으로 재유도되고, 5분 버킷이 닫힐 때마다 그날 전체를
    재집계해 통째로 덮어쓴다(결정적·멱등 — 불변 계약을 걸면 장중 갱신·정정 반영이
    영구 차단된다).
    """
    return f"{canonical_intraday_5m_prefix(market, trade_date)}part-0.parquet"


def canonical_intraday_5m_sector_key(market: str, trade_date: str) -> str:
    """업종지수 45종 5분봉의 거래일 파일 키 (ALPHA-941).

    가격과 **같은 파티션에 다른 파일로** 산다. 새 dataset 을 파지 않는 이유는 소비자다 —
    분석엔진은 이 파티션을 파일명 없이 통째로 건다(`statics/duck.py` 의 `s3_intraday_5m`
    hive 등록 + `FROM s3_intraday_5m b WHERE b.market='KR'`). 여기 놓으면 봉 읽는 경로가
    **무변경**이고, 새 dataset 을 파면 그 질의를 UNION 으로 고쳐야 한다.

    파티션을 나눠 쓰는 것이 안전한 근거는 **행이 서로소**라는 것이다 — 이 파일의 `ticker`
    는 KRX 업종코드(`1005`·`2063` 4자리)고 `part-0` 은 종목코드(6자리)라 교집합이 공집합
    이다. 소비자가 `*.parquet` 글롭으로 읽어도 겹치는 (ticker, ts) 가 없어 이중계상이
    구조적으로 불가능하다(벤더 백필 파일과 다른 점이 정확히 이것이다 — 그쪽은 같은
    종목코드를 담아서 나란히 놓이면 두 번 세어진다).

    스키마·ts 시맨틱은 `canonical_intraday_5m_key` 와 **같다** — 같은 뷰로 읽히므로
    갈리면 안 된다. 값의 성질만 다르다: 지수는 자기가 체결되지 않아 `volume=0` 이
    정상이고(2026-08-10 실측 6.2%), 그것을 결손으로 접는 소비자가 있으면 그건 소비자
    쪽 결함이다.

    🔴 `source_vendor` 는 `1m_rollup_sector` 로 **가격과 갈라 둔다**. 파티션이 파일
    합집합으로 읽히는데 여기 `source_symbol` 은 종목코드가 아니라 KRX 업종코드라,
    파티션 전체를 훑는 소비자에게 두 어휘를 가를 축이 필요하다 — 실제로
    `analysis-engine/collect/intraday.py:load_symbols` 가 `DISTINCT source_symbol` 로
    FMP 수집 유니버스를 잇고 그 축으로 이 45개를 거른다.
    """
    return f"{canonical_intraday_5m_prefix(market, trade_date)}part-sector-index.parquet"


def canonical_intraday_5m_writer_keys(market: str, trade_date: str) -> frozenset[str]:
    """1분 롤업 writer 가 이 파티션에서 **소유하는 파일 전부** — foreign 가드의 판정 축.

    가드가 `k != part-0` 로 물으면 롤업 자신의 두 번째 파일을 남의 것으로 읽고 **가격
    롤업이 그날부터 통째로 멈춘다**(ALPHA-941). 그렇다고 가드를 빼면 벤더 백필
    (`part-<vendor>-backfill.parquet`)과 나란히 놓여 겹치는 행이 두 번 세어지는 원래
    사고가 되살아난다. 그래서 **소유 목록**으로 좁힌다 — 여기 없는 파일은 여전히 거부다.

    ⚠️ 판정하는 자리가 둘이다(`rollup._rollup_day` 의 산출 가드 · `rollup.
    unfilled_settled_days` 의 구멍 판정). 두 벌로 적으면 한쪽만 늘어 산출은 되는데
    감시는 그날을 영구 `contested` 로 보고하는(또는 그 반대) 상태가 된다.

    ⚠️ 대조는 **`.parquet` 키에만** 건다(`is_foreign_5m_key`). `S3Storage.list_keys` 는
    프리픽스 아래 **모든** 키를 주는데 거기엔 콘솔·도구가 만드는 0바이트 디렉터리 마커
    (`…/trade_date=2026-08-10/`)가 섞일 수 있다. 그건 소비자의 `*.parquet` 글롭에 안
    걸려 이중계상을 못 만드는데, 낯선 파일로 세면 **두 레인이 그날 영구 정지**한다.
    가드의 근거가 "소비자가 글롭으로 읽어 겹치는 행이 두 번 세어진다"이므로 판정 대상도
    그 글롭이 읽는 것이어야 한다.
    """
    return frozenset({
        canonical_intraday_5m_key(market, trade_date),
        canonical_intraday_5m_sector_key(market, trade_date),
    })


def is_foreign_5m_key(key: str, market: str, trade_date: str) -> bool:
    """이 키가 5분 파티션의 **낯선 writer 파일**인가 — 산출 가드·구멍 판정의 공용 판정.

    소비자가 읽는 것(`*.parquet`)만 대상이다. 판정을 한 함수로 두는 이유는
    `canonical_intraday_5m_writer_keys` 도크스트링의 "자리가 둘" 과 같다.
    """
    return key.endswith(".parquet") and key not in canonical_intraday_5m_writer_keys(
        market, trade_date)


def raw_news_minute_page_key(
    source: str, market: str, session_date: str, window_start_hhmm: str,
    attempt: int, page: int,
) -> str:
    """1분 뉴스 poll 이 받은 **벤더 page 원본**의 키 (ALPHA-669, v0.7 8절).

    가격 artifact 와 달리 축이 generation 이 아니라 **attempt** 다 — 뉴스 feed 는
    라이브라서 같은 window 를 재poll 하면 다른 rows 가 온다. generation 축을 쓰면
    재시도가 같은 key 에 다른 바이트를 PUT 해 ArtifactImmutabilityError 로 그 window
    가 영구히 막힌다. attempt 는 window claim 이 DB 에서 증가시키는 값이라 프로세스
    재시작 뒤에도 단조롭다.
    """
    return (
        f"raw/source={source}/dataset=news_minute/market={market}"
        f"/session_date={session_date}/window={window_start_hhmm}"
        f"/attempt={attempt}/page={page}.ndjson"
    )


def minute_poll_manifest_key(
    dataset: str, source: str, market: str, session_date: str,
    window_start_hhmm: str, attempt: int,
) -> str:
    """1분 레인 poll 판정 기록의 키 (ALPHA-669 뉴스 · ALPHA-875 공시).

    **window 단위 산출물이 없는 dataset** 이 쓴다 — 라이브 소스를 매 분 폴링해 관측을 남기는
    쪽(뉴스·공시)이고, `dataset=` 으로 갈리므로 소비자가 늘어도 이 함수 하나다.

    가격 manifest 와 달리 축이 generation 이 아니라 **attempt** 다 — 라이브 소스라
    같은 window 의 재poll 은 다른 관측을 낳는데, DB 가 확정하는 generation 은 커밋이
    성공해야 오른다. commit 실패 뒤 재poll 이 같은 generation key 에 다른 바이트를
    PUT 하면 그 window 가 불변 위반으로 영구히 막힌다(raw page key 와 같은 이유).
    """
    return (
        f"operations_archive/minute_manifests/dataset={dataset}/source={source}"
        f"/market={market}/session_date={session_date}/window={window_start_hhmm}"
        f"/attempt={attempt}/poll.json"
    )


def minute_window_manifest_key(
    dataset: str, source: str, market: str, session_date: str,
    window_start_hhmm: str, generation: int,
) -> str:
    """window unit manifest(received/no_trade/missing/invalid)의 결정적 키.

    artifact 와 같은 파티션 축이되 존은 operations_archive — manifest 는 canonical
    데이터가 아니라 수집 판정 기록이다(collection_logs 와 같은 결). source 가 키에
    남는 이유도 그것이다: 판정 기록은 수집 주체 축으로 갈라 보관한다(ALPHA-705 —
    canonical artifact 쪽은 source 를 컬럼으로 내렸다).
    """
    return (
        f"operations_archive/minute_manifests/dataset={dataset}/source={source}"
        f"/market={market}/session_date={session_date}/window={window_start_hhmm}"
        f"/generation={generation}/manifest.json"
    )


# ── 백엔드 ──────────────────────────────────────────────
class Storage(Protocol):
    """레이크 키-바이트 저장 계약. 키는 위 빌더가 만든 상대경로."""

    def put_bytes(self, key: str, data: bytes) -> None:
        """key 에 바이트를 쓴다 — 덮어쓰기 방지(불변 계약)는 `put_immutable` 몫이다."""
        ...

    def get_bytes(self, key: str) -> bytes:
        """key 의 바이트 — 없으면 백엔드별 not-found 예외."""
        ...

    def get_bytes_with_version(self, key: str) -> tuple[bytes | None, str | None]:
        """CAS 병합용 현재 바이트와 backend version. 결손은 (None, None)."""
        ...

    def put_bytes_if_version(self, key: str, data: bytes, version: str | None) -> bool:
        """현재 version이 같을 때만 쓴다. 경합이면 False."""
        ...

    def list_keys(self, prefix: str) -> list[str]:
        """문자열 prefix 로 시작하는 키 전부(정렬)."""
        ...

    # 멱등 삭제 — 없는 키는 성공이다(같은 압축이 두 번 돌아도 두 번째가 안 죽는다).
    #
    # ⚠️ **재생성 가능한 중간 산출물의 압축용이다.** raw 존과 `put_immutable` 이 지키는
    # 불변 계약에는 쓰지 마라 — 거기 바이트는 판정의 근거라 지우면 복원할 수 없다.
    # 호출부는 `steps/tag_news` 가 흡수를 끝낸 장중 미러 조각(ALPHA-900)과
    # `steps/normalize_etf` 가 단일 part로 병합한 뒤 남은 같은 파티션의 직접 자식
    # part 파일(ALPHA-1017)뿐이다.
    #
    # ⚠️ **위 계약은 산문이 아니라 IAM 이 진다** — 태스크 롤의 `s3:DeleteObject` 는
    # 두 객체 형태로 좁혀져 있다(`modules/data-pipeline/iam.tf`). 다른 존·다른
    # 프리픽스를 지우려 부르면 여기서가 아니라 런타임 AccessDenied 로 죽는다.
    def delete_keys(self, keys: list[str]) -> None:
        """멱등 일괄 삭제 — 용도 제한·IAM 계약은 위 주석 참조."""
        ...


class LocalStorage:
    """로컬 파일 스텁 — 키를 루트 아래 동일 상대경로 파일로 저장한다."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self._cas_lock = threading.Lock()

    def _path(self, key: str) -> Path:
        return self.root / key

    def put_bytes(self, key: str, data: bytes) -> None:
        """부모 디렉터리를 만들고 파일로 쓴다."""
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def get_bytes(self, key: str) -> bytes:
        """파일 바이트 — 없으면 FileNotFoundError."""
        return self._path(key).read_bytes()

    def get_bytes_with_version(self, key: str) -> tuple[bytes | None, str | None]:
        """로컬 CAS snapshot — 같은 인스턴스의 조건부 write와 짝이다."""
        with self._cas_lock:
            path = self._path(key)
            if not path.exists():
                return None, None
            data = path.read_bytes()
            return data, hashlib.sha256(data).hexdigest()

    def put_bytes_if_version(self, key: str, data: bytes, version: str | None) -> bool:
        """테스트·로컬 실행용 process-local CAS."""
        with self._cas_lock:
            path = self._path(key)
            current = path.read_bytes() if path.exists() else None
            current_version = hashlib.sha256(current).hexdigest() if current is not None else None
            if current_version != version:
                return False
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            return True

    def list_keys(self, prefix: str) -> list[str]:
        """S3 와 같은 문자열 prefix 매칭 — forward-slash 키, 정렬(아래 주석)."""
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

    def delete_keys(self, keys: list[str]) -> None:
        """멱등 삭제(missing_ok) — 빈 디렉터리는 남긴다(아래 주석)."""
        # missing_ok: 없는 키를 지우는 건 성공이다(프로토콜 주석) — 빈 디렉터리는
        # 남겨 둔다. S3 에는 디렉터리가 없어, 지우면 두 백엔드의 list_keys 결과가
        # 갈릴 일이 애초에 없는 쪽으로 맞춘다.
        for key in keys:
            self._path(key).unlink(missing_ok=True)


class S3Storage:
    """S3 백엔드. boto3 는 지연 import — 단위테스트는 boto3 없이 모듈을 import 한다."""

    def __init__(self, bucket: str):
        self.bucket = bucket
        self._client = None

    @property
    def client(self):  # pragma: no cover - 통합(실 S3)
        """지연 생성 S3 client."""
        if self._client is None:
            import boto3

            self._client = boto3.client("s3")
        return self._client

    def put_bytes(self, key: str, data: bytes) -> None:  # pragma: no cover - 통합
        """put_object 로 쓴다."""
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data)

    def get_bytes(self, key: str) -> bytes:  # pragma: no cover - 통합
        """get_object 바이트 — 없으면 ClientError(NoSuchKey)."""
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def get_bytes_with_version(self, key: str) -> tuple[bytes | None, str | None]:  # pragma: no cover - 통합
        """S3 객체 바이트와 ETag — 조건부 PUT의 compare token."""
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
        except Exception as exc:
            error = getattr(exc, "response", {}).get("Error", {})
            if error.get("Code") in ("NoSuchKey", "404", "NotFound"):
                return None, None
            raise
        return response["Body"].read(), response["ETag"]

    def put_bytes_if_version(
        self, key: str, data: bytes, version: str | None,
    ) -> bool:  # pragma: no cover - 통합
        """S3 If-Match/If-None-Match 조건부 PUT. 선행 writer가 있으면 False."""
        condition = {"IfNoneMatch": "*"} if version is None else {"IfMatch": version}
        try:
            self.client.put_object(Bucket=self.bucket, Key=key, Body=data, **condition)
        except Exception as exc:
            error = getattr(exc, "response", {}).get("Error", {})
            if error.get("Code") in ("PreconditionFailed", "ConditionalRequestConflict", "409", "412"):
                return False
            raise
        return True

    def list_keys(self, prefix: str) -> list[str]:  # pragma: no cover - 통합
        """list_objects_v2 페이지네이션 전량(정렬)."""
        paginator = self.client.get_paginator("list_objects_v2")
        keys: list[str] = []
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            keys.extend(obj["Key"] for obj in page.get("Contents", []))
        return sorted(keys)

    def delete_keys(self, keys: list[str]) -> None:  # pragma: no cover - 통합
        """1,000키 단위 delete_objects — 부분 실패 처리는 아래 주석 참조."""
        # delete_objects 는 한 번에 1,000 키가 상한이라 끊어 보낸다. S3 는 없는 키의
        # 삭제도 성공으로 답하므로 로컬 백엔드의 missing_ok 와 계약이 같다.
        #
        # ⚠️ **부분 실패는 HTTP 200 으로 온다** — 권한·객체 잠금으로 일부만 못 지워도
        # 예외가 아니라 응답 `Errors` 에 담긴다. 삼키면 호출부(`tag_news`)가 전건
        # 흡수·삭제로 계수하고 런도 성공으로 끝나는데 조각은 남는다(Rule 12). 올리면
        # 그 파티션이 exit 비0으로 드러나고, 되쓰기는 이미 끝났으므로 다음 런이
        # 같은 조각을 다시 흡수한다(멱등).
        for start in range(0, len(keys), 1000):
            chunk = keys[start:start + 1000]
            response = self.client.delete_objects(
                Bucket=self.bucket,
                Delete={"Objects": [{"Key": key} for key in chunk]},
            )
            errors = response.get("Errors") or []
            if errors:
                raise RuntimeError(
                    f"S3 삭제 부분 실패 {len(errors)}/{len(chunk)}건: "
                    f"{[(e.get('Key'), e.get('Code')) for e in errors[:5]]}"
                )


def make_storage(config: StorageConfig) -> Storage:
    """설정에 따라 백엔드를 고른다. 잘못된 조합은 StorageConfig 검증이 이미 막았다."""
    if config.backend == "s3":
        return S3Storage(bucket=config.bucket)  # type: ignore[arg-type]
    return LocalStorage(root=config.local_root)
