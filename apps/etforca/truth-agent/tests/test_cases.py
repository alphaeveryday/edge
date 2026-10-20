"""사건 계약 규칙. 각 규칙은 공개되는 판정의 신뢰를 지키는 장치라서, 규칙이 풀리면 테스트가 실패해야 한다."""
from copy import deepcopy
from datetime import date
import json
from pathlib import Path

import pytest

from edge_truth_agent.cases import CaseError, load_dir, problems, validate

SEED = Path(__file__).resolve().parents[1] / "seed"
TODAY = date(2026, 10, 20)


def seed(name: str) -> dict:
    return json.loads((SEED / f"{name}.json").read_text(encoding="utf-8"))


def test_seed_cases_satisfy_contract():
    # 처음 게시하는 실제 사건이므로 하나라도 계약을 어기면 배포하지 않는다.
    assert len(load_dir(SEED)) == 4


def test_status_change_requires_official_or_disclosure_source():
    # 판정은 공식 판단·법정공시가 있을 때만 바뀐다. 보도만으로 '밝혀짐'을 붙이면 안 된다.
    case = seed("geumyang-monlaa")
    case["resolution"]["sourceId"] = "news-mk-11153329"
    found = problems(case, today=TODAY)
    assert any("공식 판단(official)" in line for line in found)


def test_confirmed_requires_matching_behavior_verdict():
    # '밝혀짐'인데 유형별 판정에 확인이 하나도 없으면 공개 문장과 판정 근거가 어긋난다.
    case = seed("geumyang-monlaa")
    case["behaviors"][0]["verdict"] = "suspected"
    assert any("판정이 'confirmed'" in line for line in problems(case, today=TODAY))


def test_pending_case_cannot_carry_resolution():
    # 판단 중 사건에 확인일이 있으면 화면이 판정 난 사건처럼 보인다.
    case = seed("hybe-ipo-2019")
    case["resolvedAt"] = "2026-10-01"
    assert any("판단 중(pending)" in line for line in problems(case, today=TODAY))


def test_every_cited_source_must_exist():
    # 출처 목록에 없는 id 를 인용하면 독자가 근거를 따라갈 수 없다(지어낸 인용 차단).
    case = seed("hybe-ipo-2019")
    case["body"][1]["sourceIds"] = ["news-made-up"]
    found = problems(case, today=TODAY)
    assert any("'news-made-up' 는 sources 에 없는 출처" in line for line in found)


def test_headline_rejects_absolute_accusation_words():
    # 공개 문장은 확인 수준을 넘어 단정하지 않는다(방법론 §8).
    case = seed("hybe-ipo-2019")
    case["headline"][0] = "하이브는 기존 주주에게 거짓말을 했다는 게 경찰 발표입니다."
    assert any("'거짓말'" in line for line in problems(case, today=TODAY))


def test_future_dates_are_rejected():
    # 아직 오지 않은 날의 공식 판단은 존재할 수 없다.
    case = seed("geumyang-monlaa")
    case["resolution"]["at"] = case["resolvedAt"] = "2027-01-01"
    assert any("미래 날짜" in line for line in problems(case, today=TODAY))


def test_shape_errors_name_the_field():
    # 에이전트가 오류 위치만 보고 고칠 수 있어야 한다.
    case = seed("hybe-ipo-2019")
    case["behaviors"][0]["type"] = "lie"
    found = problems(case, today=TODAY)
    assert found and found[0].startswith("behaviors[0].type")


