"""검정 에이전트 루프 — 거친 가설을 받아 **구체화**하고 함의만 넘긴다.

## 세 층의 계약 (사용자 설계)

    가설 에이전트  거칠게 낸다      닫힌 슬롯, 부호는 의도
    검정 에이전트  구체화한다        <- 이 모듈
    설명 에이전트  함의만 받는다     조립만. 원자료를 다시 안 본다

## 검정 층이 하는 일 (판정이 아니라 설계 + 구체화)

1. **거친 처치를 쪼갠다.** 사건타입 하나에 술어·단계·역할·신규성 네 축을 곱해
   후보를 만든다. `CONTRACT.SIGNING` 은 MOU 와 확정계약이 다른 처치다.
2. **위약을 반드시 붙인다.** 재보도(DUPLICATE_REBROADCAST)는 새 정보가 없으니
   ATT≈0 이어야 한다. 유의하면 그 셀의 설계가 실패한 것이고 함의를 넘기지 않는다.
3. **CATE 로 조건을 찾는다.** 유의한 처치가 있으면 어느 조건에서 강해지는지.
4. **찾은 것을 계열족으로 환원한다** (`reduce.py`). 환원 안 되면 어휘 확장 요청.
5. **중복 탐색을 안 한다.** 가설 층이 이미 본 것(`probe_brief`)을 첫 입력으로 받는다.

## 판정은 코드가 한다

이 루프는 LLM 을 부르지 않는다. 8셀 실측에서 LLM 판정이 코드 결론을 뒤집고 산출물을
흐렸다 - 순열·게이트·CATE 는 결정론이어야 한다. 이 모듈의 '에이전트' 는 **탐색 전략**
이지 판정자가 아니다: 어느 후보를 어느 순서로 시행할지 정하고, 위약이 깨지면 멈춘다.

사용:  python -m edge_analysis.statics.verifier <event_type> <YYYY-MM-DD> [층]
"""
from __future__ import annotations

import sys
from dataclasses import dataclass

from .vocab import ALPHA, PLACEBO_NOVELTY

MAX_PROBES = 8          # 셀당 시행 상한 (다중비교 폭발 방지 - Bonferroni 분모)
COND_CANDIDATES = ("국면/수준", "배수/수준", "레버리지/수준", "주주/수준",
                   "공매도/수준", "거래량/수준")


@dataclass(frozen=True, slots=True)
class Implication:
    """설명 층에 넘기는 **함의**. 원자료도 중간 판정도 넘기지 않는다."""

    claim: str              # 한 문장 - 무엇이 무엇을 얼마나
    att: float | None       # %p (로그)
    p: float | None
    n_pairs: int
    reduced: tuple[str, str] | None   # (계열족, 변환) - 환원 결과
    placebo_ok: bool
    pretrend_ok: bool
    balanced: bool
    note: str = ""

    @property
    def credible(self) -> bool:
        """함의를 넘길 자격. **셋 다 통과해야** 한다 - 하나라도 깨지면 침묵이 낫다."""
        return (self.placebo_ok and self.pretrend_ok and self.balanced
                and self.p is not None and self.att is not None)


