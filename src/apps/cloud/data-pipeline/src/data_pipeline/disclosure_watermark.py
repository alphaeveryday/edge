"""공시 배치 증분 창 결정 — 원장 워터마크 (ALPHA-987).

거래일 계산(`OPS_KR_HOLIDAYS` 배선·연말 달력 갱신) 대신 **자기 수집 로그**로 창을 정한다:
직전 완주 배치 런의 `window_to` 부터 오늘까지. 주말·공휴일·연휴 길이와 무관하고, 직전 런
실패·건너뜀까지 자동으로 덮는다 — DART 는 증분 커서가 없어 같은 날짜창 재독이 안전하다
(본문 재다운로드는 스텝의 seen-map 이 막는다).

창 시작은 워터마크 **당일**이다(다음날이 아니다) — 직전 런 이후 그날 늦게 노출된 공시
(18:10 런 뒤 19:09 도착 실측, 20:00 이후 미관측 꼬리)는 `rcept_dt` 가 그날이라 다음날
시작 창의 질의에 안 걸린다. 당일 재독이면 정상 상태 창이 [어제, 오늘]로 종전
`DEFAULT_LOOKBACK_DAYS=1` 과 동일하고, 마지막 날의 본문 fetch 실패도 다음 런이 자동
재시도한다(같은 날을 다시 읽어 seen-map 미스인 본문만 받는다).

완주 술어는 `status ∈ {success, partial} ∧ list_truncated == False ∧ ingest_lane == "batch"` 다.
`status` 만으로는 "창을 다 읽었나"에 답할 수 없고(partial 은 본문 실패로도 선다),
`list_truncated` 만으로도 안 된다(StopFetch·status 이상 경로는 플래그를 안 세운 채 죽는다).
`ingest_lane` 이 없는 로그(PR1 이전·수동 잔재)는 자격 없음 — 두 레인이 같은 프리픽스를
쓰고 run_id 접두는 규약이 아니라, 필드 부재를 레거시로 접는 것이 유일한 판별이다.
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta

from .lake import Storage, collection_log_prefix

logger = logging.getLogger(__name__)

# 창 상한 = 워터마크 탐색 범위(UTC 일). 탐색이 이 범위까지만 보므로 정상 경로에서 창이
# 상한을 넘지 않는다. 10일 근거: 최장 연휴(추석 ~6일)+장애 여유를 덮고, 3월 피크 실측
# (2일 창 최악 116페이지 = 일 ~58페이지 지속) 기준 10일 창 ≈ 300~400페이지로
# `max_pages=500` 안이다(14일이면 ~460 으로 경계선). 이보다 긴 공백은 폴백으로 떨어져
# `window_source` 가 매 런 드러낸다 — 초과분은 어느 상한에서도 수동 백필 소관이다.
# ⚠️ `sources.dart_disclosure.WINDOW_CHUNK_DAYS`(30, 벤더 분할 폭)를 재사용하지 않는다 —
# 분할 폭과 회수 상한은 다른 축이라 한쪽 튜닝이 다른 쪽을 조용히 옮긴다.
WATERMARK_MAX_WINDOW_DAYS = 10

_SOURCE = "dart"
_DATASET = "disclosures"


def find_watermark(storage: Storage, *, today_utc: date, today_kst: date) -> str | None:
    """직전 완주 배치 런의 `window_to`(YYYY-MM-DD) — 없으면 None.

    `started_date=`(UTC 일) 파티션을 오늘부터 `WATERMARK_MAX_WINDOW_DAYS` 일 역방향으로
    훑고, 자격 로그들의 **max(window_to)** 를 취한다. "가장 최근 런"이 아니라 max 인
    이유: 오늘 돌린 과거 구간 수동 백필(window_to 가 과거)이 워터마크를 뒤로 당기면
    안 된다 — 최근 창을 완주한 런이 있으면 그쪽이 이긴다.

    ⚠️ max 는 **연속성이 있어야** 건전하다 — 어떤 런의 window_to 로 전진하는 것은 그 창
    시작 이전이 이미 덮여 있을 때만 결손을 만들지 않는다. 그래서 두 단계다:

    1. **스케줄 런**(window_source 가 watermark·fallback_no_watermark·default(그림자))만으로
       기준을 세운다 — watermark 런의 시작은 직전 워터마크(자기 앵커), fallback_no_watermark
       는 "체인이 없음을 확인한" 콜드스타트 씨앗, 그림자 시대는 1분 레인이 커버하던
       기간이라 체인 계약 밖이다. window_source **부재(PR2 이전)는 자격 없음** — 스케줄
       런과 수동 단발 백필이 같은 무라벨이라 연속성을 판별할 수 없다. **fallback_lookup_error 런은 제외**:
       자기 앞의 체인을 못 본 채 좁은 기본창을 돌았으므로 이 로그가 워터마크가 되면 그때
       못 본 결손이 모든 자동 창 밖으로 밀려 영구 확정된다(그림자 런의 조회 실패도
       `watermark_shadow.source` 로 같은 이유로 거른다).
    2. **cli 런**(운영자 백필)은 창 시작이 기준에 닿을 때만(window_from ≤ 기준+1일) 연장
       자격을 준다 — 비연속 수동 창([25,25] 단발 등)이 기준을 점프시키면 그 사이 결손이
       자동 회수에서 영영 빠진다. 연속 백필은 정당하게 전진시킨다.

    개별 로그의 결함(JSON 파싱 실패·비객체·날짜 비정상·미래 창)은 그 로그만 자격 없음으로
    건너뛴다 — 하나가 조회 전체를 죽이면 폴백이 상시화된다. 스토리지 예외(list/**get** 실패)는
    전파한다 — 호출자가 `fallback_lookup_error` 로 접는다(파싱 실패와 섞어 삼키면 인프라
    장애가 "워터마크 없음"이 되어, 그 폴백 런이 콜드스타트 씨앗으로 오인된다).
    """
    prefix = collection_log_prefix(_SOURCE, _DATASET)
    chain: list[date] = []
    manual: list[tuple[date, date]] = []  # (window_from, window_to)
    for offset in range(WATERMARK_MAX_WINDOW_DAYS + 1):  # 오늘 포함 역방향
        day = (today_utc - timedelta(days=offset)).isoformat()
        for key in storage.list_keys(f"{prefix}started_date={day}/"):
            if not key.endswith("/log.json"):
                continue
            raw = storage.get_bytes(key)  # 실패는 전파 — 위 docstring 의 이유
            try:
                log = json.loads(raw)
            except ValueError:
                logger.warning("워터마크 조회: 로그 파싱 실패 — 제외 %s", key)
                continue
            if not isinstance(log, dict):
                logger.warning("워터마크 조회: 로그가 객체가 아님 — 제외 %s", key)
                continue
            if log.get("ingest_lane") != "batch":
                continue  # minute 레인·필드 부재(레거시) 모두 자격 없음
            if log.get("status") not in ("success", "partial"):
                continue  # stopped·error 는 절단 플래그 없이 죽는다 — status 로 거른다
            if log.get("list_truncated") is not False:
                continue  # True 는 절단, 부재(레거시)는 판정 불가 — 둘 다 자격 없음
            source_label = log.get("window_source")
            if source_label not in ("default", "watermark", "fallback_no_watermark", "cli"):
                # **화이트리스트다** — fallback_lookup_error 를 지명해 빼는 차단목록이면
                # 오기·스키마 드리프트로 변형된 라벨이 자격을 얻는다. 모르는 라벨은 체인을
                # 봤다는 증명이 없는 쪽으로 접는다. **부재(None)도 자격 없음**: PR2 이전
                # 로그는 스케줄 런과 수동 백필이 같은 무라벨이라 연속성을 판별할 수 없다
                # (`ingest_lane` 부재 = PR1 이전과 동형 규칙). 체인 부트스트랩은 컷오버 후
                # 첫 스케줄 런의 fallback_no_watermark 씨앗이 담당하므로 잃는 것이 없다.
                continue
            shadow = log.get("watermark_shadow")
            if isinstance(shadow, dict) and shadow.get("source") == "fallback_lookup_error":
                continue  # 그림자 런의 조회 실패 — 겉 라벨(default)만으론 안 걸린다
            try:
                window_to = date.fromisoformat(log.get("window_to"))
            except (TypeError, ValueError):
                logger.warning("워터마크 조회: window_to 비날짜 — 제외 %s", key)
                continue
            if window_to > today_kst:
                # 미래로 끝나는 창(운영자 미래 백필)은 현재까지의 커버리지를 증명하지
                # 못한다 — max 에 넣으면 정상 워터마크를 이겨 창을 [오늘,오늘]로 좁힌다.
                continue
            if source_label == "cli":
                try:
                    window_from = date.fromisoformat(log.get("window_from"))
                except (TypeError, ValueError):
                    continue  # 시작을 모르는 수동 창은 연속성을 증명할 수 없다
                manual.append((window_from, window_to))
            else:
                chain.append(window_to)
    best = max(chain, default=None)
    if best is None:
        return None  # cli 런만으론 체인을 못 연다 — 다음 스케줄 런이 씨앗을 심는다
    # cli 연장 — 연속인 것만, 고정점까지(연속 백필 여러 개가 사다리를 이룰 수 있다).
    extended = True
    while extended:
        extended = False
        for window_from, window_to in manual:
            if window_to > best and window_from <= best + timedelta(days=1):
                best, extended = window_to, True
    return best.isoformat()


def resolve_window(
    storage: Storage,
    *,
    enabled: bool,
    explicit: bool,
    scheduled_window: tuple[str, str],
    today_kst: date,
    today_utc: date,
) -> tuple[tuple[str, str], dict]:
    """이 런이 실제로 쓸 (from, to) 와 collection_log 에 실을 window_meta 를 정한다.

    enabled=False(그림자)면 창은 종전대로(scheduled_window) 두고 워터마크 계산 결과만
    `watermark_shadow` 로 로그에 남긴다 — 배포 직후부터 계산이 실제 창과 일치하는지
    관측할 수 있게(회수 대상은 마감일에만 생겨 그냥 두면 두 달 반 동안 "잘 도는 것"과
    "조용히 안 도는 것"이 구분되지 않는다). explicit(운영자 `--from/--to`)는 항상
    그대로 존중한다(`window_source="cli"`).

    워터마크 조회 실패·부재는 fail-loud 로 죽이지 않고 기본창 폴백 + `window_source` 로
    드러낸다 — 스케줄러 retry=0 이라 여기서 죽으면 그날 수집 자체가 죽어, 회수 장치의
    실패가 막으려던 결손을 새로 만든다. 콜드스타트·컷오버 직후가 정상적으로 이 경로를
    탄다(컷오버 직후엔 탐색 범위 안에 배치 자격 로그가 없어 폴백 = 1분 레인이 이미
    커버한 구간을 대량 재수집하지 않는다).
    """
    try:
        watermark = find_watermark(storage, today_utc=today_utc, today_kst=today_kst)
        lookup_failed = False
    except Exception:
        logger.exception("워터마크 조회 실패 — 기본창 폴백")
        watermark, lookup_failed = None, True

    today = today_kst.isoformat()
    stale: str | None = None
    if watermark is not None and (today_kst - date.fromisoformat(watermark)).days > WATERMARK_MAX_WINDOW_DAYS:
        # 탐색 범위 안의 로그라도 window_to 는 더 과거일 수 있다(오늘 돌린 옛 구간 백필이
        # 유일한 자격 로그인 경우). 상한 초과 창은 페이지 절단을 부르므로 폴백으로 접되,
        # 그 사실을 숨기지 않는다(Rule 12) — stale_watermark 로 로그에 남긴다.
        stale, watermark = watermark, None

    if watermark is None:
        source = "fallback_lookup_error" if lookup_failed else "fallback_no_watermark"
        computed = None
    else:
        source = "watermark"
        # 불변식: watermark ≤ 오늘 — find_watermark 가 미래 window_to 를 제외한다.
        computed = (watermark, today)

    if explicit:
        actual, label = scheduled_window, "cli"
    elif enabled and computed is not None:
        actual, label = computed, "watermark"
    elif enabled:
        actual, label = scheduled_window, source
    else:
        actual, label = scheduled_window, "default"

    meta: dict = {"window_source": label}
    if stale is not None:
        meta["stale_watermark"] = stale
    if label == "watermark":
        # 정상 상태(어제 완주)면 0 — 0이 아닌 날이 곧 회수가 일어난 날이라 사후에 가를 수 있다.
        meta["recovered_days"] = max(0, (today_kst - date.fromisoformat(actual[0])).days - 1)
    if not enabled:
        meta["watermark_shadow"] = {
            "source": source,
            "window_from": computed[0] if computed else None,
            "window_to": computed[1] if computed else None,
            # 스케줄 런에서만 의미 있는 대조다 — cli 백필 창과 다른 것은 당연하다.
            "matches_actual": computed == tuple(actual) if computed else False,
        }
    return tuple(actual), meta
