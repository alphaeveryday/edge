"""컨센서스 개정 — **전방 이익 기대가 어느 쪽으로 얼마나 고쳐졌는가**.

이 도구가 존재하는 이유는 설계 문서 §18 의 포기를 되돌리기 위해서다. 그 포기
("일간 이익/배수 분해 포기")의 근거는 실측이었다: `pit_daily.per` 가 trailing 이라
ΔlnEPS 가 **45% 정확히 0** 이고, 갱신일에는 분모가 교체되어 **정의상 불연속**이
생긴다(n=449,343). 그 실측은 지금도 옳다 - 틀린 것은 그 위에 얹힌 전제였다.
포기는 "전방 컨센서스가 없다" 를 전제했고, 그 전제는 `s3_dg_consensus` 뷰가
없어서 데이터가 안 보였던 데서 나왔다(222객체 · 141MB · 34,558,136행 실측).
trailing EPS 의 계단은 **관측 결함**이지만 컨센서스 개정은 **관측 대상 자체**다 -
애널리스트가 숫자를 고친 사건이 곧 신호다. 그래서 분해가 아니라 개정을 잰다.

**항목 이름을 사전에서 못 찾는다.** `dgwide.items_dict()` 947 항목의 코드 접두는
CI20·S410·S41B·S420 뿐이고, 컨센서스가 쓰는 149 코드는 전부 `FM3…`·`FM5…` 다 -
교집합 **0개**(실측 질의). 그래서 여기서 이름을 붙인 다섯 코드는 사전이 아니라
**측정과 문서 실측으로 고정**했고, 그 근거를 `basis` 로 같이 낸다. 근거 없는
코드에 이름을 지어 주면 그 이름이 그대로 납품 산문에 실린다 - 그래서 나머지
144 코드는 값이 숫자로 오더라도 `name="미해소"` 로 **제외**하고 사유를 남긴다.
숫자가 있다는 사실과 그 숫자가 무엇인지 아는 것은 다른 일이다.

**PIT 는 `as_of_date <= day` 하나뿐이다.** 컨센서스는 주간 스냅샷이고(74개 격자,
2025-02-03 ~ 2026-07-31 실측), `fiscal_year` 가 미래인 것은 선견이 아니다 -
추정치이므로 미래 연도를 보는 것이 정상이다. 선견은 오직 "그날 아직 공표되지
않은 스냅샷을 읽는 것" 이다. 그래서 SQL 의 `WHERE` 에 더해 **파이썬에서 한 번 더**
자른다. 이유는 회귀 검사 가능성이다: 필터가 SQL 에만 있으면 가짜 레이크로 선견을
검사할 수 없고, 검사할 수 없는 규율은 다음 리팩터에서 조용히 사라진다.
"""
from __future__ import annotations

import math
import re
from datetime import date, timedelta

import numpy as np

from .paneltest import MIN_N
from .surface import Need, register
from .tool_dg import _dg_ticker, _lit, _num, _resolve

CONS = "s3_dg_consensus"

# 이름을 붙인 다섯 코드와 그 근거. **사전 미해소이므로 근거가 이름과 함께 다닌다.**
# - 문서실측: `docs/onboarding/dataguide/schema.md` §6 이 A005930 as_of 2026-07-31 을
#   목표주가 493,542 · EPS 47.929(천원) 으로 적었고, 레이크의 FM30021150=493542 ·
#   FM30011505=47929 와 정확히 일치한다.
# - 항등식: 레이크 안에서만 닫히는 관계식으로 고정했다(2026-07-31 · FY2026 · n=1,229).
#     FM30011250/FM30011100×100 == FM30013015  → 1,227/1,229
#     FM30011400/FM30011100×100 == FM30013020  → 1,160/1,229
#     FM30011450/FM30011100×100 == FM30013030  → 1,197/1,229
#   세 비율의 **공통 분모**이고 동시에 최댓값(1,228/1,229)인 FM30011100 이 매출액이다.
#   FM30011450/FM30011505 는 세 시점(2025-07-01·2026-01-26·2026-07-31)에 걸쳐
#   종목마다 **불변**이다(중앙 CV 6.6e-05, n=706) - 즉 그 비가 주식수이고
#   FM30011450 이 EPS 의 분자다. FM30011400 은 같은 검사에서 CV 1.0e-02 로 150배
#   불안정해 분자가 아니다(706 중 630 종목에서 450 이 더 안정적).
_ITEMS: dict[str, tuple[str, str]] = {
    "FM30011100": ("매출액(컨센서스 평균)", "항등식: 마진 3종의 공통 분모 1227/1229"),
    "FM30011250": ("영업이익(컨센서스 평균)", "항등식: /매출×100 == FM30013015 1227/1229"),
    "FM30011450": ("순이익(컨센서스 평균)", "항등식: /EPS 가 시점 불변 CV 6.6e-05 n=706"),
    "FM30011505": ("EPS(컨센서스 평균)", "문서실측: schema.md §6 47.929천원 == 47929"),
    "FM30021150": ("목표주가(컨센서스 평균)", "문서실측: schema.md §6 493,542 일치"),
}

