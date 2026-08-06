"""표현력 측정의 계약 — **못 담는 것을 담았다고 하면 측정 자체가 죽는다.**"""
from edge_analysis.statics.ops.expressive import SLOTS, Reduction, Survey, generate, score


def _slots(**over):
    s = {k: {"grade": "사상", "value": "거래량/변화", "changed": "", "want": ""}
         for k in SLOTS}
    s.update(over)
    return s


def test_grade_is_derived_from_slots_not_asserted():
    # 등급을 채점자가 따로 주장하면 슬롯 판정과 어긋날 수 있다 - 유도만 허용한다.
    assert Reduction("p", _slots()).grade == "완전"
    assert Reduction("p", _slots(채널={"grade": "대리", "changed": "범위가 넓어짐"})).grade == "대리"
    assert Reduction("p", _slots(노출={"grade": "불가"})).grade == "부분"
    # 방아쇠가 안 잡히면 뼈대가 없다 - 나머지가 다 사상돼도 불가다
    assert Reduction("p", _slots(방아쇠={"grade": "불가"})).grade == "불가"


def test_expressiveness_and_measurability_are_separate_axes():
    # ④ 어휘로 담겼는데 표본이 없는 것(데이터 일감)과 안 담긴 것(어휘 일감)은 다르다.
    assert Reduction("p", _slots(노출={"grade": "사상", "value": "거래량/변화"})).measurable()
    assert Reduction("p", _slots(노출={"grade": "사상", "value": "수급/수준"})).measurable() is False
    assert Reduction("p", _slots(노출={"grade": "불가"})).measurable() is None


def test_proxy_without_stated_change_is_demoted_to_blocked():
    # '대리'는 뜻이 바뀐 사상이다. 무엇이 바뀌었는지 안 쓰면 조용한 성공이 되고,
    # 조용한 성공은 표현력을 부풀린다 - 채점자가 근거를 빼먹으면 불가로 떨어뜨린다.
    calls = {}

    def ask(system, user):
        calls["n"] = calls.get("n", 0) + 1
        good = {"방아쇠": "COMPANY.PRODUCT.LAUNCH", "채널": "S주식수", "노출": "거래량/변화",
                "조건": "수급/누적", "결과": "수익률", "부호": "+1"}
        return {"slots": {**{k: {"grade": "사상", "value": v} for k, v in good.items()},
                          "채널": {"grade": "대리", "value": "S주식수", "changed": ""}},
                "lost": ""}

    r = score(ask, "산문", event_types=["COMPANY.PRODUCT.LAUNCH"])
    assert r.slots["채널"]["grade"] == "불가"
    assert "미기재" in r.slots["채널"]["want"]
    assert r.grade == "부분"


def test_unknown_grade_falls_to_blocked_never_to_success():
    # 채점자가 모르는 값을 뱉으면 성공으로 세지 않는다 - 침묵하는 통과 금지.
    r = score(lambda s, u: {"slots": {k: {"grade": "아마도"} for k in SLOTS}},
              "산문", event_types=[])
    assert r.grade == "불가" and set(r.blocked) == set(SLOTS)


def test_generator_never_sees_the_vocabulary():
    # 어휘를 주면 자기검열이 일어나 분모가 오염된다 - 재려는 것이 사라진다.
    seen = {}

    class M:
        catalog = type("C", (), {"call": staticmethod(lambda n, a="": "obs")})()
        done = True
        def brief(self): return "brief"
        def menu(self): return ""
        def observe(self, n, a=""): return ""

    def ask(system, user):
        seen["text"] = system + user
        return {"hypotheses": ["실적 서프라이즈가 외국인 수급을 끌어 주가를 밀었다"]}

    out = generate(ask, M(), facts="셀", n=2)
    assert out and "수급" in out[0]
    for banned in ("채널 8", "계열족", "닫힌 어휘", "P판가", "COMPANY."):
        assert banned not in seen["text"], banned


def test_survey_report_names_owner_of_each_blocked_slot():
    # 막힘이 누구 일감인지 안 나오면 숫자가 행동으로 안 바뀐다.
    sv = Survey("T/d", [Reduction("p", _slots(방아쇠={"grade": "불가", "want": "경쟁사 출시"})),
                        Reduction("p", _slots(채널={"grade": "불가", "want": "환율 전가"}))])
    txt = sv.report()
    assert "상류(사건타입 53" in txt and "우리(채널 8)" in txt
    assert "경쟁사 출시" in txt and "환율 전가" in txt
    assert "0%" in txt          # 둘 다 뼈대 못 세우거나 부분 - 환원율이 정직하게 낮다


def test_unsaid_slot_is_not_a_vocabulary_failure():
    # 20R 첫 실측: 자유 산문이 방향을 안 밝히자 채점자가 부호를 '불가'로 찍었다.
    # 그건 산문이 덜 구체적인 것이지 어휘가 못 담은 게 아니다 - 어휘 탓으로 세면
    # 표현력이 부당하게 낮아진다. 표현력은 어휘의 속성이지 산문의 속성이 아니다.
    r = Reduction("p", _slots(부호={"grade": "미명시"}, 조건={"grade": "미명시"}))
    assert r.grade == "완전"                 # 말한 슬롯은 전부 사상됐다
    assert r.blocked == [] and r.unsaid == ["조건", "부호"]
    txt = Survey("T/d", [r]).report()
    assert "원문 미명시" in txt and "어휘 일감 아님" in txt
    assert "막힌 슬롯" not in txt              # 어휘 일감 목록에 안 올라간다


