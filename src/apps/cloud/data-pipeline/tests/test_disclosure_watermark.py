"""disclosure_watermark 테스트 — 완주 술어·max(window_to)·폴백·그림자 (ALPHA-987).

핵심 계약: 워터마크는 **배치 레인의 완주 런**만 자격이 있고(1분 레인·레거시 로그가 같은
프리픽스에 섞여 있다), 창은 워터마크 **당일부터**(늦은 노출 꼬리 재독), 조회 실패·부재는
죽지 않고 폴백 + `window_source` 로 드러난다(스케줄러 retry=0 — 여기서 죽으면 회수 장치가
결손을 새로 만든다). 각 테스트는 '왜'를 주석으로 남긴다(AGENTS Rule 9).
"""

import json
from datetime import date

from data_pipeline import disclosure_watermark as dw
from data_pipeline.lake import LocalStorage, collection_log_key

TODAY_KST = date(2026, 8, 26)
TODAY_UTC = date(2026, 8, 26)


def _put_log(storage, started_date, run_id, **fields):
    payload = {"job_name": "ingest_raw_disclosure", "status": "success",
               "list_truncated": False, "ingest_lane": "batch", **fields}
    key = collection_log_key("dart", "disclosures", started_date, run_id)
    storage.put_bytes(key, json.dumps(payload).encode("utf-8"))


def test_watermark_is_max_window_to_not_latest_run(tmp_path):
    # WHY: "가장 최근 런"으로 잡으면 오늘 돌린 **과거 구간 수동 백필**(window_to 가 과거)이
    #      워터마크를 뒤로 당겨, 다음 스케줄 런이 이미 완주한 구간을 통째로 재수집한다.
    #      max(window_to) 는 그 오염이 구성상 불가능하다.
    storage = LocalStorage(tmp_path)
    _put_log(storage, "2026-08-25", "run_a", window_to="2026-08-25")
    _put_log(storage, "2026-08-26", "run_backfill", window_to="2026-07-01")  # 더 최근 런
    assert dw.find_watermark(storage, today_utc=TODAY_UTC) == "2026-08-25"


def test_minute_and_legacy_logs_are_not_eligible(tmp_path):
    # WHY(완료 조건 '레인 혼재 회귀 방지'): 두 레인이 같은 프리픽스를 쓰고 run_id 접두는
    #      규약이 아니다 — mdw(1분 레인) 로그·ingest_lane 없는 레거시 로그가 워터마크로
    #      집히면 컷오버 직후 첫 조회가 정확히 그걸 봐서, 배치가 안 돈 날을 완주로 오인한다.
    storage = LocalStorage(tmp_path)
    _put_log(storage, "2026-08-26", "mdw_s1_0900_1", ingest_lane="minute",
             window_to="2026-08-26")
    legacy = {"job_name": "ingest_raw_disclosure", "status": "success",
              "window_to": "2026-08-26"}  # PR1 이전 — ingest_lane·list_truncated 부재
    storage.put_bytes(collection_log_key("dart", "disclosures", "2026-08-26", "run_old"),
                      json.dumps(legacy).encode("utf-8"))
    assert dw.find_watermark(storage, today_utc=TODAY_UTC) is None


def test_incomplete_runs_are_not_eligible(tmp_path):
    # WHY: 완주 술어는 status 와 list_truncated 를 **함께** 본다 — partial 은 본문 실패로도
    #      서므로 status 단독은 과소, StopFetch·error 는 절단 플래그를 안 세운 채 죽으므로
    #      list_truncated 단독은 과대 판정이다. 절단·중단·에러·skip 이 워터마크가 되면
    #      그 창의 미수집 꼬리가 영구 결손된다.
    storage = LocalStorage(tmp_path)
    _put_log(storage, "2026-08-25", "r_trunc", list_truncated=True, window_to="2026-08-25")
    _put_log(storage, "2026-08-25", "r_stop", status="stopped", window_to="2026-08-25")
    _put_log(storage, "2026-08-25", "r_err", status="error", window_to="2026-08-25")
    _put_log(storage, "2026-08-25", "r_skip", status="skipped", window_to="2026-08-25")
    assert dw.find_watermark(storage, today_utc=TODAY_UTC) is None
    # partial(본문만 실패, 목록 완주)은 자격이 있다 — 전진 안 하면 남의 회사 malformed 행
    # 1건(영구 재현)으로 워터마크가 영구 정지한다. 본문 재시도는 당일 재독이 준다.
    _put_log(storage, "2026-08-25", "r_partial", status="partial", window_to="2026-08-25")
    assert dw.find_watermark(storage, today_utc=TODAY_UTC) == "2026-08-25"