def verify(lake, day: str, *, etype: str, layer: str = "고유",
           brief: str = "", max_probes: int = MAX_PROBES) -> tuple[list[Implication], str]:
    """(함의 목록, 로그). 함의는 `credible` 만 설명 층으로 간다.

    순서가 전략이다:
      ① 위약(재보도) 먼저 - 깨지면 이 셀에서 이 타입은 못 쓴다. 조기 중단.
      ② 거친 처치 - 기준선
      ③ 구체화 후보 - 단계·신규성으로 쪼갠 것
      ④ 유의한 것에만 CATE - 조건을 찾는다
    """
    from .reduce import reduce_item
    from .trial import run_trial
    log: list[str] = []
    if brief:
        log.append(brief)
    out: list[Implication] = []
    alpha = ALPHA / max(max_probes, 1)
    log.append(f"[검정 층] {etype} · {layer}층 · 임계 α/m={alpha:.4f} (m={max_probes})")

    # ① 위약 먼저 - 재보도가 유의하면 이 셀에서 이 타입은 신뢰 불가
    pb = run_trial(lake, day, etype=etype, layer=layer, novelty=PLACEBO_NOVELTY)
    pb_ok = True
    if pb.get("verdict") == "계산됨":
        pb_ok = pb["p"] >= ALPHA
        log.append(f"  위약(재보도): ATT {pb['att'] * 100:+.3f}%p (p={pb['p']:.3f}, "
                   f"짝 {pb['pairs']}) — " + ("통과" if pb_ok else "**실패: 재보도에"
                   " 새 정보가 없는데 유의하다. 이 타입은 이 셀에서 못 쓴다**"))
        if not pb_ok:
            return [], "\n".join(log)
    else:
        log.append(f"  위약(재보도): 판정불가 — {pb.get('reason', '?')[:70]}"
                   " (위약 없이 진행 - 그 사실을 함의에 싣는다)")
        pb_ok = False

    # ②③ 기준선 + 구체화 후보
    probes: list[tuple[str, dict]] = [("전체", {})]
    for st in ("DEFINITIVE_SIGNED", "MOU_LOI", "ANNOUNCED", "EFFECTIVE"):
        probes.append((f"단계={st}", {"stage": st}))
    probes.append(("신규 보도", {"novelty": "FIRST_IN_THREAD"}))
    probes.append(("발행자", {"role": "ISSUER"}))
    probes = probes[:max_probes]

    for name, kw in probes:
        r = run_trial(lake, day, etype=etype, layer=layer, **kw)
        if r.get("verdict") != "계산됨":
            log.append(f"  {name}: 판정불가 — {r.get('reason', '?')[:64]}")
            continue
        sig = r["p"] < alpha
        log.append(f"  {name}: 짝 {r['pairs']:<5} ATT {r['att'] * 100:+.3f}%p "
                   f"(p={r['p']:.3f}) 균형 {'o' if r['balanced'] else 'X'} "
                   f"사전추세 {'o' if r['pretrend_ok'] else 'X'}"
                   + ("  **유의**" if sig else ""))
        if not sig:
            continue
        # ④ 유의한 것에만 CATE - 어느 조건에서 강해지나
        best = None
        for ck in COND_CANDIDATES:
            rc = run_trial(lake, day, etype=etype, layer=layer, cond_key=ck, **kw)
            if rc.get("verdict") != "계산됨" or rc.get("inter") is None:
                continue
            if rc["inter_p"] < alpha and (best is None or rc["inter_p"] < best[2]):
                best = (ck, rc["inter"], rc["inter_p"], rc.get("att_cond"))
        cond_say = ""
        if best:
            ck, d, pp, ac = best
            cond_say = (f" · 조건 {ck} 에서 강해진다 (교호항 {d * 100:+.3f}%p, "
                        f"p={pp:.3f}" + (f", 조건부 ATT {ac * 100:+.3f}%p" if ac else "") + ")")
            log.append(f"    CATE: {cond_say.strip(' ·')}")
        red = reduce_item(*_expose_name(kw))
        out.append(Implication(
            claim=f"{etype.split('.')[-1]}({name}) 가 {layer}층 수익을 "
                  f"{r['att'] * 100:+.3f}%p 움직였다{cond_say}",
            att=r["att"], p=r["p"], n_pairs=r["pairs"], reduced=red,
            placebo_ok=pb_ok, pretrend_ok=r["pretrend_ok"], balanced=r["balanced"],
            note=("위약 미계측" if not pb_ok else "")))
    if not out:
        log.append("  → 유의한 처치 없음. **설명 층에 넘길 함의가 없다** - 이 타입은 "
                   "이 셀을 설명하지 않는다 (그것도 결과다)")
    return out, "\n".join(log)


def _expose_name(kw: dict) -> tuple[str, str]:
    """구체화 슬롯 → 환원 입력. 처치 축은 계열족이 아니므로 대개 None 이 맞다."""
    return (" ".join(str(v) for v in kw.values()) or "처치", "")


def say_implications(imps: list[Implication]) -> str:
    """설명 층으로 넘기는 형태. **자격 없는 함의는 사유와 함께 접는다.**"""
    if not imps:
        return "[함의] 없음 — 이 타입은 이 셀을 설명하지 않는다"
    out = []
    for i in imps:
        if i.credible:
            out.append(f"[함의] {i.claim} (p={i.p:.3f}, 짝 {i.n_pairs})")
        else:
            bad = [n for n, ok in (("위약", i.placebo_ok), ("사전추세", i.pretrend_ok),
                                   ("균형", i.balanced)) if not ok]
            out.append(f"[접음] {i.claim[:70]} — {' · '.join(bad)} 실패로 넘기지 않는다")
    return "\n".join(out)


def _selfcheck() -> None:
    ok = Implication("x", 0.01, 0.001, 100, ("배수", "수준"), True, True, True)
    assert ok.credible
    assert not Implication("x", 0.01, 0.001, 100, None, False, True, True).credible
    assert not Implication("x", 0.01, 0.001, 100, None, True, False, True).credible
    assert not Implication("x", 0.01, 0.001, 100, None, True, True, False).credible
    assert not Implication("x", None, None, 0, None, True, True, True).credible
    assert "없음" in say_implications([])
    assert "[접음]" in say_implications(
        [Implication("c", 0.01, 0.001, 9, None, False, True, True)])
    print("ok")


def main() -> None:
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    from .duck import CausalLake
    layer = sys.argv[3] if len(sys.argv) > 3 else "고유"
    imps, log = verify(CausalLake(), sys.argv[2], etype=sys.argv[1], layer=layer)
    print(log)
    print()
    print(say_implications(imps))


if __name__ == "__main__":
    main()
