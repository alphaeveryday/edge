"""RCT 근사 시행(Trial) — **닫힌 슬롯 조합이 통계 검정으로 컴파일된다.**

## 왜 이 실험을 하는가

지금 가설의 의미가 너무 넓다. `edge_test` 가 재는 것은
    "그 사건이 난 날들 중, 노출 상위 20% 가 다르게 반응했나"
이고, 이건 **처치가 무작위 배정된 비교가 아니다** - 관측 특성으로 나눈 군이다.
위약군(같은 사건일의 노출 하위)도 노출이 내생이면 같이 오염된다.

RCT 근사는 처치를 **사건 발생 자체**로 두고, 대조군을 같은 날·같은 업종·유사
시총·유사 β 인데 **그 사건이 안 난** 종목으로 매칭한다.

## 두 설계 중 어느 쪽인가 (이 모듈이 실험의 B안이다)

  A안 샌드박스   에이전트가 SQL/파이썬을 써서 실행
  B안 연산 대수  닫힌 슬롯 조합 -> 코드가 검정으로 컴파일   <- 이 모듈

B안의 주장: RCT 근사에 필요한 자유도는 **6개 슬롯**으로 닫힌다.

    처치   사건 타입 + 역할            (닫힌 어휘: 사건타입 53 · 역할)
    대조   매칭 축 + 캘리퍼            (닫힌 어휘: 업종·시총·β)
    시점   PIT 클램프                  (전역 규약 - 선택 여지 없음)
    결과   층 y                        (닫힌 어휘: 시장·섹터·고유·되돌림)
    조절   조건 술어                   (닫힌 어휘: 계열족 × 변환 × 비교자)
    추정   ATT | CATE                  (닫힌 어휘)

이 여섯이 닫히면 샌드박스가 필요 없다. 필요해지는 순간은 **새 추정량**(IV·RDD·
합성통제)이고, 그건 프로젝트 규약상 사람의 스키마 변경이다 - 에이전트가 즉석에서
만들 것이 아니다.
"""
from __future__ import annotations

import sys

import numpy as np

from .lasso import MISS_MAX, moderate
from .paneltest import (ALPHA, MIN_N, PERMS, SEED, _base, _panel_rows,
                        _pctile, _two_sided)
from .paneltest import FEATURES, LAYER_Y

# 매칭 캘리퍼 — 전역 상수. 셀별 조정 금지 (조정하면 대조군을 고르는 셈이다).
CAL_LOGCAP = 0.75      # |Δln(시총)| 상한 ≈ 2.1배
CAL_BETA = 0.40        # |Δβ_m| 상한
MATCH_K = 3            # 처치 1건당 대조 최대 수
MIN_PAIRS = 20         # 짝이 이보다 적으면 판정불가
SMD_MAX = 0.10         # 매칭 후 표준화 평균차 상한 (RCT 관례). 넘으면 균형 실패
LEADS = (1, 2)         # 사전추세 위약: 처치 t−1·t−2 의 ATT 는 0 이어야 한다

# 짝 행의 고정 앞머리 폭. 조절자 열은 그 뒤에 붙는다 - `_summarize` 는 앞머리만
# 읽고 `_mod_panel` 은 뒤꼬리만 읽는다. 두 곳이 같은 상수를 봐야 어긋나지 않는다.
_MOD0 = 13         # run_trial:  tid d cid y_t y_c lc_t lc_c bm_t bm_c l1_t l1_c l2_t l2_c
_MULTI_D0 = 4      # run_multi:  tid d cid diff | D0..D_{K-1} | 조절자
# 귀무를 **이름으로** 말한다. ATT 와 조절은 다른 귀무를 쓰는데 산문이 둘 다
# "p=0.003" 으로만 쓰면 읽는 쪽이 같은 검정으로 오해한다.
_NULL_SAY = {"pair": "짝 부호 순열", "label": "날짜 층화 조건 라벨 순열"}

_TRIAL_SQL = """
, ev AS (
    SELECT DISTINCT e.instrument_id AS iid, e.trade_date AS d
    FROM v_event e
    WHERE e.event_type_code = '{etype}' {role} {refine}
      AND e.trade_date < DATE '{day}'
)
, b AS (
    SELECT g.instrument_id AS iid, g.trade_date AS d, g.{y} AS y,
           g.sector_code AS sec, g.mcap_pit AS mcap, g.beta_m AS bm,
           LAG(g.{y}, 1) OVER (PARTITION BY g.instrument_id ORDER BY g.trade_date) AS y_l1,
           LAG(g.{y}, 2) OVER (PARTITION BY g.instrument_id ORDER BY g.trade_date) AS y_l2{extra}
    FROM g
    WHERE g.sector_code IS NOT NULL AND g.mcap_pit > 0 AND g.beta_m IS NOT NULL
      AND g.{y} IS NOT NULL AND g.trade_date < DATE '{day}'
)
, t AS (SELECT b.* FROM b JOIN ev ON ev.iid = b.iid AND ev.d = b.d)
, c AS (SELECT b.* FROM b LEFT JOIN ev ON ev.iid = b.iid AND ev.d = b.d
        WHERE ev.iid IS NULL)
, m AS (
    SELECT t.iid AS tid, t.d AS d, c.iid AS cid,
           row_number() OVER (PARTITION BY t.iid, t.d
               ORDER BY abs(ln(t.mcap) - ln(c.mcap)) / {cal_m}
                      + abs(t.bm - c.bm) / {cal_b}) AS rn
    FROM t JOIN c ON c.d = t.d AND c.sec = t.sec
    WHERE abs(ln(t.mcap) - ln(c.mcap)) <= {cal_m} AND abs(t.bm - c.bm) <= {cal_b}
)
SELECT m.tid, CAST(m.d AS VARCHAR) AS d, m.cid,
       t.y AS y_t, c.y AS y_c,
       ln(t.mcap) AS lc_t, ln(c.mcap) AS lc_c, t.bm AS bm_t, c.bm AS bm_c,
       t.y_l1 AS l1_t, c.y_l1 AS l1_c, t.y_l2 AS l2_t, c.y_l2 AS l2_c{sel}
FROM m JOIN t ON t.iid = m.tid AND t.d = m.d
       JOIN c ON c.iid = m.cid AND c.d = m.d
WHERE m.rn <= {k}
"""


