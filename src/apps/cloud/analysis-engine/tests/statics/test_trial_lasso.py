"""시행 × 라쏘 조절자 — **단일 조건 절단에서 J개 동시 조절로** 옮긴 계약을 고정한다.

이 파일이 지키는 것은 넷이다.

  1. 조절자를 넘겨도 **ATT 가 안 움직인다.** 두 검정은 귀무가 다르다(ATT=짝 부호,
     조절=날짜 층화 라벨). 개편이 ATT 를 건드렸으면 그건 향상이 아니라 회귀다.
  2. **결측을 대입하지 않는다.** 결측률 상한을 넘은 열은 후보에서 빠지고 **사유가
     남는다**. 조용히 빠지면 산출물이 '그 조건은 효과를 안 바꿨다' 로 읽히는데
     실제로는 재지도 않은 것이다.
  3. 결측 행을 드러내고 나서 표본이 얇으면 **조절만** 판정불가다. ATT 는 전량으로 낸다.
  4. 구 계약(`cond_key`)은 **소리를 내며** 사라진다. 조용히 무시하면 호출자는 조건을
     걸었다고 믿는데 코드는 안 걸었다.

레이크는 가짜다 - 진짜 DB·S3 없이 돈다. 가짜는 질의에서 `AS m<i>` 를 세어 조절자
열 수를 정한다: 패널 확장이 SQL 로 실제 나갔는지가 그 자체로 검사다.
"""

from __future__ import annotations

import re

import numpy as np
import pytest

from edge_analysis.statics.lasso import MISS_MAX
from edge_analysis.statics.paneltest import FEATURES
from edge_analysis.statics.trial import (MIN_PAIRS, run_multi, run_trial, say,
                                         say_multi)

DAY = "2026-06-01"
ETYPE = "COMPANY.PRODUCT.LAUNCH"
ETYPE2 = "COMPANY.GUIDANCE.RAISE"
# 조절자 이름은 FEATURES **삽입 순서**를 따른다 - 열 인덱스와 이름이 어긋나면
# 결측 사유가 엉뚱한 조건에 붙는다.
MODS_ALL = ["/".join(k) for k in FEATURES]
TRUE_MOD = MODS_ALL[0]
_DATES = [f"2026-05-{1 + i:02d}" for i in range(10)]


def _pairs(n: int, j: int, *, seed: int = 7, effect: float = 0.04,
           hole: dict[int, range] | None = None) -> list[tuple]:
    """짝 행을 만든다. δ = 0.01 + effect·c₀ + 잡음 - **0번 열만** 진짜 조절자다.

    `hole[j]` 는 j 번 열이 NULL 인 행 번호. 열마다 다른 행을 비워야 '열별 결측률은
    상한 이하인데 전수 관측 행은 얇다' 를 만들 수 있다 - 그게 재무 6종의 실제 모양이다.
    """
    hole = hole or {}
    rng = np.random.default_rng(seed)
    M = rng.random((n, j)) if j else np.zeros((n, 0))
    out = []
    for i in range(n):
        y_c = float(rng.normal(0, 0.01))
        y_t = y_c + 0.01 + (effect * float(M[i, 0]) if j else 0.0) \
            + float(rng.normal(0, 0.002))
        lc, bm = 20.0 + float(rng.normal(0, 0.3)), 1.0 + float(rng.normal(0, 0.1))
        out.append((f"t{i:04d}", _DATES[i % len(_DATES)], f"c{i:04d}", y_t, y_c,
                    lc, lc + float(rng.normal(0, 0.05)),
                    bm, bm + float(rng.normal(0, 0.02)),
                    float(rng.normal(0, 0.01)), float(rng.normal(0, 0.01)),
                    float(rng.normal(0, 0.01)), float(rng.normal(0, 0.01)),
                    *(None if i in hole.get(c, ()) else float(M[i, c])
                      for c in range(j))))
    return out


