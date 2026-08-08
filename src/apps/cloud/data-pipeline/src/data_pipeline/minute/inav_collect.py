"""장중 iNAV window collector — KIS 단독 (ALPHA-851).

`price_collect.py` 가 형식 선례다: unit 을 **정렬 순회**하고 4분류로 갈라
`(result, records, manifest)` 를 낸다. 벤더가 하나뿐이라(토스 분봉에 NAV 축이 없다)
어댑터를 따로 떼지 않고 이 파일이 순회와 KIS 결합을 함께 진다 — 두 번째 벤더가 생기면
그때 `price_collect`/`kis_collector` 처럼 가른다.

가격과 갈리는 곳은 셋이다:

1. **봉이 아니라 스냅샷이다.** NAV 는 구간의 OHLCV 가 아니라 한 시점의 값이라
   `no_trade`(거래 없는 분) 축이 없다 — 어휘(`UNIT_CLASSES`)는 그대로 쓰되 그 칸은 늘
   빈다. dataset 마다 어휘를 새로 만들면 manifest 검증·EOD QC 가 갈라진다.
2. **기대 집합이 ETF 계열뿐이다.** 구성종목에는 NAV 가 없다 — 무엇을 기대할지는
   호출자(Worker)가 `request.unit_ids` 로 정한다. 여기서는 받은 것만 순회한다.
3. **1콜이 30분치다.** 한 window 를 채우는 데 필요한 건 그중 한 행이고, 나머지 29행은
   버린다. 겹침 복구(결손 window 를 그 29행으로 소급 채우기)는 `_process(claim)` =
   window 1 계약 밖이라 Worker 의 별도 경로 소관이다.

⚠️ **시각 라벨의 축이 미실측이다.** `bsop_hour` 가 구간의 시작인지 끝인지 벤더가
일관되지 않다(`kis_inav._extra_provenance` 실측 메모: 같은 15:16:00 이 cls=60·cls=30 에서
다른 값). 여기서는 **라벨 = window_start** 로 읽는다. 틀렸다면 전 구간이 정확히 1분씩
밀린 것이라, 정정은 canonical 전체의 **균일한 −1분 relabel** 이고 재수집이 필요 없다
(iNAV 는 소급 조회가 불가라 재수집이 필요한 선택이었으면 배선을 막았어야 한다).
08-10 장중 실행에서 `nav-comparison-trend` 스냅샷과 대조하면 갈린다.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime

from ..sources.candle import to_decimal
from ..sources.http import StopFetch
from ..sources.kis_inav import KST, KisInavSource
from .models import CollectionRequest, CollectionResult, content_checksum
from .price_collect import Outcome, status_of

logger = logging.getLogger(__name__)

# canonical 레코드가 담는 값 → KIS 응답 필드. **단위를 이름에 담는다** — `dprt` 는
# 퍼센트인데(실측 확정, `kis_inav._note_premium_unit`) `sql_surface.v_nav.premium` 은
# 비율이라, 같은 이름을 쓰면 조인하는 쪽이 100배 틀린 값을 읽는다.
_VALUE_FIELDS = (("nav", "nav"), ("market_price", "stck_prpr"), ("premium_pct", "dprt"))
# 이 값이 없으면 담을 게 없다 — `kis_inav.REQUIRED_ROW_FIELDS` 가 이미 행 단위로 거른다.
_REQUIRED_VALUE = "nav"
# 이 레인의 window 길이(초). 벤더 어휘가 아니라 **우리 격자**다 — 표본 간격이 이와 같아야
# `bsop_hour` 라벨이 window 라벨과 맞는다.
_LANE_INTERVAL_SEC = 60


def window_label(window_start: datetime) -> str:
    """window → 벤더 시각 라벨(HHMMSS). 비교는 이 6자리 고정 표기로만 한다.

    라벨을 파싱해 시각으로 되돌리지 않고 **문자열로 대조**하는 이유: 선행 0 이 잘린
    `"9300"` 을 `strptime` 이 09:30:00 으로 관대하게 받아들이는데(`kis_inav._time_stamp`
    가 막는 그 함정), 여기서는 애초에 6자리 정규 표기와 같지 않으면 안 맞는 것으로
    끝나 그 관대함이 들어올 자리가 없다.
    """
    return window_start.astimezone(KST).strftime("%H%M%S")


def select_window_row(rows: list[dict], window_start: datetime, unit_id: str):
    """그 window 의 행 하나, 또는 `Outcome.MISSING`/`Outcome.INVALID`.

    1콜이 30행이라 그중 이 window 것만 고른다(`select_window_candle` 과 같은 축).
    """
    label = window_label(window_start)
    matched = [row for row in rows if str(row.get("bsop_hour")) == label]
    if not matched:
        return Outcome.MISSING
    if len(matched) > 1:
        # 같은 라벨이 두 번 오면 어느 쪽이 참인지 우리가 고를 수 없다 — 첫 건을 조용히
        # 채택하면 벤더가 순서를 바꾸는 것만으로 값과 세대가 흔들린다.
        logger.error("%s 가 window %s 에 iNAV %d행을 줬다 — 유일성 위반",
                     unit_id, label, len(matched))
        return Outcome.INVALID
    return matched[0]


def record_of(unit_id: str, row: dict, window_start: datetime) -> dict:
    """artifact 에 실리는 iNAV 한 줄. 값 형식이 어긋나면 raise 한다(호출부가 invalid 로).

    ⚠️ **`fetched_at` 을 담지 않는다.** raw 는 그걸 provenance 로 붙이지만(bronze 규약),
    canonical artifact 의 checksum 은 곧 세대 identity 라 실행 시각이 섞이면 값이 같은
    재실행마다 checksum 이 달라져 `ArtifactImmutabilityError` 가 난다.

    Decimal 을 문자열로 낸다 — float 로 접으면 정밀도가 깨지고 `canonical_json` 의
    NaN/Infinity 거부 규약과도 어긋난다(`price_collect.record_of` 와 같은 이유).
    """
    values = {}
    for name, key in _VALUE_FIELDS:
        raw = row.get(key)
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            if name == _REQUIRED_VALUE:
                raise ValueError(f"{unit_id} iNAV 행에 {key} 가 없다")
            # 괴리·현재가는 결측이어도 NAV 는 쓸 수 있다 — 행을 통째로 버리면 소급이
            # 불가한 그 분의 NAV 가 영구히 사라진다. 없다는 사실을 그대로 싣는다.
            values[name] = None
            continue
        values[name] = str(to_decimal(raw, key, unit_id))
    return {"unit_id": unit_id, "ts": window_start, **values}


def collect_inav_units(
    request: CollectionRequest,
    now: datetime,
    *,
    rows_for: Callable[[str], list[dict] | str],
    retry_count: Callable[[], int],
    clock: Callable[[], datetime],
    artifact_uri: str,
) -> tuple[CollectionResult, tuple[dict, ...], dict[str, list[str]]]:
    """unit 전체를 순회해 `(result, records, manifest)` 를 낸다 — collector 계약 그대로."""
    started = now
    received: list[str] = []
    missing: list[str] = []
    invalid: list[str] = []
    records: list[dict] = []
    retries_before = retry_count()

    # unit_id 정렬 순회 — 같은 멤버십을 다른 순서로 요청해도 records·checksum 이 같다
    for unit_id in sorted(request.unit_ids):
        outcome = rows_for(unit_id)
        if outcome is Outcome.MISSING:
            missing.append(unit_id)
            continue
        if outcome is Outcome.INVALID:
            invalid.append(unit_id)
            continue
        row = select_window_row(outcome, request.window_start, unit_id)
        if row is Outcome.MISSING:
            missing.append(unit_id)
            continue
        if row is Outcome.INVALID:
            invalid.append(unit_id)
            continue
        try:
            records.append(record_of(unit_id, row, request.window_start))
        except ValueError:
            # 값 형식 위반은 **재시도로 안 풀린다** — missing 으로 접으면 같은 손상
            # 응답을 매 window 다시 부른다.
            logger.exception("%s iNAV 값 형식 위반 — invalid", unit_id)
            invalid.append(unit_id)
            continue
        received.append(unit_id)

    # `no_trade` 는 iNAV 에 없는 축이다(모듈 도크스트링 1번) — 빈 칸으로 남겨 4분류
    # 어휘를 그대로 지킨다. 빼면 `build_window_manifest` 의 완전분할 검증과 갈린다.
    manifest = {"received": received, "no_trade": [],
                "missing": missing, "invalid": invalid}
    # checksum 은 데이터에서만 유도한다 — 실행 시각·세대가 들어가면 값이 같은 재실행이
    # 다른 checksum 이 돼 "같은 checksum → generation 불변"이 깨진다
    result_checksum = content_checksum(
        [request.dataset, request.window_start, request.window_end, records]
    )
    result = CollectionResult(
        status=status_of(received, [], missing, invalid),
        expected_count=len(request.unit_ids),
        succeeded_count=len(received),
        failed_count=len(missing) + len(invalid),
        retry_count=retry_count() - retries_before,
        artifact_uri=artifact_uri,
        manifest_checksum=content_checksum(manifest),
        result_checksum=result_checksum,
        watermark_before=None,
        watermark_after=request.window_end,
        generation=1,
        stage_timestamps={"collection_started_at": started,
                          "collection_finished_at": clock()},
    )
    return result, tuple(records), manifest


class KisInavCollector:
    """`KisInavSource` 를 window 계약에 물린다 — 새 HTTP 코드는 없다.

    토큰 발급·`EGW00201` 재시도·`rt_cd` 판정·행 결손 격리·지연/단위 관측이 전부 그
    어댑터에 있어서, 여기서는 **어느 ETF 를 언제 부르는가**만 정한다.

    ⚠️ `interval_sec` 은 60 이어야 한다. 1분 window 는 1분 간격 표본으로만 채워지고,
    다른 간격이면 라벨이 격자에 아예 안 맞아 전 unit 이 매 window missing 이 된다 —
    그 상태는 원장에 "벤더가 안 준다"로 보여 원인이 설정임을 가린다. 기동에서 막는다.

    ponytail: 토큰 만료(24h) 재발급 경로가 없다 — `KisNavSource._fetch_etf` 는 배치용이라
    만료를 안 본다(`kis_minute` 은 `KisAuth.invalidate()` 로 1회 재발급한다). ALPHA-851 은
    수동 bounded 실행이라 프로세스가 24시간을 못 산다. **세션 자동 편입(상주 전환)에서
    같이 붙여라** — 그때부터는 반드시 만난다(`kis_auth.KisAuth.invalidate` 도크스트링).
    """

    def __init__(self, source: KisInavSource, clock: Callable[[], datetime]):
        # **레인 상수**에 건다. 벤더 어휘 상수(`MIN_INTERVAL_SEC`·`INTERVAL_STEP_SEC`)는
        # 둘 다 "KIS 가 무엇을 받아주는가"에서 나온 값이라, 벤더가 30초 코드를 더하는 날
        # 함께 30 으로 내려간다 — 그러면 정상 설정(60)이 거부되고 30초가 통과해 이 가드가
        # 막으려던 결과를 가드가 만든다. 이 60 은 벤더와 무관하다: **우리 window 가 1분**이다.
        if source.interval_sec != _LANE_INTERVAL_SEC:
            raise SystemExit(
                f"1분 레인의 iNAV 는 interval_sec={_LANE_INTERVAL_SEC} 만 쓴다"
                f"(받은 값 {source.interval_sec}) — 다른 간격이면 표본 라벨이 1분 격자에"
                " 안 맞아 전 unit 이 매 window missing 이 된다"
            )
        self.source = source
        self.clock = clock
        # unit_id(= our_etf_id, KRX 단축코드) → KIS 질의 심볼. `plan()` 이 etf_map 에서
        # 파생하고 파생 실패는 거기서 기록된다 — 여기서 다시 파생하면 두 벌이 된다.
        self._symbols = dict(source.plan())
        self._token: str | None = None

    def collect(
        self, request: CollectionRequest, now: datetime
    ) -> tuple[CollectionResult, tuple[dict, ...], dict[str, list[str]]]:
        return collect_inav_units(
            request, now,
            rows_for=lambda unit_id: self._rows_for(unit_id),
            retry_count=lambda: self.source.retry_count,
            clock=self.clock,
            artifact_uri="pending://artifact",
        )

    def _rows_for(self, unit_id: str) -> list[dict] | str:
        """그 ETF 의 최근 30행, 또는 `Outcome.MISSING`/`Outcome.INVALID`.

        한 종목의 실패가 window 전체를 죽이지 않게 하되 **소스 전역 실패는 전파한다** —
        자격증명 하나가 틀렸을 때 전 종목 missing 인 INCOMPLETE 가 매분 쌓이면 아무도
        그 하나를 고치러 가지 않는다(Rule 12, `kis_collector` 와 같은 계약).
        """
        symbol = self._symbols.get(unit_id)
        if symbol is None:
            # universe 와 etf_map 이 갈렸다 — **재시도로 안 풀린다**(매 window 반복).
            # missing 으로 접으면 벤더가 안 준 것처럼 보여 원인이 설정임을 가린다.
            logger.error(
                "iNAV %s 는 etf_map 에 없다 — universe 와 수집 유니버스가 갈렸다(invalid)",
                unit_id,
            )
            return Outcome.INVALID
        if self._token is None:
            # 토큰은 프로세스 1회 — 종목마다 발급하면 KIS 의 분당 1회 제한에 걸린다.
            self._token = self.source.auth.token()
        try:
            return self.source._fetch_etf(unit_id, symbol, "", "", self._token)
        except StopFetch:
            logger.error("KIS iNAV 소스 전역 실패 — 수집 중단", exc_info=True)
            raise
        except Exception as error:
            # 종목 단위 실패(요청 실패·깨진 JSON·rt_cd 오류·빈 output·**봉투 형상
            # 위반**)는 전부 재시도 축이다. 봉투를 INVALID 로 올리면 안 되는 이유는
            # 블라스트 반경이다 — `status_of` 는 invalid 하나로 **window 전체**를
            # INVALID 로 만들고 INVALID 는 재청구 대상이 아닌데, iNAV 는 소급이 불가라
            # 그 분이 전 종목에 대해 영구히 없어진다. 스키마 드리프트는 한 응답의
            # 모양이 아니라 **전 unit·전 window 지속**으로 드러난다.
            logger.error("KIS iNAV 실패 %s(%s): %s", unit_id, symbol, error)
            return Outcome.MISSING
