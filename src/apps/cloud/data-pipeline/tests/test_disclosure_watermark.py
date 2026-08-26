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
    # window_source 기본이 "watermark"(정상 스케줄 런) — 부재는 PR2 이전 로그라 자격이
    # 없으므로, 부재를 검증하는 테스트가 명시적으로 빼고 만든다.
    payload = {"job_name": "ingest_raw_disclosure", "status": "success",
               "list_truncated": False, "ingest_lane": "batch",
               "window_source": "watermark", **fields}
    key = collection_log_key("dart", "disclosures", started_date, run_id)
    storage.put_bytes(key, json.dumps(payload).encode("utf-8"))


def test_watermark_is_max_window_to_not_latest_run(tmp_path):
    # WHY: "가장 최근 런"으로 잡으면 오늘 돌린 **과거 구간 수동 백필**(window_to 가 과거)이
    #      워터마크를 뒤로 당겨, 다음 스케줄 런이 이미 완주한 구간을 통째로 재수집한다.
    #      max(window_to) 는 그 오염이 구성상 불가능하다.
    storage = LocalStorage(tmp_path)
    _put_log(storage, "2026-08-25", "run_a", window_to="2026-08-25")
    _put_log(storage, "2026-08-26", "run_backfill", window_to="2026-07-01")  # 더 최근 런
    assert dw.find_watermark(storage, today_utc=TODAY_UTC, today_kst=TODAY_KST) == "2026-08-25"


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
    assert dw.find_watermark(storage, today_utc=TODAY_UTC, today_kst=TODAY_KST) is None


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
    assert dw.find_watermark(storage, today_utc=TODAY_UTC, today_kst=TODAY_KST) is None
    # partial(본문만 실패, 목록 완주)은 자격이 있다 — 전진 안 하면 남의 회사 malformed 행
    # 1건(영구 재현)으로 워터마크가 영구 정지한다. 본문 재시도는 당일 재독이 준다.
    _put_log(storage, "2026-08-25", "r_partial", status="partial", window_to="2026-08-25")
    assert dw.find_watermark(storage, today_utc=TODAY_UTC, today_kst=TODAY_KST) == "2026-08-25"


def test_probe_range_is_bounded(tmp_path):
    # WHY: 탐색 범위(=창 상한)를 벗어난 로그는 안 본다 — 그보다 긴 공백은 페이지 절단을
    #      부르는 창이라 자동 회수 대상이 아니다(폴백 + 수동 백필 소관).
    storage = LocalStorage(tmp_path)
    _put_log(storage, "2026-08-10", "r1", window_to="2026-08-10")  # 16일 전 — 범위 밖
    assert dw.find_watermark(storage, today_utc=TODAY_UTC, today_kst=TODAY_KST) is None


def test_corrupt_log_is_skipped_not_fatal(tmp_path):
    # WHY: 깨진 로그 하나가 조회 전체를 죽이면 폴백이 상시화된다 — 그 로그만 자격 없음.
    storage = LocalStorage(tmp_path)
    storage.put_bytes(collection_log_key("dart", "disclosures", "2026-08-26", "r_bad"),
                      b"not json")
    _put_log(storage, "2026-08-25", "r_ok", window_to="2026-08-25")
    assert dw.find_watermark(storage, today_utc=TODAY_UTC, today_kst=TODAY_KST) == "2026-08-25"


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


def test_lookup_error_fallback_run_log_is_not_eligible(tmp_path):
    # WHY: 조회 실패 폴백 런은 자기 앞의 체인을 못 본 채 좁은 기본창을 돌았다 — 그 성공
    #      로그가 워터마크가 되면 그때 못 본 결손(직전 워터마크~기본창 사이)이 모든 자동
    #      창 밖으로 밀려 영구 확정된다. 반대로 콜드스타트 폴백(fallback_no_watermark)은
    #      앞 체인이 없어 씨앗으로 정당하다 — 빼면 워터마크가 영영 서지 못한다.
    storage = LocalStorage(tmp_path)
    _put_log(storage, "2026-08-23", "r_chain", window_to="2026-08-23")
    _put_log(storage, "2026-08-25", "r_fb", window_to="2026-08-25",
             window_source="fallback_lookup_error")
    assert dw.find_watermark(storage, today_utc=TODAY_UTC, today_kst=TODAY_KST) == "2026-08-23"
    _put_log(storage, "2026-08-25", "r_seed", window_to="2026-08-24",
             window_source="fallback_no_watermark")
    assert dw.find_watermark(storage, today_utc=TODAY_UTC, today_kst=TODAY_KST) == "2026-08-24"


