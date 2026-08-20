"""가격 Step1 — 원본저장 (S004. raw 존 저장 S028 을 이 스텝에 흡수).

FMP EOD 에서 종목별 일봉을 수집해, market 별로 ingest_date 파티션(수집일) ndjson
으로 raw 존에 append 하고, 실행 결과를 collection_log 로 남긴다.

raw 는 받은 행을 그대로 보존한다(전부 append) — 중복 판정·정규화는 하지 않는다.
가격은 뉴스와 달리 질의 팬아웃(같은 항목이 여러 심볼 질의에 걸림)이 없어 한 런에서
(market,ticker,trade_date) 중복이 사실상 안 생기고, 생긴다면 그건 FMP 이상치라
raw 가 있는 그대로 남겨야 한다(조용히 버리면 fail-loud 위반). 그 키로의 upsert/
dedup 은 정체성 결정이라 후속 canonical/market_data/price_daily(S006/S007) 소관이다.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from ..config import Settings
from ..lake import (
    Storage,
    canonical_etf_holdings_partition,
    canonical_price_daily_partition,
    collection_log_key,
    raw_price_partition,
)
from ..parse import krx_short_code
from ..sources import FmpPriceSource, KisDailyPriceSource, StopFetch

# 이 스텝은 벤더 무관(관례 인터페이스 duck typing)이다 — 타입힌트만 현재 가격 어댑터들의
# 합집합으로 둔다. 새 가격 벤더를 추가하면 이 합집합에 더한다(로직은 손대지 않는다).
PriceSourceAdapter = FmpPriceSource | KisDailyPriceSource

logger = logging.getLogger(__name__)


def _krx_expected_etfs(settings: Settings) -> frozenset[str] | None:
    """**유니버스 뿌리** — 수집·정제·적재가 공유하는 ETF 전체 집합 = config
    `krx_etf.source.etf_map` 의 키(ALPHA-590). canonical holdings 를 읽는 소비자는 전부
    이걸로 한 번 거른다(`canonical_etf_holdings_partition` 도크스트링).

    holdings 파티션이 아니라 config 가 정본이다 — 파티션은 수집 결과라 부분 실패로 줄 수
    있지만 ETF 목록 자체는 설정이라 절대 줄면 안 된다. `krx_etf` 섹션 자체가 없으면 정본이
    **부재**한 것이라 None — 빈 etf_map(정본이 "0종"이라 말함)과 구분한다.
    """
    return frozenset(settings.krx_etf.source.etf_map) if settings.krx_etf else None


def _kr_holdings_universe(
    storage: Storage, *, include_etf: bool = True,
    expected_etfs: frozenset[str] | None = None,
) -> list[str]:
    """canonical KR holdings **ETF 별 최신 스냅샷 합집합**의 구성종목·ETF 티커 목록(ALPHA-419).

    수집 유니버스를 holdings 에서 파생한다 — 정적 targets/symbol_map 은 유니버스와
    어긋난다(구성종목 36개 중 2개만 등재됐던 원인, 뉴스 ALPHA-416·417 과 같은 축).
    스냅샷이 없으면 빈 목록 — 기존 targets 경로만 남는다(신규 레이크에서 정상).

    티커 형태 판정은 `parse.krx_short_code` 하나로 간다(ALPHA-463) — 문자 섞인 신형
    단축코드를 빠뜨리지도, 6자 US 심볼을 KR 로 주워담지도 않는다.

    `include_etf=False` 는 **ETF 자기 티커를 뺀 구성종목만** 낸다 — 공시(ALPHA-477)처럼
    소비처가 발행회사 축인 소스용이다. ETF 는 DART 신고자가 아니라 corpCode.xml 에 아예
    없으므로, 넣으면 31 종이 매 런 '미매핑'으로 잡혀 결측이 아닌 것을 결측으로 센다.
    """
    # 구성종목과 ETF 자신(etf_id=티커) 둘 다 — ETF 종가는 트리거·설명의 대조축이다.
    fields = ("constituent_ticker", "etf_id") if include_etf else ("constituent_ticker",)
    tickers: set[str] = set()
    for row in _latest_kr_holdings_rows(storage, expected_etfs):
        for value in (row.get(f) for f in fields):
            code = krx_short_code(value)
            if code:
                tickers.add(code)
    return sorted(tickers)


def _kr_etf_ids(storage: Storage, expected_etfs: frozenset[str] | None = None) -> set[str]:
    """canonical KR holdings 최신 스냅샷의 **ETF 자기 티커** 집합(ALPHA-477).

    ETF 는 DART 신고자가 아니라 corpCode.xml 에 없다 — 공시 수집은 유니버스가 holdings 파생이든
    정적 targets(091160 이 등재돼 있다)든 출처와 무관하게 이 집합을 빼야 한다. 안 빼면 매 런
    같은 종이 미매핑으로 잡혀 `ops.failed_records>0` → 원장이 영구 INCOMPLETE 가 된다
    (`ops/wrapper.py` 의 failed_records 판정).
    """
    return {code for row in _latest_kr_holdings_rows(storage, expected_etfs)
            if (code := krx_short_code(row.get("etf_id")))}


# ETF 별 최신 스냅샷을 찾아 거슬러 올라가는 소급 상한(파티션 수). 부분 스냅샷 며칠을 메우는
# 게 목적이라 이 정도면 충분하다 — 상한을 넘겨도 못 채운 ETF 는 데이터가 그만큼 오래 없다는
# 뜻이고, 그 수집 결손 자체는 KRX 스텝이 매 런 partial/error 로 이미 드러낸다.
UNIVERSE_LOOKBACK_PARTITIONS = 10


def _is_calendar_date(value: str) -> bool:
    """실존하는 **정준형** YYYY-MM-DD 달력일인가.

    라운드트립 동치로 판정한다 — 비달력일("2026-02-30")은 파싱에서, fromisoformat 이
    3.11+ 에서 추가로 허용하는 비정준형("99991231"·"2026-W31-1")은 동치 비교에서 걸린다.
    """
    try:
        return value == date.fromisoformat(value).isoformat()
    except ValueError:
        return False


def _latest_kr_holdings_rows(
    storage: Storage, expected_etfs: frozenset[str] | None = None
) -> list[dict]:
    """canonical KR holdings 의 **ETF 별 최신 스냅샷 합집합** 행. 없으면 빈 목록 (ALPHA-590).

    `max(as_of_date)` 파티션 하나만 읽으면 부분 스냅샷(일부 ETF 수집 실패)이 곧 유니버스가
    된다 — 못 받은 ETF 의 구성종목이 다음 수집에서 조용히 빠진다(단일 ETF 소속 종목이 68%,
    KODEX 200 하나만 빠져도 전체의 53% 소실). 최신→과거로 훑으며 아직 못 본 ETF 의 행만
    채워 부분 실패가 유니버스를 축소하지 못하게 한다. `expected_etfs`(config etf_map 키 —
    파티션이 아니라 config 가 ETF 목록의 정본)가 다 차면 멈추므로, 온전한 최신 스냅샷이
    있는 평시에는 이전과 똑같이 파티션 하나만 읽는다.
    """
    marker = canonical_etf_holdings_partition("KR", "")  # ".../as_of_date="
    found = {key[len(marker):].split("/", 1)[0] for key in storage.list_keys(marker)}
    found.discard("")
    # 최신→과거 순회는 "사전순 정렬 = 시간순"에 기대므로 실존 달력일만 취한다 — 비정상 키
    # ("9999-99-99" 등 형태만 맞는 비달력일 포함)가 정렬 상위를 차지하면 소급 상한만
    # 갉아먹고 정상 최신 파티션이 스캔에서 밀린다.
    dates = {d for d in found if _is_calendar_date(d)}
    if found - dates:
        logger.warning("KR holdings 비정상 as_of_date 파티션 키 무시: %s", sorted(found - dates))
    rows: list[dict] = []
    seen: set[str] = set()
    for depth, as_of in enumerate(sorted(dates, reverse=True)[:UNIVERSE_LOOKBACK_PARTITIONS]):
        if expected_etfs is not None and expected_etfs <= seen:
            break
        partition_rows: list[dict] = []
        prefix = canonical_etf_holdings_partition("KR", as_of)
        for key in storage.list_keys(prefix + "/"):
            if key.endswith(".parquet"):
                partition_rows.extend(_read_parquet_rows(storage.get_bytes(key)))
        fresh = {etf for row in partition_rows
                 if (etf := row.get("etf_id")) and etf not in seen}
        if expected_etfs is not None:
            # ETF 목록의 정본은 config — 폐지·제외된 ETF 행이 파티션에 남아 있어도 되살리지
            # 않는다(안 거르면 유령 ETF 구성종목을 소급 상한만큼 계속 수집한다). None 은
            # 정본 부재(krx_etf 섹션 없음)라 필터하지 않는다.
            fresh &= expected_etfs
        if depth and fresh:
            # 최신 파티션이 부분 스냅샷이었다는 뜻 — 조용한 보강 금지, 로그로 드러낸다.
            logger.warning(
                "KR holdings 유니버스 보강: 최신 스냅샷에 없는 ETF %d종을 as_of=%s 에서 채움 (%s)",
                len(fresh), as_of, ",".join(sorted(fresh)),
            )
        rows.extend(row for row in partition_rows if row.get("etf_id") in fresh)
        seen |= fresh
    if expected_etfs and expected_etfs - seen:
        # 소급 상한 안에서도 못 채운 ETF — 유니버스가 그만큼 좁게 돈다는 사실을 드러낸다.
        logger.warning(
            "KR holdings 유니버스 결손: 최근 %d개 파티션에 없는 ETF %d종 (%s)",
            UNIVERSE_LOOKBACK_PARTITIONS, len(expected_etfs - seen),
            ",".join(sorted(expected_etfs - seen)),
        )
    return rows


def _read_parquet_rows(data: bytes) -> list[dict]:
    import io
    import pyarrow.parquet as pq

    return pq.read_table(io.BytesIO(data)).to_pylist()


# 유니버스 신규 편입 종목에만 붙이는 이력 창(달력일). 400일 ≈ 270거래일 (ALPHA-989).
#
# **이 숫자를 정하는 것은 소비자다.** 일봉 이력의 가장 깊은 살아 있는 소비자는
# `analysis-engine/statics/attribute.py` 의 `SIGMA_N`(60거래일 창) · `SIGMA_MIN_N`(최소 40)
# 이고, 그 위에 `paneltest` w20 · `vocab.MIN_N`(30) · `tool_stability`(2×30 사건일)가 얹힌다.
# 60거래일 창이 **롤링**이라 하한을 딱 60에 맞추면 몇 주 뒤 다시 모자란다 — 4배 여유를 둔다.
# 저쪽 상수가 움직이면 여기도 옮겨라(레포가 달라 import 로 못 묶는다 — 5분봉 백필 스크립트의
# `--min-days` 가 같은 이유로 같은 처지였다).
#
# 왜 `DEFAULT_PRICE_LOOKBACK_DAYS` 상향이 아닌가: 신규 편입 종목이 필요로 하는 건 5일이
# 아니라 수백 거래일인데, 그 창을 **전 종목**(413종)에 매일 물리면 수집량이 통째로 커진다.
NEWCOMER_LOOKBACK_DAYS = 400

# 편입 판정의 기준 파티션을 찾아 거슬러 올라가는 상한. holdings 쪽 상한과 숫자는 같지만
# **다른 사실**이라 따로 둔다(저건 부분 스냅샷 보강, 이건 못 쓰는 최신 파티션 회피).
NEWCOMER_REFERENCE_PARTITIONS = 10

# 편입 판정을 **존재가 아니라 깊이**로 하기 위한 소급 파티션 수(거래일).
#
# 존재만 보면 "canonical 에 있다"가 "이력이 있다"를 증명하지 못한다. 티커는 **불완전하게도**
# 들어올 수 있다 — ① 판정 불가 런에서 증분 5일치만 실려 들어감 ② 이력 fetch 가 유효 페이지를
# 읽다가 실패해도 어댑터가 모은 봉을 그대로 냄(`kis_price._fetch_symbol`) ③ MAX_PAGES 절단.
# 셋 다 결과가 같다: **얕게 들어와서 '이미 있음'이 되고 이력은 영영 재시도되지 않는다.**
# SFN 이 partial 런도 정제로 계속 보내므로(`statemachine.tf` NotifyRawPartial) 그 얕은 행은
# 실제로 canonical 에 들어간다.
#
# 그래서 최신 기준 파티션 **하나 더**, 이만큼 과거의 파티션도 본다. 둘 중 하나에라도 없으면
# 편입이다 — 얕게 들어온 티커는 과거 파티션에 없으므로 **성공할 때까지 자격이 유지된다**
# (새 상태 저장 없이 "keep eligible until success"). 어댑터가 최신→과거로 페이지네이션해
# 절단이 **과거 끝**을 잃는다는 점이 이 판정과 맞물린다.
#
# 값은 소비자 깊이(analysis-engine `attribute.SIGMA_N` = 60거래일)에 맞춘다. 대가: 갓 상장한
# 종목은 이 기간 동안 계속 편입으로 잡혀 하루 몇 콜을 더 쓴다 — 영구가 아니라 **자연 소멸**
# 한다(그만큼 지나면 과거 파티션에도 들어간다).
NEWCOMER_DEPTH_PARTITIONS = 60


def _newcomers(
    storage: Storage, universe: list[str], window_end: str
) -> tuple[list[str], str, str]:
    """(유니버스 신규 편입 티커, 그들에게 붙일 창 하한, **판정 포기 사유**) — canonical 일봉
    **최신 파티션에 없는** 티커 (ALPHA-989).

    세 번째 값은 판정을 못 한 사유다(정상이면 빈 문자열). 호출부가 collection_log 에 실어
    운영에서 보이게 한다 — 로그 한 줄만으로는 "판정을 건너뛴 런"과 "편입이 없던 런"이
    바깥에서 같은 모양(symbols_newcomer=0)이라 구분이 안 된다.

    유니버스는 canonical holdings 파생이라 ETF 가 추가되면 **즉시** 넓어지는데, 수집 창은
    `DEFAULT_PRICE_LOOKBACK_DAYS`(5일)다. 그래서 넓어진 유니버스는 최근 5일만 다시 긁고 그
    이전 날짜에는 새 종목이 **영영** 안 채워진다 — dev 레이크에서 절벽이 세 번 났고
    (07-13 233→341 · 07-27 343→365 · 08-06 362→413) 셋 다 같은 모양이었다. 유니버스가
    넓어진 그 런이 곧 이력을 메우게 하는 것이 이 판정의 자리다.

    판정 기준을 "최신 파티션"으로 둔 대가 둘을 명시한다:

    - ⚠️ holdings 에 남은 상장폐지·거래정지 티커는 매 런 편입으로 잡혀 하루 1콜을 쓴다
      (이력이 없으니 페이지1에서 `new==0` 으로 끝난다). 로그로 드러낸다.
    - 그날 하루만 결측이었던 종목도 잡힌다 — 낭비가 아니라 **자가복구**다(그 구멍이 메워진다).

    canonical 이 통째로 비었으면 빈 목록이다. 전 종목이 '신규'라 판정이 뜻을 잃고, 첫 런이
    곧 이력의 시작이기 때문이다 — 새 레이크의 이력은 이 경로가 아니라 명시적 `--from` 백필이
    맡는다(그게 ALPHA-989 의 나머지 절반이다).
    """
    marker = canonical_price_daily_partition("KR", "")  # ".../trade_date="
    found = {key[len(marker):].split("/", 1)[0] for key in storage.list_keys(marker)}
    found.discard("")
    # 비달력일 키가 정렬 상위를 차지하면 엉뚱한 파티션을 기준으로 삼는다 — holdings 쪽
    # `_latest_kr_holdings_rows` 가 같은 이유로 같은 판정·같은 경고를 쓴다(사실을 하나로).
    dates = {d for d in found if _is_calendar_date(d)}
    if found - dates:
        logger.warning("canonical 일봉 비정상 trade_date 파티션 키 무시: %s", sorted(found - dates))
    if not dates:
        # canonical 이 통째로 비었다 — 손상이 아니라 새 레이크다(위 도크스트링).
        return [], window_end, ""

    # 최신 하나만 보지 않고 **쓸 수 있는 파티션을 만날 때까지 물러난다.** 최신이 못 쓸 수
    # 있기 때문이다: ① `normalize_price._write_canonical` 은 병합 결과가 0행이어도
    # part-00000 을 쓴다(벤더 교차 충돌이 그 날 키를 전부 지우면 그렇다) ② 스키마 드리프트.
    # 그때 판정을 그냥 포기하면 **원 결함이 되살아난다** — 같은 런의 1차 수집이 신규 티커의
    # 최근 5일을 canonical 에 넣으므로 다음 런부터 그 티커는 '이미 있음'으로 보이고, 이력
    # 창은 영영 안 붙는다.
    #
    # **기준이 낡아도 놓치지 않는다** — 신규 상장·편입 종목은 더 오래된 파티션에도 없으므로
    # 물러난 기준에서도 그대로 편입으로 잡힌다. 낡은 기준이 못 잡는 것은 '최근 파티션에만
    # 빠진 종목'(거래정지 등)뿐인데, 그건 이미 이력이 있어 손실이 아니라 콜 낭비를 던 것이다.
    # 틀리는 방향이 한쪽으로만 열려 있다는 뜻이라 물러나기가 안전하다.
    # holdings 스캔이 부분 스냅샷에 대해 하는 것과 같은 자세다.
    # 창 하한은 루프 **앞**에서 만든다 — 비달력일 `window_end` 는 입력 오류이고 1차 fetch 도
    # 같은 값을 쓰므로, 파티션 읽기 성패와 무관하게 즉시 올라가야 한다(여기서 삼키면 창이
    # 틀린 채로 수집이 돈다). 읽기 실패와 달리 이건 격리 대상이 아니다.
    since = (date.fromisoformat(window_end) - timedelta(days=NEWCOMER_LOOKBACK_DAYS)).isoformat()
    for candidate in sorted(dates, reverse=True)[:NEWCOMER_REFERENCE_PARTITIONS]:
        known, unreadable = _partition_tickers(storage, candidate)
        if unreadable:
            # 못 읽은 행이 하나라도 있으면 그 파티션은 기준으로 못 쓴다 — '그 종목들이
            # 없다'로 읽으면 손상 파일에 실려 있던 종목이 통째로 편입으로 잡힌다.
            logger.error(
                "canonical 일봉 trade_date=%s 에서 티커를 못 읽은 행 %d (읽은 티커 %d종) — "
                "기준으로 쓰지 않고 이전 파티션으로 물러난다(스키마 드리프트 의심)",
                candidate, unreadable, len(known))
            continue
        if not known:
            logger.warning(
                "canonical 일봉 trade_date=%s 파티션이 비었다 — 이전 파티션으로 물러난다",
                candidate)
            continue
        # 깊이 축 — 이 티커가 **오래전에도** 있었는가. 없으면 얕게 들어온 것이라 편입으로
        # 남긴다. canonical 자체가 그만큼 깊지 않으면 그 질문에 답할 수 없으므로(부트스트랩)
        # 존재 판정만 쓴다 — 답할 수 없는 것을 '아니오'로 읽으면 전 종목이 편입이 된다.
        deep = _deep_reference_tickers(storage, sorted(dates, reverse=True), candidate)
        shallow = sorted(t for t in universe if t not in known or (deep is not None and t not in deep))
        return shallow, since, ""


    # 소급 상한 안에 쓸 수 있는 파티션이 하나도 없다. **여기서 조용히 성공하면 안 된다** —
    # 같은 런의 1차 수집이 신규 티커의 최근 5일을 canonical 에 넣으므로, 다음 런부터 그
    # 티커는 '이미 있음'으로 보이고 400일 이력은 영영 안 붙는다(ALPHA-989 그 자체). 사유만
    # 남기고 넘어가면 그 영구 결손을 아무도 모른 채 지나간다. 호출부가 이 사유를 보고 런을
    # partial 로 내린다 — 최근 10개 canonical 파티션이 전부 못 쓸 상태라는 건 알람이 맞다.
    scanned = min(len(dates), NEWCOMER_REFERENCE_PARTITIONS)
    return [], window_end, f"no_usable_partition(scanned={scanned})"


def _deep_reference_tickers(
    storage: Storage, newest_first: list[str], latest_usable: str
) -> set[str] | None:
    """`latest_usable` 에서 `NEWCOMER_DEPTH_PARTITIONS` 만큼 과거의 쓸 수 있는 파티션의
    티커 집합. canonical 이 그만큼 깊지 않거나 쓸 수 있는 게 없으면 None(판정 보류).

    None 과 빈 집합은 다르다 — 빈 집합은 '그때 아무도 없었다'라 전 종목이 편입이 되고,
    None 은 '그 질문에 답할 수 없다'라 존재 판정만 쓴다(Rule 12 — 모르는 것을 아는 척 안 한다).
    """
    # 슬라이스가 깊이 요건을 겸한다 — canonical 이 그만큼 깊지 않으면 빈 목록이라 루프가
    # 안 돌고 아래 None 으로 떨어진다(별도 가드를 두면 같은 사실이 두 벌이 된다).
    older = [d for d in newest_first if d < latest_usable]
    for candidate in older[NEWCOMER_DEPTH_PARTITIONS - 1:]:
        known, unreadable = _partition_tickers(storage, candidate)
        if not unreadable and known:
            return known
    return None


def _partition_tickers(storage: Storage, trade_date: str) -> tuple[set[str], int]:
    """그 파티션의 (읽은 티커 집합, 못 읽은 행 수).

    '읽혔다'는 유니버스와 **같은 형태**일 때만이다 — 공백 없는 비어있지 않은 str.
    truthy 만 보면 타입 드리프트를 못 잡는다: ticker 가 int64 로 바뀌면 집합이 정수가 되어
    문자열 유니버스와 하나도 안 겹치는데 '못 읽은 행'은 0이라 어느 가드에도 안 걸린다.
    """
    known: set[str] = set()
    unreadable = 0
    try:
        keys = list(storage.list_keys(canonical_price_daily_partition("KR", trade_date) + "/"))
    except Exception:
        # 파일 읽기와 같은 이유로 목록 조회도 격리한다 — 여기서 올리면 그날 수집이 죽는다.
        logger.exception("canonical 일봉 trade_date=%s 목록 조회 실패 — 이 파티션은 기준에서 뺀다",
                         trade_date)
        return set(), 1
    for key in keys:
        if key.endswith(".parquet"):
            try:
                rows = _read_parquet_rows(storage.get_bytes(key))
            except Exception:
                # 🔴 **여기서 예외를 올리면 그날 가격 수집이 통째로 안 돈다** — 이 판정은
                # 1차 `source.fetch` **앞**에 있어서, 깨진 parquet 하나나 S3 일시 오류가
                # 수집 전체를 죽인다. 게다가 새 raw 가 안 생겨 그 파티션이 계속 최신으로
                # 남으므로 **매 런이 같은 자리에서 죽는다**(수동 복구 전까지 영구 정지).
                # 파일 단위로 격리해 그 파티션을 '못 씀'으로 분류하고 이전으로 물러난다.
                logger.exception("canonical 일봉 parquet 읽기 실패: %s — 이 파티션은 기준에서 뺀다", key)
                unreadable += 1
                continue
            for row in rows:
                ticker = row.get("ticker")
                if not isinstance(ticker, str) or not ticker.strip():
                    # 문자열이 아니거나(int64 드리프트) 사실상 빈 값 — 못 읽었다.
                    # truthy 만 보면 타입 드리프트를 못 잡는다: ticker 가 int64 로 바뀌면
                    # 집합이 정수가 되어 문자열 유니버스와 하나도 안 겹치는데 '못 읽은 행'은
                    # 0이라 어느 가드에도 안 걸린다.
                    unreadable += 1
                    continue
                if ticker != ticker.strip():
                    # ⚠️ 공백이 붙은 티커는 **상류가 통과시킨다** — `normalize_price._blank`
                    # 는 strip 후 비지 않으면 canonical 로 보낸다. 여기서 그걸 '못 읽음'으로
                    # 치면 정상 파티션을 통째로 버리고, 그 값이 계속 있으면 편입 판정이
                    # 영영 멈춘다(가드가 상류 계약보다 엄격하면 그 차이가 곧 결함이다).
                    # 정체성은 정돈한 코드다(`parse.krx_short_code` 와 같은 축) — 정돈해
                    # 읽되 사실은 드러낸다.
                    logger.warning(
                        "canonical 일봉 trade_date=%s 에 공백이 붙은 ticker %r — 정돈해 읽는다",
                        trade_date, ticker)
                known.add(ticker.strip())
    return known, unreadable


JOB_NAME = "ingest_price_raw"
DATASET = "price_daily"  # collection_log·raw 파티션의 dataset= 키


def run(
    settings: Settings,
    storage: Storage,
    source: PriceSourceAdapter,
    run_id: str,
    from_date: str | None = None,
    to_date: str | None = None,
) -> int:
    """수집 실행. 성공 0, 중단/실패 비0 반환. 결과는 항상 collection_log 로 남긴다.

    from_date/to_date 는 소스에 넘길 수집 날짜창(YYYY-MM-DD). 스케줄 증분·백필 창은
    run 엔트리가 정해 넘긴다.
    """
    started_at = datetime.now(timezone.utc)
    started_date = started_at.isoformat()[:10]
    vendor = source.source_name  # 파티션·로그의 source= 키 (하드코딩 대신 소스가 규정)
    log: dict = {
        "run_id": run_id,
        "job_name": JOB_NAME,
        "source_vendor": vendor,
        "window_from": from_date,
        "window_to": to_date,
        "started_at": started_at.isoformat(),
        # 편입 판정의 상태는 **모든 경로에 존재해야** 한다 — 조건부 필드면 '없음'이
        # 구버전 로그·비활성 런·스캔 전 실패 셋을 한 모양으로 뭉갠다. 여기 초기값이
        # "스캔에 도달하지 못했다"이고, 아래에서 도달한 경로만 덮어쓴다.
        "newcomer_scan": "not_reached",
    }

    if not source.enabled:
        # 크리덴셜 미주입 환경(로컬 등)은 실패가 아니라 명시적 skip — 로그로 드러낸다.
        # 벤더 무관이라 메시지도 소스가 규정한 vendor 로 남긴다(fmp=api_key, kis=앱키/시크릿).
        # 로그 쓰기 실패는 스토리지 장애라 스케줄러에 비0으로 드러낸다(ALPHA-451) — 예외를
        # 밖으로 던지지 않는다는 뜻의 best-effort 이지 exit 0 이 아니다. 기록을 못 남긴 채
        # 성공으로 끝나면 감사 레코드 유실을 아무도 모른다. 비0이 raw 게이트(And)를 막는
        # 대가는 **아래 terminal 경로가 이미 같은 값으로 치르고 있다** — 여기만 exit 0 이면
        # 같은 장애가 어느 줄에서 났느냐로 결과가 갈린다(뒤집으려면 저장소 15곳을 함께).
        logger.warning("%s 가격 비활성(크리덴셜 미주입) — 수집 건너뜀", vendor)
        try:
            _write_log(storage, vendor, started_date, run_id, {**log, "status": "skipped",
                                                               "reason": f"{vendor} disabled or missing credentials",
                                                               "ops": {"records_out": 0, "failed_records": 0}})
        except Exception:
            logger.exception("collection_log 기록 실패(skip 경로)")
            return 1
        return 0

    # 파티션 키는 market 만(ingest_date 는 런 전체가 started_date 로 동일). raw 는
    # 받은 행을 그대로 append 해 전부 보존한다 — 중복 판정·upsert 는 후속 canonical 소관.
    partitions: dict[str, list[dict]] = defaultdict(list)
    fetched = 0
    status, error, reason = "success", None, None
    exit_code = 0
    # 2차 수집(신규 편입 이력)이 소스의 `fetch_failures` 를 리셋하기 전에 옮겨 둔 1차분.
    carried_failures: list[dict] = []
    # 편입 판정 근거를 못 찾았는가 — 런을 partial 로 내리는 축(아래 실패 판정과 나란히).
    scan_incomplete = False

    try:
        # 수집 유니버스 — 소스가 옵트인하면(KIS, ALPHA-419) canonical KR holdings 최신
        # 스냅샷의 구성종목·ETF 티커를 targets 에 union 한다. 유니버스가 곧 분석 유니버스라
        # 커버리지가 holdings 를 따라간다(정적 맵 드리프트 제거). 얼마나 더해졌는지는 로그로.
        # holdings 읽기 실패도 이 try 안 — "결과는 항상 collection_log" 계약을 지킨다.
        symbols = list(settings.targets.symbols)
        log["symbols_from_holdings"] = 0
        newcomers: list[str] = []
        newcomer_since = ""
        scan_skip = ""
        if getattr(source, "universe_from_holdings", False):
            universe = _kr_holdings_universe(storage, expected_etfs=_krx_expected_etfs(settings))
            log["symbols_from_holdings"] = len(set(universe) - set(symbols))
            symbols = sorted(set(symbols) | set(universe))
            # 창 하한은 `to_date`(호출부가 벤더 달력으로 뽑은 창 끝, ALPHA-883) 기준이다 —
            # 여기서 달력을 다시 고르면 사실이 두 벌이 된다.
            # ⚠️ `--from` 만 준 백필은 `to_date=None` 으로 들어오고, 그때 폴백 `started_date`
            # 는 **UTC** 다. 어댑터는 그 자리에서 KST 오늘로 떨어지므로(kis_price `end_default`)
            # 00:00~08:59 KST 진입이면 창 **길이**가 400 대신 401일이 된다. 이 축은 증분 창과
            # 다르다 — 증분은 하루가 밀리면 그날 데이터를 통째로 잃지만, 여기 하루는 이미
            # 4배 여유를 둔 이력 창이 하루 더 깊어질 뿐이다. 그 대가를 치르고 네 번째 KST
            # 상수를 만들지 않는다. `from_date` 로 나가는 값은 아래 로그와 같은 값 하나다.
            # 스캔 **진입**을 먼저 남긴다 — 아래 호출이 예외로 죽으면(파티션 읽기 실패·
            # window_end 가 비달력일) 초기값 "not_reached" 가 그대로 남아 '스캔에 못 갔다'로
            # 위장된다. 실제로는 갔다가 죽은 것이고, 그 둘은 진단이 다르다.
            log["newcomer_scan"] = "scan_failed"
            newcomers, newcomer_since, scan_skip = _newcomers(
                storage, universe, to_date or started_date)
            log["newcomer_scan"] = scan_skip or "ok"
            if scan_skip:
                # 판정 근거를 못 찾았다 = 이 런이 놓친 편입 종목의 이력이 **영구** 결손이
                # 될 수 있다. 수집 자체는 됐으니 error 는 아니지만 success 도 아니다.
                logger.error(
                    "신규 편입 판정 불가(%s) — 이 런에 편입 종목이 있었다면 그 이력은 "
                    "영구 결손이다(다음 런은 '이미 있음'으로 본다)", scan_skip)
                scan_incomplete = True
        else:
            # 유니버스를 holdings 에서 파생하지 않는 소스(FMP)는 편입 판정 자체가 없다 —
            # "안 돌았다"와 "못 돌았다"는 다른 사실이다.
            log["newcomer_scan"] = "not_applicable"
        # 이미 그만큼 깊은 창으로 도는 런(명시 `--from` 백필)은 2차 수집이 순수 낭비다.
        if newcomers and (from_date is None or from_date <= newcomer_since):
            # 편입은 있었지만 1차 창이 이미 그만큼 깊다 — 편입 0 인 런과 구분해 남긴다.
            log["newcomer_scan"] = "covered_by_primary_window"
            newcomers = []
        log["symbols_newcomer"] = len(newcomers)
        log["newcomer_window_from"] = newcomer_since if newcomers else None
        # 🔴 **편입분은 이력 창으로만 받는다 — 증분 창에서 뺀다.** 둘 다 받으면 이력 fetch 가
        # 실패해도 증분 5일치는 남고, SFN 은 partial 런도 NormalizeParallel 로 **계속 보내므로**
        # (`statemachine.tf` NotifyRawPartial) 그 5일치가 canonical 에 들어간다. 그러면 다음
        # 런의 존재 기반 판정이 그 티커를 '이미 있음'으로 보고, **400일 이력은 영영 재시도되지
        # 않는다** — partial 로 보고했는데 결손은 영구다. 빼 두면 실패한 종목은 행이 하나도
        # 안 남아 계속 편입으로 잡힌다(성공할 때까지 자격 유지 = 새 상태 저장 없이 자가복구).
        # 이력 창은 증분 창을 포함하므로(위 가드가 아닌 경우만 여기 온다) 커버리지 손실은 없다.
        newcomer_set = set(newcomers)
        passes = [([s for s in symbols if s not in newcomer_set], from_date, to_date)]
        if newcomers:
            # 편입은 드문 사건이라 평시엔 이 창 자체가 안 생긴다 — 로그로 드러낸다.
            logger.warning(
                "유니버스 신규 편입 %d종 — 이력 창(%s~%s)으로 수집(증분 창에서 제외): %s",
                len(newcomers), newcomer_since, to_date or started_date, ",".join(newcomers),
            )
            passes.append((newcomers, newcomer_since, to_date))
        planned_total: int | None = None
        for index, (pass_symbols, window_from, window_to) in enumerate(passes):
            if index:
                # ⚠️ `fetch` 는 진입 때 `fetch_failures` 를 **리셋**한다 — 다음 호출이 앞 실패를
                # 지우기 전에 옮겨 둔다. 안 옮기면 앞에서 죽은 심볼이 런 상태(partial/error)
                # 에서 사라져 결손이 성공으로 마감된다.
                carried_failures.extend(getattr(source, "fetch_failures", []))
            for record in source.fetch(pass_symbols, window_from, window_to):
                fetched += 1
                partitions[record["market"]].append(record)
            # "매핑 대상 0 = skip" 은 **런 전체**의 판정이라 창별로 나눠 세면 안 된다 — 편입
            # 창만 보고 0을 읽으면 정상 수집한 런이 skip 으로 위장된다. 합으로 센다.
            planned = getattr(source, "planned_symbols", None)
            if planned is not None:
                planned_total = (planned_total or 0) + planned
    except StopFetch as exc:
        # 4xx/429 — 부분 수집분은 저장하고 상태로 드러낸다(조용한 성공 금지).
        logger.error("가격 수집 중단(4xx/429): %s", exc)
        status, error, exit_code = "stopped", str(exc), 1
    except Exception as exc:
        # 예기치 못한 실패(재시도 소진 등)도 '결과는 항상 collection_log' 계약을
        # 지킨다 — 부분 수집분 저장 + status=error 로 남기고 비0 종료.
        logger.exception("가격 수집 실패")
        status, error, exit_code = "error", str(exc), 1

    # raw 저장도 계약("결과는 항상 collection_log") 안에 둔다 — put_bytes 가
    # 실패(IAM·네트워크·부분 쓰기)해도 예외를 삼켜 status=error 로 남기고 로그를 쓴다.
    saved = 0
    try:
        for market, records in sorted(partitions.items()):
            key = f"{raw_price_partition(vendor, market, started_date, run_id)}/part-00000.ndjson"
            lines = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records)
            storage.put_bytes(key, lines.encode("utf-8"))
            saved += len(records)
    except Exception as exc:
        logger.exception("raw 저장 실패")
        status, error, exit_code = "error", str(exc), 1

    # 심볼 단위로 격리한 실패를 런 상태에 반영한다(격리≠은폐 — fail loud).
    #  - 저장분 있고 일부 실패 → partial(성공했지만 온전치 않음)
    #  - 저장분 0인데 실패 있음 → error(수집이 사실상 실패)
    #  - MAX_PAGES 절단(kind=truncation)은 데이터 유효 + 다음 창 이어받음이라 성공으로 본다
    #    (ALPHA-351). 절단도 아래 로그(failed_symbols)엔 남겨 fail-loud 는 유지한다.
    # 같은 심볼이 1·2차 수집에서 모두 실패하면 **한 번만** 센다 — 이 목록의 축은 심볼이고
    # (`records_failed_symbols`·`ops.failed_records`), 두 번 세면 실패 1종이 2종으로 보고돼
    # 원장이 실제보다 나쁜 상태를 가리킨다.
    by_symbol: dict[tuple, dict] = {}
    for failure in carried_failures + list(getattr(source, "fetch_failures", [])):
        key = (failure.get("our_ticker"), failure.get("symbol"))
        prev = by_symbol.get(key)
        if prev is None:
            by_symbol[key] = failure
        elif prev.get("kind") == "truncation" and failure.get("kind") != "truncation":
            # ⚠️ 절단이 실제 실패를 덮으면 안 된다 — 절단은 성공으로 치는 종류라(ALPHA-351)
            # 그걸 남기면 partial 이어야 할 런이 success 로 끝난다. 심각한 쪽이 이긴다.
            by_symbol[key] = failure
    failed_symbols = list(by_symbol.values())
    real_failures = [f for f in failed_symbols if f.get("kind") != "truncation"]
    if status == "success" and scan_incomplete:
        # 심볼 실패와 같은 급으로 다룬다 — 저장분은 있지만 온전치 않다. `failed_records` 는
        # 건드리지 않는다(원장을 영구 INCOMPLETE 로 만드는 축이고, 이건 심볼 실패가 아니다).
        status, exit_code = "partial", 1
        error = error or f"신규 편입 판정 불가 ({log['newcomer_scan']})"
    if status in ("success", "partial") and real_failures:
        if saved == 0:
            status, exit_code = "error", 1
            error = f"모든 수집 심볼 실패 ({len(real_failures)}건)"
        else:
            status, exit_code = "partial", 1

    # 활성 소스인데 매핑된 대상이 0개면(심볼맵 누락·전 대상 미매핑 KR 등) 수집이
    # 사실상 불가능한 설정 — success(0건)로 위장하지 않고 skip 으로 드러낸다(Rule 12).
    if status == "success" and planned_total == 0:
        status, reason = "skipped", "no mapped targets"

    # 로그 쓰기도 best-effort — 스토리지가 통째로 죽어 로그마저 못 남기면 최소한
    # 비0 종료로 스케줄러/ECS 에 실패를 알린다(감사 로그 유실은 로거로만 남김).
    try:
        _write_log(storage, vendor, started_date, run_id, {
            **log,
            "status": status,
            "error": error,
            "reason": reason,
            "records_fetched": fetched,
            "records_saved": saved,
            "records_failed_symbols": len(failed_symbols),
            "failed_symbols": failed_symbols,
            "partitions": len(partitions),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            # 원장 관측용 공통 봉투(ALPHA-181). 위 카운터의 **의미 선택**이다 — 절단(truncation)도
            # 유실로 세서 창이 잘린 런이 VALID 로 올라가지 않게 한다(ALPHA-351 은 exit 만 성공).
            "ops": {"records_out": saved, "failed_records": len(failed_symbols)},
        })
    except Exception:
        logger.exception("collection_log 기록 실패 — 스토리지 장애로 감사 로그 유실")
        exit_code = exit_code or 1
    logger.info(
        "ingest_price_raw 완료: status=%s fetched=%d saved=%d failed_symbols=%d partitions=%d",
        status, fetched, saved, len(failed_symbols), len(partitions),
    )
    return exit_code


def _write_log(storage: Storage, vendor: str, started_date: str, run_id: str, payload: dict) -> None:
    key = collection_log_key(vendor, DATASET, started_date, run_id)
    storage.put_bytes(key, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
