"""비교 실행기 — 같은 셀·같은 모델·같은 계약 검사로 **두 제안기**를 돌린다.

    baseline  STORM 원본. 도구 15개를 한 덩어리(catalog.SCHEMA)로 매 턴 주고,
              어휘는 digest() 한 방에 5,600자로 준다.
    dyntool   상태기계. 그 턴에 부를 수 있는 것만 주고, 어휘는 가족→타입으로 쪼개 준다.
              전이 조건(접지 1회 등)을 **코드가** 지킨다.

바뀌는 것은 **어휘·도구를 어떻게 주느냐** 하나뿐이다. 셀·모델·온도·계약 검사(`propose.check`)
·출력 스키마는 같게 둔다. 그래야 차이가 이 변수 탓이 된다.

    python run.py --n 8 --model deepseek-v4-flash
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import fsm as F                                   # noqa: E402
from storm import cells as CE                     # noqa: E402
from storm import llm as L                        # noqa: E402
from storm import propose as P                    # noqa: E402
from storm import scope as SC                     # noqa: E402

RUNS = HERE / "runs"
MAX_TURNS = 12

# 상태기계 판 시스템 프롬프트. **원본과 같은 계약**을 요구하되 도구 카탈로그가 없다 -
# 메뉴는 매 턴 사용자 메시지로 온다(상태가 바뀌면 메뉴도 바뀌므로).
SYSTEM_DYN = """너는 가격 이상(異常) 하나를 받아 **검정 가능한 인과 명제**를 세운다.

## 진행 방식 — 단계가 있다
SCOPE → EVIDENCE → VOCAB → STRUCTURE → EMIT 순서로 간다.
**지금 단계에서 부를 수 있는 도구만 보인다.** 없는 이름을 부르면 없다고 답이 온다.
다음 단계로 가려면 `next()` 를 불러라. 조건을 못 채웠으면 안 넘어간다 - 무엇이 모자란지 알려준다.

어휘를 통째로 주지 않는다. VOCAB 단계에서 `families()` → `family(이름)` → `types(id)` 로
필요한 가지만 파고들어라. 지어낸 ID 는 `ground()` 에서 걸린다.

## 명제의 조건 - 넷 다 필수
1. **반증 가능** - 틀렸다면 무엇이 관측되나 (`breaks_if`)
2. **판별 가능** - 경쟁 구조 2개 이상이 서로 다른 관측을 예측한다 (`discriminator`)
3. **접지 가능** - event_id·타입 ID 는 **도구가 준 것만**
4. **부호 없음** - 부호는 대비 검정의 산출이다

## 매 턴 출력 (JSON 하나)
도구를 부를 때:  {"thought": "...", "call": "events('ORG_KR_000660','2026-06-01','2026-06-01')"}
끝났을 때(EMIT): {"thought": "...", "dag": {...}}

%s

