"""시장 사건의 인과 검정 — **단위를 날짜로 바꾼다**.

## 왜 종목 패널로는 못 하나

기존 시행(`trial.run_trial`)의 단위는 (종목, 날짜)이고 대조군은 **같은 날 같은 업종의
사건 없는 종목**이다. 시장 광역 사건(서킷브레이커·금리·관세·지수편입 규칙)은 그 날
**전 종목이 처치**라 대조군이 없다 - SUTVA 위반이고, 억지로 재면 사건 없는 종목을
'대조' 로 불러 시장 충격을 종목 이질성으로 위장한다.

그래서 이 모듈은 단위를 **날짜**로 바꾼다:

    처치   그 사건 타입이 발생한 거래일
    대조   매칭된 거래일 (국면 · 요일 · 직전 추세를 맞춘 날)
    결과   시장 지수(KODEX 200) 당일 로그수익

## 매칭 기준 (사건연구의 캘리퍼)

  국면      직전 20일 실현변동성 - 변동성 국면이 다르면 같은 뉴스가 다른 크기를 낸다
  요일      월·금 효과와 만기 주기를 섞지 않는다
  직전 추세  직전 5일 누적 - 추세 중간의 하루와 반전 직후의 하루는 다르다

대조일에서 **같은 타입은 물론 다른 시장 광역 사건도 뺀다**(`clean_controls`).
안 빼면 '사건 있는 날 vs 다른 사건 있는 날' 이 되어 ATT 가 두 처치의 차가 된다.

## 정직하게 못 하는 것

  · 하루에 여러 타입이 겹치면 처치가 배타적이 아니다. 겹침을 세어 보고한다.
  · 처치일 수가 곧 표본이다 - 실측 상한 59일(RULE_CHANGE), 대부분 10~28일.
    `MIN_DAYS` 미달은 **판정불가**다(기각이 아니다).
  · 국내 사건과 밤사이 해외가 같은 날 오면 분리 불가 - 갭을 뺀 **장중** 수익을
    결과로 쓰는 옵션(`intraday=True`)이 그 오염을 줄이지만 없애지는 못한다.

사용:  python -m edge_analysis.statics.mkttrial <YYYY-MM-DD> [event_type]
"""
from __future__ import annotations

import sys

import numpy as np

from .paneltest import ALPHA, PERMS, SEED, _base, _two_sided

MIN_DAYS = 10            # 처치일 최소 - 미달은 판정불가
MATCH_K = 3              # 처치일당 대조일 수
VOL_WIN = 20             # 국면 창 (실현변동성)
TREND_WIN = 5            # 직전 추세 창
VOL_CAL = 0.35           # |Δlog 변동성| 캘리퍼
TREND_CAL = 0.04         # |Δ직전 누적| 캘리퍼
MARKET_WIDE = ("MARKET_STRUCTURE", "MACRO", "POLICY")


def market_days(lake, day: str, symbol: str = "069500") -> tuple[list, np.ndarray]:
    """(날짜들, 로그수익). 시장 지수의 일별 계열 - `day` 까지만(선견 금지)."""
    # **날짜로 뭉친다.** `layers_daily` 는 069500 을 kind='market' 과 'stock' 두 번
    # 싣는다(같은 종가) - 그대로 읽으면 897일이 1794행이 되고 로그차가
    # [0, 실제, 0, ...] 로 오염된다. 실측에서 그 상태로 ATT 가 나왔다.
    rows = lake.sql(
        "SELECT CAST(date AS DATE) AS d, any_value(close) FROM layers_daily "
        f"WHERE symbol = '{symbol}' AND date <= DATE '{day}' AND close > 0 "
        "GROUP BY 1 ORDER BY 1")
    if len(rows) < VOL_WIN + TREND_WIN + 2:
        return [], np.empty(0)
    d = [r[0] for r in rows]
    c = np.asarray([float(r[1]) for r in rows])
    return d[1:], np.diff(np.log(c))


def event_days(lake, day: str, etype: str = "") -> set:
    """그 타입(또는 전 시장 광역 타입)이 발생한 거래일. `day` 까지만."""
    where = (f"event_type_code = '{etype}'" if etype else
             " OR ".join(f"event_type_code LIKE '{p}%'" for p in MARKET_WIDE))
    try:
        rows = lake.sql(_base(day) + "SELECT DISTINCT trade_date FROM v_event "
                        f"WHERE ({where}) AND trade_date <= DATE '{day}'")
    except Exception:                       # noqa: BLE001 - 부재는 빈 집합 + 호출자가 사유
        return set()
    return {r[0] for r in rows}