def run_trial(lake, day: str, *, etype: str, layer: str = "고유",
              role: str = "", stage: str = "", novelty: str = "", predicate: str = "",
              moderators: list[str] | None = None,
              k: int = MATCH_K) -> dict:
    """사건 = 처치, 매칭 대조군 = 위약. ATT 와 (조절자를 넘기면) 라쏘 조절자 검정.

    반환 슬롯은 판정을 담지 않는다 - 게이트는 호출자가 `edge_gate` 로 세운다.
    (판정 자리를 여기 두면 '수치는 코드가, 판정은 코드가' 가 두 곳이 된다.)

    ## 두 검정은 귀무가 다르다 - 그래서 분리한다

        ATT   짝 부호 순열                "그 처치는 효과가 없다"
        조절  날짜 층화 조건 라벨 순열     "조건이 효과를 안 바꾼다"

    ATT 는 `_summarize` 가 지금까지와 **한 글자도 다르지 않게** 낸다. 조절자를 넘겨도
    ATT 의 행 집합·난수열·계산이 그대로다 - 조절이 ATT 를 움직이면 그건 개편이 아니라
    회귀다. 조절은 `lasso.moderate()` 가 따로 푼다.

    ## 왜 절단(백분위 임계 + 비교자)을 폐기했나

    이전 계약은 `cond_key`·`cond_pct`·`cond_cmp` 로 조건을 이진화해 하나만 봤다. 셋 다
    틀렸다. (1) 이진화는 정보를 죽인다 - 79번째 백분위와 21번째가 같은 '미충족' 이
    된다. (2) 임계 0.8 은 임의다 - 어디서 왔는지 아무도 못 댄다. (3) 조건 하나씩 재면
    J 번 재는 셈인데 다중비교가 안 잡혔다. 지금은 백분위를 [0,1] 실수 그대로 싣고
    J 개를 **한 번에** 넣는다 - maxT 순열이 다중비교를 귀무 분포에 흡수한다.

    `moderators` 원소는 `"계열족/변환"` (=`FEATURES` 키). 못 재는 조합을 조용히 버리면
    산문이 없는 조건을 말하게 되므로, 하나라도 모르면 전체를 판정불가로 되돌린다.
    """
    y = LAYER_Y.get(layer)
    if y is None:
        return {"verdict": "판정불가", "reason": f"층은 {sorted(LAYER_Y)} 중 하나다"}
    mods = list(moderators or [])
    bad = _unknown_moderators(mods)
    if bad:
        return {"verdict": "판정불가", "reason": bad}
    cols = [FEATURES[tuple(m.split("/"))] for m in mods]
    sql = (_base(day) + _TRIAL_SQL).format(
        etype=etype, day=day, y=y, k=k,
        role=f"AND e.role_code = '{role}'" if role else "",
        refine=" ".join(
            f"AND e.{c} = '{v}'" for v, c in
            ((stage, "lifecycle_stage"), (novelty, "novelty_status"),
             (predicate, "predicate_code")) if v),
        cal_m=CAL_LOGCAP, cal_b=CAL_BETA,
        extra="".join(f", g.{c} AS m{i}" for i, c in enumerate(cols)),
        sel="".join(f", t.m{i}" for i in range(len(cols))))
    try:
        rows = _panel_rows(lake, sql, strict=False)
    except Exception as e:                          # noqa: BLE001 - 부재는 사유와 함께
        return {"verdict": "판정불가", "reason": f"{type(e).__name__}: {str(e)[:120]}"}
    rows = [r for r in rows if r[3] is not None and r[4] is not None]
    if len(rows) < MIN_PAIRS:
        return {"verdict": "판정불가",
                "reason": f"매칭 짝 {len(rows)} < {MIN_PAIRS} - 캘리퍼 안에 대조군이 없다"
                          f" (업종 동일 · |Δln시총|≤{CAL_LOGCAP} · |Δβ|≤{CAL_BETA})"}
    tag = "직접 " + etype.split(".")[-1] + "".join(
        f" · {v}" for v in (stage, novelty, predicate, role) if v)
    out = _summarize(rows, layer=layer, k=k, tag=tag)
    out["etype"], out["day"] = etype, day
    if not mods:
        return out
    panel, out["mod_dropped_missing"], reason = _mod_panel(rows, mods, base=_MOD0)
    if panel is None:
        out["moderation"], out["mod_reason"] = None, reason
        return out
    idx, C, names = panel
    diff = np.array([float(r[3]) - float(r[4]) for r in rows])
    dates = np.array([str(r[1]) for r in rows])
    out["moderation"] = moderate(diff[idx], C, dates[idx], names)
    return out


def _unknown_moderators(mods: list[str]) -> str:
    """모르는 조절자 이름이면 사유 문자열, 전부 알면 빈 문자열.

    조용히 버리지 않는 것이 요점이다. 오타 하나로 조절자가 사라지면 산출물은
    '그 조건은 효과를 안 바꿨다' 로 읽히는데 실제로는 **재지도 않았다**.
    """
    bad = [m for m in mods if tuple(m.split("/")) not in FEATURES]
    return (f"조절자 {bad} 은 못 잰다 - 재는 것: "
            f"{sorted('/'.join(f) for f in FEATURES)}") if bad else ""


