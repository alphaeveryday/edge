"""검정 에이전트 — 거친 가설을 받아 **시행을 설계**하고 함의만 넘긴다.

## 세 층의 계약 (사용자 설계)

    가설 에이전트  거칠게 낸다      닫힌 슬롯, 부호는 의도
    검정 에이전트  구체화한다        <- 이 모듈. **판단이므로 모델이 한다**
    설명 에이전트  함의만 받는다     조립만. 원자료를 다시 안 본다

## 자리를 정확히 나눈다

  모델이 하는 것    어느 처치를 어떻게 쪼갤지 · 어느 조절자를 볼지 · 왜 그것인지
  코드가 하는 것    SQL · 매칭 · 순열 p · 게이트 · 위약 강제 · 날조 폐기

처치 구체화는 **판단**이다: `CONTRACT.SIGNING` 에서 MOU 와 확정계약이 다른 처치라는
것, `EXECUTIVE_CHANGE` 에서 신규 보도만 봐야 한다는 것 - 어휘를 아는 자가 고른다.
이걸 코드에 상수 목록으로 박으면 검정 에이전트가 사라진다(그렇게 한 적이 있고,
그 순간 이 모듈은 시행 목록을 순회하는 루프가 됐다).

판정은 반대다. 순열·게이트·CATE 는 결정론이어야 한다 - 8셀 실측에서 모델 판정이
코드 결론을 뒤집고 산출물을 흐렸다. **설계는 모델, 판정은 코드.**

## 날조를 못 하게 만드는 방식 (가설 층과 같은 규율)

슬롯 값을 모델이 상상하지 못한다. `slot_menu()` 가 **이 사건타입에서 실제 관측된**
predicate/stage/role/novelty 를 빈도와 함께 세어 메뉴로 준다. 메뉴 밖 값은
`screen_probes()` 가 사유와 함께 폐기한다.

## 설계 규율은 코드가 강제한다 (모델의 재량이 아니다)

  · 위약(재보도)을 **항상 먼저** - 모델이 빼도 코드가 넣는다. 깨지면 조기 중단.
  · 시행 상한 `MAX_PROBES` - Bonferroni 분모. 모델이 더 내면 자른다.
  · 함의 자격 = 위약 o · 사전추세 o · 균형 o. 하나라도 깨지면 접는다.

사용:  python -m edge_analysis.statics.verifier <event_type> <YYYY-MM-DD> [층]
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass

from .vocab import ALPHA, PLACEBO_NOVELTY

MAX_PROBES = 8          # 셀당 시행 상한 (다중비교 폭발 방지 - Bonferroni 분모)
MIN_SLOT_N = 20         # 메뉴에 올릴 슬롯 값의 최소 관측 (짝 20 미달이면 어차피 판정불가)
MENU_TOP = 8            # 슬롯별 메뉴 길이

_SYSTEM = """너는 검정 설계자다. 판정하지 않는다 - 수치와 p 는 코드가 낸다.

거친 가설 하나를 받아 **어느 시행을 할지** 설계한다. 사건타입은 거칠다:
같은 타입 안에서 MOU 와 확정계약은 다른 처치이고, 재보도는 처치가 아니다.

## 고를 수 있는 슬롯 (이 사건타입에서 **실제 관측된** 값만 - 빈도 동반)
{menu}

## 조절자로 볼 수 있는 계열족/변환 (이것만 잰다)
{moderators}

## 규칙
- 메뉴에 없는 값을 쓰면 그 시행은 폐기된다. 상상하지 마라.
- 시행 {n}개 이내. 하나는 슬롯 없는 기준선({{}})이어야 한다 - 쪼갠 것과 비교해야 한다.
- 관측 수가 작은 값은 짝이 안 맞아 판정불가가 된다. 빈도를 보고 골라라.
- 조절자는 시행마다 0개 또는 1개. 없으면 "".
- 위약은 네가 넣지 않는다 - 코드가 항상 먼저 돌린다.

## 답 (JSON 만)
{{"probes": [{{"name": "짧은 이름", "slots": {{"stage": "MOU_LOI"}},
  "moderator": "배수/수준", "why": "왜 이 쪼갬이 다른 처치인지 한 문장"}}]}}