dag 스키마는 아래와 같다.
%s
"""


def _dag_schema() -> str:
    """**원본 프롬프트에서 출력 계약만 떼어 온다** - 스키마가 갈리면 비교가 안 된다."""
    src = P.SYSTEM
    i = src.find("## 출력")
    if i < 0:
        raise RuntimeError("원본 프롬프트에서 출력 계약을 못 찾았다 - 비교가 성립 안 한다")
    # `{{` 는 str.format 용 이스케이프다. 여기서는 format 을 안 쓰므로 되돌린다.
    return src[i:].replace("{{", "{").replace("}}", "}")


def _user(cell: dict, machine: F.Machine, trace: list[dict], turn: int) -> str:
    card = SC.card(cell["etf"], cell["date"]) if cell.get("etf") else ""
    hist = "\n".join(f"[{t['call']}]\n{t['out'][:900]}" for t in trace[-4:])
    return (f"{card}\n\n{machine.menu()}\n\n"
            f"턴 {turn}/{MAX_TURNS}. 지금까지 관측:\n{hist or '(없음)'}")


def run_dyn(cell: dict, client: L.LLM) -> dict:
    """상태기계 판 제안. 원본과 **같은 검사**를 통과해야 성공이다."""
    m, trace, t0 = F.Machine(), [], time.time()
    system = SYSTEM_DYN % (P.PATTERNS, _dag_schema())
    sent = len(system)
    for turn in range(1, MAX_TURNS + 1):
        user = _user(cell, m, trace, turn)
        sent += len(user)
        try:
            r = client.complete_json(system, user)
        except Exception as e:                     # noqa: BLE001 - 실패도 기록이다
            return {"cell": cell, "error": f"{type(e).__name__}: {e}",
                    "trace": trace, "fsm": m.stats(), "chars_sent": sent}
        if isinstance(r, dict) and r.get("dag"):
            dag = r["dag"]
            bad = P.check(dag, cell.get("scope"))
            return {"cell": cell, **dag, "trace": trace, "fsm": m.stats(),
                    "violations": bad, "turns": turn, "chars_sent": sent,
                    "secs": round(time.time() - t0, 1)}
        call = (r or {}).get("call") or "next()"
        trace.append({"call": call, "out": m.observe(call)})
    return {"cell": cell, "error": "턴 소진", "trace": trace, "fsm": m.stats(),
            "turns": MAX_TURNS, "chars_sent": sent, "secs": round(time.time() - t0, 1)}


def run_base(cell: dict, client: L.LLM) -> dict:
    """원본 제안기. 같은 모델·같은 셀로 돌린다."""
    t0, trace = time.time(), []
    system = P.sysprompt()
    sent = len(system)
    for turn in range(1, MAX_TURNS + 1):
        user = P._user(cell, trace, turn)
        sent += len(user)
        try:
            r = client.complete_json(system, user)
        except Exception as e:                     # noqa: BLE001
            return {"cell": cell, "error": f"{type(e).__name__}: {e}",
                    "trace": trace, "chars_sent": sent}
        if isinstance(r, dict) and r.get("dag"):
            dag = r["dag"]
            return {"cell": cell, **dag, "trace": trace, "violations": P.check(dag, cell.get("scope")),
                    "turns": turn, "chars_sent": sent, "secs": round(time.time() - t0, 1)}
        call = (r or {}).get("call") or ""
        trace.append({"call": call, "out": P.observe(call) if call else "(호출 없음)"})
    return {"cell": cell, "error": "턴 소진", "trace": trace, "turns": MAX_TURNS,
            "chars_sent": sent, "secs": round(time.time() - t0, 1)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="스키마-프롬프트 vs 동적-도구 비교")
    ap.add_argument("--n", type=int, default=6)
    ap.add_argument("--model", default="deepseek-v4-flash")
    ap.add_argument("--arm", choices=("both", "base", "dyn", "dyn2"), default="both")
    ap.add_argument("--seed", type=int, default=20260801)
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    RUNS.mkdir(exist_ok=True)

    import os
    os.environ["DEEPSEEK_MODEL"] = a.model
    client = L.LLM()
    rows = CE.sample(a.n, strata=True)
    print(f"{len(rows)}개 셀 · 모델 {a.model}")

    for i, row in enumerate(rows, 1):
        cell = CE.spec(row)
        key = f"{cell['etf'].replace('.', '_')}_{cell['date']}"
        arms = (("base", run_base), ("dyn", run_dyn), ("dyn2", run_dyn))
        for arm, fn in arms:
            if a.arm not in ("both", arm):
                continue
            out = RUNS / f"{arm}_{key}.json"
            if out.exists():
                print(f"  [{i}] {arm} {key} 건너뜀(있음)")
                continue
            r = fn(cell, client)
            out.write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
            v = r.get("violations")
            print(f"  [{i}] {arm} {key} 턴={r.get('turns')} 위반={len(v) if v else 0} "
                  f"보낸글자={r.get('chars_sent')} {r.get('error', '')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