def _mod_panel(rows: list, mods: list[str], *, base: int
               ) -> tuple[tuple | None, dict[str, str], str]:
    """짝 행 → 조절자 설계행렬. `((행 인덱스, X, 이름) | None, 결측제외, 사유)`.

    ## 결측을 어떻게 결정하는가 (**대입 금지**)

    재무 6종(`lev_*`·`prof_*`·`grow_lvl`·`icov_lvl`)은 v_fin ASOF 조인이라 결측이 흔하다.
    22열 **전수 관측** 행만 쓰면 열별 관측 확률이 곱해져 n 이 급감한다 - 열 하나가 30%
    비면 나머지 21열이 완벽해도 표본의 30% 가 날아간다. 그렇다고 채워 넣으면(평균·
    중앙값·전기값) 이 저장소의 '부재는 부재로 말한다' 와 정면으로 어긋난다: 채운 값에
    붙은 계수는 자료가 아니라 대입 규칙의 그림자다.

    그래서 두 관문으로 나눈다.
      ① **열 관문**: 결측률 > `MISS_MAX` 인 열은 후보에서 뺀다 + 사유를 남긴다.
         버렸다는 사실이 곧 산출물의 일부다 - 침묵하면 '안 뽑혔다' 로 오독된다.
      ② **행 관문**: 살아남은 열들의 전수 관측 행만 쓴다. 남은 열은 각각 결측이
         `MISS_MAX` 이하이므로 교집합 손실에 상한이 있다.
    ①을 건너뛰고 ②만 하면 결측 심한 열 하나가 표본 전체를 끌어내린다. ②를 건너뛰고
    열마다 다른 행을 쓰면 계수들이 서로 다른 모집단의 것이라 한 식에 못 들어간다.

    처치 효과는 이 관문 **밖**에서 전량 표본으로 이미 산출됐다. 조절자가 그 표본을
    깎으면 그건 개편이 아니라 손실이다.
    """
    n = len(rows)
    dropped: dict[str, str] = {}
    live: list[tuple[int, str]] = []
    for j, name in enumerate(mods):
        miss = sum(1 for r in rows if r[base + j] is None) / n
        if miss > MISS_MAX:
            dropped[name] = f"결측률 {miss * 100:.0f}% > {MISS_MAX * 100:.0f}%"
        else:
            live.append((base + j, name))
    if not live:
        return None, dropped, (f"조절자 후보 {len(mods)}개가 전부 결측률 "
                               f"{MISS_MAX * 100:.0f}% 초과 - 대입은 하지 않는다")
    idx = np.array([i for i, r in enumerate(rows)
                    if all(r[c] is not None for c, _ in live)], dtype=int)
    if len(idx) < MIN_PAIRS:
        return None, dropped, (
            f"결측 행을 드러낸 뒤 짝 {len(idx)} < {MIN_PAIRS} (전체 {n}) - 조절자 "
            f"{len(live)}열 전수 관측 행이 얇다. 처치 효과는 {n}짝 전량으로 냈다")
    # 백분위는 **이 표본 안에서** 매긴다. 원 단위를 섞으면(ROE %p 와 PBR 배수) 공통
    # λ 가 단위 큰 열만 살려 선택이 스케일의 함수가 된다.
    X = np.column_stack([_pctile(np.array([float(rows[i][c]) for i in idx]))
                         for c, _ in live])
    return (idx, X, [nm for _, nm in live]), dropped, ""


def say(r: dict) -> str:
    """시행 결과를 한 문단으로. 판정은 호출자가 세운다 - 여기서 의견을 내지 않는다."""
    if r.get("verdict") != "계산됨":
        return f"RCT 근사 판정불가 — {r.get('reason', '?')}"
    s = (f"RCT 근사(매칭 위약): 처치 {r['treated']}건 · 짝 {r['pairs']}개 · "
         f"{r['dates']}일 · ATT {r['att'] * 100:+.3f}%p "
         f"(양측 p={r['p']:.3f} · 귀무 {_NULL_SAY[r.get('null_kind', 'pair')]}) · "
         f"처치 {r['y_t'] * 100:+.2f}% vs 대조 {r['y_c'] * 100:+.2f}% "
         f"[{r['layer']}층 · 업종 동일 · |Δln시총|≤{r['caliper'][0]} · "
         f"|Δβ|≤{r['caliper'][1]} · k≤{r['k']}]")
    if r.get("att_adj") is not None:
        s += (f"\n  편향보정 ATT {r['att_adj'] * 100:+.3f}%p (p={r['p_adj']:.3f}) "
              "— Δ공변량 0 에서의 절편 (캘리퍼 잔차 제거)")
    sm = " · ".join(f"{k} SMD {v:+.3f}" for k, v in r["smd"].items())
    s += (f"\n  균형: {sm} (상한 {SMD_MAX}) — "
          + ("통과" if r["balanced"] else "**실패: 이 ATT 는 처치효과가 아니라 매칭 잔차다**"))
    if r.get("lead"):
        bits = []
        for j, v in sorted(r["lead"].items()):
            bits.append(f"t−{j} 표본부족" if v is None else
                        f"t−{j} ATT {v[0] * 100:+.3f}%p (p={v[1]:.3f}, n={v[2]})")
        s += ("\n  사전추세 위약: " + " · ".join(bits) + " — "
              + ("0 과 구분 안 됨 = 통과" if r.get("pretrend_ok") else
                 "**유의: 처치 전에 이미 갈렸다 = 매칭 실패 또는 선견**"))
    s += _say_moderation(r)
    return s


