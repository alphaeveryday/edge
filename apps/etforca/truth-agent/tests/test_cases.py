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


def test_validate_returns_case_when_clean():
    case = seed("koreazinc-offering-2024")
    assert validate(deepcopy(case), today=TODAY) == case