# `signed` 를 낼 항목의 우선순위. **EPS 가 먼저**인 이유는 이 도구가 되살리려는 것이
# §18 이 포기한 **분자 채널**이기 때문이다 - 매출 개정은 이익 기대의 대리변수일 뿐이고,
# 마진 가정이 같이 바뀌면 부호가 갈린다. EPS 가 없을 때만 아래로 내려간다.
_SIGN_ORDER = ("FM30011505", "FM30011450", "FM30011250", "FM30011100")

# 일자·결산월 코드는 `TRY_CAST(value AS DOUBLE)` 이 **숫자로 읽는다**. 실측:
# FM30011680~683 = 20260731(추정 기준일), FM30012000 = 202612(결산월),
# FM52150000 = 20160120. 캐스팅만으로 수치 판정을 하면 '20260731' 이 개정률
# 계산에 들어가 "기준일이 0.3% 상향" 같은 문장이 나온다.
# 연도 자리를 1950~2049 로, 월 자리를 01~12 로 묶는다. 느슨하게 뒀더니 실측에서
# 바로 새어 나왔다: A005930 의 FM30041256 = 207611 이 `20|76|11` 로 읽혀 일자
# 코드로 오분류됐다(그 코드의 실제 범위는 -1,053 ~ 999,903 이다).
_DATE_LIKE = re.compile(r"^(19[5-9]\d|20[0-4]\d)(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])?$")


def _nope(reason: str, **kw) -> dict:
    """부재는 **사유와 함께**. 빈 `items` 나 `rev_pct=0` 을 조용히 돌려주면 호출자가
    그것을 "개정이 없었다"(= 기대가 안 변했다) 로 읽는다. 커버리지가 좁은 축이라
    (주간 스냅샷당 EPS 보유 1,252종목 / 등장 티커 8,590개 실측) 이 혼동이 상시 일어난다."""
    out = {"verdict": "판정불가", "reason": reason, "fiscal_year": None,
           "items": {}, "excluded": {}, "headline": "", "signed": None,
           "supports": None, "as_of_used": {}, "note": ""}
    out.update(kw)
    return out


def _rev(latest: float, prior: float) -> float:
    """개정률. 둘 다 양수면 로그, 아니면 단순 변화율.

    왜 로그를 기본으로 쓰는가: 개정은 곱셈적이고(추정을 10% 깎았다), 상·하향을
    대칭으로 다뤄야 z 와 횡단면 분위수가 한쪽으로 안 쏠린다. 왜 적자에서는 로그를
    안 쓰는가: 컨센서스 순이익은 **음수가 정상**이고(적자 추정) 로그가 정의되지
    않는다. 그 경우를 빼 버리면 "적자 종목의 개정은 없었다" 가 되는데, 적자 폭
    확대야말로 가장 큰 개정이다. 부호 뒤집힘(-100 → +50)에서 로그는 무의미하고
    단순 변화율은 +1.5 로 방향(개선)을 옳게 준다.
    """
    if latest > 0 and prior > 0:
        return math.log(latest / prior)
    return (latest - prior) / abs(prior)


def _pick_prior(grid: list[str], cut: str) -> str:
    """`cut` 이하의 **가장 최근** 스냅샷. 격자가 주간이라 정확히 `horizon` 일 전
    스냅샷은 거의 없다 - 없다고 판정불가를 내면 이 도구는 사실상 안 돈다.
    `cut` 을 넘지 않는 쪽으로만 당기는 이유는 창이 길어지면 개정 폭이 과대평가될
    뿐이지만 넘기면 **선견**이 되기 때문이다 - 두 오류는 등가가 아니다."""
    ok = [d for d in grid if d <= cut]
    return ok[-1] if ok else ""