def _say_moderation(r: dict) -> str:
    """조절 결과 문장. **'원인' 이라고 쓰지 않는다.**

    라쏘가 뽑은 것은 δ 를 예측하는 데 유용한 열이지 인과 조절자가 아니다. 조건은
    무작위 배정되지 않았고(준무작위인 것은 처치뿐이다), 뽑히지 않은 교란과 상관될 수
    있다. 산문이 '원인' 으로 승격시키면 게이트가 못 잡는다 - 코드가 그 단어를 쓰지
    않는 것이 유일한 방벽이다.

    `run_trial`·`run_multi` 가 같은 조각을 쓴다. 두 곳에서 쓰면 어휘가 갈린다.
    """
    out = ""
    if r.get("mod_dropped_missing"):
        out += ("\n  조절자 후보 제외(결측 · 대입 안 함): " + " · ".join(
            f"{nm} {why}" for nm, why in sorted(r["mod_dropped_missing"].items())))
    if "moderation" not in r:
        return out
    m = r["moderation"]
    if m is None:
        return out + f"\n  조절 판정불가 — {r.get('mod_reason', '?')}"
    if m.get("verdict") != "계산됨":
        return out + f"\n  조절 판정불가 — {m.get('reason', '?')}"
    sel = " · ".join(
        f"{nm} {v * 100:+.3f}%p (Π={m['pi'][nm]:.2f}, p={m['p_step'].get(nm, 1.0):.3f})"
        for nm, v in sorted(m["selected"].items(), key=lambda kv: -abs(kv[1])))
    # `n/짝` 로 쓴다. 살아남은 수만 쓰면 행 관문이 표본을 얼마나 깎았는지 안 보인다 -
    # 실측: 22열이 각각 결측률 상한(20%)에 걸치면 전수 관측 행은 400짝 중 76개다.
    # 열 관문은 **열별** 상한이라 교집합 손실을 못 막는다. 그 사실을 숫자로 남긴다.
    out += (f"\n  조절(라쏘 · 후보 {m['j']}열 · n={m['n']}/{r['pairs']}짝 · "
            f"λ={m['lam']:g}): "
            + (sel or "뽑힌 열 없음 — 조건이 효과를 바꾼다는 증거가 없다")
            + f" · maxT p={m['p_max']:.3f} (귀무 {_NULL_SAY[m['null_kind']]})")
    out += ("\n    크기는 post-LASSO 계수, 유의는 순열 p — 두 출처를 섞지 않는다. "
            "뽑힌 열은 δ 를 **조절**하는 것이지 만드는 것이 아니다 "
            "(조건은 무작위 배정이 아니다).")
    if m.get("dropped_collinear"):
        out += ("\n    준중복 제외: " + " · ".join(
            f"{a} ← {b}" for a, b in sorted(m["dropped_collinear"].items())))
    return out


def _selfcheck() -> None:
    """대수가 표현할 수 있는 것과 없는 것을 명시적으로 남긴다.

    이 목록이 곧 '샌드박스가 필요해지는 경계' 다. 넷 다 프로젝트 규약상 **사람의
    스키마 변경** 사안이지 에이전트가 즉석에서 만들 것이 아니다.
    """
    assert CAL_LOGCAP > 0 and CAL_BETA > 0 and MATCH_K >= 1
    assert MIN_PAIRS >= MIN_N // 2
    assert LEADS == (1, 2)
    unsupported = ("IV(도구변수)", "RDD(회귀단절)", "합성통제", "성향점수(로짓) 매칭")
    # 구현한 것: 사전추세 위약(리드) · 다중 처치 동시 투입 · 균형(SMD) · 라쏘 조절자
    assert len(unsupported) == 4
    print("ok · 미지원(샌드박스 경계):", " · ".join(unsupported))

_MULTI_SQL = """
, ev AS (
    SELECT DISTINCT e.instrument_id AS iid, e.trade_date AS d, e.event_type_code AS et
    FROM v_event e
    WHERE e.event_type_code IN ({types}) AND e.trade_date < DATE '{day}'
)
, ind AS (
    SELECT iid, d{flags} FROM ev GROUP BY iid, d
)
, b AS (
    SELECT g.instrument_id AS iid, g.trade_date AS d, g.{y} AS y,
           g.sector_code AS sec, g.mcap_pit AS mcap, g.beta_m AS bm{extra}
    FROM g
    WHERE g.sector_code IS NOT NULL AND g.mcap_pit > 0 AND g.beta_m IS NOT NULL
      AND g.{y} IS NOT NULL AND g.trade_date < DATE '{day}'
)
, t AS (SELECT b.*{icols} FROM b JOIN ind ON ind.iid = b.iid AND ind.d = b.d)
, c AS (SELECT b.* FROM b LEFT JOIN ind ON ind.iid = b.iid AND ind.d = b.d
        WHERE ind.iid IS NULL)
, m AS (
    SELECT t.iid AS tid, t.d AS d, c.iid AS cid,
           row_number() OVER (PARTITION BY t.iid, t.d
               ORDER BY abs(ln(t.mcap) - ln(c.mcap)) / {cal_m}
                      + abs(t.bm - c.bm) / {cal_b}) AS rn
    FROM t JOIN c ON c.d = t.d AND c.sec = t.sec
    WHERE abs(ln(t.mcap) - ln(c.mcap)) <= {cal_m} AND abs(t.bm - c.bm) <= {cal_b}
)
SELECT m.tid, CAST(m.d AS VARCHAR) AS d, m.cid, t.y - c.y AS diff{tcols}{sel}
FROM m JOIN t ON t.iid = m.tid AND t.d = m.d
       JOIN c ON c.iid = m.cid AND c.d = m.d
WHERE m.rn <= {k}
"""


