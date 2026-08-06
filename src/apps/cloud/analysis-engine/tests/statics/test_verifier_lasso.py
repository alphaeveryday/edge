"""검정 층의 **조절자 판정**과 산문 — 게이트가 ATT 하나인가, 그리고 사람이 보는
줄에 "어느 조건에서 살아났는가" 가 실리는가.

실측에서 사용자가 지적한 로그가 출발점이다:

    RULE_CHANGE  처치일 21/36 · 대조 짝 63 · ATT -0.332%p (양측 p=0.774) ...

여기엔 **어떤 기업 조건에서 이 룰이 살아났는지가 없다**. 그래서 조절 줄을 넣되,
넣는 순간 생기는 유혹(유의하지 않은 처치에서 조절자를 캐는 것 = 방향 채굴)을
코드가 막는지 함께 본다. 그것이 이 파일이 지키는 네 계약이다:

  ① ATT 단일 게이트   미유의면 조절자를 **보지도 싣지도** 않는다
  ② Π 관문           안정성 선택 빈도가 낮으면 산문에 없다 (우연히 한 번 뽑힌 것)
  ③ 순열 p 관문       단계적 하강 p 가 α 이상이면 산문에 없다
  ④ 어휘             라쏘가 뽑은 것은 조절자이지 **원인이 아니다** - '원인' 금지

레이크는 쓰지 않는다. `run_trial` 을 가짜로 대신해 검정 층의 **판정 논리만** 잰다
(진짜 레이크를 쓰면 이 네 계약이 아니라 데이터 유무를 재게 된다).
"""

from __future__ import annotations

import inspect

import pytest

from edge_analysis.statics.core import trial as trial_mod
from edge_analysis.statics.core import verifier as V
from edge_analysis.statics.core.lasso import PI_MIN
from edge_analysis.statics.core.paneltest import FEATURES
from edge_analysis.statics.core.vocab import PLACEBO_NOVELTY


class _Lake:
    """슬롯 메뉴가 비는 레이크. 메뉴가 비면 `design(ask=None)` 이 기준선 하나만 낸다."""

    def bind_day(self, day: str) -> None:
        pass

    def sql(self, q: str):
        return []


def _mod(pi: dict[str, float], p_step: dict[str, float],
         selected: dict[str, float], lam_sens: dict | None = None) -> dict:
    """`lasso.moderate()` 반환 모양 그대로. 검정 층은 이 dict 만 소비한다."""
    return {"verdict": "계산됨", "null_kind": "label", "att_base": 0.008,
            "p_max": min(p_step.values(), default=1.0), "p_step": p_step, "pi": pi,
            "selected": selected, "lam": 0.002,
            "lam_sensitivity": lam_sens or {f"{lm:g}": sorted(selected)
                                            for lm in (0.0005, 0.001, 0.002,
                                                       0.005, 0.01)},
            "n": 214, "j": len(pi), "dropped_collinear": {}}


def _trial(att=0.0083, p=0.004, moderation=None, dropped=None) -> dict:
    out = {"verdict": "계산됨", "att": att, "p": p, "pairs": 214,
           "pretrend_ok": True, "balanced": True, "null_kind": "pair",
           "mod_dropped_missing": dropped or {}}
    if moderation is None:
        out["moderation"] = None
        out["mod_reason"] = "조절자 표본 n=12"
    else:
        out["moderation"] = moderation
    return out


@pytest.fixture()
def spy(monkeypatch):
    """`run_trial` 을 가로챈다. 반환 dict 는 시험마다 `spy.result` 로 갈아끼운다.

    가짜 시그니처가 곧 계약 검사다 - 검정 층이 폐기된 `cond_key` 를 넘기면 TypeError.
    """
    calls: list[dict] = []

    def fake(lake, day, *, etype, layer="고유", role="", stage="", novelty="",
             predicate="", moderators=None, k=3):
        calls.append({"novelty": novelty, "moderators": moderators})
        if novelty == PLACEBO_NOVELTY:      # 위약은 항상 먼저 - 통과시켜 본선으로
            return {"verdict": "계산됨", "att": 0.0001, "p": 0.9, "pairs": 180,
                    "pretrend_ok": True, "balanced": True}
        return fake.result

    fake.result = _trial()
    fake.calls = calls
    monkeypatch.setattr(trial_mod, "run_trial", fake)
    return fake