def test_malformed_candidates_do_not_break_or_win(tmp_path):
    # WHY: 자격 필드를 갖춘 로그도 window_to 가 비날짜이거나 로그 자체가 비객체 JSON 일 수
    #      있다 — 하나가 조회를 죽이면(예외가 resolve_window 폴백으로 새면) 정상 후보까지
    #      버려지고, 미래 window_to(운영자 미래 백필)가 문자열 max 를 이기면 창이 [오늘,
    #      오늘]로 좁아져 어제의 늦은 노출 꼬리를 놓친다. 전부 그 후보만 제외돼야 한다.
    storage = LocalStorage(tmp_path)
    storage.put_bytes(collection_log_key("dart", "disclosures", "2026-08-26", "r_list"),
                      b"[]")  # 유효 JSON 이지만 비객체
    _put_log(storage, "2026-08-26", "r_baddate", window_to="not-a-date")
    _put_log(storage, "2026-08-26", "r_future", window_to="2026-08-30")  # 미래 백필
    _put_log(storage, "2026-08-25", "r_ok", window_to="2026-08-25")
    assert dw.find_watermark(storage, today_utc=TODAY_UTC, today_kst=TODAY_KST) == "2026-08-25"


def test_cli_run_extends_only_when_contiguous(tmp_path):
    # WHY: 비연속 수동 창(--from 25 --to 25 단발)이 워터마크를 점프시키면 직전 워터마크와의
    #      사이(20~24)가 자동 회수에서 영영 빠진다 — cli 런은 창 시작이 기준에 닿을 때만
    #      연장 자격이 있다. 연속 백필(체인에 닿는 창)은 정당하게 전진시킨다.
    storage = LocalStorage(tmp_path)
    _put_log(storage, "2026-08-19", "r_chain", window_to="2026-08-19")
    _put_log(storage, "2026-08-25", "r_manual", window_source="cli",
             window_from="2026-08-25", window_to="2026-08-25")  # 20~24 미커버
    assert dw.find_watermark(storage, today_utc=TODAY_UTC, today_kst=TODAY_KST) == "2026-08-19"
    _put_log(storage, "2026-08-25", "r_bridge", window_source="cli",
             window_from="2026-08-20", window_to="2026-08-24")  # 체인에 닿는다
    # 사다리: bridge(20~24)가 기준 19 에 닿아 24 로, 그러면 manual(25)도 닿아 25 로.
    assert dw.find_watermark(storage, today_utc=TODAY_UTC, today_kst=TODAY_KST) == "2026-08-25"


def test_cli_only_history_cannot_seed_the_chain(tmp_path):
    # WHY: cli 런만으론 체인을 못 연다 — 씨앗은 "체인이 없음을 확인한" 스케줄 런
    #      (fallback_no_watermark)이 심는다. 수동 단발이 씨앗이 되면 그 앞 결손이 접힌다.
    storage = LocalStorage(tmp_path)
    _put_log(storage, "2026-08-25", "r_manual", window_source="cli",
             window_from="2026-08-25", window_to="2026-08-25")
    assert dw.find_watermark(storage, today_utc=TODAY_UTC, today_kst=TODAY_KST) is None


def test_get_failure_propagates_as_lookup_error(tmp_path):
    # WHY: GET 인프라 장애를 파싱 실패와 섞어 삼키면 "워터마크 없음"이 되고, 그 폴백 런의
    #      로그가 콜드스타트 씨앗으로 오인돼 장애 당시 못 본 결손이 접힌다 — GET 실패는
    #      전파돼 fallback_lookup_error(자격 없는 라벨)로 기록돼야 한다.
    class DenyGet(LocalStorage):
        def get_bytes(self, key):
            raise PermissionError("AccessDenied")

    storage = DenyGet(tmp_path)
    _put_log(LocalStorage(tmp_path), "2026-08-25", "r1", window_to="2026-08-25")
    actual, meta = dw.resolve_window(
        storage, enabled=True, explicit=False,
        scheduled_window=("2026-08-25", "2026-08-26"),
        today_kst=TODAY_KST, today_utc=TODAY_UTC)
    assert actual == ("2026-08-25", "2026-08-26")
    assert meta["window_source"] == "fallback_lookup_error"