def run_multi(lake, day: str, etypes: list[str], *, layer: str = "고유",
              moderators: list[str] | None = None, k: int = MATCH_K) -> dict:
    """**다중 처치 동시 투입.** 같은 날 여러 사건이 있으면 하나씩 재면 서로 흡수한다.

        δₚ = Σₖ bₖ·D_{p,k} + Σⱼ dⱼ·c_{p,j} + uₚ        (b 벌점 없음 · d 벌점)

    실측 000660 07-29 에 실적·목표주가·서킷브레이커가 다 있었다. 하나씩 재면 각
    ATT 가 나머지 둘의 효과를 흡수한다 - 그러면 '왜 **이** 이벤트' 에 답할 수 없다.
    대조군은 **어느 처치도 안 받은** 종목이다.

    처치 지시자 D 는 라쏘의 **벌점 밖 열**(`free`)로 들어간다. 벌점을 물리면 처치
    효과가 0 으로 수축돼 게이트가 무너진다 - 절편을 벌점 밖에 둔 것과 같은 이유다.
    그래서 다중 처치 대비와 조절자 선택이 **한 적합** 안에서 풀린다. 둘을 따로 재면
    각 처치의 대비는 조절자 없이, 조절자 계수는 처치 혼합 없이 추정돼 두 수치가 서로
    다른 식의 것이 된다 - 산문이 그걸 나란히 놓으면 없는 일관성을 주장하는 셈이다.

    귀무는 둘이고 섞지 않는다:
      ATT   짝 부호 순열 (날짜 층화 자동 - 짝이 같은 날 안에서만 만들어졌다)
      조절  날짜 층화 조건 라벨 순열. 처치 지시자는 **안 섞는다** - 섞으면 귀무가
            '조건이 효과를 안 바꾼다' 가 아니라 '처치가 없다' 로 바뀐다.
    """
    y = LAYER_Y.get(layer)
    if y is None:
        return {"verdict": "판정불가", "reason": f"층은 {sorted(LAYER_Y)} 중 하나다"}
    if not 2 <= len(etypes) <= 6:
        return {"verdict": "판정불가", "reason": "다중 처치는 2~6개다"}
    mods = list(moderators or [])
    bad = _unknown_moderators(mods)
    if bad:
        return {"verdict": "판정불가", "reason": bad}
    cols = [FEATURES[tuple(m.split("/"))] for m in mods]
    flags = "".join(
        f", max(CASE WHEN et = '{e}' THEN 1 ELSE 0 END) AS d{i}"
        for i, e in enumerate(etypes))
    sql = (_base(day) + _MULTI_SQL).format(
        types=", ".join(f"'{e}'" for e in etypes), day=day, y=y, k=k,
        cal_m=CAL_LOGCAP, cal_b=CAL_BETA, flags=flags,
        icols="".join(f", ind.d{i}" for i in range(len(etypes))),
        tcols="".join(f", t.d{i}" for i in range(len(etypes))),
        extra="".join(f", g.{c} AS m{i}" for i, c in enumerate(cols)),
        sel="".join(f", t.m{i}" for i in range(len(cols))))
    try:
        rows = _panel_rows(lake, sql, strict=False)
    except Exception as e:                          # noqa: BLE001
        return {"verdict": "판정불가", "reason": f"{type(e).__name__}: {str(e)[:120]}"}
    rows = [r for r in rows if r[3] is not None]
    if len(rows) < MIN_PAIRS:
        return {"verdict": "판정불가", "reason": f"매칭 짝 {len(rows)} < {MIN_PAIRS}"}
    diff = np.array([float(r[3]) for r in rows])
    dates = np.array([str(r[1]) for r in rows])
    D = np.array([[float(r[_MULTI_D0 + i]) for i in range(len(etypes))] for r in rows])
    if (D.sum(axis=0) < 3).any():
        thin = [etypes[i] for i in range(len(etypes)) if D[:, i].sum() < 3]
        return {"verdict": "판정불가", "reason": f"처치 표본 3건 미만: {thin}"}

    def fit(v: np.ndarray) -> np.ndarray:
        beta, *_ = np.linalg.lstsq(D, v, rcond=None)
        return beta

    obs = fit(diff)
    rng = np.random.default_rng(SEED)
    null = np.empty((PERMS, len(etypes)))
    for i in range(PERMS):
        null[i] = fit(diff * rng.choice([-1.0, 1.0], size=len(diff)))
    ps = [_two_sided(float((null[:, i] >= obs[i]).mean())) for i in range(len(etypes))]
    # 단독 투입과 비교하면 흡수량이 보인다.
    solo = {}
    for e in etypes:
        r1 = run_trial(lake, day, etype=e, layer=layer, k=k)
        solo[e] = (r1["att"], r1["p"]) if r1.get("verdict") == "계산됨" else None
    out = {"verdict": "계산됨", "null_kind": "pair", "layer": layer, "pairs": len(rows),
           "etypes": etypes, "att": [float(v) for v in obs], "p": ps,
           "n_treat": [int(v) for v in D.sum(axis=0)], "solo": solo, "day": day}
    if not mods:
        return out
    panel, out["mod_dropped_missing"], reason = _mod_panel(
        rows, mods, base=_MULTI_D0 + len(etypes))
    if panel is None:
        out["moderation"], out["mod_reason"] = None, reason
        return out
    idx, C, names = panel
    out["moderation"] = moderate(
        diff[idx], np.column_stack([D[idx], C]), dates[idx], list(etypes) + names,
        free=np.array([True] * len(etypes) + [False] * C.shape[1]))
    return out


def say_multi(r: dict) -> str:
    if r.get("verdict") != "계산됨":
        return f"다중 처치 판정불가 — {r.get('reason', '?')}"
    out = [f"다중 처치 동시 투입 ({r['layer']}층 · 짝 {r['pairs']}개 · 귀무 "
           f"{_NULL_SAY[r['null_kind']]}) — 대조군은 어느 처치도 안 받은 종목"]
    for e, a, p, n in zip(r["etypes"], r["att"], r["p"], r["n_treat"]):
        s = r["solo"].get(e)
        solo = (f" · 단독 {s[0] * 100:+.3f}%p (p={s[1]:.3f})" if s else " · 단독 판정불가")
        absorb = ("" if not s else
                  f" · 흡수 {abs(s[0] - a) * 100:+.3f}%p" if abs(s[0] - a) > 1e-9 else "")
        out.append(f"  {e.split('.')[-1]:<22} n={n:<5} ATT {a * 100:+.3f}%p "
                   f"(p={p:.3f}){solo}{absorb}")
    m = r.get("moderation")
    if m is not None and m.get("verdict") == "계산됨":
        # **차이만** 쓴다. 자유 열 계수의 낱값은 절편과 함께만 식별된다 - 모든 짝이
        # 처치를 하나 이상 받으므로 D 블록과 절편이 준중복이고, 둘 사이의 배분은
        # 좌표하강의 기하가 정한다. 낱값을 위의 ATT 옆에 놓으면 서로 다른 설계의
        # 수치를 같은 것으로 읽게 만든다(실측: 같은 자료에서 LAUNCH ATT +2.42%p 인데
        # 자유 열 계수는 -0.05%p 였다 - 절편이 공통 몫을 가져갔을 뿐이다).
        # 처치 **간** 대비는 절편이 소거돼 두 설계에서 같은 것을 뜻한다. 그것이
        # '왜 **이** 이벤트인가' 의 답이고, 이 모듈이 존재하는 이유다.
        b0, a0 = m["free_coef"][r["etypes"][0]], r["att"][0]
        out.append(f"  처치 간 대비 (기준 {r['etypes'][0].split('.')[-1]} · 조절자 "
                   "동시 적합 · 처치는 벌점 밖이라 수축되지 않는다): " + " · ".join(
                       f"{e.split('.')[-1]} {(m['free_coef'][e] - b0) * 100:+.3f}%p"
                       f" (조절자 없이 {(a - a0) * 100:+.3f}%p)"
                       for e, a in zip(r["etypes"][1:], r["att"][1:])))
    out.append(_say_moderation(r).lstrip("\n"))
    return "\n".join(x for x in out if x)


