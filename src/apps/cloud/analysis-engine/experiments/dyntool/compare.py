"""두 팔을 **같은 자로** 잰다 — 정량 지표 + 정성 비교용 발췌.

정량만으로는 "의미 그래프가 더 좋아졌나"를 못 답한다. 그래서 수치와 함께 각 팔의
구조·근거·접지를 나란히 뽑아 사람이 읽게 한다.

지표는 셋으로 나뉜다.

    계약    위반 수 · 구조 수 · 간선 수 · 판별자 유무      (판가름이 가능한 제안인가)
    접지    ground 성공 · 지어낸 ID · testimonial 인용     (전제가 실재하나)
    비용    턴 수 · 보낸 글자 · 도구 호출 · 초              (같은 값을 더 싸게 얻나)
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNS = HERE / "runs"
import os
ARMS = tuple((os.environ.get("ARMS") or "base,dyn").split(","))

_EVID = re.compile(r"(EVT_[A-Za-z0-9_]+|ORG_KR_\d+|[A-Z_]+\.[A-Z_]+\.[A-Z_]+)")


def load(arm: str) -> dict[str, dict]:
    out = {}
    for p in sorted(RUNS.glob(f"{arm}_*.json")):
        out[p.stem[len(arm) + 1:]] = json.loads(p.read_text(encoding="utf-8"))
    return out


def _warrants(d: dict) -> list[dict]:
    ws = []
    for s in d.get("structures") or []:
        for e in s.get("edges") or []:
            ws += e.get("warrants") or []
    return ws


def metrics(d: dict) -> dict:
    """한 실행의 지표. **에러도 0 이 아니라 에러로 센다** - 빈 값과 실패는 다르다."""
    if d.get("error") and not d.get("structures"):
        return {"ok": 0, "error": d["error"][:40], "turns": d.get("turns"),
                "chars": d.get("chars_sent"), "secs": d.get("secs")}
    st = d.get("structures") or []
    edges = [e for s in st for e in (s.get("edges") or [])]
    ws = _warrants(d)
    trace = d.get("trace") or []
    grounds = [t for t in trace if t.get("call", "").startswith("ground(")]
    ok_ground = [t for t in grounds if "접지" in (t.get("out") or "")
                 and "실패" not in (t.get("out") or "")]
    errs = [t for t in trace if (t.get("out") or "").startswith("오류")]
    return {
        "ok": 1,
        "violations": len(d.get("violations") or []),
        "structures": len(st),
        "edges": len(edges),
        "discriminator": 1 if (d.get("discriminator") or "").strip() else 0,
        "shocks": len(d.get("shocks") or []),
        "warrants": len(ws),
        "warrant_kinds": len({w.get("kind") for w in ws if w.get("kind")}),
        "testimonial": sum(1 for w in ws if w.get("kind") == "testimonial"),
        "breaks_if": sum(1 for e in edges if (e.get("breaks_if") or "").strip()),
        "needs": sum(1 for e in edges if (e.get("needs") or "")),
        "ground_calls": len(grounds),
        "ground_ok": len(ok_ground),
        "tool_errors": len(errs),
        "turns": d.get("turns"),
        "chars": d.get("chars_sent"),
        "secs": d.get("secs"),
    }


def _avg(rows: list[dict], k: str) -> float | None:
    v = [r[k] for r in rows if isinstance(r.get(k), (int, float))]
    return round(sum(v) / len(v), 2) if v else None


def table() -> None:
    data = {a: load(a) for a in ARMS}
    keys = sorted(set(data["base"]) & set(data["dyn"]))
    if not keys:
        print("짝지을 셀이 없다 - 두 팔을 같은 셀로 돌려라")
        return
    print(f"짝지은 셀 {len(keys)}개\n")

    per = {a: [metrics(data[a][k]) for k in keys] for a in ARMS}
    fields = ["ok", "violations", "structures", "edges", "discriminator", "shocks",
              "warrants", "warrant_kinds", "testimonial", "breaks_if", "needs",
              "ground_calls", "ground_ok", "tool_errors", "turns", "chars", "secs"]
    print(f"{'지표':<16}{'base':>10}{'dyn':>10}{'차이':>10}")
    print("-" * 46)
    for f in fields:
        b, d = _avg(per["base"], f), _avg(per["dyn"], f)
        if b is None and d is None:
            continue
        diff = "" if (b is None or d is None) else f"{d - b:+.2f}"
        print(f"{f:<16}{str(b):>10}{str(d):>10}{diff:>10}")

    print("\n셀별 계약 위반")
    for k in keys:
        vb = (data["base"][k].get("violations") or [])
        vd = (data["dyn"][k].get("violations") or [])
        print(f"  {k:<24} base {len(vb)}  dyn {len(vd)}")
        for v in vb[:2]:
            print(f"      base: {v[:90]}")
        for v in vd[:2]:
            print(f"      dyn : {v[:90]}")


def qualitative(key: str | None = None) -> None:
    """정성 비교용 발췌. 구조 이름·간선·근거 종류를 나란히 놓는다."""
    data = {a: load(a) for a in ARMS}
    keys = sorted(set(data["base"]) & set(data["dyn"]))
    for k in ([key] if key else keys):
        print(f"\n{'=' * 70}\n셀 {k}")
        for a in ARMS:
            d = data[a].get(k) or {}
            print(f"\n--- {a} " + (f"(에러: {d.get('error')})" if d.get("error") else ""))
            print(f"  충격: {[s.get('say', '')[:50] for s in (d.get('shocks') or [])]}")
            for s in (d.get("structures") or []):
                print(f"  구조 [{s.get('id')}] {str(s.get('say', ''))[:70]}")
                for e in (s.get("edges") or []):
                    kinds = [w.get("kind") for w in (e.get("warrants") or [])]
                    print(f"    {e.get('from')} → {e.get('to')} | 근거 {kinds} | "
                          f"반증 {str(e.get('breaks_if', ''))[:40]}")
            print(f"  판별자: {str(d.get('discriminator', ''))[:100]}")
            if a == "dyn" and d.get("fsm"):
                print(f"  상태기계: {d['fsm']}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) > 1 and sys.argv[1] == "-q":
        qualitative(sys.argv[2] if len(sys.argv) > 2 else None)
    else:
        table()
