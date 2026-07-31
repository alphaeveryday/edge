"""보고서 백필 — **분류축이 레코드에 실리고, 하루가 재개 단위다.**

여기서 지키는 것.

    분류   미 정보당국 축(kind·source_class·Admiralty 신뢰도·PMESII 주제)이 컬럼으로 실린다
    발표일 available_at 은 발표일이다. 크롤 시각(fetched_at)과 섞으면 과거가 전부 늦게
           알려진 것으로 취급된다
    재개   하루 단위. 더 잘게 쪼개면 같은 하루가 두 run 에 반쯤 쌓인다
    격리   source=korea_kr · dataset=reports · run_id=backfill-reports-* · draft 접두사

망은 타지 않는다 - 목록 페이지 가져오기(`fetcher`)를 주입한다.
"""

from __future__ import annotations

import json

import pytest

from data_pipeline.backfill.classification import (
    CREDIBILITY_UNASSESSED,
    KIND_CURRENT,
    ReportClass,
)
from data_pipeline.backfill.manifest import Manifest
from data_pipeline.backfill.reports import (
    DATASET,
    SOURCE,
    _ROW,
    backfill_reports,
    days_between,
)
from data_pipeline.lake import LocalStorage

LIST_HTML = """
<a href="/briefing/pressReleaseView.do?newsId=156700001&x=1">
  <span class="text"><strong>2026년 상반기 수출 실적 발표   </strong></span></a>
<a href="/briefing/pressReleaseView.do?newsId=156700002">
  <span class="text"><strong>반도체 특별법 시행령 개정</strong></span></a>
"""


def _fake_fetcher(rows_by_day):
    def f(day, *, sleep=0.0):
        if day not in rows_by_day:
            raise OSError(f"타임아웃 {day}")
        return [dict(r, published_at=day, available_at=day) for r in rows_by_day[day]]
    return f


def _rows(storage, key):
    body = storage.get_bytes(key).decode("utf-8")
    return [json.loads(x) for x in body.splitlines() if x]


def test_the_list_regex_reads_real_markup():
    """포팅한 것은 추출 규칙 하나다 - 페이지 구조 지식이라 다시 만들 이유가 없다."""
    got = _ROW.findall(LIST_HTML)

    assert [nid for nid, _ in got] == ["156700001", "156700002"]
    assert got[0][1].strip() == "2026년 상반기 수출 실적 발표"


def test_days_are_walked_most_recent_first():
    """중단돼도 쓸 만한 것이 남아야 한다 - 최근이 값이 크다."""
    assert days_between("2026-07-01", "2026-07-04") == [
        "2026-07-04", "2026-07-03", "2026-07-02", "2026-07-01"]
    assert days_between("2026-07-04", "2026-07-01")[0] == "2026-07-04"  # 순서 뒤집혀도


def test_classification_columns_ride_on_every_record(tmp_path):
    """산업 하나로 자르면 국가·출처·신뢰도가 사라진다 - 축이 레코드에 있어야 한다."""
    st = LocalStorage(tmp_path)
    f = _fake_fetcher({"2026-07-01": [{"report_id": "korea_kr:1", "title": "t",
                                       "url": "u", "source_id": "1"}]})

    log = backfill_reports(st, start="2026-07-01", end="2026-07-01",
                           ingest_date="2026-07-31", fetcher=f)
    row = _rows(st, f"{log['prefix']}/part-2026-07-01.ndjson")[0]

    assert row["kind"] == KIND_CURRENT and row["source_class"] == "GOV"
    assert row["report_type"] == "PRESS_RELEASE" and row["unit"] == "POLICY"
    assert row["cadence"] == "AD_HOC"
    assert row["geo"] == "KR" and row["region"] == "APAC"
    assert row["domain"] == "POLITICAL"
    assert row["reliability"] == f"B{CREDIBILITY_UNASSESSED}"   # GOV → B, 미확증 → 6
    assert row["license"] == "PUBLIC"
    assert row["available_at"] == "2026-07-01" and row["fetched_at"] > "2026"


def test_reports_write_under_their_own_source_and_dataset(tmp_path):
    """재무 백필·포워드 폴러와 파티션이 겹치지 않는다."""
    st = LocalStorage(tmp_path)
    f = _fake_fetcher({"2026-07-01": []})

    log = backfill_reports(st, start="2026-07-01", end="2026-07-01",
                           ingest_date="2026-07-31", fetcher=f, key_prefix="draft")

    assert log["prefix"] == (
        "draft/raw/source=korea_kr/dataset=reports/market=KR"
        "/ingest_date=2026-07-31/run_id=backfill-reports-korea_kr-20260731")
    assert st.list_keys("raw/") == []
    assert log["source"] == SOURCE and log["dataset"] == DATASET


