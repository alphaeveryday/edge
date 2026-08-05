"""14 도구를 한 번 불러 **(문장 · 근거)** 쌍으로 낸다 - 산문이 읽는 관측 층.

왜 따로 있나: 산문은 문단으로 읽혀야 하고, 문단 안에서 수치가 튀면 읽히지 않는다.
그래서 **문장은 사람 말로, 잰 값은 문장 뒤 대괄호로** 나눈다. 그 분리를 산문 쪽이
매번 손으로 하면 도구가 늘 때마다 어법이 갈린다 - 여기서 한 번만 정한다.

세 가지를 코드가 강제한다:

1. **없는 도구는 사유가 남는다.** `판정불가` 는 조용히 사라지지 않고 `skipped` 로
   올라간다. 부재를 '효과 없음' 으로 읽히게 두면 그게 기각 위장이다.
2. **가설이 필요한 도구는 부르지 않는다.** `fin_item`(질의어) · `dg_probe`(항목코드) ·
   `stability`(노출 축) · `edge_tests`(튜플 목록) · `run_trial`(사건타입 후보)은
   가설 에이전트가 자리를 채워야 한다. 관측 패스가 결과를 보고 대신 고르면
   proxy·표본 선택 편향이다.

방향(`sign`)은 도구가 신고한 `signed` 에서만 온다. 키 이름을 추측해 부호로 읽으면
`macro_z` 의 절댓값 z 를 방향으로 읽던 그 실수로 돌아간다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import surface as S
from .vocab import ALPHA

# 가설이 정해져야 부를 수 있는 도구와 **비어 있는 자리**의 이름.
NEEDS_HYPOTHESIS = {
    "fin_item": "질의어(어느 재무 항목인가)",
    "dg_probe": "항목코드(어느 시장 항목인가)",
    "stability": "노출 축(어느 계열로 재는가)",
    "edge_tests": "스키마로 고른 튜플 후보 목록",
    "run_trial": "뉴스 의미로 고른 사건타입 후보",
}
# 가격 움직임에 대한 주장을 만들지 않는 도구 - 사전 조회용.
MAX_TRIALS = 3          # 문단에 세울 일단위 ATT 문장 수 상한
NOT_A_CLAIM = {"dg_catalog": "사전 조회 도구 - 오늘 움직임에 대한 주장을 만들지 않는다"}


@dataclass(frozen=True, slots=True)
class Obs:
    """관측 하나. `text` 는 문단에 들어가는 문장, `ground` 는 그 뒤에 붙는 근거."""

    tool: str
    text: str
    ground: str
    sign: int = 0            # -1 내림 · 0 방향 없음 · +1 오름 (도구가 신고한 것만)


def _sign(r: dict) -> int:
    v = r.get("signed")
    if v is None:
        return 0
    return 1 if float(v) > 0 else (-1 if float(v) < 0 else 0)


def _pct(v) -> str:
    return "?" if v is None else f"{float(v) * 100:+.2f}%"


def _base_rate(r: dict) -> Obs:
    ex = float(r["exceed_p"])
    rare = ex <= 0.05
    return Obs("base_rate",
               "오늘 움직임은 이 종목의 과거와 견줘 드문 크기였어요." if rare else
               "오늘 움직임은 이 종목에서 흔히 있던 크기였어요.",
               f"기저율 · 과거 {r['n']}일 중 이보다 큰 날 {ex * 100:.1f}%")


def _peer(r: dict) -> Obs:
    same = bool(r.get("same_sign"))
    return Obs("peer_rank",
               "같은 업종도 같은 방향으로 움직였어요." if same else
               "같은 업종은 반대로 갔는데 이 종목만 움직였어요.",
               f"동종 {r['n_peers']}종목 중 {r['rank']}위 · "
               f"업종중앙 {_pct(r['peer_median'])}", _sign(r))


def _flow(r: dict) -> Obs:
    s = _sign(r)
    top = r.get("top") or ""
    return Obs("flow_detail",
               "투자자 자금은 들어오는 쪽이었어요." if s > 0 else
               ("투자자 자금은 빠지는 쪽이었어요." if s < 0 else
                "투자자 자금은 어느 쪽으로도 기울지 않았어요."),
               f"수급 {r['n_days']}일 누적 합 {_pct(r.get('signed'))}"
               + (f" · 최대 {top}" if top else ""), s)


def _macro(r: dict) -> Obs:
    # `who` 가 이미 '무엇이 몇 z' 를 담는다 - 뒤에 z 를 또 붙이면 같은 수가 두 번 나온다.
    return Obs("macro_z",
               "전날 해외 지표와 환율 가운데 평소와 다르게 움직인 것이 있었어요.",
               "거시 · " + (str(r.get("who")) or f"z={float(r['z']):+.1f}"))


def _series(r: dict) -> Obs | None:
    """계열 방아쇠. **거시 계열은 뺀다** - 그 자리는 `macro_z` 가 이미 말했다."""
    z = {k: v for k, v in (r.get("z") or {}).items() if k != "거시"}
    if not z:
        return None
    hot = sorted(z.items(), key=lambda kv: -abs(float(kv[1])))[:3]
    return Obs("series_z",
               "이 종목의 지표 가운데 평소 범위를 벗어난 것이 있었어요.",
               "계열 · " + " · ".join(f"{k} z={float(v):+.1f}" for k, v in hot))


def _clean(s: str) -> str:
    """도구 메모에서 **개발용 대괄호 주석**을 뗀다 - 산문에 `[문서실측: …]` 이 새어 나왔다."""
    return re.sub(r"\s*\[[^\]]*\]", "", str(s)).strip()


def _consensus(r: dict) -> Obs:
    s = _sign(r)
    return Obs("consensus_revision",
               "앞으로 벌 이익에 대한 기대가 올라와 있었어요." if s > 0 else
               ("앞으로 벌 이익에 대한 기대가 내려와 있었어요." if s < 0 else
                "앞으로 벌 이익에 대한 기대는 거의 그대로였어요."),
               f"컨센서스 {r.get('fiscal_year') or ''} · "
               f"{_clean(r.get('headline') or '')}".strip(" ·"),
               s)




def _trial(r: dict, etype: str) -> tuple[float, Obs] | None:
    """(p, 관측). **유의하지 않으면 '실제로 더 올랐다' 고 말하지 않는다.**

    실측에서 잡힌 거짓: p=1.000 인 ATT 에 '실제로 더 올랐어요' 라고 썼다. 추정 부호가
    있다는 것과 그 부호가 0 과 구별된다는 것은 다른 말이다 - 어법을 갈라 놓는다.
    사건타입 이름도 문장에 넣는다. 이름이 없으면 같은 문장이 타입 수만큼 반복된다.
    """
    if r.get("att") is None:
        return None
    att, p = float(r["att"]), float(r.get("p_adj") or r.get("p") or 1.0)
    s = 1 if att > 0 else -1
    nm = etype.split(".")[-1]
    up = "더 올리는" if s > 0 else "더 내리는"
    if p < ALPHA and r.get("pretrend_ok"):
        text = f"{nm} 사건이 가격을 {up} 영향이 확인됐어요."
    elif p < ALPHA:
        text = f"{nm} 사건 전부터 가격이 움직이고 있어 이 사건의 영향만 확인할 수 없어요."
        s = 0
    else:
        text = f"{nm} 사건의 영향은 뚜렷하게 확인되지 않았어요."
        s = 0
    return p, Obs("run_trial", text,
                  f"일단위 ATT {att * 100:+.2f}%p p={p:.3f} · 짝 {r.get('pairs')}"
                  f" · {'사전추세 통과' if r.get('pretrend_ok') else '사전추세 미통과'}", s)


# 도구 이름 -> (추가 인자, 문장 제작기). 인자는 셀이 정하고 우리가 고르지 않는다.
_PLAIN = {
    "base_rate": _base_rate, "peer_rank": _peer, "flow_detail": _flow,
    "macro_z": _macro, "series_z": _series, "consensus_revision": _consensus,
}


def observe(lake, ticker: str, instrument_id: str, day: str, *,
            etypes: list[str] | None = None) -> tuple[list[Obs], list[str]]:
    """14 도구를 한 번 돌려 (관측, 못 부른 사유) 를 낸다.

    `etypes` 는 **오늘 실제로 있었던 사건타입**이다. 없으면 인과 도구는 부를 자리가
    없다 - 우리가 타입을 고르면 그게 표본 고르기다.
    """
    obs: list[Obs] = []
    skipped: list[str] = []

    for name, make in _PLAIN.items():
        try:
            r = S.call(lake, name, day=day, instrument_id=instrument_id,
                       ticker=ticker.split(".")[0])
        except Exception as exc:                                  # noqa: BLE001
            skipped.append(f"{name}: 호출 실패 ({type(exc).__name__})")
            continue
        if r.get("verdict") != "계산됨":
            skipped.append(f"{name}: {r.get('reason') or '판정불가'}")
            continue
        obs.append(make(r))

    # 공급계약은 종목 커버리지가 얇아 대개 판정불가다 - 사유를 그대로 올린다.
    try:
        r = S.call(lake, "business_mix", day=day, instrument_id=instrument_id)
        if r.get("verdict") == "계산됨":
            obs.append(Obs("business_mix",
                           "매출이 몇몇 거래처에 몰려 있는 구조예요.",
                           f"사업 · 계약 {r['n_contracts']}건 · 최대 거래처 "
                           f"{r.get('top_counterparty') or '?'} {_pct(r.get('top_share'))}"))
        else:
            skipped.append(f"business_mix: {r.get('reason') or '판정불가'}")
    except Exception as exc:                                      # noqa: BLE001
        skipped.append(f"business_mix: 호출 실패 ({type(exc).__name__})")


    for name, why in NEEDS_HYPOTHESIS.items():
        skipped.append(f"{name}: {why} 가 정해져야 부른다 - 관측 패스가 고르지 않는다")
    for name, why in NOT_A_CLAIM.items():
        skipped.append(f"{name}: {why}")
    return obs, skipped


__all__ = ["MAX_TRIALS", "NEEDS_HYPOTHESIS", "NOT_A_CLAIM", "Obs", "observe"]