class _Lake:
    """가짜 레이크. 질의를 보고 시행/다중 두 모양에 답한다.

    조절자 열 수를 **질의에서 센다.** 고정 상수로 두면 `{extra}`/`{sel}` 이 안 나가도
    테스트가 초록이 된다 - 그러면 패널 확장을 검사한 것이 아니다.
    """

    def __init__(self, rows: list[tuple], *, n_treat: int = 2, seed: int = 3):
        self.rows, self.n_treat, self.seed = rows, n_treat, seed
        self.seen: list[str] = []

    @staticmethod
    def n_mod(q: str) -> int:
        return len(re.findall(r"g\.\w+ AS m\d+", q))

    def sql(self, q: str):
        self.seen.append(q)
        j = self.n_mod(q)
        base = [r[:13] + r[13:13 + j] for r in self.rows]
        if "AS y_t" in q:                       # 단일 처치 시행
            return base
        # 다중 처치: (tid, d, cid, diff, D0..D_{K-1}, 조절자)
        rng = np.random.default_rng(self.seed)
        out = []
        for r in base:
            d = [float(v) for v in rng.integers(0, 2, self.n_treat)]
            if not any(d):
                d[0] = 1.0                      # 처치가 하나도 없으면 짝이 아니다
            # 두 번째 처치가 δ 를 0.02 더 민다 - 흡수 검사의 신호
            out.append((r[0], r[1], r[2], r[3] - r[4] + 0.02 * d[-1],
                        *d, *r[13:]))
        return out


def _run(rows, **kw):
    return run_trial(_Lake(rows), DAY, etype=ETYPE, **kw)


# ── (a) 22열 전량을 넘기면 조절자 검정이 채워진다 ─────────────────────────


def test_all_features_go_in_as_moderators_and_the_real_one_is_found():
    """노출 슬롯이 사라졌으니 **모든 피처가 조절자 후보**다 - 22열이 한 번에 간다.

    이전 계약은 조건 한 열만 실었고, 여러 조건을 보려면 J 번 재야 했다(다중비교가
    안 잡혔다). 지금은 maxT 순열이 J 를 귀무 분포에 흡수한다.
    """
    lake = _Lake(_pairs(120, len(MODS_ALL)))
    r = run_trial(lake, DAY, etype=ETYPE, moderators=MODS_ALL)

    assert r["verdict"] == "계산됨"
    # SQL 이 **어휘 전량**을 실었다 - 이게 패널 확장의 증거다. 개수를 손으로 박으면
    # 계열족을 늘릴 때마다 이 테스트가 거짓 경보를 낸다(금리 족 추가에서 실제로 그랬다).
    from edge_analysis.statics.paneltest import FEATURES
    assert lake.n_mod(lake.seen[0]) == len(MODS_ALL) == len(FEATURES)
    assert lake.seen[0].count(", t.m") == len(FEATURES)

    m = r["moderation"]
    assert m["verdict"] == "계산됨", m
    assert m["n"] == 120 and m["j"] == len(FEATURES)
    assert m["null_kind"] == "label", "조절 귀무는 라벨 순열이다 - ATT 와 다르다"
    assert TRUE_MOD in m["selected"], m["selected"]
    assert m["p_max"] < 0.05, m["p_max"]
    # 잡음 21열은 안 뽑힌다. 뽑히면 산문이 없는 조건을 말한다.
    assert set(m["selected"]) == {TRUE_MOD}, m["selected"]
    # 크기는 post-LASSO. 심은 것은 0.04 (백분위 0→1 사이의 기울기).
    assert abs(m["selected"][TRUE_MOD] - 0.04) < 0.01, m["selected"]
    assert not r["mod_dropped_missing"], r["mod_dropped_missing"]


def test_unknown_moderator_is_refused_not_silently_dropped():
    """오타 하나로 조절자가 사라지면 '효과를 안 바꿨다' 로 오독된다 - 소리를 낸다."""
    r = _run(_pairs(40, 2), moderators=[TRUE_MOD, "없는계열/없는변환"])
    assert r["verdict"] == "판정불가"
    assert "없는계열/없는변환" in r["reason"]


# ── (b) 결측률 상한 초과 열은 후보에서 빠지고 **사유가 남는다** ───────────


