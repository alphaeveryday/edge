"""P1 · 지문 — **가설 이전에, LLM 이전에 관측 자신을 잰다.**

왜 순서가 이런가. 제안을 먼저 받으면 무엇을 잴지를 제안이 정한다. 그러면 측정은 이야기를
지키는 쪽으로 흐르고(스펙 쇼핑), 그 편향은 결과가 좋아지는 방향이라 사후에 드러나지 않는다.
지문을 먼저 뜨면 지켜야 할 이야기가 아직 없는 상태에서 사실이 고정된다.

두 번째 이유는 값이다. 애널리스트가 제일 먼저 하는 일이 후보 공간을 가장 많이 자른다.
공시 전에 이미 같은 방향으로 움직였으면 "당일 신규 정보" 부류는 그 뒤 논쟁 없이 죽는다.
그래서 이 단계는 후보를 주지 않는다 - **후보를 죽일 재료**를 준다. 각 축의 `kills` 가
P2 프롬프트에 그대로 실려 어휘를 좁히지 않고 결과를 좁힌다.

세 번째. **못 잰 축은 사라지지 않는다.** 안 그린 간선과 없는 관계가 같은 표현(부재)으로
붙는 것이 이전 구조를 무너뜨린 실패다. 여기서는 분봉이 없어 못 재는 것과 재서 조용한 것이
다른 값이다 - 전자는 `available=False` + `missing_input`, 후자는 `available=True` 에 평평한
수치다. 전자는 다음 수집 의제가 되고 후자는 증거가 된다.

원장에 없는 축(분봉·수급·공매도·합성 캘린더·교차자산)은 물어봐야 소용없으므로 조회조차
하지 않고 부재로 선언한다. 조회 실패도 같은 자리로 떨어진다 - 지문은 관문이 아니라 재료라
한 축이 죽어도 설명은 계속된다.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date, datetime

import numpy as np

from ..observability import log
from .contracts import Axis, Fingerprint, Question

TOP_K = 3           # 집중도를 재는 상위 기여 종목 수
MIN_PEERS = 5       # 이보다 얇은 피어 표본으로는 동반 여부에 검정력이 없다
CO_MOVE = 0.60      # 같은 부호 비율이 이 위면 동반으로 본다
DRIFT_DAYS = 20     # 사건 전 누적 초과수익 창 (거래일)

# 원장 미보유 축. 조회하지 않고 부재로 선언한다 - `adapters/sql_surface.py:73` 의 목록과
# 같은 사실이고, 여기 적어야 P2 가 없는 데이터를 전제한 가설을 세우지 않는다.
NO_INTRADAY = {
    "intraday_shape": "분봉·틱 없음 - 같은 일간 등락에서 점프·표류·되돌림을 가를 수 없다",
    "intraday_timing": "분봉·틱 없음 - 공시 시각과 반응 시각의 선후를 못 가른다",
}
NO_LEDGER = {
    "flow": "투자자 유형별 수급 없음 - 누가 샀는지 원장에 없다",
    "calendar": "합성 캘린더 없음 - 만기·리밸런스 효력일·락업 해제를 붙일 수 없다",
    "cross_asset": "교차자산(금리·환율·변동성지수) 없음 - 매크로 공통 충격을 떼어낼 수 없다",
    "short_interest": "공매도·대차·신용 잔고 없음 - 숏커버 가설을 검정할 수 없다",
}


# ── 배관 ────────────────────────────────────────────────────────────────
def _measured(name: str, take: Callable[[], Axis]) -> Axis:
    """축 하나를 재되 **실패는 침묵이 아니라 `available=False` 로 떨어진다.**

    한 축의 조회 실패로 지문 전체를 예외로 올리면, 원장 한 구석이 비었다는 이유로 셀 하나가
    통째로 설명되지 않는다. 지문은 관문이 아니라 재료다. 무엇이 왜 없었는지는 로그와
    `missing_input` 양쪽에 남으므로 조용히 사라지지도 않는다.
    """
    try:
        return take()
    except Exception as exc:  # noqa: BLE001 - 축 하나의 실패가 지문을 막지 않는다
        detail = f"{type(exc).__name__}: {exc}"
        log("causal.p1.axis_failed", axis=name, error=detail[:300])
        return Axis(name=name, available=False, missing_input=f"조회 실패 - {detail}"[:300])


def _as_date(v: object) -> date | None:
    """`datetime` 을 먼저 잡는다 - `datetime` 은 `date` 의 하위형이라 순서가 뒤집히면
    `date - datetime` 뺄셈이 TypeError 로 죽는다. 원장의 `available_at` 은 타임스탬프다."""
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def _finite(a: np.ndarray) -> np.ndarray:
    return a[np.isfinite(a)]


def _pct(x: float) -> str:
    return f"{x * 100:+.2f}%p"


# ── 축 ──────────────────────────────────────────────────────────────────
def _shape(cd, q: Question) -> Axis:
    """등락 모양을 **기여 집중도**로 분류한다. 일간 원장으로 잴 수 있는 모양은 이것뿐이다.

    분모를 상위 기여 합이 아니라 `observed` 로 두는 이유: `contributors` 에는 상위 5종목만
    실린다(`adapters/llm.py:129`). 그 안에서 비율을 내면 어떤 셀이든 상위 3이 60%를 넘어
    전부 concentrated 로 찍힌다 - 축이 아니라 상수가 된다.

    균등 기여 세계의 몫(`k/n_hold`)과 대조하는 이유: 보유 4종목 ETF 는 상위 3이 70%를
    가져가는 것이 기본값이다. 절대 문턱만 쓰면 작은 바스켓이 언제나 집중으로 오분류된다.
    반대로 200종목 바스켓은 균등 몫이 거의 0이라 어떤 값이든 균등 대비 몇 배로 보인다.
    그래서 양쪽 다 **절대 문턱과 균등 대비를 함께** 건다 - 한쪽만 걸면 반대쪽 크기의
    바스켓이 통째로 오분류된다(상위 3이 34%인 200종목 바스켓은 균등의 22배지 broad 가
    아니다). 어느 쪽도 못 넘으면 `mixed` 이고, 그건 판정 실패가 아니라 모양이 없는 것이다.
    """
    move = q.observed
    if not q.contributors or abs(move) < 1e-4:
        return Axis(name="shape", available=False,
                    missing_input="기여 분해가 없거나 ETF 등락이 1bp 미만 - 집중도가 정의되지 않는다")
    same = sorted((t for t in q.contributors if t[1] * move > 0),
                  key=lambda t: abs(t[1]), reverse=True)
    if not same:
        return Axis(name="shape", available=False,
                    missing_input="등락과 같은 방향으로 기여한 종목이 상위 5 안에 없다")
    k = min(TOP_K, len(same))
    top = sum(abs(c) for _, c in same[:k]) / abs(move)

    # 보유 종목 수만 쓴다. 스냅샷이 없으면 기준선 없이 절대 문턱으로 떨어지되 축은 살린다 -
    # 보유 원장 한 건 결측으로 모양 판정을 통째로 잃는 것이 더 비싸다.
    n_hold = 0
    try:
        n_hold = int(cd.weight(q.etf_instrument_id, q.trade_date).get("n_hold") or 0)
    except Exception as exc:  # noqa: BLE001
        log("causal.p1.axis_failed", axis="shape.weight", error=f"{type(exc).__name__}: {exc}")
    base = k / n_hold if n_hold >= k else None

    if top >= 0.60 and (base is None or top >= 2 * base):
        kind = "concentrated"
        kills = ("ETF 전 종목에 고르게 걸리는 원인 (지수 편입·매크로·시장 전반) - 등락 대부분이 "
                 "상위 소수 종목에서 나왔다",)
    elif top <= 0.35 and (base is None or top <= 2 * base):
        kind = "broad"
        kills = ("단일 종목 특이 사건으로 등락 전부를 설명하는 가설 - 기여가 넓게 퍼져 있어 "
                 "한 종목이 가진 무게로는 총량이 안 맞는다",)
    else:
        kind, kills = "mixed", ()

    names = ", ".join(n for n, _ in same[:k])
    says = f"상위 {k}종목({names})이 등락의 {top:.0%} 를 만들었다 - {kind}."
    if base is not None:
        says += f" 보유 {n_hold}종목이 균등 기여하는 세계의 몫은 {base:.0%}."
    return Axis(name="shape", available=True, says=says, kills=kills,
                value={"kind": kind, "top_k_share": round(top, 4), "k": k,
                       "top_names": [n for n, _ in same[:k]], "n_hold": n_hold,
                       "uniform_share": round(base, 4) if base is not None else None})


def _breadth(cd, q: Question, candidates: list[dict]) -> Axis:
    """후보 종목의 **산업 피어가 같은 날 동반 움직였나.**

    이 한 축이 종목 특이 가설과 산업 공통 충격을 가른다. 피어 대다수가 같은 방향으로
    움직였다면 종목 하나에 갇힌 원인은 그 동조를 만들 수 없다 - 논쟁 전에 죽는다.

    피어를 `industry_map` 으로 찾고 `universe` 로 다시 거르는 이유: 맵은 분류만 알고
    그날 거래가 있었는지는 모른다. 거래 없는 종목을 표본에 넣으면 nan 이 동조율을 희석한다.
    후보 종목 자신은 `exclude` 로 뺀다 - 처치를 대조에 섞으면 재려는 대비가 사라진다.
    """
    ids = [str(c["instrument_id"]) for c in candidates if c.get("instrument_id")]
    if not ids:
        return Axis(name="breadth", available=False,
                    missing_input="후보 사건에 instrument_id 가 없어 피어를 정의할 수 없다")
    imap = cd.industry_map(q.trade_date)
    inds = sorted({imap[i] for i in ids if imap.get(i)})
    if not inds:
        return Axis(name="breadth", available=False,
                    missing_input="후보 종목의 산업 분류가 원장에 없다")

    # 술어에 산업명을 리터럴로 박으므로 작은따옴표를 두 배로 만든다. `_guard` 는 토큰만
    # 보지 문자열 종료는 보지 않는다 - 여기서 안 막으면 이름 하나가 술어를 갈아탄다.
    quoted = [n.replace("'", "''") for n in inds]
    where = " OR ".join(f"industry_name = '{n}'" for n in quoted)
    pairs = cd.universe(where, [q.trade_date],
                        exclude=[(i, q.trade_date) for i in ids])
    ar = _finite(cd.ar(pairs))
    n = int(ar.size)
    if not n:
        return Axis(name="breadth", available=False,
                    missing_input=f"{' · '.join(inds)} 피어의 당일 초과수익 관측 0건")

    sign = 1.0 if q.residual >= 0 else -1.0
    agree = float(np.mean(np.sign(ar) == sign))
    med = float(np.median(ar))
    thin = n < MIN_PEERS

    if not thin and agree >= CO_MOVE:
        kills = ("이 종목만의 특이 사건 - 같은 산업 피어 대다수가 같은 날 같은 방향으로 "
                 "움직였다. 한 기업에 갇힌 원인으로는 그 동조를 만들 수 없다",)
    elif not thin:
        kills = ("산업·섹터 전반의 공통 충격 - 같은 산업 피어가 따라 움직이지 않았다",)
    else:
        kills = ()

    says = (f"{' · '.join(inds)} 피어 {n}종목 중 {agree:.0%} 가 잔차와 같은 부호, "
            f"피어 중위 초과수익 {_pct(med)}.")
    if thin:
        says += f" 표본이 {MIN_PEERS}종목 미만이라 판별력이 없다 - 어느 쪽도 죽이지 않는다."
    return Axis(name="breadth", available=True, says=says, kills=kills,
                value={"industries": inds, "n_peers": n, "same_sign_ratio": round(agree, 4),
                       "median_ar": round(med, 6), "co_moved": bool(not thin and agree >= CO_MOVE)})


def _available_at(sql, candidates: list[dict]) -> dict[str, object]:
    """`v_event` 에서 공개 시점을 다시 읽는다. **후보 dict 의 `event_date` 는 잘린 값이다.**

    `_candidates()` 는 `available_at[:10]` 만 담는다(`adapters/llm.py:180`). 날짜만으로는
    같은 날 장 마감 후 공시와 장 시작 전 공시가 구분되지 않는데, 이 축이 답하려는 것이
    바로 그 선후다. 사건당 등장 엔티티 수만큼 행이 있으므로 최초 공개 시각으로 접는다.

    `sql` 이 없거나 질의가 실패해도 후보 dict 의 날짜로 축이 성립한다 - 정밀도만 잃는다.
    """
    if sql is None:
        return {}
    ids = [x for x in
           {re.sub(r"[^A-Za-z0-9_.:-]", "", str(c.get("event_id") or "")) for c in candidates} if x]
    if not ids:
        return {}
    lst = ", ".join(f"'{i}'" for i in sorted(ids))
    try:
        rows = sql.query("SELECT source_event_id, min(available_at) AS opened_at "
                         f"FROM v_event WHERE source_event_id IN ({lst}) "
                         "GROUP BY source_event_id")
    except Exception as exc:  # noqa: BLE001 - 정밀도 손실이지 축의 손실이 아니다
        log("causal.p1.axis_failed", axis="event_timing.available_at",
            error=f"{type(exc).__name__}: {exc}"[:300])
        return {}
    return {str(r["source_event_id"]): r["opened_at"] for r in rows if r.get("opened_at")}


def _event_timing(sql, q: Question, candidates: list[dict]) -> Axis:
    """후보 사건의 공개 시점이 셀 거래일보다 **얼마나 앞서는가.**

    전날 마감 후 공시면 당일 반영이 자연스럽다. 여러 날 전이면 그 사이 거래일들이 이미
    반영했어야 하므로, 당일 귀속을 주장하려면 왜 그날까지 미뤄졌는지를 가설이 말해야 한다.

    거래일 **이후** 공개(lag < 0)는 따로 잡는다. PIT 클램프는 `as_of` 기준이라 거래일 종가
    이후에 공개된 사건도 후보 목록에 들어올 수 있는데, 그건 당일 가격에 물리적으로 들어갈
    수 없다. 가장 싸게 후보를 죽이는 검사다.
    """
    seen = _available_at(sql, candidates)
    rows = []
    for c in candidates:
        raw = seen.get(str(c.get("event_id"))) or c.get("available_at") or c.get("event_date")
        d = _as_date(raw)
        if d is None:
            continue
        rows.append((str(c.get("label") or c.get("event_type_code") or "?")[:60],
                     (q.trade_date - d).days))
    if not rows:
        return Axis(name="event_timing", available=False,
                    missing_input="후보 사건에 공개 시점이 없다")

    lags = [g for _, g in rows]
    lo, hi = min(lags), max(lags)
    future = [n for n, g in rows if g < 0]
    stale = [n for n, g in rows if g >= 2]

    kills: list[str] = []
    if future:
        kills.append(f"거래일 이후에 공개된 후보를 원인으로 두는 가설 ({', '.join(future[:3])}) - "
                     "당일 종가에 들어갈 수 없다")
    if lo >= 2:
        kills.append(f"당일 신규 정보 충격 - 후보 사건이 전부 {lo}일 이상 전이다. 당일 귀속을 "
                     "주장하려면 그날까지 반영이 미뤄진 이유를 함께 세워야 한다")

    says = f"후보 공개 시점은 거래일 {lo}~{hi}일 전."
    if stale:
        says += f" 2일 이상 지난 것 {len(stale)}건."
    if future:
        says += f" 거래일 이후 공개 {len(future)}건."
    if not seen:
        # 되읽기가 없었으면 후보 dict 의 잘린 날짜만 남는다. 그 사실을 여기 적지 않으면
        # 정밀도 손실이 조용해지고, P2 가 없는 해상도로 장 전후를 논한다.
        says += " 공개 시각을 되읽지 못해 날짜 해상도다 - 장 전후는 가를 수 없다."
    if not stale and not future:
        says += " 당일 반영이 시점상 자연스럽다."
    return Axis(name="event_timing", available=True, says=says, kills=tuple(kills),
                value={"min_lag_days": lo, "max_lag_days": hi, "n": len(rows),
                       "stale": stale, "after_trade_date": future,
                       "source": "v_event" if seen else "candidate"})


def _pre_drift(cd, q: Question, candidates: list[dict]) -> Axis:
    """사건 전 누적 초과수익. **이미 움직였으면 그 뒤 논쟁이 무의미하다.**

    두 방향 모두를 죽이는 축이라 값이 크다. 사건 전에 이미 같은 방향으로 예산만큼 움직였다면
    "당일 신규 정보"는 그 표류를 만든 것부터 설명해야 하고, 반대로 사건 전이 평평했다면
    선반영·정보유출 가설은 남겼어야 할 자국이 없다.

    기준을 ETF 자신으로 두는 이유: 설명 대상이 ETF 잔차다. ETF 관측이 없을 때만 후보 종목
    중위값으로 물러선다 - 구성종목의 표류는 같은 것을 재는 대리변수지 같은 값이 아니다.
    """
    pairs: list[tuple[str, date]] = [(str(q.etf_instrument_id), q.trade_date)]
    for c in candidates:
        d = _as_date(c.get("event_date"))
        if c.get("instrument_id") and d:
            pairs.append((str(c["instrument_id"]), d))
    m = cd.mom(pairs, days=DRIFT_DAYS)
    etf = float(m[0]) if m.size and np.isfinite(m[0]) else None
    peer = _finite(m[1:]) if m.size > 1 else np.array([], dtype=float)
    peer_med = float(np.median(peer)) if peer.size else None

    ref = etf if etf is not None else peer_med
    if ref is None:
        return Axis(name="pre_drift", available=False,
                    missing_input=f"사건 전 {DRIFT_DAYS}거래일 초과수익을 잴 관측이 없다")

    b = q.budget
    if b > 0 and abs(ref) >= b and ref * q.residual > 0:
        kills = ("당일 신규 정보로 등락 전부를 설명하는 가설 - 사건 전 이미 같은 방향으로 "
                 f"{_pct(ref)} 움직였다. 그 표류를 만든 것이 무엇인지부터 답해야 한다",)
    elif b > 0 and abs(ref) <= 0.5 * b:
        kills = ("선반영·정보유출 가설 - 사건 전 누적 초과수익이 "
                 f"{_pct(ref)} 로 평평하다. 샐 것이 샜다면 그 자국이 남았어야 한다",)
    else:
        kills = ()

    src = "ETF" if etf is not None else "후보 종목 중위"
    says = (f"사건 전 {DRIFT_DAYS}거래일 누적 초과수익 {src} {_pct(ref)}, "
            f"설명 예산 {b * 100:.2f}%p.")
    if etf is not None and peer_med is not None:
        says += f" 후보 종목 중위는 {_pct(peer_med)}."
    return Axis(name="pre_drift", available=True, says=says, kills=kills,
                value={"days": DRIFT_DAYS, "etf": etf, "candidate_median": peer_med,
                       "n_candidates": int(peer.size), "budget": round(b, 6)})


def _type_extremity(cd, q: Question, candidates: list[dict]) -> Axis:
    """이 등락이 후보 **사건 타입의 과거 분포 어디에** 있나.

    대조 대상을 잔차가 아니라 `required_effect`(잔차/비중)로 두는 이유: 타입 사전은
    구성종목 수준 초과수익의 분포인데 잔차는 ETF 수준이다. 비중으로 나눠야 같은 자에서
    잰다 - 비중 0.8%인 종목이 4%p 잔차를 만들려면 500% 가 필요하고, 그 즉시 죽는다.

    타입 과거 최대를 넘으면 그 후보의 **단독 원인 가설**이 산술로 죽는다. 검정이 아니라
    분포 사실이라 값이 싸고, 그래서 가설을 세우기 전에 돌린다.
    """
    rows = []
    for c in candidates:
        p = c.get("prior") or {}
        if not p.get("n"):
            continue
        need = cd.required_effect(q.residual, c.get("share"))
        label = str(c.get("label") or c.get("event_type_code") or "?")[:60]
        q50, q90 = float(p.get("abs_q50") or 0.0), float(p.get("abs_q90") or 0.0)
        mx = float(p.get("abs_max") or 0.0)
        if need is None:
            rows.append({"label": label, "need": None, "band": "no_weight", "max": mx,
                         "n": int(p["n"]), "type": c.get("event_type_code")})
            continue
        a = abs(need)
        band = ("beyond_max" if mx and a > mx else "tail" if q90 and a > q90 else
                "typical" if q50 and a <= q50 else "mid")
        rows.append({"label": label, "need": round(a, 6), "band": band, "max": mx,
                     "q50": q50, "q90": q90, "n": int(p["n"]), "type": c.get("event_type_code")})
    if not rows:
        return Axis(name="type_extremity", available=False,
                    missing_input="후보 사건 타입의 과거 관측이 0건 - 분포와 대조할 수 없다")

    kills = [f"{r['label']} 단독 원인 가설 - 이 등락을 설명하려면 {r['need']:.2%} 가 필요한데 "
             f"{r['type']} 타입 과거 최대가 {r['max']:.2%} 다 (n={r['n']})"
             for r in rows if r["band"] == "beyond_max"]
    sized = [r for r in rows if r["need"] is not None]
    if sized and all(r["band"] == "typical" for r in sized):
        kills.append("전례 없는 충격 가설 - 필요 초과수익이 후보 타입 과거 중위 이하다. "
                     "평범한 크기를 설명하는 데 예외적 메커니즘을 세울 이유가 없다")

    parts = []
    for r in rows:
        if r["need"] is None:
            parts.append(f"{r['label']}: 비중 없음 - 크기 대조 불가")
        else:
            parts.append(f"{r['label']}: 필요 {r['need']:.2%} vs 타입 중위 {r.get('q50', 0):.2%}"
                         f" / 최대 {r['max']:.2%} ({r['band']}, n={r['n']})")
    return Axis(name="type_extremity", available=True, kills=tuple(kills),
                says="필요 초과수익과 타입 과거 분포 - " + " | ".join(parts), value=rows)


# ── 진입점 ──────────────────────────────────────────────────────────────
def take(cd, sql, *, question: Question, candidates: list[dict]) -> Fingerprint:
    """지문을 뜬다. **여기서 나온 `kills` 가 P2 프롬프트에 그대로 실린다.**

    축의 순서가 곧 읽는 순서다. 모양 → 넓이 → 시점 → 사전 표류 → 크기. 앞의 축이 뒤의 축을
    해석하는 틀을 준다(집중된 등락에서만 개별 사건의 필요 크기가 의미를 갖는다).

    잴 수 있는 축과 없는 축을 한 목록에 섞어 담는다. 나누면 `brief()` 가 부재를 각주로 밀고,
    각주로 밀린 부재는 읽히지 않는다 - 없는 데이터를 전제한 가설이 그 틈으로 들어온다.
    """
    cands = list(candidates or [])
    axes = [
        _measured("shape", lambda: _shape(cd, question)),
        Axis(name="intraday_shape", available=False, missing_input=NO_INTRADAY["intraday_shape"]),
        _measured("breadth", lambda: _breadth(cd, question, cands)),
        _measured("event_timing", lambda: _event_timing(sql, question, cands)),
        Axis(name="intraday_timing", available=False,
             missing_input=NO_INTRADAY["intraday_timing"]),
        _measured("pre_drift", lambda: _pre_drift(cd, question, cands)),
        _measured("type_extremity", lambda: _type_extremity(cd, question, cands)),
    ]
    axes += [Axis(name=k, available=False, missing_input=v) for k, v in NO_LEDGER.items()]

    fp = Fingerprint(axes=axes)
    log("causal.p1.done", axes=len(axes), measured=sum(1 for a in axes if a.available),
        kills=len(fp.kills), unavailable=len(fp.unavailable))
    return fp


__all__ = ["CO_MOVE", "DRIFT_DAYS", "MIN_PEERS", "NO_INTRADAY", "NO_LEDGER", "TOP_K", "take"]