def test_an_empty_day_is_recorded_as_empty_not_missing(tmp_path):
    """빈 날(공휴일)과 못 받은 날은 다르다. 같게 두면 재개가 영원히 다시 시도한다."""
    st = LocalStorage(tmp_path)
    f = _fake_fetcher({"2026-07-01": [], "2026-07-02": [
        {"report_id": "korea_kr:2", "title": "t", "url": "u", "source_id": "2"}]})

    log = backfill_reports(st, start="2026-07-01", end="2026-07-02",
                           ingest_date="2026-07-31", fetcher=f)

    assert log["empty_days"] == 1 and log["failed"] == 0 and log["rows"] == 1
    again = backfill_reports(st, start="2026-07-01", end="2026-07-02",
                             ingest_date="2026-07-31", fetcher=f)
    assert again["skipped"] == 2 and again["fetched"] == 0


def test_a_failed_day_is_retried_on_resume(tmp_path):
    """실패는 건너뜀이 아니다 - 다음 실행에서 다시 시도해야 결손이 메워진다."""
    st = LocalStorage(tmp_path)
    ok = {"2026-07-02": [{"report_id": "korea_kr:2", "title": "t", "url": "u",
                          "source_id": "2"}]}

    first = backfill_reports(st, start="2026-07-01", end="2026-07-02",
                             ingest_date="2026-07-31", fetcher=_fake_fetcher(ok))
    assert first["failed"] == 1

    ok["2026-07-01"] = [{"report_id": "korea_kr:1", "title": "t2", "url": "u",
                         "source_id": "1"}]
    second = backfill_reports(st, start="2026-07-01", end="2026-07-02",
                              ingest_date="2026-07-31", fetcher=_fake_fetcher(ok))

    assert second["fetched"] == 1 and second["failed"] == 0
    man = Manifest.from_bytes(st.get_bytes(second["manifest"]))
    assert man.failed == [] and len(man.ok) == 2


def test_the_classification_vocabulary_is_enforced():
    """어휘 밖 값이 조용히 들어가면 나중에 조회 규칙이 사람의 기억에 남는다."""
    with pytest.raises(ValueError, match="kind"):
        ReportClass(kind="rumor", source_class="GOV")
    with pytest.raises(ValueError, match="source_class"):
        ReportClass(kind=KIND_CURRENT, source_class="TWITTER")
    with pytest.raises(ValueError, match="domain"):
        ReportClass(kind=KIND_CURRENT, source_class="GOV", domain="VIBES")
    with pytest.raises(ValueError, match="unit"):
        ReportClass(kind=KIND_CURRENT, source_class="GOV", unit="VIBE")


def test_a_report_type_must_belong_to_its_kind():
    """**계층 검사.** 어휘만 검사하면 `current` 에 `AMENDMENT` 가 붙는 것을 못 막는다.

    정정공시(AMENDMENT)는 경고 계열이다. 시황으로 분류되면 반증조건을 찾을 때 안 보인다 -
    분류 실수가 조회 실패로 나타나고, 그 원인을 사후에 찾기 어렵다.
    """
    with pytest.raises(ValueError, match="하위가 아니다"):
        ReportClass(kind=KIND_CURRENT, source_class="FILING",
                    report_type="AMENDMENT")

    ok = ReportClass(kind="warning", source_class="FILING", report_type="AMENDMENT")
    assert ok.as_columns()["report_type"] == "AMENDMENT"

    # 종별을 안 적는 것은 허용한다 - 상위만으로 쌓기 시작할 수 있어야 백필이 막히지 않는다.
    assert ReportClass(kind=KIND_CURRENT, source_class="GOV").report_type == ""


def test_region_is_derived_not_declared():
    """수집기가 권역을 따로 선언하면 국가와 어긋난 값이 들어간다."""
    assert ReportClass(kind="basic", source_class="FILING", geo="KR").region == "APAC"
    assert ReportClass(kind="basic", source_class="FILING", geo="US").region == "AMER"
    assert ReportClass(kind="basic", source_class="FILING", geo="ZZ").region == "OTHER"


def test_every_kind_has_sub_types_so_nothing_stays_lumped():
    """최상위만 있으면 `current` 안에서 공시·보도자료·뉴스가 한 덩어리가 된다.

    셋은 신뢰도·갱신 주기·파싱 방식이 다르고, **사슬에서 쓰이는 자리가 다르다** -
    공시는 사건 그 자체, 보도자료는 정책 경로의 입력, 뉴스는 반응의 관측이다.
    """
    from data_pipeline.backfill.classification import KINDS, REPORT_TYPES

    assert set(REPORT_TYPES) == set(KINDS)
    assert all(len(v) >= 5 for v in REPORT_TYPES.values())
    assert len({t for ts in REPORT_TYPES.values() for t in ts}) == sum(
        len(v) for v in REPORT_TYPES.values()), "종별 이름이 두 kind 에 겹친다"


def test_reliability_separates_source_grade_from_corroboration():
    """같은 수치라도 A1 과 D4 는 사슬에서 구간 폭이 달라야 한다."""
    filing = ReportClass(kind="basic", source_class="FILING", credibility="1")
    press = ReportClass(kind="current", source_class="PRESS")

    assert filing.reliability == "A1"
    assert press.reliability == "D6"          # 언론 + 미확증