def _run(spy, **kw):
    """검정 층 한 바퀴. m=1 이라 α=ALPHA (Bonferroni 분모가 계산을 흐리지 않는다)."""
    imps, log = V.verify(_Lake(), "2026-08-04", etype="EARNINGS.RESULT_RELEASE",
                         max_probes=1, **kw)
    return imps, log, V.say_implications(imps)


def test_moderator_candidates_are_all_measurable_features():
    """후보는 **잴 수 있는 것 전량**이다. 셀별로 고르면 그 고름이 곧 판정이다."""
    assert V.MOD_CANDIDATES == tuple(sorted("/".join(k) for k in FEATURES))
    assert len(V.MOD_CANDIDATES) == len(FEATURES) >= 10


def test_verify_passes_every_candidate_to_the_trial(spy):
    """모델도 사람도 지목하지 않는다 - 전량을 넘기고 라쏘가 고른다."""
    _run(spy)
    main = [c for c in spy.calls if c["novelty"] != PLACEBO_NOVELTY]
    assert main and all(c["moderators"] == list(V.MOD_CANDIDATES) for c in main)


def test_insignificant_att_never_looks_at_moderators(spy):
    """(a) ATT 가 죽었는데 조절자를 보고하면 그게 방향 채굴이다.

    조절 결과를 **들려줘도** 로그·산문 어디에도 나오면 안 된다.
    """
    spy.result = _trial(att=-0.0033, p=0.774, moderation=_mod(
        {"주주/수준": 0.95}, {"주주/수준": 0.001}, {"주주/수준": 0.004}))
    imps, log, say = _run(spy)
    assert imps == []
    assert "주주/수준" not in log and "조절" not in log
    assert "없음" in say and "주주/수준" not in say


def test_unstable_moderator_is_not_narrated(spy):
    """(b) Π<0.70 = 부표본을 바꾸면 사라지는 조절자. 그걸 실으면 잡음을 설명한다."""
    spy.result = _trial(moderation=_mod(
        {"주주/수준": PI_MIN - 0.01}, {"주주/수준": 0.001}, {"주주/수준": 0.004}))
    imps, _log, say = _run(spy)
    assert imps and imps[0].mods == ()
    assert "주주/수준" not in say
    assert "조건 무관(전역 효과)" in say


def test_insignificant_step_p_is_not_narrated(spy):
    """(c) 순열 p 가 α 이상이면 크기가 있어도 못 싣는다 - 유의성의 출처는 순열이다."""
    spy.result = _trial(moderation=_mod(
        {"주주/수준": 0.91}, {"주주/수준": 0.30}, {"주주/수준": 0.004}))
    imps, _log, say = _run(spy)
    assert imps[0].mods == () and "주주/수준" not in say


def test_surviving_moderators_reach_the_prose_with_size_and_evidence(spy):
    """관문을 넘은 조절자는 **크기·Π·p 를 달고** 산문에 실린다.

    크기는 post-LASSO(`selected`), 유의성은 순열(`p_step`). 두 출처를 섞지 않는다.
    """
    spy.result = _trial(moderation=_mod(
        {"주주/수준": 0.86, "배수/수준": 0.74, "신용/수준": 0.30},
        {"주주/수준": 0.003, "배수/수준": 0.041, "신용/수준": 0.002},
        {"주주/수준": 0.0031, "배수/수준": -0.0019}))
    imps, log, say = _run(spy)
    assert [k for k, *_ in imps[0].mods] == ["주주/수준", "배수/수준"]   # |크기| 순
    assert "조절: 주주/수준 높을수록 +0.310%p (Π=0.86, p=0.003)" in say
    assert "배수/수준 높을수록 -0.190%p (Π=0.74, p=0.041)" in say
    assert "신용/수준" not in say                      # Π=0.30 은 못 넘는다
    assert "λ 격자 5개 전부에서 같은 조절자 선택" in say
    assert "조절(라쏘)" in log


def test_lambda_grid_disagreement_is_called_spec_sensitive(spy):
    """(d) 격자마다 선택이 갈리면 그 사실이 결과다. 하나만 골라 쓰면 스펙 쇼핑이다."""
    spy.result = _trial(moderation=_mod(
        {"주주/수준": 0.86}, {"주주/수준": 0.003}, {"주주/수준": 0.0031},
        lam_sens={"0.0005": ["주주/수준", "신용/수준"], "0.001": ["주주/수준"],
                  "0.002": ["주주/수준"], "0.005": [], "0.01": []}))
    _imps, log, say = _run(spy)
    assert "스펙 민감" in say and "스펙 민감" in log
    assert "0.005:없음" in say          # 갈린 내용을 숨기지 않는다


