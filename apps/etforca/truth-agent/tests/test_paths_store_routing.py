"""칸 경로·저장소·배정 규칙."""
from datetime import date

import pytest

from conftest import article, seed
from edge_truth_agent import paths, routing
from edge_truth_agent.store import ABSENT, ConflictError, LocalStore


def test_paths_read_write_append_and_remove():
    case = seed("hybe-ipo-2019")
    assert paths.get(case, "contrast.after.text").startswith("같은 시기")
    paths.put(case, "body[+]", {"p": "새 문단"})
    assert case["body"][-1] == {"p": "새 문단"}
    paths.remove(case, "body[-1]".replace("-1", str(len(case["body"]) - 1)))
    assert case["body"][-1] != {"p": "새 문단"}


@pytest.mark.parametrize("bad", ["", "body[", "[0]", "body..p", "body[+].p", "body.[0]"])
def test_paths_reject_malformed_paths(bad):
    # 에이전트가 틀린 경로를 넘기면 엉뚱한 칸을 고치지 말고 오류를 받아야 한다.
    with pytest.raises(paths.PathError):
        paths.put(seed("hybe-ipo-2019"), bad, "x")


def test_local_store_conditional_write_rejects_stale_version(tmp_path):
    # 읽은 뒤 바뀐 파일을 덮어쓰면 사람의 정정이 조용히 사라진다.
    store = LocalStore(tmp_path)
    first = store.put("cases/a.json", b"1", if_match=ABSENT)
    store.put("cases/a.json", b"2", if_match=first)
    with pytest.raises(ConflictError):
        store.put("cases/a.json", b"3", if_match=first)
    with pytest.raises(ConflictError):
        store.put("cases/a.json", b"4", if_match=ABSENT)


def test_two_letter_name_needs_a_non_hangul_left_boundary():
    # '금양' 같은 두 글자 이름이 다른 낱말 속에서 잡히면 엉뚱한 기사가 관련 보도로 공개된다.
    assert routing.mentions("금양 상장폐지 결정", "금양")
    assert routing.mentions("[단독] 금양은 ...", "금양")
    assert not routing.mentions("지금양산 계획", "금양")
    # 이름 뒤에 한글이 이어지면 조사일 때만 같은 이름이다 — 실제로 '하이브리드' 자동차 기사가 하이브 사건에 붙었다.
    assert routing.mentions("하이브는 상장 전", "하이브")
    assert not routing.mentions("렉서스 ES 하이브리드 상장", "하이브")


def test_related_needs_a_case_topic_not_just_the_company_name():
    # 회사 이름만 나오는 실적·신상품 기사는 이 사건의 보도가 아니다(공개 화면의 관련 보도가 잡음으로 찬다).
    hybe, zinc = seed("hybe-ipo-2019"), seed("koreazinc-offering-2024")
    assert routing.related("경찰, 방시혁 하이브 의장 불구속 송치", hybe)
    assert not routing.related("하이브, BTS 투어에 3분기 흑자전환 전망…목표가 29만원", hybe)
    assert routing.related("고려아연 유상증자 의혹…수사 주체 바뀌나", zinc)
    assert not routing.related("소액주주단체, 고려아연·한진그룹 회장 배임 혐의로 경찰 고발", zinc)


def test_related_news_that_no_longer_fits_is_pruned_but_cited_sources_stay():
    # 예전 규칙으로 붙은 잡음 관련 보도는 다음 실행이 걷어 내야 한다. 본문이 인용하는 출처는 남긴다.
    case = seed("hybe-ipo-2019")
    cited = case["contrast"]["before"]["sourceId"]
    junk = article("[시승기] 렉서스 ES 하이브리드의 매력", aid="f" * 64, url="https://www.example.com/car")
    case["sources"].append({"id": junk.source_id, "kind": "news", "publisher": "x", "title": junk.title, "url": junk.url,
                            "publishedAt": "2026-10-19", "accessedAt": "2026-10-20"})
    case["related"] += [{"sourceId": junk.source_id, "addedAt": "2026-10-20T00:00:00+09:00"},
                        {"sourceId": cited, "addedAt": "2026-10-20T00:00:00+09:00"}]
    assert routing.add_related(case, [], date(2026, 10, 20), "2026-10-20T10:00:00+09:00")
    assert [r["sourceId"] for r in case["related"]] == [cited]
    assert junk.source_id not in {s["id"] for s in case["sources"]} and cited in {s["id"] for s in case["sources"]}


def test_route_skips_locked_cases_and_matches_aliases():
    hybe, geumyang = seed("hybe-ipo-2019"), seed("geumyang-monlaa")
    geumyang["locked"] = True
    found = routing.route([article("방시혁 검찰 기소"), article("금양 정리매매", aid="b" * 64)], [hybe, geumyang])
    assert list(found) == ["hybe-ipo-2019"]


def test_session_only_when_procedural_cue_present():
    # 이름만 나온 기사로 모델을 부르면 비용만 들고 바뀌는 것이 없다.
    assert routing.needs_session([article("하이브 방시혁 검찰 기소")])
    assert not routing.needs_session([article("하이브 신인 그룹 데뷔")])


def test_add_related_caps_list_and_keeps_cited_sources():
    case = seed("hybe-ipo-2019")
    cited = case["contrast"]["before"]["sourceId"]
    case["related"].append({"sourceId": cited, "addedAt": "2026-10-20T00:00:00+09:00"})
    # 실제 id 는 sha256 이라 앞 16자리(출처 id)가 겹치지 않는다. 시험 id 도 앞자리부터 달라야 한다.
    many = [article(f"하이브 상장 기사 {i}", aid=f"{i:04d}" + "a" * 60, url=f"https://www.yna.co.kr/view/{i}") for i in range(routing.RELATED_LIMIT + 5)]
    assert routing.add_related(case, many, date(2026, 10, 20), "2026-10-20T10:00:00+09:00")
    assert len(case["related"]) == routing.RELATED_LIMIT
    ids = {s["id"] for s in case["sources"]}
    assert cited in ids  # 인용 중인 출처는 관련 보도에서 밀려나도 남는다
    assert not routing.add_related(case, many[-3:], date(2026, 10, 20), "2026-10-20T10:00:00+09:00")  # 같은 기사는 다시 안 붙음