def test_column_over_missing_cap_is_dropped_with_a_stated_reason():
    """대입하지 않는다. 대신 뺐다는 사실과 그 결측률을 남긴다.

    32% 는 재무 6종(ASOF 조인)의 실제 모양이다 - 채워 넣으면 그 계수는 자료가 아니라
    대입 규칙의 그림자가 된다.
    """
    mods = MODS_ALL[:3]
    rows = _pairs(100, 3, hole={1: range(32)})          # 1번 열만 32% 결측
    r = _run(rows, moderators=mods)

    assert r["verdict"] == "계산됨"
    assert set(r["mod_dropped_missing"]) == {mods[1]}, r["mod_dropped_missing"]
    why = r["mod_dropped_missing"][mods[1]]
    assert "32%" in why and "20%" in why, why

    m = r["moderation"]
    assert m["verdict"] == "계산됨"
    # 남은 두 열은 결측이 없으므로 행 손실도 없다 - 열 관문이 표본을 지켰다
    assert m["n"] == 100 and m["j"] == 2
    assert mods[1] not in m["pi"], "뺀 열이 판정에 다시 나타나면 안 된다"
    assert TRUE_MOD in m["selected"], m["selected"]


def test_exactly_at_the_cap_survives_the_column_gate():
    """상한은 초과(>)에서 자른다 - 경계에서 갈리면 20% 열이 조용히 사라진다."""
    rows = _pairs(100, 2, hole={1: range(20)})          # 정확히 20%
    r = _run(rows, moderators=MODS_ALL[:2])
    assert not r["mod_dropped_missing"], r["mod_dropped_missing"]
    assert r["moderation"]["j"] == 2
    assert r["moderation"]["n"] == 80, "20% 결측 행은 드러난다 - 채우지 않는다"
    assert MISS_MAX == 0.20


# ── (c) 조절자를 안 넘기면 ATT 는 **숫자까지** 이전과 같다 ────────────────


def test_att_is_bit_identical_with_and_without_moderators():
    """조절이 ATT 를 움직이면 개편이 아니라 회귀다.

    같은 짝 집합 · 같은 SEED · 같은 계산이므로 부동소수점까지 같아야 한다. 근사
    비교로 두면 조절자가 ATT 표본을 몰래 깎아도 초록이 된다.
    """
    rows = _pairs(120, len(MODS_ALL))
    plain, lake = _Lake(rows), _Lake(rows)
    r0 = run_trial(plain, DAY, etype=ETYPE)
    r1 = run_trial(lake, DAY, etype=ETYPE, moderators=MODS_ALL)

    for key in ("att", "p", "att_adj", "p_adj", "pairs", "treated", "dates",
                "y_t", "y_c", "balanced", "pretrend_ok"):
        assert r0[key] == r1[key], f"{key}: {r0[key]} != {r1[key]}"
    assert r0["smd"] == r1["smd"] and r0["lead"] == r1["lead"]

    # 조절자가 없으면 SQL 도 확장되지 않는다 - 개편 전 패널과 같은 모양이다
    assert plain.n_mod(plain.seen[0]) == 0
    assert ", t.m" not in plain.seen[0] and " AS m0" not in plain.seen[0]

    # ATT 자체가 짝 차이 평균이라는 것도 독립으로 확인한다
    want = float(np.mean([r[3] - r[4] for r in rows]))
    assert abs(r0["att"] - want) < 1e-12, (r0["att"], want)
    assert r0["null_kind"] == "pair"
    assert "moderation" not in r0, "안 넘긴 조절은 키조차 만들지 않는다"


# ── (d) 구 계약은 소리를 내며 사라진다 ────────────────────────────────────


def test_old_cond_key_contract_raises_typeerror():
    """`cond_key`/`cond_pct`/`cond_cmp` 는 폐기됐다.

    절단은 정보를 죽이고(79번째와 21번째가 같은 '미충족'), 임계 0.8 은 임의였다.
    조용히 무시하면 호출자는 조건을 걸었다고 믿는데 코드는 안 건다.
    """
    rows = _pairs(40, 1)
    for kw in ({"cond_key": TRUE_MOD}, {"cond_pct": 0.8}, {"cond_cmp": ">="}):
        with pytest.raises(TypeError):
            _run(rows, **kw)


# ── (e) 결측 행을 드러낸 뒤 얇으면 **조절만** 판정불가 ───────────────────