def _hist_z(vals: dict[str, float], grid: list[str], horizon: int,
            now: float, upto: str) -> tuple[float | None, int]:
    """그 종목 자기 개정 이력에 대고 잰 z 와 표본 수.

    왜 자기 이력인가: 개정 폭의 자릿수는 종목마다 다르다. 커버리지가 두꺼운
    대형주는 주간 개정이 0.1% 단위로 움직이고, 애널리스트 두세 명이 붙은 종목은
    한 명이 숫자를 고치면 20% 가 튄다. 횡단면 z 하나로 재면 후자의 평범한 주가
    상시 '이례' 로 뜬다.

    **오늘 개정은 참조 분포에서 뺀다**(`t < upto`). 넣으면 자기 자신으로 자기를
    검정하는 것이고, 큰 개정일수록 표준편차를 자기가 부풀려 z 를 눌러 버린다 -
    즉 이례성을 과소평가한다.

    창이 겹친다(주간 격자에 90일 창이면 한 관측이 13번 재사용된다). 그래서 표준
    편차가 실제보다 작게 나오고 |z| 는 **위로 편향**된다. 겹치지 않게 자르면
    74개 격자에서 표본이 4~5개로 줄어 z 자체가 성립하지 않는다 - 편향을 숨기지
    않고 여기 적어 두는 쪽을 골랐다.
    """
    prev: list[float] = []
    for t in grid:
        if t >= upto:                       # 오늘 것과 그 뒤는 참조에서 뺀다
            continue
        s = _pick_prior(grid, str(date.fromisoformat(t) - timedelta(days=horizon)))
        if not s or s == t:
            continue
        a, b = vals.get(t), vals.get(s)
        if a is None or b is None or b == 0:
            continue
        prev.append(_rev(a, b))
    if len(prev) < MIN_N:
        return None, len(prev)
    x = np.array(prev, dtype=float)
    sd = float(x.std(ddof=1))
    if sd == 0:
        # 이력이 통째로 같은 값이면 z 는 0/0 이다. 0 을 돌려주면 "평범했다" 가
        # 되는데 사실은 "분산이 없어 못 잰다" 다 - 부재와 기각은 다른 사건이다.
        return None, len(prev)
    return float((now - float(x.mean())) / sd), len(prev)


def _classify(raw: str, seen: list[str]) -> str:
    """제외 사유. **왜 빠졌는지**를 코드마다 남긴다 - 149 코드 중 이름을 아는 것은
    5 개뿐이라, 사유가 없으면 호출자는 "그 종목엔 그 항목이 없다" 와 "그 항목이
    무엇인지 우리가 모른다" 를 구분할 수 없다. 앞은 데이터 부재이고 뒤는 사전 부재다.

    일자 판정은 **그 종목의 전 스냅샷**(최대 74개)을 본다. 한 시점만 보면 진짜
    수치가 우연히 연월 꼴이 되는 순간 일자 코드로 굳는다 - 값 하나로 코드의 성질을
    단정하는 것이 애초에 틀린 추론이다. 일자·결산월 코드는 **언제나** 그 꼴이다.
    """
    s = "" if raw is None else str(raw).strip()
    if _num(s) is None:
        return f"범주형(값 예: {s[:12]!r}) - 개정률이 정의되지 않는다"
    if seen and all(_DATE_LIKE.match(v) for v in seen):
        return (f"일자·결산월 코드(값 {s} · 스냅샷 {len(seen)}개 모두 같은 꼴) - "
                f"숫자로 캐스팅되지만 개정률이 무의미하다")
    return "사전 미해소 - items_dict 947 항목에 없는 FM 코드다(이름을 지어내지 않는다)"