def test_unmeasurable_moderation_says_why_not_zero(spy):
    """부재 ≠ 기각. 조절자를 못 재면 '없다' 가 아니라 **판정불가 + 사유**다."""
    spy.result = _trial(moderation=None)
    imps, _log, say = _run(spy)
    assert imps[0].mods == () and "판정불가" in imps[0].mod_note
    assert "조절: 판정불가 — 조절자 표본 n=12" in say


def test_dropped_missing_candidates_are_logged_as_backfill_queue(spy):
    """결측으로 못 본 후보는 로그에 남는다 - 그 목록이 다음 백필 우선순위다."""
    spy.result = _trial(att=-0.0033, p=0.774,
                        dropped={"수급/누적": "결측률 32% > 20%"})
    _imps, log, _say = _run(spy)
    assert "조절 후보 결측 제외: 수급/누적(결측률 32% > 20%)" in log


def test_no_path_of_the_prose_says_cause(spy):
    """(e) 라쏘가 뽑은 것은 **예측에 유용한 조절자**이지 인과 조절자가 아니다.

    산문 전 경로(유의·미유의·판정불가·스펙 민감·접힘)를 돌며 '원인' 을 금지한다.
    이 가드가 없으면 설명 층이 조절자를 인과로 승격시켜 옮겨 쓴다.
    """
    cases = [
        _trial(),                                                   # 판정불가 조절
        _trial(att=-0.0033, p=0.774),                               # 미유의
        _trial(moderation=_mod({"주주/수준": 0.86}, {"주주/수준": 0.003},
                               {"주주/수준": 0.0031})),              # 조절자 있음
        _trial(moderation=_mod({"주주/수준": 0.20}, {"주주/수준": 0.9}, {})),
        _trial(moderation=_mod({"주주/수준": 0.86}, {"주주/수준": 0.003},
                               {"주주/수준": 0.0031},
                               lam_sens={"0.001": ["주주/수준"], "0.01": []})),
    ]
    for res in cases:
        spy.result = res
        _imps, log, say = _run(spy)
        assert "원인" not in say, say
        assert "원인" not in log, log
        assert "조절" in say or "없음" in say
    # 접힌 함의도 같은 규율
    folded = V.Implication("x 가 고유층을 +0.83%p 움직였다", 0.0083, 0.004, 214,
                           None, "통과", False, True)
    assert "원인" not in V.say_implications([folded])
    assert "원인" not in V.say_implications([])
    # 모듈 프롬프트·산문 어디에도 인과 어휘를 흘리지 않는다
    assert "원인" not in V._SYSTEM


def test_model_no_longer_owns_the_moderator_slot():
    """(f) 조절자 선택은 판정이므로 모델에서 뺏었다 - 슬롯 자체가 없다."""
    assert "moderator" not in V._SYSTEM and "조절자" in V._SYSTEM
    assert "moderators" not in inspect.signature(V.screen_probes).parameters
    # 모델이 굳이 적어 내도 읽지 않는다 (조용히 무시 - 폐기 사유를 낭비하지 않는다)
    good, _bad = V.screen_probes(
        [{"name": "MOU", "slots": {}, "moderator": "배수/수준"}],
        {"stage": [("MOU_LOI", 100)]})
    assert good and all("moderator" not in p for p in good)
    probes, bad, _menu = V.design(None, _Lake(), etype="X", day="2026-08-04",
                                  layer="고유")
    assert all("moderator" not in p for p in probes) and bad
    # 프롬프트가 실제로 **렌더된다** - 슬롯을 뺐는데 자리표시자가 남으면 KeyError 로
    # 죽고, 그 경로는 레이크가 필요해 실행 테스트가 없던 자리다.
    seen: list[str] = []

    def ask(system, user):
        seen.append(system)
        return {"probes": [{"name": "MOU", "slots": {}, "moderator": "배수/수준",
                            "why": "쪼갬 근거"}]}

    got, _bad2, _menu2 = V.design(ask, _Lake(), etype="X", day="2026-08-04",
                                  layer="고유")
    # `.format` 이 살아서 돌았고(자리표시자 불일치면 KeyError) 치환도 끝났다
    assert seen and not {"{menu}", "{n}", "{moderators}"} & set(seen[0].split())
    assert "moderator" not in seen[0]
    assert all("moderator" not in p for p in got)


def test_selfcheck_runs():
    V._selfcheck()