def test_thin_after_listwise_deletion_stops_moderation_but_not_att():
    """열마다 다른 행이 비면 전수 관측 행은 곱으로 줄어든다 - 그래도 대입은 없다.

    4열이 각각 20%(열 관문 통과)인데 **비는 행이 겹치지 않아** 교집합은 8짝뿐이다.
    조절은 판정불가이고 ATT 는 40짝 전량으로 나온다. '조절자가 ATT 를 죽이면 개편이
    향상이 아니다' 가 여기서 강제된다.
    """
    rows = _pairs(40, 4, hole={j: range(8 * j, 8 * j + 8) for j in range(4)})
    r = _run(rows, moderators=MODS_ALL[:4])

    assert r["verdict"] == "계산됨", "ATT 는 살아 있다"
    assert r["pairs"] == 40
    assert not r["mod_dropped_missing"], "열 관문은 통과했다 - 각 열 20%"
    assert r["moderation"] is None
    assert f"짝 8 < {MIN_PAIRS}" in r["mod_reason"], r["mod_reason"]
    assert "40짝 전량" in r["mod_reason"], "ATT 표본이 안 깎인 사실을 말한다"

    # 조절자를 안 넘긴 호출과 ATT 가 같다
    assert _run(rows)["att"] == r["att"]


def test_every_candidate_over_the_cap_is_undecided_never_zero():
    """전부 결측이면 '조절 없음' 이 아니라 판정불가다 - 부재 ≠ 기각."""
    rows = _pairs(60, 3, hole={j: range(40) for j in range(3)})   # 67% 결측
    r = _run(rows, moderators=MODS_ALL[:3])
    assert r["verdict"] == "계산됨" and r["moderation"] is None
    assert len(r["mod_dropped_missing"]) == 3
    assert "대입은 하지 않는다" in r["mod_reason"], r["mod_reason"]


# ── 산문: '원인' 이라고 쓰지 않는다 ───────────────────────────────────────


def test_prose_says_moderation_never_causation():
    """라쏘가 뽑은 것은 예측에 유용한 열이지 인과 조절자가 아니다.

    조건은 무작위 배정되지 않았다(준무작위인 것은 처치뿐). 산문이 '원인' 으로
    승격시키면 게이트가 못 잡는다 - 코드가 그 단어를 안 쓰는 것이 유일한 방벽이다.
    """
    r = _run(_pairs(100, 3), moderators=MODS_ALL[:3])
    txt = say(r)
    assert "원인" not in txt, txt
    assert "조절" in txt and TRUE_MOD in txt
    assert "귀무 짝 부호 순열" in txt and "귀무 날짜 층화 조건 라벨 순열" in txt, txt
    assert "post-LASSO" in txt, "크기 출처를 밝힌다 - 라쏘 계수와 섞지 않는다"


def test_prose_states_the_missing_gate_and_the_undecided_moderation():
    """뺀 열과 판정불가 사유는 산문에 **나와야** 한다. 침묵은 0 으로 읽힌다."""
    rows = _pairs(40, 4, hole={j: range(8 * j, 8 * j + 8) for j in range(4)})
    txt = say(_run(rows, moderators=MODS_ALL[:4]))
    assert "조절 판정불가" in txt and f"짝 8 < {MIN_PAIRS}" in txt

    dropped = say(_run(_pairs(100, 3, hole={1: range(32)}),
                       moderators=MODS_ALL[:3]))
    assert "조절자 후보 제외(결측 · 대입 안 함)" in dropped
    assert "32%" in dropped


def test_prose_shows_how_much_the_row_gate_cut():
    """`n` 만 쓰면 행 관문의 손실이 안 보인다 - `n/짝` 으로 쓴다.

    열 관문은 **열별** 상한이라 교집합 손실을 못 막는다: 3열이 각각 20%(전부 통과)
    인데 비는 행이 겹치지 않으면 전수 관측은 100짝 중 40개다. 그 60짝이 어디로
    갔는지 산문이 말하지 않으면 읽는 쪽은 조절과 ATT 를 같은 표본으로 읽는다.
    """
    rows = _pairs(100, 3, hole={j: range(20 * j, 20 * j + 20) for j in range(3)})
    r = _run(rows, moderators=MODS_ALL[:3])
    assert not r["mod_dropped_missing"] and r["moderation"]["n"] == 40
    assert r["pairs"] == 100, "ATT 는 100짝 전량 그대로다"

    # **두 짝 수가 나란히 찍힌다.** ATT 줄에 100짝, 조절 줄에 40/100짝. 하나만
    # 찍히면 읽는 쪽이 두 검정을 같은 표본으로 읽고, 그러면 게이트가 조절자에
    # 의존하는 것처럼 보인다. 다르면 다르다고 보여야 한다.
    txt = say(r)
    assert "짝 100개" in txt, txt                    # ATT 표본
    assert "n=40/100짝" in txt, txt                  # 조절 표본 + 손실

    # 다중 처치 산문도 같은 규율이다
    mtxt = say_multi(run_multi(_Lake(rows), DAY, [ETYPE, ETYPE2],
                               moderators=MODS_ALL[:3]))
    assert "짝 100개" in mtxt and "n=40/100짝" in mtxt, mtxt