def _cross_section(lake, fy: int, latest: str, prior: str, day: str
                   ) -> dict[str, list[float]]:
    """그날 두 스냅샷에 다 있는 **모든 종목**의 개정률. 분위수의 모집단이다.

    왜 필요한가: z 는 "이 종목치고 큰 개정인가" 만 답한다. 업종 전체가 같은 주에
    깎이면 그 종목의 z 도 크게 나오고, 산문은 그것을 종목 고유 사건으로 귀속한다.
    횡단면 분위수는 "그 주에 모두가 깎일 때 이 종목은 어디였나" 를 답한다.

    파티션 두 개만 읽는다(`as_of_date` 가 하이브 키다). 그래서 8,590 종목 횡단면이
    자기 이력 질의보다 싸다 - 이력은 74 파티션을 다 열어야 한다.
    두 파티션 다 `day` 이하이지만 파이썬에서 한 번 더 자른다(선견 차단의 단일 규율).
    """
    codes = ",".join(_lit(c) for c in _ITEMS)
    try:
        rows = lake.sql(
            f"SELECT as_of_date, ticker, item_code, value FROM {CONS} "
            f"WHERE fiscal_year = {fy} AND item_code IN ({codes}) "
            f"  AND as_of_date IN (DATE {_lit(latest)}, DATE {_lit(prior)}) "
            f"  AND as_of_date <= DATE {_lit(day)}")
    except Exception:                       # noqa: BLE001 - 횡단면은 보조축이다
        # 여기서 실패해도 개정률과 z 는 살아 있다. 빈 dict 를 돌려주면 `x_pct` 가
        # None 이 되고 그건 "못 쟀다" 로 읽힌다 - 0 을 지어내는 것과 다르다.
        return {}
    pair: dict[tuple[str, str], list[float | None]] = {}
    for d, tkr, code, val in rows:
        d = str(d)
        if d > day or d not in (latest, prior):
            continue
        slot = pair.setdefault((str(code), str(tkr)), [None, None])
        slot[0 if d == latest else 1] = _num(val)
    out: dict[str, list[float]] = {}
    for (code, _tkr), (a, b) in pair.items():
        if a is None or b is None or b == 0:
            continue
        out.setdefault(code, []).append(_rev(a, b))
    return out


@register("consensus_revision",
          "그 종목의 **전방 컨센서스가 최근 horizon 일 동안 어느 쪽으로 몇 % 고쳐졌는지**를 "
          "매출·영업이익·순이익·EPS·목표주가로 낸다. 개정 폭이 그 종목 자기 이력(z)과 "
          "그날 횡단면(분위수)에서 얼마나 이례적인지까지 같이 내고, 이름을 확정하지 못한 "
          "컨센서스 항목은 사유와 함께 제외 목록으로 신고한다.",
          needs=(Need(CONS, days=8, date_col="as_of_date"),), vocab=())
