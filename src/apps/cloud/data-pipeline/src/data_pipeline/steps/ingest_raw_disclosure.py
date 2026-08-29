"""공시(disclosure) Step1 — 원본저장 (raw 존 append, 전부 보존).

⚠️ 한 가지 예외가 있다: 소스가 **한 순회 안에서 완전히 같은 행**을 접는다. 목록이 수집 중에도
자라 페이지 경계가 밀리면 같은 행이 두 페이지에 나오는데, 그걸 그대로 두면 같은 문서를 두 번
내려받는다. 접히는 건 내용이 전부 같은 행뿐이라 증거는 잃지 않지만, 그래서 `list_rows_seen`
(벤더가 건넨 행 수)과 raw 행 수는 다를 수 있다 — 서로 다른 질문에 대한 답이고 둘 다 참이다.
정체성 병합·정정 판정 같은 **서로 다른 관측을 접는 일**은 여전히 하지 않는다(canonical 소관).

OpenDART 공시목록(list.json)을 **날짜창 단위로 시장 전체** 수집해(종목별 질의 아님 —
`sources/dart_disclosure.py`) market 별 ingest_date 파티션(수집일) ndjson 으로 raw 존에 append
하고, 각 대상 공시의 서류 원본(document.xml, euc-kr HTML ZIP)을 rcept_no 별 객체로 무변형
저장한다 — 뉴스(ingest_raw)와 동형인 **bronze 통일 규약**이다.

⚠️ **메타는 유니버스 행 전량이고, 본문만 대상 유형이다**(ALPHA-865). 소스는 report_nm 으로
행을 버리지 않고 `is_target` 플래그만 달아 보내므로(목록 질의는 어차피 전 유형을 훑어 버려도
콜이 안 준다) 이 스텝이 그 플래그로 **본문 다운로드만** 제한한다 — 본문은 행당 1콜이라
제한하지 않으면 본문 콜이 하루 ~11건에서 `universe_matched` 만큼(수십~100/일 규모)으로 뛴다.
감쇠 두 축은 collection_log 의 `universe_matched`·`type_matched` 가 따로 센다.

⚠️ **이 스텝은 완전성을 판정하지 않는다.** 소스가 남기는 `list_total_count`·`list_rows_seen`
은 창 규모의 **관측**이지 판정이 아니다 — 목록은 수집 중에도 자라(접수 피크 16시) 페이지
경계가 밀리므로, 둘의 차이가 절단인지 유입인지 구분되지 않는다. 실제 완전성 근거는 같은
날짜창을 다시 읽는 **다음 런과의 rcept_no 집합 비교**이고, 그 판정 주체는 원장·EOD 다.

⚠️ **틱 멱등은 본문에만 건다**(ALPHA-720). 증분 커서가 없어 매 실행이 날짜창 전체를 다시
읽으므로, 장중 레인처럼 같은 날 여러 번 돌면 **같은 `document.xml` ZIP 을 슬롯 수만큼
내려받는다**. 그래서 수집일 전후(UTC 오늘·어제)에 이미 저장된 본문 객체를 seen-map 으로
읽어 두고, 히트한 `rcept_no` 는 받지 않고 메타 행의 `document_raw_path` 를 **기존 키**로
채운다(정제가 그 ZIP 을 그대로 연다).

메타(ndjson)는 **접지 않는다** — 매 실행이 자기 run_id 파티션에 창 전체 관측을 남기는 것이
이 소스의 유일한 완전성 근거이고(위 문단), 메타까지 접으면 런 사이 rcept_no 집합 비교 대상이
사라져 근거를 스스로 없앤다. 본문 재다운로드만 없어지고 증거는 그대로다.

⚠️ 장중 잦은 실행(미니배치)을 붙이면 그 레인은 **원장 밖**이라 침묵을 아무도 못 본다 —
슬롯도 expected_task 도 없어 "1시간째 0건"을 판정할 주체가 없다. 백스톱은 15:40 일일 런이다:
같은 날짜창을 다시 훑으므로 데이터 구멍은 그날 안에 메워지고 그 런은 원장 안에 있다. 즉
**구멍은 안 생기고 지연만 EOD 까지 늘어난다.** 장중 침묵을 장중에 알아야 하면 별도 장치다.

⚠️ 뉴스형 판정: 날짜창에 대상 공시가 0건인 건 정상이다(그날 대상 유형 공시 없음).
재무제표(ingest_raw_financial)의 "매핑 대상 있는데 0행=error" 가드는 두지 않는다(Rule 7 — 스텝별
판정). 메타 행은 전부 보존하고, 본문 수집이 실패해도 메타는 남긴다(bronze — 정체성 병합·정정
판정·corp_code↔ticker bridge 는 후속 canonical 소관).
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from ..config import Settings
from ..lake import (
    Storage,
    collection_log_key,
    raw_disclosure_day_prefix,
    raw_disclosure_document_key,
    raw_disclosure_partition,
)
from ..sources import DartDisclosureSource, StopFetch
from ..sources.dart_disclosure import BODY_FORMAT
from . import disclosure_raw_manifest
from .ingest_price_raw import _kr_etf_ids, _kr_holdings_universe, _krx_expected_etfs

logger = logging.getLogger(__name__)

JOB_NAME = "ingest_raw_disclosure"
DATASET = "disclosures"  # collection_log·raw 파티션의 dataset= 키
DisclosureSourceAdapter = DartDisclosureSource

# seen-map 을 만들 때 훑는 수집일 수(오늘 포함, 과거로). 2 인 이유는 파티션 축이 UTC 이기
# 때문이다: `ingest_date` 는 `datetime.now(timezone.utc)` 의 날짜라 KST 09:00 = UTC 00:00 에서
# 갈린다 — 한 KR 영업일의 슬롯들이 두 UTC 날짜에 흩어지므로 하루만 보면 오전 슬롯이 받아 둔
# 본문을 오후 슬롯이 못 찾는다.
_DOC_LOOKBACK_DAYS = 2


def _existing_documents(
    storage: Storage, vendor: str, market: str, started_date: str
) -> dict[str, str]:
    """이미 받아 둔 본문 객체 → `{rcept_no: key}` (ALPHA-720).

    같은 수집일의 **모든 run_id** 를 훑는다 — 슬롯마다 run_id 가 다르므로 자기 파티션만 보면
    아무것도 못 찾는다. 히트한 문서는 다시 받지 않고 메타가 이 키를 가리킨다.

    **본문 fetch 가 실패해 객체가 없는 건은 여기 안 들어와 다음 실행이 자동 재시도한다** —
    별도 재시도 장치를 두지 않는 이유다(실패 목록을 따로 들고 다니면 그게 또 하나의 상태다).

    ponytail: 조회 범위가 수집일 2일 고정이라 그보다 오래된 창의 백필(`--from 2026-01-01`)은
    여전히 재다운로드한다. 창 폭에 비례하는 LIST 를 피하려는 의도적 상한이고 기존 동작과
    같다 — 넓은 창의 재다운로드가 실제로 문제가 되면 창에서 수집일 후보를 뽑는 쪽으로 넓혀라.
    """
    day = date.fromisoformat(started_date)
    found: dict[str, str] = {}
    # 과거 → 오늘 순. 같은 rcept_no 가 여러 날에 있으면 최신 키가 남는다(어느 쪽이든 같은
    # 바이트지만, 오래된 파티션이 먼저 지워지는 보존정책에서 최신이 더 오래 산다).
    for offset in range(_DOC_LOOKBACK_DAYS - 1, -1, -1):
        prefix = raw_disclosure_day_prefix(
            vendor, market, (day - timedelta(days=offset)).isoformat()
        )
        for key in storage.list_keys(prefix):
            # **읽는 쪽의 수용 집합 = 쓰는 쪽(raw_disclosure_document_key)의 출력 집합.**
            # "이 프리픽스 아래 아무 .zip" 으로 잡으면 나중에 이 파티션에 다른 객체를 두는
            # 순간(격리본·아카이브 등) 그게 조용히 본문으로 인정돼, 정제가 엉뚱한 ZIP 을 연다.
            # 빈 이름(`documents/.zip`)도 여기서 걸러 seen-map 에 빈 키가 생기지 않게 한다.
            _, sep, name = key.rpartition("/documents/")
            if not sep or "/" in name or not name.endswith(".zip"):
                continue
            rcept_no = name.removesuffix(".zip")
            if rcept_no:
                found[rcept_no] = key
    return found


def run(
    settings: Settings,
    storage: Storage,
    source: DisclosureSourceAdapter,
    run_id: str,
    from_date: str | None = None,
    to_date: str | None = None,
    *,
    window_meta: dict | None = None,
) -> int:
    """배치 진입점 — 수집 실행. 성공 0, 중단/실패 비0 반환(SFN·CLI 가 쓰는 계약).

    from_date/to_date 는 소스에 넘길 수집 날짜창(YYYY-MM-DD). None 이면 소스 기본(최신분) —
    스케줄 증분·백필 창은 run 엔트리가 정해 넘긴다(뉴스와 동형).

    window_meta 는 창 결정 관측(`disclosure_watermark.resolve_window` 의 window_source·
    watermark_shadow 등) — 로그에 그대로 실린다. 창 결정이 이 스텝 밖(run 엔트리)에서
    일어나므로 관측만 받아 적는다.
    """
    # 이 진입점이 곧 배치 레인이다(SFN·CLI) — 1분 레인은 collect() 를 직접 부른다.
    return collect(
        settings, storage, source, run_id, from_date, to_date,
        ingest_lane="batch", window_meta=window_meta,
    )["exit_code"]


def collect(
    settings: Settings,
    storage: Storage,
    source: DisclosureSourceAdapter,
    run_id: str,
    from_date: str | None = None,
    to_date: str | None = None,
    *,
    ingest_lane: str,
    window_meta: dict | None = None,
) -> dict:
    """`run` 과 같은 수집을 하고 **관측을 돌려준다** (ALPHA-875 — 1분 레인이 쓴다).

    ingest_lane 은 이 런이 어느 수집 경로였는지("batch" | "minute") — 로그에 그대로 남는다.
    두 레인이 같은 collection_log 프리픽스를 쓰고 `job_name`·run_id 로는 구분되지 않아
    (run_id 접두는 규약이 아니다), 이 필드가 없으면 배치 워터마크(ALPHA-987)가 1분 레인
    로그를 배치 완주로 오인한다. 기본값을 두지 않는다 — 새 호출부가 조용히 어느 레인으로
    떨어지면 그 오인이 되살아난다. ⚠️ `lane` 이라 부르지 않는다: minute 쪽 `lane` 은
    window 클레임 축("realtime"|"recovery")으로 이미 쓰는 다른 축이다.

    반환:
      - `exit_code` : `run` 이 그대로 내보내는 값
      - `log`       : collection_log 에 쓴 payload 그대로(원장 판정 입력)
      - `rcept_nos` : 이 폴링이 관측한 rcept_no 정렬 튜플. **window checksum 의 재료**다 —
                      이 소스는 증분 커서가 없어 매 tick 이 날짜창 전체를 재독하므로, 같은
                      집합을 다시 봤다면 같은 값이어야 세대가 유지된다(`commit_disclosure_window`).
                      raw 메타 바이트를 해시하면 `fetched_at` 이 매 tick 달라 세대가 늘 증가한다.
      - `raw_keys`  : 이 런이 쓴 메타 ndjson exact key. minute은 정제에 직접 넘기고 batch는
                      run-scoped manifest로 확정해 input_run_id 소비자가 GET한다.
      - `list_truncated` : 목록을 **끝까지 못 읽었나**(`_stop_early` → `_segment_truncated`).
                      `status` 로는 이걸 알 수 없다 — `partial` 은 본문 fetch 실패나 남의 회사
                      malformed 행 하나로도 서고(그때 목록은 온전히 읽혔다), 절단도 같은
                      `partial` 이 된다. "창을 다 읽었나"를 묻는 소비자는 이 값을 봐야 한다.

    ⚠️ 로그를 S3 에서 되읽지 않고 돌려주는 이유: 로그 키의 `started_date` 는 **UTC 날짜**라
    (`collection_log_key`) 호출자가 키를 재구성하려면 그 날짜를 맞혀야 하는데, UTC 자정이
    09:00 KST — 즉 이 레인 격자의 **한복판**이다. 개장 시각 tick 마다 키를 틀릴 수 있다.
    """
    if ingest_lane not in ("batch", "minute"):
        # 오타난 레인이 로그에 그대로 실리면 수집은 성공하는데 워터마크는 그 런을 어느
        # 레인에도 귀속하지 못한다 — 생산자에서 즉시 죽인다(Rule 12, `_WINDOW_CALENDAR`
        # 의 미선언 fail-loud 와 같은 자세).
        raise ValueError(f"알 수 없는 ingest_lane: {ingest_lane!r} (batch|minute)")
    started_at = datetime.now(timezone.utc)
    started_date = started_at.isoformat()[:10]  # = ingest_date
    vendor = source.source_name  # 파티션·로그의 source= 키 (하드코딩 대신 소스가 규정)
    log: dict = {
        # 창 결정 관측(window_source·watermark_shadow 등, ALPHA-987) — 배치 엔트리만
        # 넘긴다(1분 레인은 세션 격자가 창을 정해 이 축이 없다). **맨 앞**에 편다:
        # 아래 명시 필드가 항상 이겨야 임의 dict 가 run_id·ingest_lane 같은 로그
        # 정체성을 조용히 덮지 못한다(워터마크 술어·run_id 소비자가 그 정체성을 믿는다).
        **(window_meta or {}),
        "run_id": run_id,
        "job_name": JOB_NAME,
        "ingest_lane": ingest_lane,
        "source_vendor": vendor,
        "window_from": from_date,
        "window_to": to_date,
        "started_at": started_at.isoformat(),
    }

    if ingest_lane == "batch":
        try:
            storage.put_bytes(
                disclosure_raw_manifest.key(run_id),
                disclosure_raw_manifest.bytes_for(run_id, False, []),
            )
        except Exception as exc:
            logger.exception("raw run manifest 초기화 실패")
            failed = {
                **log,
                "status": "error",
                "error": f"raw run manifest 초기화 실패: {exc}",
                "reason": None,
                "list_truncated": False,
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "ops": {"records_out": 0, "failed_records": 0},
            }
            try:
                _write_log(storage, vendor, started_date, run_id, failed)
            except Exception:
                logger.exception("collection_log 기록 실패(manifest 초기화 실패 경로)")
            return {"exit_code": 1, "log": failed, "rcept_nos": (), "raw_keys": [],
                    "list_truncated": False}

    if not source.enabled:
        # 키 미주입 환경(로컬 등)은 실패가 아니라 명시적 skip — 로그로 드러낸다.
        # 로그 쓰기 실패는 스토리지 장애라 스케줄러에 비0으로 드러낸다(ALPHA-451) — 예외를
        # 밖으로 던지지 않는다는 뜻의 best-effort 이지 exit 0 이 아니다. 기록을 못 남긴 채
        # 성공으로 끝나면 감사 레코드 유실을 아무도 모른다. 비0이 raw 게이트(And)를 막는
        # 대가는 **아래 terminal 경로가 이미 같은 값으로 치르고 있다** — 여기만 exit 0 이면
        # 같은 장애가 어느 줄에서 났느냐로 결과가 갈린다(뒤집으려면 저장소 15곳을 함께).
        logger.warning("%s 공시 비활성(api_key 미주입) — 수집 건너뜀", vendor)
        skipped = {**log, "status": "skipped",
                   "reason": f"{vendor} disabled or no api_key",
                   # 창을 읽은 적이 없으니 절단도 아니다 — 값 자체는 status=skipped 가
                   # 가리지만, 필드는 넣는다: "필드 부재 = PR1 이전 로그" 판별이 새 로그
                   # 전부에 성립해야 워터마크가 부재를 레거시로 접을 수 있다.
                   "list_truncated": False,
                   "ops": {"records_out": 0, "failed_records": 0}}
        skip_exit_code = 0
        try:
            if ingest_lane == "batch":
                disclosure_raw_manifest.write_completed(storage, run_id, [])
        except Exception as exc:
            logger.exception("raw run manifest 완료 기록 실패(skip 경로)")
            skipped.update({
                "status": "error",
                "error": f"raw run manifest 완료 기록 실패: {exc}",
                "reason": None,
            })
            skip_exit_code = 1
        try:
            _write_log(storage, vendor, started_date, run_id, skipped)
        except Exception:
            logger.exception("collection_log 기록 실패(skip 경로)")
            return {"exit_code": 1, "log": skipped, "rcept_nos": (), "raw_keys": [],
                    "list_truncated": False}
        return {"exit_code": skip_exit_code, "log": skipped, "rcept_nos": (), "raw_keys": [],
                "list_truncated": False}

    # 메타(공시목록 행)는 market 별 ndjson 으로, 본문(document.xml ZIP)은 rcept_no 별 객체로
    # 버퍼링했다가 저장 단계에서 한 번에 쓴다 — put 실패를 한 곳에서 계약대로 처리하려는 것.
    partitions: dict[str, list[dict]] = defaultdict(list)
    doc_failures: list[dict] = []
    # market → 이미 받아 둔 본문 색인. 시장이 실제로 나올 때 처음 만든다(안 나온 시장의
    # 프리픽스를 LIST 하지 않게). 저장 성공분을 여기 되먹여, 한 창 안에서 같은 rcept_no 의
    # 서로 다른 관측(rm ""→"정")이 두 번 와도 본문은 한 번만 받는다.
    doc_index: dict[str, dict[str, str]] = {}
    fetched = documents_saved = documents_reused = 0
    status, error, reason = "success", None, None
    exit_code = 0

    # 본문(document.xml ZIP)은 대용량 바이너리라 버퍼링하지 않고 받는 즉시 저장한다 — 넓은
    # 백필(사업보고서 다수)에서 전체 ZIP 을 메모리에 쌓으면 raw 를 하나도 못 쓰고 ECS 가 OOM
    # 날 수 있다(Codex #83 P2). 메타(작은 ndjson)만 파티션별로 버퍼링해 저장 단계에서 쓴다.
    try:
        # 수집 유니버스 — 소스가 옵트인하면(ALPHA-477) canonical KR holdings 최신 스냅샷의
        # **구성종목**을 targets 에 union 한다(가격·수급과 같은 축). holdings 읽기 실패도 이
        # try 안 — "결과는 항상 collection_log" 계약을 지킨다. 얼마나 더해졌는지는 로그로.
        #
        # ETF 자기 티커는 **출처와 무관하게** 뺀다: holdings 파생분만 걸러선 부족하고, 정적
        # targets 에도 091160(KODEX 반도체)이 등재돼 있다. ETF 는 DART 신고자가 아니라 공시가
        # 나올 수 없다 — 남겨두면 유니버스(planned_symbols)만 부풀려 "수집 대상"과 "수집될 수
        # 있는 것"이 갈린다. (종전엔 여기에 더해 corpCode.xml 미매핑으로 매 런 잡혀
        # ops.failed_records>0 → 원장 영구 INCOMPLETE 였다. 그 경로는 소스가 종목별 질의를
        # 걷어내며 사라졌지만, 유니버스를 사실대로 세는 이유는 그대로 남는다.)
        symbols = list(settings.targets.symbols)
        log["symbols_from_holdings"] = log["symbols_excluded_etf"] = 0
        if getattr(source, "universe_from_holdings", False):
            expected = _krx_expected_etfs(settings)
            universe = _kr_holdings_universe(storage, include_etf=False, expected_etfs=expected)
            etf_ids = _kr_etf_ids(storage, expected)
            union = set(symbols) | set(universe)
            merged = union - etf_ids
            # 차감 **뒤** 기준으로 센다 — fund-of-funds 스냅샷에서는 어떤 코드가
            # constituent_ticker 이면서 etf_id 이기도 해, 차감 전에 세면 실제로 fetch 에
            # 넘어가지도 않은 심볼을 '더했다'고 보고하게 된다.
            log["symbols_from_holdings"] = len(merged - set(symbols))
            log["symbols_excluded_etf"] = len(union) - len(merged)
            symbols = sorted(merged)
        for record in source.fetch(symbols, from_date, to_date):
            fetched += 1
            market = record["market"]
            if not record.get("is_target"):
                # 비대상 유형 — 메타만 남기고 본문은 받지 않는다(ALPHA-865). 목록은 어차피
                # 전 유형을 훑으므로 이 행을 저장해도 API 콜은 안 늘지만, 본문은 행당 1콜이라
                # 여기서 제한하지 않으면 본문 콜이 하루 ~11건에서 universe_matched 만큼
                # (수십~100/일 규모)으로 뛴다. 정제는 report_nm 으로 라우팅하므로 이 행들은
                # records_skipped_type 으로 빠진다.
                #
                # 두 필드를 **명시적으로 None** 으로 둔다(키를 빼지 않는다) — 대상 행의 본문
                # 실패 경로가 이미 그 관례이고, 여기서만 키가 없으면 나중에 이 raw 를 다시
                # 읽는 소비자(대상을 넓힌 뒤의 재파싱)가 키 부재와 값 None 을 따로 다뤄야 한다.
                record["document_raw_path"] = None
                record["body_format"] = None
                partitions[market].append(record)
                continue
            # 게이트 **뒤**에서 읽는다 — 소스가 형상(비어있지 않은 str)을 보장하지만, 위에
            # 두면 그 보장이 깨지는 순간 `.strip()` 이 창 전체를 죽인다(비대상 행에 비문자열
            # rcept_no 가 오던 회귀를 실제로 밟았다). 대상 행만 이 값을 쓴다.
            rcept_no = (record.get("rcept_no") or "").strip()
            if market not in doc_index:
                doc_index[market] = _existing_documents(storage, vendor, market, started_date)
            existing_key = doc_index[market].get(rcept_no)
            if existing_key:
                # 이미 받아 둔 본문 — 다시 받지 않고 **기존 키를 가리킨다**(ALPHA-720).
                # 메타 행은 그대로 저장한다(창 전체 관측 보존).
                record["document_raw_path"] = existing_key
                record["body_format"] = BODY_FORMAT
                documents_reused += 1
                partitions[market].append(record)
                continue
            # 본문 수집(대상 격리) — 실패해도 메타는 보존한다(bronze). 4xx/429/쿼터는
            # StopFetch 로 전체 중단(부분 수집분은 저장하고 상태로 드러냄).
            try:
                body = source.fetch_document(rcept_no)
            except StopFetch:
                raise
            except Exception as exc:
                record["document_raw_path"] = None
                record["body_format"] = None
                doc_failures.append({
                    "rcept_no": rcept_no,
                    "our_ticker": record.get("our_ticker"),
                    "error": str(exc),
                })
            else:
                # 받는 즉시 저장(버퍼링 안 함). put 실패는 저장 인프라 오류라 아래 except 로
                # 전파돼 error 가 된다(메타 put 실패와 동일 취급 — "raw 저장 실패").
                doc_key = raw_disclosure_document_key(
                    vendor, market, started_date, run_id, rcept_no
                )
                storage.put_bytes(doc_key, body)
                record["document_raw_path"] = doc_key
                record["body_format"] = BODY_FORMAT
                documents_saved += 1
                doc_index[market][rcept_no] = doc_key
            partitions[market].append(record)
    except StopFetch as exc:
        logger.error("공시 수집 중단(4xx/429): %s", exc)
        status, error, exit_code = "stopped", str(exc), 1
    except Exception as exc:
        logger.exception("공시 수집/본문 저장 실패")
        status, error, exit_code = "error", str(exc), 1

    # 메타(ndjson)만 저장 단계에서 쓴다 — 본문은 위에서 즉시 저장됨. put 실패도 계약
    # ("결과는 항상 collection_log") 안에서 삼켜 status=error 로 남긴다.
    saved = saved_targets = 0
    raw_keys: list[str] = []
    try:
        for market, records in sorted(partitions.items()):
            key = f"{raw_disclosure_partition(vendor, market, started_date, run_id)}/part-00000.ndjson"
            lines = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records)
            storage.put_bytes(key, lines.encode("utf-8"))
            # 쓴 키를 남긴다 — 1분 레인의 정제가 이걸 그대로 받아 `raw/` 전량 스캔을 건너뛴다.
            # put 성공분만 담는다: 실패한 파티션을 정제 입력으로 넘기면 없는 객체를 읽는다.
            raw_keys.append(key)
            saved += len(records)
            # 원장이 세는 산출은 **대상 건수**다(아래 ops 주석). 전량과 갈라 두지 않으면
            # 비대상 메타가 늘 있어서 "전건 실패"가 영영 관측되지 않는다.
            saved_targets += sum(1 for r in records if r.get("is_target"))
    except Exception as exc:
        logger.exception("raw 메타 저장 실패")
        status, error, exit_code = "error", str(exc), 1

    # 대상(corp·페이지·문서) 단위로 격리한 실패를 런 상태에 반영한다(격리≠은폐 — fail loud).
    # list 실패(source.fetch_failures)와 본문 실패(doc_failures)를 합쳐 판정한다.
    #  - 저장분 있고 일부 실패 → partial(메타는 있으나 온전치 않음: 본문 결측 등)
    #  - 저장분 0인데 실패 있음 → error(수집이 사실상 실패)
    #
    # ⚠️ **관용 어휘가 없다** — 종전 두 관용(`unmapped`·`truncation`)은 목록 질의가 종목별에서
    # 창 전체로 바뀌면서 근거가 함께 사라졌다(Rule 7 — 충돌은 평균 내지 말고 하나를 고르고
    # 이유를 남긴다): `unmapped` 는 소스가 corp_code 를 해소하지 않게 돼 **발생 지점이 없고**,
    # `truncation` 의 근거("다음 증분 창이 이어받음", ALPHA-351)는 상한이 corp 당 10 페이지이던
    # 시절 것이라 창 전체가 한 순회인 지금은 성립하지 않는다(상한 도달 = 대량 미수집이고,
    # 운영자 지정 백필 창은 이어받을 창이 없다).
    #
    # 그래서 **관용 필터를 두지 않는다.** 빈 집합으로 남겨두면 아무 일도 안 하면서 유효한
    # 확장점처럼 보여, 거기 이름을 하나 넣는 것만으로 "특정 실패를 성공 처리"하는 경로가 다시
    # 조용히 생긴다. `kind` 는 로그의 분류 라벨로만 남는다 — 판정에는 쓰지 않는다.
    failed_targets = list(getattr(source, "fetch_failures", [])) + doc_failures
    real_failures = failed_targets
    if status == "success" and real_failures:
        if saved_targets == 0:
            status, exit_code = "error", 1
            # "모든 대상 실패"라고 쓰지 않는다 — 실패 목록에는 유형 판정 **앞**에서 잡히는
            # 행(남의 회사 malformed row·stock_code 비문자열)도 섞여, 대상 0건인 정상 날에
            # 그 1건만 있어도 그 문구는 거짓이 된다. 두 사실을 그대로 적는다.
            error = f"대상 저장 0건 · 실패 {len(real_failures)}건"
        else:
            status, exit_code = "partial", 1

    # 활성 소스인데 매핑된 대상이 0개면(심볼맵 누락·전 대상 미매핑) 수집이 사실상 불가능한
    # 설정 — success(0건)로 위장하지 않고 skip 으로 드러낸다(Rule 12). 미매핑은 plan() 규약상
    # 정상(후속 소스 커버)이라 error 가 아닌 skip.
    if status == "success" and getattr(source, "planned_symbols", None) == 0:
        status, reason = "skipped", "no mapped targets"

    # ⚠️ 재무제표와 달리 "매핑 대상 있는데 0행=error" 가드는 두지 않는다 — 공시는 특정 corp 의
    # 빈 날짜창(대상 유형 공시 없음)이 정상이라(뉴스형), 빈 응답을 이상으로 보면 오탐이다.

    # 관측한 rcept_no 집합 — window checksum 의 재료다(위 반환 계약). 소스가 rcept_no
    # 결측/비문자열 행을 yield 전에 걸러내므로(`dart_disclosure._scan_window` 의 rcept 게이트)
    # 여기 담기는 행은 전부 유효한 값을 갖는다. 그래도 형상을 다시 확인한다 — 저 게이트가
    # 느슨해지는 순간 조용히 빈 문자열이 checksum 에 섞여 두 다른 관측이 같은 값을 낸다.
    observed_rcept_nos = tuple(sorted({
        record["rcept_no"].strip()
        for records in partitions.values()
        for record in records
        if isinstance(record.get("rcept_no"), str) and record["rcept_no"].strip()
    }))
    # 목록을 끝까지 못 읽었나(`_stop_early` → `_segment_truncated`). `fetch()` 는 절단되면
    # 뒤 세그먼트를 돌지 않고 즉시 돌아오므로 순회가 끝난 뒤의 이 값이 곧 "이 창이 온전한가"다.
    # ⚠️ 단독으로는 완주 증명이 아니다 — StopFetch·status 이상 경로는 이 플래그를 세우지 않고
    # 죽는다(목록 미완인데 값은 False). 완주를 묻는 소비자(워터마크)는 status 와 함께 본다.
    list_truncated = bool(getattr(source, "_segment_truncated", False))
    payload = {
        **log,
        "status": status,
        "error": error,
        "reason": reason,
        "records_fetched": fetched,
        # 전량(유니버스) / 그중 대상. 둘을 갈라 둬야 "보존은 늘었는데 대상은 0"이 보인다.
        "records_saved": saved,
        "records_saved_target": saved_targets,
        # 보존해도 못 쓰는 행(비대상 + rcept_no 결측/비문자열)을 뺀 수 — 0건이 아니라
        # 수치로 남긴다(Rule 12). 실패가 아니라 별도 축이라 ops.failed_records 와 무관하다.
        "rows_dropped_malformed": getattr(source, "dropped_malformed", 0),
        "documents_saved": documents_saved,
        # 이미 있어서 **안 받은** 본문 수(ALPHA-720). 안 세면 "본문이 0건"과 "재다운로드를
        # 0건으로 줄였다"가 documents_saved=0 하나로 접혀 구분되지 않는다.
        "documents_reused": documents_reused,
        # 인자가 아니라 **실제로 수집한 창**을 남긴다 — 시작일만 준 백필은 소스가 끝을
        # 오늘로 확정하므로, 인자(None)만 기록하면 어떤 창이었는지 복원되지 않고 런 사이
        # rcept_no 집합 비교(완전성 근거)가 성립하지 않는다.
        "window_from": source.resolved_window[0],
        "window_to": source.resolved_window[1],
        "records_failed_targets": len(failed_targets),
        "failed_targets": failed_targets,
        "partitions": len(partitions),
        # 창 전체 규모 관측 — 소스가 신고한 건수(1페이지 total_count)와 실제로 훑은 행 수.
        # **판정이 아니다**: 목록은 수집 중에도 자라(접수 피크 16시) 페이지 경계가 밀리므로,
        # 둘의 차이는 절단일 수도 유입일 수도 있다. 어느 쪽인지 모르는 값으로 완전성을
        # 단언하지 않고 기록만 남긴다 — 나중에 사람이 볼 수 있게(Rule 12).
        "list_total_count": getattr(source, "list_total_count", None),
        "list_rows_seen": getattr(source, "list_rows_seen", 0),
        # 감쇠 두 축(ALPHA-865). `list_rows_seen → universe_matched` 가 유니버스가 자른
        # 몫이고 `universe_matched → type_matched` 가 유형이 자른 몫이다. 통과분(records_
        # saved)만 남기면 둘이 한 숫자로 접혀 "대상을 넓히면 얼마나 늘어나는가"에 답할 수
        # 없다 — 실측(2026-08-07) 867 → 저장 1 의 내역이 그래서 복원되지 않았다.
        # `is_target` 을 정한 **필터 기준**을 같이 남긴다. 이 값은 설정 파생 판정이라
        # 필터를 넓히면 같은 report_nm 에 다른 is_target 이 붙은 행이 파티션에 섞이는데,
        # 기준을 안 적으면 어느 런이 어느 기준이었는지 복원되지 않는다 — 이 티켓의 전제
        # ("보존해 뒀다가 나중에 넓혀 재파싱")가 바로 그 복원 가능성에 걸려 있다.
        "report_name_filters": list(getattr(source, "report_name_filters", [])),
        "universe_matched": getattr(source, "universe_matched", 0),
        "type_matched": getattr(source, "type_matched", 0),
        # 원장 워터마크(ALPHA-987)의 완주 판정 입력 — 종전엔 반환값으로만 냈으나(875 는 배치
        # 로그 바이트를 바꾸지 않으려 했다) 로그를 되읽는 소비자가 생겨 로그에도 남긴다.
        "list_truncated": list_truncated,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        # 원장 관측용 공통 봉투(ALPHA-181). 본문(documents_saved)은 메타 행의 부속이라
        # records_out 은 메타 건수로 센다 — 행 단위 유실 판정의 기준이 그쪽이다.
        #
        # ⚠️ **`saved` 가 아니라 `saved_targets` 다**(ALPHA-865). 메타를 전량 보존하게
        # 되면서 `saved` 는 유니버스 전량(~100/일)이 됐는데, `failed_records` 는 여전히
        # 대상 스코프다. 둘을 섞으면 ops/entry.py 가 명문화한 "산출과 유실은 같은
        # 스코프에서 와야 한다(비대칭 금지)"를 깨서, 유실 비율이 ~30배 축소돼 보이고
        # 콘솔 그리드(GridPage)가 실제 1건인 날 "산출 97"을 띄운다. 비대상 메타는 이
        # 런이 하기로 한 일이 아니라 부산물이라 산출로 세지 않는다 — 보존 사실은
        # records_saved 가 따로 기록한다.
        "ops": {"records_out": saved_targets, "failed_records": len(failed_targets)},
    }
    if ingest_lane == "batch":
        try:
            disclosure_raw_manifest.write_completed(storage, run_id, raw_keys)
        except Exception as exc:
            logger.exception("raw run manifest 완료 기록 실패")
            payload["status"] = "error"
            prior_error = payload.get("error")
            manifest_error = f"raw run manifest 완료 기록 실패: {exc}"
            payload["error"] = (
                f"{prior_error}; {manifest_error}" if prior_error else manifest_error
            )
            exit_code = 1
    try:
        _write_log(storage, vendor, started_date, run_id, payload)
    except Exception:
        logger.exception("collection_log 기록 실패 — 스토리지 장애로 감사 로그 유실")
        exit_code = exit_code or 1
    logger.info(
        "ingest_raw_disclosure 완료: status=%s fetched=%d saved=%d(대상 %d) docs=%d reused=%d"
        " failed=%d dropped=%d partitions=%d",
        status, fetched, saved, saved_targets, documents_saved, documents_reused,
        len(failed_targets), getattr(source, "dropped_malformed", 0), len(partitions),
    )
    return {
        "exit_code": exit_code,
        "log": payload,
        "rcept_nos": observed_rcept_nos,
        "raw_keys": raw_keys,
        # payload 와 같은 값 — 산출 시점의 주석 참조. (종전 "로그에는 넣지 않는다" 결정은
        # ALPHA-987 워터마크가 로그를 소비하게 되며 뒤집혔다.)
        "list_truncated": list_truncated,
    }


def _write_log(storage: Storage, vendor: str, started_date: str, run_id: str, payload: dict) -> None:
    key = collection_log_key(vendor, DATASET, started_date, run_id)
    storage.put_bytes(key, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