def run_market_trial(lake, day: str, *, etype: str, symbol: str = "069500",
                     clean_controls: bool = True, k: int = MATCH_K) -> dict:
    """시장 사건 하나의 ATT. 판정은 담지 않는다 - 게이트는 호출자가 세운다."""
    dates, y = market_days(lake, day, symbol)
    if not dates:
        return {"verdict": "판정불가",
                "reason": f"시장 계열 부족 - {symbol} 의 일별 종가가 "
                          f"{VOL_WIN + TREND_WIN + 2}일 미만"}
    idx = {d: i for i, d in enumerate(dates)}
    lo = VOL_WIN + TREND_WIN
    # 국면·추세 공변량: 처치일 **이전**만 쓴다 (당일 정보 금지)
    vol = np.array([np.log(y[i - VOL_WIN:i].std() + 1e-9) if i >= VOL_WIN else np.nan
                    for i in range(len(y))])
    trend = np.array([y[i - TREND_WIN:i].sum() if i >= TREND_WIN else np.nan
                      for i in range(len(y))])
    dow = np.array([d.weekday() for d in dates])

    treat_d = event_days(lake, day, etype)
    others = (event_days(lake, day) - treat_d) if clean_controls else set()
    ti = sorted(idx[d] for d in treat_d if d in idx and idx[d] >= lo)
    if len(ti) < MIN_DAYS:
        return {"verdict": "판정불가", "n_days": len(ti),
                "reason": f"처치일 {len(ti)} < {MIN_DAYS} - 시장 사건은 하루가 한 표본이다 "
                          f"(종목 패널과 달리 종목 수로 늘 수 없다)"}
    pool = [i for i in range(lo, len(y))
            if dates[i] not in treat_d and dates[i] not in others]
    if len(pool) < k * MIN_DAYS:
        return {"verdict": "판정불가", "n_days": len(ti),
                "reason": f"청정 대조일 {len(pool)} 부족 - 시장 사건이 너무 흔하다"}

    diffs, pairs, matched = [], 0, []      # matched: (처치 i, 대조 [j]) - 사전추세용
    for i in ti:
        cand = [j for j in pool
                if abs(vol[j] - vol[i]) <= VOL_CAL
                and abs(trend[j] - trend[i]) <= TREND_CAL and dow[j] == dow[i]]
        if len(cand) < k:
            continue
        cand.sort(key=lambda j: (abs(vol[j] - vol[i]), abs(trend[j] - trend[i])))
        pick = cand[:k]
        diffs.append(y[i] - float(np.mean(y[pick])))
        pairs += len(pick)
        matched.append((i, pick))
    if len(diffs) < MIN_DAYS:
        return {"verdict": "판정불가", "n_days": len(ti), "matched": len(diffs),
                "reason": f"캘리퍼 안에서 짝지어진 처치일 {len(diffs)} < {MIN_DAYS} "
                          f"(국면·요일·추세를 맞춘 날이 없다)"}

    a = np.asarray(diffs)
    att = float(a.mean())
    rng = np.random.default_rng(SEED)
    null = np.array([float((a * rng.choice([-1.0, 1.0], size=len(a))).mean())
                     for _ in range(PERMS)])
    p = _two_sided(float((null >= att).mean()))
    # 사전추세: **같은 짝**을 t-1·t-2 로 밀어 같은 차를 다시 잰다. 처치 전에 이미
    # 차가 있으면 사건이 원인이 아니라 사건이 결과다(또는 매칭이 실패했다).
    pre = {}
    for lag in (1, 2):
        pa = [y[i - lag] - float(np.mean([y[j - lag] for j in pk]))
              for i, pk in matched if i - lag >= 0 and min(pk) - lag >= 0]
        pre[f"t-{lag}"] = float(np.mean(pa)) if pa else None
    overlap = sum(1 for i, _pk in matched if dates[i] in others)
    # 무엇을 섞었는지가 무엇을 검정했는지다(21R): 짝 차의 **부호**를 섞었으므로 귀무는
    # "처치-대조 대비가 0" 이다. 날짜를 섞는 귀무("이 날이 특별한가")가 아니다 - 셀이
    # 큰 이상수익으로 선정된 뒤 그걸 물으면 순환이다.
    return {"verdict": "계산됨", "null_kind": "pair", "att": att, "p": p,
            "n_days": len(diffs),
            "pairs": pairs, "treated_all": len(ti), "pool": len(pool),
            "pretrend": pre, "overlap": overlap, "etype": etype, "day": day,
            "clean": clean_controls}