def test_probe_range_is_bounded(tmp_path):
    # WHY: 탐색 범위(=창 상한)를 벗어난 로그는 안 본다 — 그보다 긴 공백은 페이지 절단을
    #      부르는 창이라 자동 회수 대상이 아니다(폴백 + 수동 백필 소관).
    storage = LocalStorage(tmp_path)
    _put_log(storage, "2026-08-10", "r1", window_to="2026-08-10")  # 16일 전 — 범위 밖
    assert dw.find_watermark(storage, today_utc=TODAY_UTC) is None


def test_corrupt_log_is_skipped_not_fatal(tmp_path):
    # WHY: 깨진 로그 하나가 조회 전체를 죽이면 폴백이 상시화된다 — 그 로그만 자격 없음.
    storage = LocalStorage(tmp_path)
    storage.put_bytes(collection_log_key("dart", "disclosures", "2026-08-26", "r_bad"),
                      b"not json")
    _put_log(storage, "2026-08-25", "r_ok", window_to="2026-08-25")
    assert dw.find_watermark(storage, today_utc=TODAY_UTC) == "2026-08-25"


def test_enabled_steady_state_window_equals_legacy_default(tmp_path):
    # WHY: 어제 완주한 정상 상태에서 창은 [어제, 오늘] — 종전 DEFAULT_LOOKBACK_DAYS=1 과
    #      동일해야 컷오버가 창 폭을 바꾸지 않는다. 시작이 워터마크 **당일**인 것이 핵심:
    #      다음날부터면 직전 런 이후 그날 늦게 노출된 공시(19:09 실측 꼬리)를 영영 안 읽는다.
    storage = LocalStorage(tmp_path)
    _put_log(storage, "2026-08-25", "r1", window_to="2026-08-25")
    actual, meta = dw.resolve_window(
        storage, enabled=True, explicit=False,
        scheduled_window=("2026-08-25", "2026-08-26"),
        today_kst=TODAY_KST, today_utc=TODAY_UTC)
    assert actual == ("2026-08-25", "2026-08-26")
    assert meta["window_source"] == "watermark"
    assert meta["recovered_days"] == 0  # 0 이 정상인 날 — 비정상과 사후에 갈린다


def test_enabled_skipped_day_is_recovered(tmp_path):
    # WHY(완료 조건 '인위적 결손으로 회수를 증명'): 하루 런이 죽으면(retry=0 스케줄러)
    #      다음 런의 창이 그 날을 덮어야 한다 — 이것이 이 기능이 막으려는 실패이고,
    #      회수 대상이 마감일에만 생겨 라이브로는 11월까지 관측되지 않는 유일한 검증이다.
    storage = LocalStorage(tmp_path)
    _put_log(storage, "2026-08-23", "r1", window_to="2026-08-23")  # 이후 이틀 무런
    actual, meta = dw.resolve_window(
        storage, enabled=True, explicit=False,
        scheduled_window=("2026-08-25", "2026-08-26"),
        today_kst=TODAY_KST, today_utc=TODAY_UTC)
    assert actual == ("2026-08-23", "2026-08-26")  # 결손 이틀을 덮는다
    assert meta["recovered_days"] == 2


def test_no_watermark_falls_back_loudly(tmp_path):
    # WHY: 부재를 fail-loud 로 죽이면 스케줄러 retry=0 이라 그날 수집 자체가 죽는다 —
    #      회수 장치의 실패가 막으려던 결손을 만든다. 폴백하되 window_source 로 드러낸다.
    #      콜드스타트·컷오버 직후가 정상적으로 이 경로다(직전 1분 레인 커버 구간을
    #      대량 재수집하지 않는 효과도 이 폴백이 준다).
    actual, meta = dw.resolve_window(
        LocalStorage(tmp_path), enabled=True, explicit=False,
        scheduled_window=("2026-08-25", "2026-08-26"),
        today_kst=TODAY_KST, today_utc=TODAY_UTC)
    assert actual == ("2026-08-25", "2026-08-26")
    assert meta["window_source"] == "fallback_no_watermark"