_SPILL_SQL = """
, ev AS (
    SELECT DISTINCT e.instrument_id AS iid, e.trade_date AS d
    FROM v_event e
    WHERE e.event_type_code = '{etype}' AND e.trade_date < DATE '{day}'
)
, sp AS (
    -- **관계 처치**: 상대에게 사건이 났고 나에게는 안 났다. 그게 전이의 정의다.
    SELECT DISTINCT l.dst AS iid, ev.d AS d
    FROM v_link l JOIN ev ON ev.iid = l.src AND l.link_date <= ev.d
    WHERE l.link_type = '{rel}'
    UNION
    SELECT DISTINCT l.src AS iid, ev.d AS d
    FROM v_link l JOIN ev ON ev.iid = l.dst AND l.link_date <= ev.d
    WHERE l.link_type = '{rel}'
)
, b AS (
    SELECT g.instrument_id AS iid, g.trade_date AS d, g.{y} AS y,
           g.sector_code AS sec, g.mcap_pit AS mcap, g.beta_m AS bm,
           LAG(g.{y}, 1) OVER (PARTITION BY g.instrument_id ORDER BY g.trade_date) AS y_l1,
           LAG(g.{y}, 2) OVER (PARTITION BY g.instrument_id ORDER BY g.trade_date) AS y_l2
    FROM g
    WHERE g.sector_code IS NOT NULL AND g.mcap_pit > 0 AND g.beta_m IS NOT NULL
      AND g.{y} IS NOT NULL AND g.trade_date < DATE '{day}'
)
, t AS (
    SELECT b.* FROM b JOIN sp ON sp.iid = b.iid AND sp.d = b.d
    LEFT JOIN ev ON ev.iid = b.iid AND ev.d = b.d
    WHERE ev.iid IS NULL          -- 자기에게 사건이 났으면 전이가 아니라 직접이다
)
, c AS (
    SELECT b.* FROM b
    LEFT JOIN sp ON sp.iid = b.iid AND sp.d = b.d
    LEFT JOIN ev ON ev.iid = b.iid AND ev.d = b.d
    WHERE sp.iid IS NULL AND ev.iid IS NULL
)
, m AS (
    SELECT t.iid AS tid, t.d AS d, c.iid AS cid,
           row_number() OVER (PARTITION BY t.iid, t.d
               ORDER BY abs(ln(t.mcap) - ln(c.mcap)) / {cal_m}
                      + abs(t.bm - c.bm) / {cal_b}) AS rn
    FROM t JOIN c ON c.d = t.d AND c.sec = t.sec
    WHERE abs(ln(t.mcap) - ln(c.mcap)) <= {cal_m} AND abs(t.bm - c.bm) <= {cal_b}
)
SELECT m.tid, CAST(m.d AS VARCHAR) AS d, m.cid, t.y AS y_t, c.y AS y_c,
       ln(t.mcap) AS lc_t, ln(c.mcap) AS lc_c, t.bm AS bm_t, c.bm AS bm_c,
       t.y_l1 AS l1_t, c.y_l1 AS l1_c, t.y_l2 AS l2_t, c.y_l2 AS l2_c
FROM m JOIN t ON t.iid = m.tid AND t.d = m.d
       JOIN c ON c.iid = m.cid AND c.d = m.d
WHERE m.rn <= {k}
"""


def run_spillover(lake, day: str, *, etype: str, rel: str,
                  layer: str = "고유", k: int = MATCH_K) -> dict:
    """**관계 처치(전이)**: 상대에게 사건이 났고 나에겐 안 났다.

    '왜 **이** 종목인가' 에 답하는 유일한 경로다. 층 분해도 매칭도 종목 개별성을
    지우는 것이 목적이라 그 질문에 답할 수 없다 - 개별성은 (a) 조절자, (b) 관계
    두 곳에서만 온다.

    이전 `_relation_test` 는 `assignable=False` 였다 (소스-타깃 5분봉 창 정렬 부재).
    시행 대수는 **일 단위 ATT** 라 창 정렬이 필요 없다 - 하루의 몫을 쪼개는 것이
    아니라 '전이가 있었나' 를 묻기 때문이다. 그래서 배정 제약이 풀린다.

    처치군에서 **자기 사건은 제외**한다. 자기에게 났으면 전이가 아니라 직접이다.
    """
    from .vocab import RELATIONS
    if rel not in RELATIONS:
        return {"verdict": "판정불가", "reason": f"관계는 {sorted(RELATIONS)} 중 하나다"}
    y = LAYER_Y.get(layer)
    if y is None:
        return {"verdict": "판정불가", "reason": f"층은 {sorted(LAYER_Y)} 중 하나다"}
    sql = (_base(day) + _SPILL_SQL).format(
        etype=etype, rel=rel, day=day, y=y, k=k, cal_m=CAL_LOGCAP, cal_b=CAL_BETA)
    try:
        rows = _panel_rows(lake, sql, strict=False)
    except Exception as e:                          # noqa: BLE001
        return {"verdict": "판정불가", "reason": f"{type(e).__name__}: {str(e)[:120]}"}
    rows = [r for r in rows if r[3] is not None and r[4] is not None]
    if len(rows) < MIN_PAIRS:
        return {"verdict": "판정불가",
                "reason": f"전이 짝 {len(rows)} < {MIN_PAIRS} — {rel} 링크가 얇다"}
    return _summarize(rows, layer=layer, k=k, tag=f"전이 {rel}")