def _consensus_revision(lake, *, day: str, instrument_id: str = "",
                        ticker: str = "", horizon: int = 90, **kw) -> dict:
    """전방 이익 기대의 개정 방향(`signed`)과 이례성(z · 횡단면 분위수).

    회계연도 선택: `day` 의 연도를 먼저 본다. 2026-07-31 에 FY2026 은 아직 5개월이
    남은 **전방** 추정이다. 그 연도에 스냅샷이 없으면 다음 해로 한 번만 내려간다 -
    자동으로 계속 미루면 어느 해를 본 건지 산문에서 사라지므로 `fiscal_year` 를
    반환에 박아 둔다.

    `supports` 는 호출자가 `claim_sign`(±1)을 줄 때만 정해진다. 주장 부호를 모르면
    None 이다 - 개정이 상향이라는 사실 자체는 어떤 주장도 지지하지 않는다.
    "실적 기대가 좋아져서 올랐다" 를 지지하는 것은 **상향**이고, "실적 우려로
    빠졌다" 를 지지하는 것은 **하향**이다. 이 둘을 도구가 구분 못 하면 신뢰성
    검사는 같은 수를 양쪽 주장에 다 붙여 준다.

    결정론: 순열도 표집도 없다. 같은 레이크·같은 인자면 같은 수가 나온다.
    """
    tk = _dg_ticker(ticker) if ticker else ""
    if not tk:
        if not instrument_id:
            return _nope("instrument_id·ticker 가 둘 다 비었다 - 컨센서스는 종목 축이다")
        raw, err = _resolve(lake, day, instrument_id)
        if err:
            return _nope(err)
        tk = _dg_ticker(raw)

    fy0 = int(day[:4])
    rows: list[tuple] = []
    fy = fy0
    for cand in (fy0, fy0 + 1):
        try:
            got = lake.sql(
                f"SELECT as_of_date, item_code, value FROM {CONS} "
                f"WHERE fiscal_year = {cand} AND ticker = {_lit(tk)} "
                f"  AND as_of_date <= DATE {_lit(day)}")
        except Exception as e:              # noqa: BLE001 - 사유를 남긴다
            # 조용히 삼키면 터널·뷰 사망이 "이 종목은 커버리지가 없다" 로 위장된다.
            return _nope(f"레이크 질의 실패: {type(e).__name__}: {str(e)[:120]}")
        # **선견 차단은 여기서도 한 번 더.** SQL 에만 두면 이 규율을 가짜 레이크로
        # 검사할 수 없고, 검사되지 않는 규율은 다음 리팩터에서 조용히 사라진다.
        got = [r for r in got if str(r[0]) <= day]
        if got:
            rows, fy = got, cand
            break
    if not rows:
        return _nope(f"{tk} 의 FY{fy0}·FY{fy0 + 1} 컨센서스가 {day} 이전에 없다 - "
                     f"애널리스트 커버리지 부재이지 '기대가 안 변했다'가 아니다")

    grid = sorted({str(r[0]) for r in rows})
    latest = grid[-1]
    prior = _pick_prior(grid[:-1],
                        str(date.fromisoformat(day) - timedelta(days=horizon)))
    if not prior:
        return _nope(f"{day} 기준 {horizon}일 전 스냅샷이 없다 - 이 종목의 컨센서스는 "
                     f"{grid[0]} 부터 {len(grid)}개뿐이다(주간 격자)",
                     fiscal_year=fy,
                     as_of_used={"latest": latest, "prior": "", "n_grid": len(grid)})

    # 코드 → {as_of: 값}. 숫자로 안 읽히는 값은 떨어뜨리되 원문은 `raws` 에 모은다 -
    # 그 원문 전체가 곧 범주형·일자 판정의 근거다(값 하나로는 못 가른다).
    series: dict[str, dict[str, float]] = {}
    shown: dict[str, str] = {}
    raws: dict[str, list[str]] = {}
    for d, code, val in rows:
        d, code = str(d), str(code)
        s = "" if val is None else str(val).strip()
        raws.setdefault(code, []).append(s)
        if d == latest:
            shown[code] = s
        v = _num(val)
        if v is not None:
            series.setdefault(code, {})[d] = v

    xs = _cross_section(lake, fy, latest, prior, day)

    items: dict[str, dict] = {}
    for code, (label, basis) in _ITEMS.items():
        vals = series.get(code, {})
        a, b = vals.get(latest), vals.get(prior)
        if a is None or b is None or b == 0:
            continue
        rev = _rev(a, b)
        z, n_obs = _hist_z(vals, grid, horizon, rev, latest)
        peers = xs.get(code, [])
        items[code] = {
            "name": "미해소", "label": label, "basis": basis,
            "latest": a, "prior": b, "rev_pct": round(rev * 100, 4),
            "z": None if z is None else round(z, 4), "n_obs": n_obs,
            "x_pct": (round(float((np.array(peers) <= rev).mean()), 4)
                      if len(peers) >= MIN_N else None),
            "x_n": len(peers)}

    excluded = {c: _classify(v, raws.get(c, [])) for c, v in sorted(shown.items())
                if c not in items}
    if not items:
        return _nope(f"이름을 확정한 5개 항목이 {tk} FY{fy} 의 {prior}→{latest} 두 "
                     f"스냅샷에 모두 오지는 않았다 - 그날 값이 온 코드는 {len(shown)}개다",
                     fiscal_year=fy, excluded=excluded,
                     as_of_used={"latest": latest, "prior": prior, "n_grid": len(grid)})

    top = max(items.values(), key=lambda x: abs(x["rev_pct"]))
    # 이름과 근거를 **한 문자열로 붙여** 낸다. 떼어 놓으면 산문이 이름만 베끼고
    # 근거는 흘린다 - 사전 미해소 코드에서 그건 곧 이름을 지어낸 것과 같다.
    headline = f"{top['label']} [{top['basis']}]"

    signed, src = None, ""
    for code in _SIGN_ORDER:
        if code in items:
            signed = items[code]["rev_pct"] / 100.0
            src = items[code]["label"]
            break

    claim = kw.get("claim_sign")
    supports: bool | None = None
    if claim is not None and float(claim) != 0 and signed is not None and signed != 0:
        supports = (float(claim) > 0) == (signed > 0)

    span = (date.fromisoformat(latest) - date.fromisoformat(prior)).days
    lag = (date.fromisoformat(day) - date.fromisoformat(latest)).days
    note = (f"FY{fy} · {prior} → {latest}(목표 {horizon}일, 실제 {span}일) · "
            f"방향은 {src} 기준 · 최신 스냅샷은 {day} 보다 {lag}일 앞선다 · "
            f"이름 미확정 {len(excluded)}개 제외")
    if claim is None:
        note += " · claim_sign 미지정이라 supports 는 판정하지 않았다"

    return {"verdict": "계산됨", "reason": "", "fiscal_year": fy, "items": items,
            "excluded": excluded, "headline": headline, "signed": signed,
            "supports": supports, "note": note,
            "as_of_used": {"latest": latest, "prior": prior, "n_grid": len(grid)}}


__all__ = ["CONS"]