def test_unknown_window_source_label_is_not_eligible(tmp_path):
    # WHY: 자격은 화이트리스트다 — fallback_lookup_error 를 지명해 빼는 차단목록이면
    #      오기·드리프트로 변형된 라벨("fallback_lookup_eror" 등)이 자격을 얻어, 체인을
    #      못 본 런이 결손을 접는 경로가 철자 하나로 되살아난다.
    storage = LocalStorage(tmp_path)
    _put_log(storage, "2026-08-25", "r_typo", window_to="2026-08-25",
             window_source="fallback_lookup_eror")
    assert dw.find_watermark(storage, today_utc=TODAY_UTC, today_kst=TODAY_KST) is None


def test_pre_pr2_log_without_window_source_is_not_eligible(tmp_path):
    # WHY: PR2 이전 로그(window_source 부재)는 스케줄 런과 수동 단발 백필이 같은 무라벨이라
    #      연속성을 판별할 수 없다 — 자격을 주면 배포 전 단발 백필([25,25])이 체인을
    #      점프시켜 그 앞 결손이 자동 회수에서 영영 빠진다. 부재 = 자격 없음
    #      (`ingest_lane` 부재 = PR1 이전과 동형 규칙).
    storage = LocalStorage(tmp_path)
    legacy = {"job_name": "ingest_raw_disclosure", "status": "success",
              "list_truncated": False, "ingest_lane": "batch",
              "window_to": "2026-08-25"}  # window_source 없음
    storage.put_bytes(collection_log_key("dart", "disclosures", "2026-08-25", "r_pre_pr2"),
                      json.dumps(legacy).encode("utf-8"))
    assert dw.find_watermark(storage, today_utc=TODAY_UTC, today_kst=TODAY_KST) is None


def test_shadow_lookup_error_run_is_not_eligible(tmp_path):
    # WHY: 그림자 런의 조회 실패는 window_source 가 아니라 watermark_shadow.source 에
    #      남는다 — 겉 라벨만 거르면 그 런의 로그가 자격을 얻어, 활성 런과 같은 이유
    #      (체인을 못 본 채 돈 런)로 결손을 접는다.
    storage = LocalStorage(tmp_path)
    _put_log(storage, "2026-08-25", "r_shadow_err", window_to="2026-08-25",
             window_source="default",
             watermark_shadow={"source": "fallback_lookup_error", "window_from": None,
                               "window_to": None, "matches_actual": False})
    assert dw.find_watermark(storage, today_utc=TODAY_UTC, today_kst=TODAY_KST) is None


def test_mdw_keys_are_skipped_without_get(tmp_path):
    # WHY: 전이기엔 탐색 범위에 1분 레인 로그가 하루 최대 720개 남는다(실측 4,661개/11일,
    #      전량 GET ≈ 5분+) — 원장 deadline(1200s)의 절반을 조회가 태운다. mdw 접두는
    #      `_run_id_for` 가 결정적으로 붙이므로 GET 없이 걸러도 판정이 같다(음성 프리필터 —
    #      틀려도 후보를 잃고 폴백하는 안전 방향). GET 횟수로 프리필터의 실재를 단언한다.
    class CountGet(LocalStorage):
        def __init__(self, root):
            super().__init__(root)
            self.gets = 0

        def get_bytes(self, key):
            self.gets += 1
            return super().get_bytes(key)

    storage = CountGet(tmp_path)
    _put_log(storage, "2026-08-26", "mdw_s1_0900_1", ingest_lane="minute",
             window_to="2026-08-26")
    _put_log(storage, "2026-08-25", "r_ok", window_to="2026-08-25")
    assert dw.find_watermark(storage, today_utc=TODAY_UTC, today_kst=TODAY_KST) == "2026-08-25"
    assert storage.gets == 1  # mdw 키는 GET 자체가 없다