def _summarize(rows: list, *, layer: str, k: int, tag: str = "") -> dict:
    """짝 목록 → ATT · 균형(SMD) · 사전추세 위약. `run_trial`/`run_spillover` 공용.

    컬럼 규약: (tid, d, cid, y_t, y_c, lc_t, lc_c, bm_t, bm_c, l1_t, l1_c, l2_t, l2_c)
    이 13열이 앞머리(`_MOD0`)이고 조절자 열은 뒤에 붙는다 - 여기서는 **안 읽는다**.
    조절이 ATT 표본을 못 건드린다는 것이 그 분리의 요점이다.
    한 곳에서만 조립한다 - 두 곳이면 진단이 갈린다.
    """
    dates = np.array([str(r[1]) for r in rows])
    yt = np.array([float(r[3]) for r in rows])
    yc = np.array([float(r[4]) for r in rows])
    diff = yt - yc
    att = float(diff.mean())
    rng = np.random.default_rng(SEED)
    null = np.array([float((diff * rng.choice([-1.0, 1.0], size=len(diff))).mean())
                     for _ in range(PERMS)])
    p = _two_sided(float((null >= att).mean()))
    smd = {}
    for nm, a, b in (("ln시총", 5, 6), ("β_m", 7, 8)):
        av = np.array([float(r[a]) for r in rows])
        bv = np.array([float(r[b]) for r in rows])
        sd = float(np.sqrt((np.var(av, ddof=1) + np.var(bv, ddof=1)) / 2))
        smd[nm] = float((av.mean() - bv.mean()) / sd) if sd > 0 else 0.0
    # **편향 보정 (Abadie-Imbens).** 캘리퍼 안이면 통과시키므로 공변량 잔차가 남고,
    # 균형 실패(SMD>상한)가 실측에서 반복됐다(전이 셋 다 β_m SMD 0.125~0.151).
    # 캘리퍼를 셀별로 좁히는 것은 **대조군을 고르는 것**이라 금지 - 대신 짝 차이를
    # 공변량 차이에 회귀해 잔차 편향을 뺀다. ATT_adj = Δ공변량 0 에서의 절편.
    dlc = np.array([float(r[5]) - float(r[6]) for r in rows])
    dbm = np.array([float(r[7]) - float(r[8]) for r in rows])
    Xa = np.column_stack([np.ones(len(rows)), dlc, dbm])

    def _adj(v: np.ndarray) -> float:
        beta, *_ = np.linalg.lstsq(Xa, v, rcond=None)
        return float(beta[0])

    att_adj = _adj(diff)
    rg2 = np.random.default_rng(SEED)
    nadj = np.array([_adj(diff * rg2.choice([-1.0, 1.0], size=len(diff)))
                     for _ in range(PERMS)])
    p_adj = _two_sided(float((nadj >= att_adj).mean()))
    lead = {}
    for j, (a, b) in ((1, (9, 10)), (2, (11, 12))):
        pr = [(float(r[a]), float(r[b])) for r in rows
              if r[a] is not None and r[b] is not None]
        if len(pr) < MIN_PAIRS:
            lead[j] = None
            continue
        dl = np.array([x[0] - x[1] for x in pr])
        rg = np.random.default_rng(SEED)
        nl = np.array([float((dl * rg.choice([-1.0, 1.0], size=len(dl))).mean())
                       for _ in range(PERMS)])
        lead[j] = (float(dl.mean()), _two_sided(float((nl >= dl.mean()).mean())), len(dl))
    return {"verdict": "계산됨", "null_kind": "pair", "att": att, "p": p,
            "pairs": len(rows),
            "treated": len({(r[0], r[1]) for r in rows}), "dates": len(set(dates)),
            "y_t": float(yt.mean()), "y_c": float(yc.mean()),
            "caliper": (CAL_LOGCAP, CAL_BETA), "k": k, "layer": layer, "tag": tag,
            "smd": smd, "balanced": all(abs(v) <= SMD_MAX for v in smd.values()),
            "att_adj": att_adj, "p_adj": p_adj, "lead": lead,
            "pretrend_ok": all(v is None or v[1] >= ALPHA for v in lead.values())}


# ── 가설 → 검정 컨텍스트 전달 ────────────────────────────────────────────
# 가설 에이전트가 이미 본 것을 검정 에이전트가 다시 찾으면 왕복이 두 배가 되고,
# 더 나쁘게는 **다른 결론**이 나와 산출물이 흐려진다(8셀 실측의 그 병). 가설 층이
# 무엇을 봤고 무엇을 배제했는지를 그대로 넘긴다.
#
# 역할 분담 (사용자 설계):
#   가설 에이전트  거칠게 낸다        - 닫힌 슬롯, 방향은 의도
#   검정 에이전트  구체화한다          - CATE 로 '어느 조건에서' 를 찾고, 시장요인도 환원
#   설명 에이전트  함의만 받는다        - 조립만. 원자료를 다시 안 본다


def probe_brief(tuples: list, reports: list, screens: list | None = None,
                memory: list | None = None) -> str:
    """가설 층이 **이미 본 것**. 검정 에이전트의 첫 입력에 붙는다.

    셋을 넘긴다:
      - 세운 가설과 그 판정 (다시 세우지 말라는 뜻)
      - 격자 스크린에서 이미 훑은 (타입 × 노출) 조합 (다시 훑지 말라는 뜻)
      - 회상된 과거 판정 (이미 아는 것)
    """
    out = ["=== 가설 층이 이미 본 것 (중복 탐색 금지) ==="]
    for t, r in zip(tuples, reports):
        ex = f"{t.exposure.ident}/{t.exposure.transform}"
        cd = " ∧ ".join(f"{v.ident}/{v.transform}" for v in t.conditions) or "—"
        out.append(f"  [{t.channel}] {t.trigger.kind}:{t.trigger.ident} × {ex} "
                   f"· 조건 {cd} → {r.verdict}"
                   + (f" (n={r.n}, p={r.p:.3f})" if r.p is not None else "")
                   + (f" · {r.reason[:60]}" if r.reason else ""))
    if screens:
        seen = sorted({f"{s.get('type', '?').split('.')[-1]}×{s.get('exposure', '?')}"
                       for s in screens if "p2" in s})
        out.append(f"  격자에서 이미 훑은 조합 {len(seen)}개: "
                   + ", ".join(seen[:14]) + (" …" if len(seen) > 14 else ""))
    if memory:
        out.append("  과거 판정 회상: " + " · ".join(str(m)[:70] for m in memory[:3]))
    out.append("=== 검정 층이 할 일 ===")
    out.append("  1) 위 판정을 재현하지 말고 **구체화**한다: CATE 로 '어느 조건에서' 를 찾는다")
    out.append("  2) 시장요인으로 끝내지 말고 **왜 시장요인인가**를 환원한다 "
               "(밤사이 팩터 · 시장 사건 τ · 국면)")
    out.append("  3) 설명 층에는 **함의만** 넘긴다 - 원자료를 다시 열지 않게")
    return "\n".join(out)