# ── Phase 5: 다중 처치 + 조절자를 한 적합에 ──────────────────────────────


def test_multi_treatment_indicators_are_free_columns_in_the_same_fit():
    """`δₚ = Σₖ bₖ·D_{p,k} + Σⱼ dⱼ·c_{p,j} + uₚ` — D 는 벌점 밖이다.

    벌점을 물리면 처치 효과가 0 으로 수축돼 게이트가 무너진다(절편과 같은 이유).
    한 적합에 넣어야 처치 대비와 조절자 계수가 같은 식의 것이 된다.
    """
    mods = MODS_ALL[:3]
    rows = _pairs(120, 3)
    r = run_multi(_Lake(rows), DAY, [ETYPE, ETYPE2], moderators=mods)

    assert r["verdict"] == "계산됨", r.get("reason")
    m = r["moderation"]
    assert m["verdict"] == "계산됨", m
    # 처치 지시자는 판정 대상이 아니다 - Π 도 p 도 안 붙는다
    assert set(m["free_coef"]) == {ETYPE, ETYPE2}, m["free_coef"]
    assert ETYPE not in m["pi"] and ETYPE not in m["p_step"]
    assert m["j"] == 3, "벌점 후보는 조절자 3열뿐"
    assert set(m["selected"]) == {TRUE_MOD}, m["selected"]

    # **처치 간 대비**로 읽는다. 자유 열 계수의 낱값은 절편과 함께만 식별된다 - 모든
    # 짝이 처치를 하나 이상 받으므로 D 블록과 절편이 준중복이고, 공통 몫을 절편이
    # 가져가면 낱값은 뜻을 잃는다(실측: LAUNCH ATT +2.42%p 인데 자유 열 계수는
    # -0.05%p). 차이는 절편이 소거돼 두 설계에서 같은 것을 뜻한다.
    # 두 번째 처치에만 +0.02 를 심었으니 대비가 그 값을 되찾아야 한다.
    gap = m["free_coef"][ETYPE2] - m["free_coef"][ETYPE]
    assert abs(gap - 0.02) < 0.005, m["free_coef"]

    txt = say_multi(r)
    assert "처치 간 대비" in txt and "원인" not in txt
    assert "벌점 밖" in txt, "처치가 왜 수축 안 되는지 말한다"
    assert f"{gap * 100:+.3f}%p" in txt, txt
    assert f"조절자 없이 {(r['att'][1] - r['att'][0]) * 100:+.3f}%p" in txt, txt


def test_multi_att_and_null_are_untouched_by_moderators():
    """다중 처치의 ATT 도 짝 부호 순열 그대로다 - 라쏘는 옆에서 돈다."""
    rows = _pairs(120, 3)
    r0 = run_multi(_Lake(rows), DAY, [ETYPE, ETYPE2])
    r1 = run_multi(_Lake(rows), DAY, [ETYPE, ETYPE2], moderators=MODS_ALL[:3])
    assert r0["att"] == r1["att"] and r0["p"] == r1["p"]
    assert r0["null_kind"] == "pair" and "moderation" not in r0
    assert r0["n_treat"] == r1["n_treat"]


def test_multi_sql_carries_the_moderator_columns():
    """`{extra}`/`{sel}` 이 안 나가면 조절자는 존재하지 않는 열이다."""
    lake = _Lake(_pairs(60, 3))
    run_multi(lake, DAY, [ETYPE, ETYPE2], moderators=MODS_ALL[:3])
    multi = next(q for q in lake.seen if "AS diff" in q)
    assert lake.n_mod(multi) == 3 and multi.count(", t.m") == 3
    assert "AS d0" in multi and "AS d1" in multi, "처치 지시자 열은 그대로다"