def test_lookup_error_falls_back_loudly(tmp_path):
    # WHY: 스토리지 장애(LIST/GET 실패)도 부재와 같은 방향 — 수집은 살리고 사유는 가른다
    #      (부재=정상 콜드스타트일 수 있음, 조회 실패=인프라 이상 — 사후 진단이 다르다).
    class Boom(LocalStorage):
        def list_keys(self, prefix):
            raise RuntimeError("s3 down")

    actual, meta = dw.resolve_window(
        Boom(tmp_path), enabled=True, explicit=False,
        scheduled_window=("2026-08-25", "2026-08-26"),
        today_kst=TODAY_KST, today_utc=TODAY_UTC)
    assert actual == ("2026-08-25", "2026-08-26")
    assert meta["window_source"] == "fallback_lookup_error"


def test_stale_watermark_within_probe_is_not_applied(tmp_path):
    # WHY: 탐색 범위 안의 로그라도 window_to 는 상한보다 과거일 수 있다(오늘 돌린 옛 구간
    #      백필이 유일한 자격 로그인 경우). 상한 초과 창은 페이지 절단(max_pages)을 부르니
    #      적용하지 않되, 그 사실을 숨기지 않는다(Rule 12) — stale_watermark 로 남긴다.
    storage = LocalStorage(tmp_path)
    _put_log(storage, "2026-08-26", "r_backfill", window_to="2026-07-01")
    actual, meta = dw.resolve_window(
        storage, enabled=True, explicit=False,
        scheduled_window=("2026-08-25", "2026-08-26"),
        today_kst=TODAY_KST, today_utc=TODAY_UTC)
    assert actual == ("2026-08-25", "2026-08-26")
    assert meta["window_source"] == "fallback_no_watermark"
    assert meta["stale_watermark"] == "2026-07-01"


def test_explicit_window_always_wins(tmp_path):
    # WHY: 운영자 --from/--to 는 갭을 메우려는 명시 의도다 — 워터마크가 덮어쓰면 백필이
    #      조용히 다른 창을 긁고 exit 0, 소급된 줄 착각한다(iNAV 거부 가드와 같은 성질).
    storage = LocalStorage(tmp_path)
    _put_log(storage, "2026-08-25", "r1", window_to="2026-08-25")
    actual, meta = dw.resolve_window(
        storage, enabled=True, explicit=True,
        scheduled_window=("2026-01-01", "2026-01-31"),
        today_kst=TODAY_KST, today_utc=TODAY_UTC)
    assert actual == ("2026-01-01", "2026-01-31")
    assert meta["window_source"] == "cli"


def test_shadow_mode_only_observes(tmp_path):
    # WHY(완료 조건 '그림자 대조'): 회수 대상은 마감일에만 생겨, 그냥 배포하면 두 달 반
    #      동안 "잘 도는 것"과 "조용히 안 도는 것"이 구분되지 않는다. 그림자는 창을
    #      바꾸지 않고 계산-실제 대조를 매 런 로그에 남겨 배포 직후부터 관측 가능하게 한다.
    storage = LocalStorage(tmp_path)
    _put_log(storage, "2026-08-25", "r1", window_to="2026-08-25")
    actual, meta = dw.resolve_window(
        storage, enabled=False, explicit=False,
        scheduled_window=("2026-08-25", "2026-08-26"),
        today_kst=TODAY_KST, today_utc=TODAY_UTC)
    assert actual == ("2026-08-25", "2026-08-26")  # 창은 종전 그대로
    assert meta["window_source"] == "default"
    shadow = meta["watermark_shadow"]
    assert shadow["source"] == "watermark"
    assert (shadow["window_from"], shadow["window_to"]) == ("2026-08-25", "2026-08-26")
    assert shadow["matches_actual"] is True

    # 불일치가 드러나는지 — 계산이 늘 실제와 같다고 단언하면 대조 자체가 무의미하다.
    _put_log(storage, "2026-08-24", "r0", window_to="2026-08-23")
    storage2 = LocalStorage(tmp_path / "lake2")
    _put_log(storage2, "2026-08-23", "r1", window_to="2026-08-23")
    _, meta2 = dw.resolve_window(
        storage2, enabled=False, explicit=False,
        scheduled_window=("2026-08-25", "2026-08-26"),
        today_kst=TODAY_KST, today_utc=TODAY_UTC)
    assert meta2["watermark_shadow"]["matches_actual"] is False