def test_file_name_must_match_id(tmp_path):
    case = seed("hybe-ipo-2019")
    (tmp_path / "other.json").write_text(json.dumps(case, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(CaseError, match="파일 이름"):
        load_dir(tmp_path)


@pytest.mark.parametrize("at", ["2026-10-20", "2020-01-01"])
def test_resolution_date_must_be_its_source_date(at):
    # 늦게 적으면 '먼저 기록한 사건'처럼 보여 적중률이 부풀고, 이르게 적으면 근거가 나오기 전에
    # 확인됐다는 불가능한 날짜가 공개된다.
    case = seed("geumyang-monlaa")
    case["resolution"]["at"] = case["resolvedAt"] = at
    assert any("근거 출처 발행일" in line for line in problems(case, today=TODAY))


@pytest.mark.parametrize("make", [lambda d: d / "missing", lambda d: d])
def test_missing_or_empty_case_folder_fails(tmp_path, make):
    # '0건 통과'로 빈 공개 데이터를 만들면 게시 단계의 동기화가 기존 사건을 모두 지운다.
    with pytest.raises(CaseError, match="사건 파일이 없습니다"):
        load_dir(make(tmp_path))


def test_party_rebuttal_disclosure_cannot_refute_a_case():
    # 당사자 해명공시는 반론이다. 그것만으로 '해소'를 붙이면 당사자 주장을 사실로 바꾸게 된다(방법론 §5·§8).
    case = seed("hybe-ipo-2019")
    case.update(status="refuted", resolvedAt="2024-11-29",
                resolution={"sourceId": "disclosure-spn-295677", "at": "2024-11-29", "note": "회사 해명공시"})
    case["behaviors"][0]["verdict"] = case["levels"]["behavior"] = "refuted"
    found = problems(case, today=TODAY)
    assert any("해소(refuted)는 공식 판단(official)" in line for line in found)


def test_resolution_source_cited_as_rebuttal_is_rejected():
    case = seed("hybe-ipo-2019")
    case["sources"][2] |= {"kind": "official", "url": "https://www.fsc.go.kr/no010101/1"}
    case.update(status="confirmed", resolvedAt="2024-11-29",
                resolution={"sourceId": "disclosure-spn-295677", "at": "2024-11-29", "note": "근거 메모"})
    case["behaviors"][0]["verdict"] = case["levels"]["behavior"] = "confirmed"
    assert any("당사자 반론으로 인용된 출처" in line for line in problems(case, today=TODAY))


def test_self_declared_official_kind_needs_an_official_address():
    # 출처 종류는 파일이 스스로 적는 값이라, 판정 근거만큼은 기관 주소로 다시 확인한다.
    case = seed("geumyang-monlaa")
    case["sources"][0]["url"] = "https://blog.example.com/fsc-copy"
    assert any("공식 기관·공시 도메인이 아닙니다" in line for line in problems(case, today=TODAY))


@pytest.mark.parametrize("value", ["20261007", "2026-W41-3", "2026-02-31"])
def test_dates_must_be_exact_calendar_iso_days(value):
    # 화면은 날짜를 문자열로 비교한다. 형식이 섞이면 순서와 적중 판정이 틀어진다.
    case = seed("hybe-ipo-2019")
    case["flaggedAt"] = value
    assert any("날짜가 아닙니다" in line for line in problems(case, today=TODAY))


def test_non_finite_impact_is_rejected():
    # NaN 이 공개 JSON 에 실리면 브라우저가 파일 전체를 못 읽어 사이트가 빈다.
    case = seed("hybe-ipo-2019")
    case["impact"] = float("nan")
    assert any(line.startswith("impact") for line in problems(case, today=TODAY))


def test_behavior_verdict_needs_a_cited_source():
    case = seed("hybe-ipo-2019")
    case["behaviors"][0]["sourceIds"] = []
    assert any(line.startswith("behaviors[0].sourceIds") for line in problems(case, today=TODAY))


def test_resolution_on_the_source_day_is_allowed():
    # 경계: 판단이 나온 그날 발행된 원문은 정상 근거다(> 를 >= 로 바꾸면 이 테스트가 깨진다).
    case = seed("geumyang-monlaa")
    assert case["resolution"]["at"] == case["sources"][0]["publishedAt"]
    assert problems(case, today=TODAY) == []


@pytest.mark.parametrize("field", ["updatedAt", "related"])
def test_timestamps_need_a_timezone(field):
    case = seed("hybe-ipo-2019")
    if field == "updatedAt":
        case["updatedAt"] = "2026-10-20T09:00:00"
    else:
        case["related"] = [{"sourceId": "news-donga-20260903", "addedAt": "2026-10-20T09:00:00"}]
    assert any("시간대" in line for line in problems(case, today=TODAY))


@pytest.mark.parametrize("url, kind", [
    ("https://WWW.FSC.GO.KR/no010101/1", "official"),
    ("https://www.fsc.go.kr:443/x", "official"),
    ("https://dart.fss.or.kr/dsaf001/main.do?rcpNo=1", "disclosure"),
    ("https://fsc.go.kr.evil.com/x", None),
    ("https://data.krx.co.kr/x", None),  # 시장 데이터 사이트는 공식 판단이 아니다
    ("https://evil.com\\@fsc.go.kr/x", None),  # 브라우저는 evil.com 으로 읽는다
    ("https://[fsc.go.kr/x", None),  # 해석 불가한 주소는 오류로 멈추지 않고 공식 출처가 아니다
])
def test_host_kind_matches_only_real_official_hosts(url, kind):
    from edge_truth_agent.cases import host_kind
    assert host_kind(url) == kind


def test_malformed_resolution_url_is_reported_not_crashed():
    # 사건 하나의 깨진 주소가 예외로 검사 전체를 멈추면 다른 사건 갱신까지 게시되지 못한다.
    case = seed("geumyang-monlaa")
    src = next(s for s in case["sources"] if s["id"] == case["resolution"]["sourceId"])
    src["url"] = "https://[fsc.go.kr/x"
    assert any("공식 기관·공시 도메인이 아닙니다" in line for line in problems(case, today=TODAY))


@pytest.mark.parametrize("url", ["https://[x/", "https://?x"])
def test_every_source_url_needs_a_host(url):
    # 판정 근거가 아닌 출처라도 깨진 주소는 공개 화면에 죽은 근거 링크로 나간다.
    case = seed("hybe-ipo-2019")
    case["sources"][0]["url"] = url
    assert any(line.startswith("sources[0].url") for line in problems(case, today=TODAY))


def test_backslash_url_is_rejected_by_contract():
    case = seed("geumyang-monlaa")
    case["sources"][0]["url"] = "https://evil.com\\@fsc.go.kr/no010101/87894"
    assert any(line.startswith("sources[0].url") for line in problems(case, today=TODAY))
    # 정규식 '$' 는 끝 줄바꿈 하나를 통과시킨다 — 화면에 그대로 링크되는 주소라 막아야 한다.
    case["sources"][0]["url"] = "https://www.fsc.go.kr/no010101/87894\n"
    assert any(line.startswith("sources[0].url") for line in problems(case, today=TODAY))


@pytest.mark.parametrize("field", ["updatedAt", "related"])
def test_future_timestamps_are_rejected(field):
    # 미래 갱신 시각·관련 보도 시각이 공개되면 사건 기록의 시간 순서가 깨진다.
    case = seed("hybe-ipo-2019")
    if field == "updatedAt":
        case["updatedAt"] = "2027-01-01T09:00:00+09:00"
    else:
        case["related"] = [{"sourceId": case["sources"][0]["id"], "addedAt": "2027-01-01T09:00:00+09:00"}]
    assert any("미래 시각" in line for line in problems(case, today=TODAY))


@pytest.mark.parametrize("name", ["hybe-ipo-2019", "geumyang-monlaa"])
def test_update_cannot_precede_any_date_in_the_record(name):
    # '기록 10-20 · 갱신 2020-01-01'·확인일보다 이른 갱신 같은 불가능한 순서가 공개되면 안 된다.
    case = seed(name)
    case["updatedAt"] = "2026-10-01T09:00:00+09:00"
    assert any(line.startswith("updatedAt") and "가장 늦은 날짜" in line for line in problems(case, today=TODAY))


def test_verdict_behavior_must_cite_the_resolution_source():
    # 공식 근거가 유형 판정에 이어지지 않으면 화면에는 보도만 보고 '확인'을 붙인 것처럼 보인다.
    case = seed("geumyang-monlaa")
    for b in case["behaviors"]:
        b["sourceIds"] = [s for s in b["sourceIds"] if s != case["resolution"]["sourceId"]] or ["news-mk-11153329"]
    assert any("판정 근거" in line and line.startswith("behaviors") for line in problems(case, today=TODAY))


def test_source_cannot_be_accessed_before_it_was_published():
    # 접근일을 발행일 앞으로 당기면 기록일도 함께 당겨져 '먼저 기록한 사건'으로 집계된다.
    case = seed("geumyang-monlaa")
    case["sources"][0]["accessedAt"] = "2026-10-01"
    assert any("발행일" in line and line.startswith("sources[0].accessedAt") for line in problems(case, today=TODAY))


def test_flagged_date_cannot_precede_first_source_access():
    # 출처를 보기 전 날짜로 기록하면 '먼저 기록한 사건'으로 보여 적중률이 부풀려진다.
    case = seed("geumyang-monlaa")
    case["flaggedAt"] = "2020-01-01"
    assert any("가장 이른 출처 접근일" in line for line in problems(case, today=TODAY))


def test_broken_json_file_is_reported_by_name(tmp_path):
    # 한 파일이 깨져도 어느 파일인지 알려야 고칠 수 있다(스택트레이스로 끝나지 않게).
    (tmp_path / "broken.json").write_text("{", encoding="utf-8")
    with pytest.raises(CaseError, match="broken.json 읽을 수 없는 JSON"):
        load_dir(tmp_path)


def test_validate_returns_case_when_clean():
    case = seed("koreazinc-offering-2024")
    assert validate(deepcopy(case), today=TODAY) == case


@pytest.mark.parametrize("title, expect", [
    ("상장 계획 두고 투자자별로 다른 설명", "주체 이름"),       # 누구 사건인지 모른다
    ("하이브, 상장 계획 거짓 설명", "'거짓'"),                  # 판정 전 단정
    ("하이브 상장 의혹", "'의혹'"),                              # 뭉뚱그린 말
    ("하이브, 상장 계획 두고 다른 설명.", "마침표"),             # 제목이 아니라 문장
])
def test_short_title_rules(title, expect):
    # 훑어 읽는 자리의 제목은 주체가 보이고, 판정·감정 없이 무슨 일인지를 말해야 한다(에이전트가 매번 같은 형식으로 쓰도록 코드가 검사).
    case = seed("hybe-ipo-2019") | {"title": title}
    assert any(line.startswith("title") and expect.strip("'") in line for line in problems(case, today=TODAY))