def test_scorer_prompt_separates_vocabulary_from_measurability():
    # 같은 실측에서 채점자가 '수급 계열족 없음'이라 찍었는데 수급은 어휘에 있다.
    # measurable 목록(4개)을 어휘로 착각한 것 - 프롬프트가 그 둘을 못 박아야 한다.
    from edge_analysis.statics.ops.expressive import _SCORE
    from edge_analysis.statics.core.vocab import SERIES_FAMILIES

    assert "수급" in SERIES_FAMILIES
    assert "이 목록은 어휘가 아니다" in _SCORE
    assert "그때만" in _SCORE and "다른 축" in _SCORE


def test_claimed_mapping_is_verified_against_the_closed_vocabulary():
    # 20R 실측: 채점자가 "사상"이라 주장하며 방아쇠에 채널을, 노출에 티커를 넣었다.
    # 그대로 믿으니 환원율이 100%로 부풀었다. 어휘가 닫혀 있으니 코드가 검산한다 -
    # 사람 기준 표본 없이도 이 실패 유형만은 잡힌다.
    from edge_analysis.statics.ops.expressive import in_vocab

    assert in_vocab("방아쇠", "COMPANY.PRODUCT.LAUNCH", ["COMPANY.PRODUCT.LAUNCH"])
    assert not in_vocab("방아쇠", "S주식수", [])          # 채널을 방아쇠 슬롯에
    assert not in_vocab("노출", "삼성전자(005930.KS)", [])  # 티커를 노출 슬롯에
    assert not in_vocab("조건", "수급", [])              # 계열족만, 변환 없음
    assert in_vocab("조건", "수급/누적", [])
    assert in_vocab("노출", "SUPPLY_CHAIN", []) and not in_vocab("조건", "SUPPLY_CHAIN", [])

    r = score(lambda s, u: {"slots": {**{k: {"grade": "사상", "value": "수익률"} for k in SLOTS},
                                      "방아쇠": {"grade": "사상", "value": "AI 데이터센터 투자 확대"}},
                            "lost": ""},
              "산문", event_types=["COMPANY.PRODUCT.LAUNCH"])
    assert r.slots["방아쇠"]["grade"] == "불가"        # 허위사상은 사상이 아니다
    assert "허위사상" in r.slots["방아쇠"]["want"]
    assert "방아쇠" in r.bogus and r.grade == "불가"
    assert "채점자 허위사상" in Survey("T/d", [r]).report()


def test_collapse_detection_flags_one_value_swallowing_all_mechanisms():
    # 20R 실측: 5개 가설이 서로 다른 메커니즘인데 채널이 전부 S주식수 였다.
    # 형식 검산은 통과한다(S주식수 는 어휘에 있다) - 코드가 볼 수 있는 마지막 신호가
    # 값의 쏠림이다. 이건 사상이 아니라 뭉갬이다.
    items = [Reduction(f"가설{i}", _slots(채널={"grade": "사상", "value": "S주식수"}))
             for i in range(5)]
    txt = Survey("T/d", items).report()
    assert "의미 붕괴 의심" in txt and "채널" in txt and "5/5" in txt

    varied = [Reduction(f"가설{i}", _slots(채널={"grade": "사상", "value": c}))
              for i, c in enumerate(["S주식수", "P판가", "C원가", "FX환", "K위험"])]
    warn = [l for l in Survey("T/d", varied).report().splitlines() if "의미 붕괴" in l]
    assert not any("채널" in l for l in warn)      # 갈라진 슬롯은 경고 안 뜬다


def test_trigger_in_vocabulary_but_absent_from_cell_is_not_a_vocabulary_gap():
    # 20R 실측: 표현력 측정이 '증권사 목표주가 하향 (사건타입 목록에 없음)' 이라
    # 찍었는데 MARKET_INFO.ANALYST.TARGET_PRICE_CHANGE 는 어휘 53종에 31건 있다.
    # 채점자에게 **셀 접지 목록**만 줘서 "이 셀에 없음"이 "어휘에 없음"이 된 것 -
    # 미도달을 부재로 보고하는 병이 어휘 축에서 재발했다. 어휘 구멍을 부풀린다.
    VOCAB = ("COMPANY.PRODUCT.LAUNCH", "MARKET_INFO.ANALYST.TARGET_PRICE_CHANGE")
    CELL = ("COMPANY.PRODUCT.LAUNCH",)          # 이 셀엔 목표주가 사건이 없다
    seen = {}

    def ask(system, user):
        seen["sys"] = system + user
        return {"slots": {**{k: {"grade": "사상", "value": v} for k, v in
                             (("채널", "S주식수"), ("노출", "거래량/변화"),
                              ("조건", "수급/누적"), ("결과", "수익률"), ("부호", "-1"))},
                          "방아쇠": {"grade": "사상",
                                   "value": "MARKET_INFO.ANALYST.TARGET_PRICE_CHANGE"}},
                "lost": ""}

    r = score(ask, "목표주가 하향으로 하락", event_types=list(CELL), vocab_types=VOCAB)
    assert "MARKET_INFO.ANALYST" in seen["sys"]          # 채점자가 어휘 전량을 본다
    assert r.slots["방아쇠"]["grade"] == "사상"            # 어휘에 있으니 사상이다
    assert r.ungrounded is True                          # 다만 이 셀엔 없다 - 다른 축
    assert "어휘 구멍이 아니다" in Survey("T/d", [r]).report()