def reduce_market(lake, day: str, *, symbol: str = "") -> dict:
    """**시장요인의 환원.** '시장이 79%' 로 끝내면 그건 분해이지 원인이 아니다.

    시장요인을 셋으로 쪼갠다 - 전부 이미 있는 재료다:
      ① 밤사이 해외 팩터   섹터 매칭 미국 지수 × β 구간 (gap_covariate·market_source)
      ② 장중 시장 사건     서킷브레이커·거시·정책의 τ (market_event_marks)
      ③ 국면              시장 수익 20일 변동성의 백분위 (regime_lvl)

    이 셋이 곧 '왜 시장요인인가' 의 답이고, 남는 것은 국내 미설명이다.
    검정 결과가 아니라 **환원 결과**라 판정을 붙이지 않는다 - 회계와 사실이다.
    """
    from .attribute import gap_covariate
    from .causeflow import _prev_trading_day
    from .layers import market_source, overnight
    out: dict = {"day": day, "since": _prev_trading_day(lake, day)}
    try:
        out["overnight"] = sorted(overnight(lake, day), key=lambda x: -abs(x[1]))[:4]
    except Exception as e:                          # noqa: BLE001
        out["overnight_err"] = str(e)[:90]
    if symbol:
        # 팩터는 갭 계산 성공과 무관하게 **먼저** 정해진다. ETF 는 5분봉이 없어
        # 자기 갭을 못 만들지만, 코스피 환원(market_source)은 팩터만 있으면 된다 -
        # 하나가 없다고 둘 다 침묵하면 직관적 요인을 놓친다.
        from .attribute import US_FACTOR_DEFAULT, _us_factor
        sym6 = symbol.split(".")[0]
        fsym = _us_factor(lake, sym6, day)
        out["factor_sym"] = fsym
        fname = None
        try:
            fr = lake.sql(f"SELECT any_value(name) FROM layers_daily "
                          f"WHERE kind='us' AND symbol='{fsym}'")
            fname = fr[0][0] if fr and fr[0][0] else fsym
        except Exception:                           # noqa: BLE001
            fname = fsym
        out["factor_name"] = fname
        try:
            ms0 = market_source(lake, day, proxy=fname)
            out["mkt_explained"] = ms0
        except Exception:                           # noqa: BLE001
            out["mkt_explained"] = None
        gc = gap_covariate(lake, symbol, day, 0.0)
        out["gap_factor"] = getattr(gc, "factor", None)
        out["gap_factor_ret"] = getattr(gc, "factor_ret", None)
        out["gap_beta"] = (getattr(gc, "beta_lo", None), getattr(gc, "beta_hi", None))
        out["gap_reason"] = getattr(gc, "reason", "")
    return out


def say_market(r: dict, iid: str = "", lake=None) -> str:
    """환원 결과를 문장으로. 설명 층에 넘길 **함의**만 담는다."""
    out = ["=== 시장요인 환원 (왜 시장인가) ==="]
    if r.get("overnight"):
        out.append("  ① 밤사이 해외: " + " · ".join(
            f"{nm} {v * 100:+.2f}%" for nm, v in r["overnight"]))
    if r.get("factor_name"):
        out.append(f"     섹터 매칭 팩터: {r['factor_name']} ({r.get('factor_sym')})"
                   + (f" — 자기 갭 미계측: {r['gap_reason'][:64]}"
                      if r.get("gap_reason") else ""))
    if r.get("gap_factor") and r.get("gap_factor_ret") is not None:
        fr = r.get("gap_factor_ret")
        bl, bh = r.get("gap_beta", (None, None))
        out.append(f"     섹터 매칭 팩터 {r['gap_factor']}"
                   + (f" {fr * 100:+.2f}%" if fr is not None else " (미계측)")
                   + (f" × β [{bl:.2f}, {bh:.2f}]" if bl is not None else "")
                   + (f" — {r['gap_reason']}" if r.get("gap_reason") else ""))
    ms = r.get("mkt_explained")
    if ms:
        out.append(f"     코스피 중 그 팩터로 설명되는 몫 "
                   f"[{ms[0] * 100:+.2f}, {ms[1] * 100:+.2f}]%p")
    if lake is not None and iid:
        from .causeflow import market_event_marks
        marks = market_event_marks(lake, iid, r["day"])
        out.append("  ② 장중 시장 사건 τ: " + (" · ".join(marks) if marks else "없음"))
    out.append("  ③ 국면: 계열족 `국면/수준` (시장 20일 변동성) 을 조절자로 넣어 검정한다")
    out.append("  → 남는 것이 국내 미설명이다. 여기까지가 환원이고, 그 밖은 접는다.")
    return "\n".join(out)


def main() -> None:
    """다중처치·전이 시행은 셀 러너가 아니라 여기서 부른다 - 셀 하나가 아니라
    **타입 사이**를 묻는 질의이기 때문이다(동시 투입의 흡수, 관계 전이).

    사용:  python -m edge_analysis.statics.core.trial multi  <YYYY-MM-DD> <타입,타입,...>
           python -m edge_analysis.statics.core.trial spill  <YYYY-MM-DD> <타입> <관계>
    """
    if len(sys.argv) < 2 or sys.argv[1] not in ("multi", "spill"):
        _selfcheck()
        return
    from .duck import CausalLake
    lake, day = CausalLake(), sys.argv[2]
    if sys.argv[1] == "multi":
        # 조절자는 **전량**을 넣는다 - 고르면 그게 선택이고, 결측 관문은 코드가 친다.
        print(say_multi(run_multi(lake, day, sys.argv[3].split(","),
                                  moderators=sorted("/".join(f) for f in FEATURES))))
    else:
        print(say(run_spillover(lake, day, etype=sys.argv[3], rel=sys.argv[4])))


if __name__ == "__main__":
    main()