슬롯 키는 predicate·stage·role·novelty 넷뿐이다."""


@dataclass(frozen=True, slots=True)
class Implication:
    """설명 층에 넘기는 **함의**. 원자료도 중간 판정도 넘기지 않는다."""

    claim: str              # 한 문장 - 무엇이 무엇을 얼마나
    att: float | None       # %p (로그)
    p: float | None
    n_pairs: int
    reduced: tuple[str, str] | None   # (계열족, 변환) - 환원 결과
    placebo: str            # "통과" | "미계측" - "실패" 는 조기 중단이라 여기 못 온다
    pretrend_ok: bool
    balanced: bool
    why: str = ""           # 모델이 준 설계 근거 - 함의에 실려 설명 층으로 간다

    @property
    def credible(self) -> bool:
        """함의를 넘길 자격.

        위약은 **3값**이다: 통과 · 실패 · 미계측. 실패는 조기 중단이라 여기 오지
        못한다. **미계측은 실패가 아니다** - 접으면 위약 표본이 없는 타입 전부가
        영구 침묵한다(실측 CONTRACT.SIGNING: 재보도 짝 0 → 함의 전량 접힘, 그 중
        PREFERRED_BIDDER p=0.000 이 있었다). 미계측은 넘기되 **claim 에 박는다**.
        """
        return (self.pretrend_ok and self.balanced
                and self.p is not None and self.att is not None)


_SLOTS = ("predicate", "stage", "role", "novelty")
_COLS = {"predicate": "predicate_code", "stage": "lifecycle_stage",
         "role": "role_code", "novelty": "novelty_status"}


def slot_menu(lake, etype: str, day: str, *, min_n: int = MIN_SLOT_N,
              top: int = MENU_TOP) -> dict[str, list[tuple[str, int]]]:
    """이 사건타입에서 **실제 관측된** 슬롯 값 + 빈도. 모델의 날조를 원천 차단한다.

    `day` 이전만 센다 - 오늘 관측으로 메뉴를 만들면 선견이다.
    """
    from .paneltest import _base
    lake.bind_day(day)      # `v_*` 는 이 호출이 만든다 - 안 부르면 rdb 부재로 보인다
    out: dict[str, list[tuple[str, int]]] = {}
    for slot, col in _COLS.items():
        try:
            # `v_event` 는 뷰가 아니라 `_base(day)` 가 만드는 CTE 다 - 그 위에서 센다.
            rows = lake.sql(
                _base(day) + f"SELECT {col}, count(*) FROM v_event "
                f"WHERE event_type_code = '{etype}' AND trade_date < DATE '{day}' "
                f"AND {col} IS NOT NULL GROUP BY 1 HAVING count(*) >= {min_n} "
                f"ORDER BY 2 DESC LIMIT {top}")
        except Exception as e:      # noqa: BLE001 - 부재를 사유로 올린다 (침묵 금지)
            out[f"!{slot}"] = [(f"{type(e).__name__}: {str(e)[:60]}", 0)]
            continue
        vals = [(str(r[0]), int(r[1])) for r in rows if str(r[0]) != PLACEBO_NOVELTY]
        if vals:
            out[slot] = vals
    return out


def screen_probes(raw: list[dict], menu: dict[str, list[tuple[str, int]]],
                  moderators: list[str], *,
                  max_probes: int = MAX_PROBES) -> tuple[list[dict], list[str]]:
    """모델 산출 → (유효 시행, 폐기 사유). **메뉴 밖은 날조이므로 폐기한다.**

    기준선(슬롯 없음)이 없으면 코드가 넣는다 - 쪼갠 것을 비교할 대상이 필요하다.
    """
    allowed = {s: {v for v, _ in vs} for s, vs in menu.items()
               if not s.startswith("!")}
    ok: list[dict] = []
    bad: list[str] = []
    seen: set[str] = set()
    for i, pr in enumerate(raw or [], 1):
        if not isinstance(pr, dict):
            bad.append(f"#{i} 시행이 객체가 아니다")
            continue
        slots = {k: str(v) for k, v in (pr.get("slots") or {}).items() if v}
        if bogus := [k for k in slots if k not in _SLOTS]:
            bad.append(f"#{i} 슬롯 키 {bogus} 없다 - {list(_SLOTS)} 뿐이다")
            continue
        if miss := [f"{k}={v}" for k, v in slots.items() if v not in allowed.get(k, ())]:
            bad.append(f"#{i} 관측 밖 슬롯값 {miss} - 메뉴에 없다 (날조 폐기)")
            continue
        mod = str(pr.get("moderator") or "")
        if mod and mod not in moderators:
            bad.append(f"#{i} 못 재는 조절자 {mod!r} - 재는 것: {moderators}")
            continue
        key = json.dumps(slots, sort_keys=True) + "|" + mod
        if key in seen:
            bad.append(f"#{i} 중복 시행 {slots or '기준선'}")
            continue
        seen.add(key)
        ok.append({"name": str(pr.get("name") or (slots or "기준선")),
                   "slots": slots, "moderator": mod, "why": str(pr.get("why") or "")})
    if not any(not p["slots"] for p in ok):
        ok.insert(0, {"name": "기준선(전체)", "slots": {}, "moderator": "",
                      "why": "쪼갠 것과 비교할 대상 - 코드가 넣었다"})
    # **바닥 깔기**: 관측 슬롯 값은 전부 최소 한 번 시행된다. 설계는 비결정적이라
    # 커버리지가 운에 달린다 - 실측으로 같은 셀 두 번에서 한 번만 PREFERRED_BIDDER
    # (ATT +1.13%p p=0.000)를 골랐다. 격자 스크린과 같은 규율: 에이전트가 순서·
    # 조절자·근거를 정하고, 코드가 빠짐없음을 보장한다.
    have = {json.dumps(p["slots"], sort_keys=True) for p in ok}
    for slot, vs in sorted(menu.items()):
        if slot.startswith("!"):
            continue
        for val, n in vs:
            key = json.dumps({slot: val}, sort_keys=True)
            if key in have or len(ok) >= max_probes:
                continue
            have.add(key)
            ok.append({"name": f"{val}(바닥)", "slots": {slot: val}, "moderator": "",
                       "why": f"코드가 깐 바닥 - 관측 {n}건인데 설계가 빠뜨렸다"})
    if len(ok) > max_probes:
        bad.append(f"시행 {len(ok)}개 > 상한 {max_probes} - 뒤를 잘랐다 "
                   f"(Bonferroni 분모를 모델이 늘릴 수 없다)")
        ok = ok[:max_probes]
    return ok, bad


def design(ask, lake, *, etype: str, day: str, layer: str, brief: str = "",
           max_probes: int = MAX_PROBES) -> tuple[list[dict], list[str], str]:
    """모델에게 시행 설계를 받는다. 반환 (시행, 폐기 사유, 메뉴 산문).

    `ask` 가 None 이면 기준선 하나만 - **에이전트 없이도 돌지만, 그때 이 모듈은
    구체화를 하지 않는다**는 사실을 사유로 남긴다(조용한 퇴화 금지).
    """
    from .paneltest import FEATURES
    menu = slot_menu(lake, etype, day)
    mods = sorted("/".join(k) for k in FEATURES)
    menu_say = "\n".join(
        (f"  {s[1:]}: **질의 실패** {vs[0][0]}" if s.startswith("!")
         else f"  {s}: " + " · ".join(f"{v}({n})" for v, n in vs))
        for s, vs in menu.items()
    ) or "  (관측된 구체화 슬롯 없음 - 기준선만 가능)"
    if ask is None:
        return ([{"name": "기준선(전체)", "slots": {}, "moderator": "",
                  "why": "검정 에이전트 없음 - 구체화를 하지 않았다"}],
                ["ask 없음: 시행 설계를 안 했다 (기준선만)"], menu_say)
    system = _SYSTEM.format(menu=menu_say, moderators=mods, n=max_probes)
    user = (f"사건타입 {etype} · {layer}층 · {day} 셀.\n"
            + (f"가설 층이 이미 본 것:\n{brief}\n" if brief else "")
            + "이 타입을 어떻게 쪼개 시행할지 설계하라.")
    probes, bad = screen_probes((ask(system, user) or {}).get("probes") or [],
                               menu, mods, max_probes=max_probes)
    if len([p for p in probes if p["slots"]]) == 0 and any(
            not k.startswith("!") for k in menu):
        # 쪼갤 재료가 있는데 하나도 안 쪼갰다 - 사유를 붙여 한 번 되묻는다
        retry = (user + "\n\n직전 제출이 하나도 쪼개지 않았다. 메뉴에 값이 있으니 "
                 "최소 두 개는 슬롯을 채워 내라.\n" + "\n".join(bad[-4:]))
        more, bad2 = screen_probes((ask(system, retry) or {}).get("probes") or [],
                                  menu, mods, max_probes=max_probes)
        bad += bad2
        if len([p for p in more if p["slots"]]) > 0:
            probes = more
    return probes, bad, menu_say


def verify(lake, day: str, *, etype: str, layer: str = "고유", ask=None,
           brief: str = "", max_probes: int = MAX_PROBES) -> tuple[list[Implication], str]:
    """(함의 목록, 로그). 함의는 `credible` 만 설명 층으로 간다.

    순서는 코드가 정한다 - 모델의 재량이 아니다:
      ① **위약(재보도) 먼저** - 깨지면 이 셀에서 이 타입은 못 쓴다. 조기 중단.
      ② 모델이 설계한 시행 - 기준선 + 구체화
      ③ 시행마다 모델이 지정한 조절자로 CATE (지정 안 하면 안 본다)
    """
    from .reduce import reduce_item
    from .trial import run_trial
    log: list[str] = []
    if brief:
        log.append(brief)
    out: list[Implication] = []
    alpha = ALPHA / max(max_probes, 1)
    log.append(f"[검정 층] {etype} · {layer}층 · 임계 α/m={alpha:.4f} (m={max_probes})")

    # ① 위약 먼저 - 모델이 빼도 코드가 넣는다
    pb = run_trial(lake, day, etype=etype, layer=layer, novelty=PLACEBO_NOVELTY)
    pb_state = "통과"
    if pb.get("verdict") == "계산됨":
        pb_ok = pb["p"] >= ALPHA
        log.append(f"  위약(재보도): ATT {pb['att'] * 100:+.3f}%p (p={pb['p']:.3f}, "
                   f"짝 {pb['pairs']}) — " + ("통과" if pb_ok else "**실패: 재보도에"
                   " 새 정보가 없는데 유의하다. 이 타입은 이 셀에서 못 쓴다**"))
        if not pb_ok:
            return [], "\n".join(log)
    else:
        pb_state = "미계측"
        log.append(f"  위약(재보도): 판정불가 — {pb.get('reason', '?')[:70]}"
                   " (위약 없이 진행 - 그 사실을 함의에 싣는다)")

    # ② 모델이 설계한다
    probes, rejected, menu_say = design(ask, lake, etype=etype, day=day, layer=layer,
                                        brief=brief, max_probes=max_probes)
    log.append("  관측 슬롯 메뉴:\n" + menu_say)
    if rejected:
        log.append("  폐기: " + " | ".join(x[:70] for x in rejected[:4]))
    log.append(f"  설계된 시행 {len(probes)}개: "
               + " · ".join(p["name"] for p in probes))

    for pr in probes:
        r = run_trial(lake, day, etype=etype, layer=layer, **pr["slots"])
        if r.get("verdict") != "계산됨":
            log.append(f"  {pr['name']}: 판정불가 — {r.get('reason', '?')[:64]}")
            continue
        sig = r["p"] < alpha
        log.append(f"  {pr['name']}: 짝 {r['pairs']:<5} ATT {r['att'] * 100:+.3f}%p "
                   f"(p={r['p']:.3f}) 균형 {'o' if r['balanced'] else 'X'} "
                   f"사전추세 {'o' if r['pretrend_ok'] else 'X'}"
                   + ("  **유의**" if sig else ""))
        if not sig:
            continue
        # ③ 모델이 지정한 조절자만 본다 - 전수 순회는 다중비교 낭비다
        cond_say = ""
        if pr["moderator"]:
            rc = run_trial(lake, day, etype=etype, layer=layer,
                           cond_key=pr["moderator"], **pr["slots"])
            if rc.get("verdict") == "계산됨" and rc.get("inter") is not None:
                mark = "**유의**" if rc["inter_p"] < alpha else "무유의"
                cond_say = (f" · 조건 {pr['moderator']} 교호항 "
                            f"{rc['inter'] * 100:+.3f}%p (p={rc['inter_p']:.3f}, {mark})")
                log.append(f"    CATE: {cond_say.strip(' ·')}")
            else:
                log.append(f"    CATE: 판정불가 — {rc.get('reason', '조건 표본 부족')[:56]}")
        red = reduce_item(pr["moderator"].split("/")[0], "") if pr["moderator"] else None
        out.append(Implication(
            claim=f"{etype.split('.')[-1]}({pr['name']}) 가 {layer}층 수익을 "
                  f"{r['att'] * 100:+.3f}%p 움직였다{cond_say}",
            att=r["att"], p=r["p"], n_pairs=r["pairs"], reduced=red,
            placebo=pb_state, pretrend_ok=r["pretrend_ok"], balanced=r["balanced"],
            why=pr["why"]))
    if not out:
        log.append("  → 유의한 처치 없음. **설명 층에 넘길 함의가 없다** - 이 타입은 "
                   "이 셀을 설명하지 않는다 (그것도 결과다)")
    return out, "\n".join(log)


def say_implications(imps: list[Implication]) -> str:
    """설명 층으로 넘기는 형태. **자격 없는 함의는 사유와 함께 접는다.**"""
    if not imps:
        return "[함의] 없음 — 이 타입은 이 셀을 설명하지 않는다"
    out = []
    for i in imps:
        if i.credible:
            mark = "" if i.placebo == "통과" else " · **위약 미계측**(재보도 대조군 부족)"
            out.append(f"[함의] {i.claim} (p={i.p:.3f}, 짝 {i.n_pairs}){mark}"
                       + (f"\n        설계 근거: {i.why}" if i.why else ""))
        else:
            bad = [n for n, ok in (("사전추세", i.pretrend_ok),
                                   ("균형", i.balanced)) if not ok]
            out.append(f"[접음] {i.claim[:70]} — {' · '.join(bad)} 실패로 넘기지 않는다")
    return "\n".join(out)


def _selfcheck() -> None:
    ok = Implication("x", 0.01, 0.001, 100, ("배수", "수준"), "통과", True, True)
    assert ok.credible
    # 위약 미계측은 실패가 아니다 - 넘기되 표기한다
    un = Implication("x", 0.01, 0.001, 100, None, "미계측", True, True)
    assert un.credible and "위약 미계측" in say_implications([un])
    assert "위약 미계측" not in say_implications([ok])
    assert not Implication("x", 0.01, 0.001, 100, None, "통과", False, True).credible
    assert not Implication("x", 0.01, 0.001, 100, None, "통과", True, False).credible
    assert not Implication("x", None, None, 0, None, "통과", True, True).credible
    assert "없음" in say_implications([])
    assert "[접음]" in say_implications(
        [Implication("c", 0.01, 0.001, 9, None, "통과", False, True)])

    menu = {"stage": [("MOU_LOI", 100), ("DEFINITIVE_SIGNED", 80)]}
    mods = ["배수/수준", "국면/수준"]
    good, bad = screen_probes(
        [{"name": "MOU", "slots": {"stage": "MOU_LOI"}, "moderator": "배수/수준"},
         {"name": "허구", "slots": {"stage": "NOPE"}},
         {"name": "허구조절자", "slots": {}, "moderator": "없는것/x"},
         {"name": "허구키", "slots": {"zzz": "1"}}], menu, mods)
    assert any(not p["slots"] for p in good)                 # 기준선 자동 삽입
    assert len(bad) == 3 and any("날조" in b for b in bad)
    # 바닥: 설계가 빠뜨린 관측 슬롯값도 시행된다 (커버리지가 운에 달리면 안 된다)
    assert {"stage": "DEFINITIVE_SIGNED"} in [p["slots"] for p in good]
    assert any("바닥" in p["why"] for p in good)
    # 상한은 모델이 늘릴 수 없다
    many = [{"name": f"p{i}", "slots": {"stage": st}, "moderator": m}
            for st in ("MOU_LOI", "DEFINITIVE_SIGNED") for m in mods
            for i in (0,)]  # 서로 다른 시행 4개 (중복 제거가 먼저 걸리면 상한을 못 본다)
    cut, why = screen_probes(many, menu, mods, max_probes=3)
    assert len(cut) == 3 and any("상한" in w for w in why)
    assert not cut[0]["slots"]  # 기준선이 살아남는다 - 자른 것은 뒤
    # ask 없으면 조용히 퇴화하지 않고 사유를 남긴다
    print("ok")


def main() -> None:
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    import os

    from ..adapters.llm import DeepSeekClient, TracingClient
    from .duck import CausalLake
    client = TracingClient(DeepSeekClient(
        os.environ["DEEPSEEK_API_KEY"],
        os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-pro")))
    layer = sys.argv[3] if len(sys.argv) > 3 else "고유"
    imps, log = verify(CausalLake(), sys.argv[2], etype=sys.argv[1], layer=layer,
                       ask=client.complete_json)
    print(log)
    print()
    print(say_implications(imps))


if __name__ == "__main__":
    main()