def say_market_trial(r: dict) -> str:
    """한 줄. 판정 어휘는 게이트가 준 것만 쓴다."""
    if r.get("verdict") != "계산됨":
        return (f"판정불가 — {r.get('reason', '?')}"
                + (f" (처치일 {r['n_days']})" if r.get("n_days") else ""))
    pre = " · ".join(f"{k} {v * 100:+.2f}%p" for k, v in r["pretrend"].items()
                     if v is not None) or "미계측"
    return (f"처치일 {r['n_days']}/{r['treated_all']} · 대조 짝 {r['pairs']} "
            f"(청정 후보 {r['pool']}일) · ATT {r['att'] * 100:+.3f}%p "
            f"(양측 p={r['p']:.3f}) · 사전추세 {pre}"
            + (f" · **처치일 중 {r['overlap']}일에 다른 시장 사건 겹침**"
               if r.get("overlap") else ""))


def screen_market(lake, day: str, *, top: int = 6, symbol: str = "069500") -> list[dict]:
    """시장 광역 타입을 처치일 많은 순으로 전수 시행. **탐색이지 확증이 아니다**.

    닫힌 어휘라서 격자가 유한하다 - 종목 격자 스크린과 같은 규율(커버리지를 모델의
    선택에 맡기지 않는다). 임계는 Bonferroni 로 시행 수만큼 나눈다.
    """
    try:
        rows = lake.sql(
            _base(day) + "SELECT event_type_code, count(DISTINCT trade_date) d "
            "FROM v_event WHERE (" + " OR ".join(
                f"event_type_code LIKE '{p}%'" for p in MARKET_WIDE) + ") "
            f"AND trade_date <= DATE '{day}' GROUP BY 1 "
            f"HAVING d >= {MIN_DAYS} ORDER BY 2 DESC LIMIT {top}")
    except Exception:                       # noqa: BLE001
        return []
    out = []
    for et, _d in rows:
        r = run_market_trial(lake, day, etype=str(et), symbol=symbol)
        r["etype"] = str(et)
        out.append(r)
    return out


def say_screen(rows: list[dict], alpha: float = ALPHA) -> str:
    """표. 유의는 시행 수로 나눈 임계를 쓴다 - 전수 스크린이므로 다중비교가 실재한다."""
    if not rows:
        return ("  시장 사건 검정 불가 — 처치일 "
                f"{MIN_DAYS}일 이상인 시장 광역 타입이 없다")
    a = alpha / len(rows)
    out = [f"  시장 사건 시행 {len(rows)}개 · 임계 α/m={a:.4f} "
           f"(단위=거래일 · 대조=국면·요일·추세 매칭일)"]
    for r in rows:
        nm = r["etype"].split(".")[-1]
        line = say_market_trial(r)
        mark = ("  **유의**" if r.get("verdict") == "계산됨" and r["p"] < a else "")
        out.append(f"    {nm:<22} {line}{mark}")
    hit = [r for r in rows if r.get("verdict") == "계산됨" and r["p"] < a]
    out.append("  → " + ("유의한 시장 사건 없음 - **시장 몫은 사건으로 설명되지 않는다** "
                         "(밤사이 해외·국면이 남는다)" if not hit else
                         " · ".join(f"{r['etype'].split('.')[-1]} "
                                    f"{r['att'] * 100:+.3f}%p" for r in hit)))
    return "\n".join(out)


def _selfcheck() -> None:
    assert MIN_DAYS >= 10 and MATCH_K >= 1
    assert "판정불가" in say_market_trial({"verdict": "판정불가", "reason": "x"})
    r = {"verdict": "계산됨", "att": 0.012, "p": 0.003, "n_days": 21, "pairs": 63,
         "treated_all": 28, "pool": 400, "pretrend": {"t-1": 0.0001, "t-2": None},
         "overlap": 4, "etype": "POLICY.TRADE.TARIFF_CHANGE"}
    s = say_market_trial(r)
    assert "+1.200%p" in s and "겹침" in s and "t-2" not in s
    scr = say_screen([r])
    assert "**유의**" in scr and "TARIFF_CHANGE" in scr
    none = say_screen([{"verdict": "판정불가", "reason": "처치일 부족",
                        "etype": "MACRO.X.Y"}])
    assert "사건으로 설명되지 않는다" in none
    assert "시장 사건 검정 불가" in say_screen([])
    print("ok")


def main() -> None:
    if len(sys.argv) < 2:
        _selfcheck()
        return
    from .duck import CausalLake
    lake, day = CausalLake(), sys.argv[1]
    if len(sys.argv) > 2:
        print(say_market_trial(run_market_trial(lake, day, etype=sys.argv[2])))
    else:
        print(say_screen(screen_market(lake, day)))


if __name__ == "__main__":
    main()
