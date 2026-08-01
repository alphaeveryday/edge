"""P9 · 레지스트리 — **하루짜리 그래프는 세계에 대한 진술이 아니다.**

한 셀의 귀속은 표본 1이다. 그래프가 아무리 정합해도 단일 사례에서는 검정력이 나오지
않는다 - 같은 기제를 다른 날 다른 종목에 다시 소환했을 때 같은 부호·같은 크기가 나오는지가
유일한 검정이다. 그래서 여기서 남기는 것은 **결론이 아니라 소환 기록**이다.

세 파일로 나누는 이유는 갱신 주기가 다르기 때문이다.

    mechanism.jsonl      세계에 대한 주장. 어휘가 바뀌면 version 이 오른다
    edge_instance.jsonl  그 주장을 실제로 쓴 한 번. 셀마다 한 행
    amendment.jsonl      골격 밖 주장·미해결. 3회 반복되면 승격 심사 대상이 된다

`amendment` 가 별도 파일인 것이 이 설계의 핵심이다. 실행 못 한 판별자와 미소거 U 는
한 셀에서는 그냥 실패지만, 같은 것이 세 번 반복되면 그건 실패가 아니라 **골격에 없는
구조**를 가리킨다. 침묵으로 사라지면 영원히 안 보인다.

전부 append-only 다. 덮어쓰면 이전 소환이 지워지고, 지워진 소환은 track record 가 아니다.
갱신이 필요한 필드(`n_invocations`·`last_seen`·`seen_in_cells`)는 **최신 행을 읽어 올린 새
행을 붙인다** - `latest()` 가 그 읽기다.

시각은 벽시계가 아니라 `Question.trade_date` 를 쓴다. 같은 셀을 재실행하면 같은 값이
나와야 레지스트리를 재생성해 비교할 수 있다. 벽시계를 박으면 재실행마다 diff 가 난다.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ..config import PipelineError
from ..observability import log
from .contracts import DiscriminationPlan, Findings, Hypothesis, WorldGraph

KINDS = ("mechanism", "edge_instance", "amendment")
# 같은 실체를 가리키는 행을 잇는 키. append-only 라 키가 곧 이력의 축이다.
_KEY = {
    "mechanism": lambda r: str(r["mechanism_id"]),
    "edge_instance": lambda r: f"{r['mechanism_id']}@{r['cell']}",
    "amendment": lambda r: str(r["claim"]),
}
PROMOTE_AT = 3      # 이 횟수부터 골격 승격 심사 대상


def latest(root: Path | str, kind: str) -> dict[str, dict[str, Any]]:
    """레지스트리 최신 상태. **뒤 행이 앞 행을 이긴다.**

    append-only 파일에서 "현재"를 읽는 유일한 방법이다. 파일 전체를 매번 읽는 것은
    한 셀당 수십 행 규모에서 인덱스보다 싸고, 인덱스는 append 원자성을 깬다.

    깨진 줄은 건너뛴다. 동시 실행 중 잘린 행이 레지스트리 전체를 못 읽게 만드는 것이
    더 나쁜 실패 모드다 - 한 행을 잃는 쪽이 낫다.
    """
    if kind not in _KEY:
        raise PipelineError(f"알 수 없는 레지스트리 종류: {kind} (가능: {KINDS})")
    p = Path(root) / f"{kind}.jsonl"
    out: dict[str, dict[str, Any]] = {}
    if not p.exists():
        return out
    key = _KEY[kind]
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
            out[key(row)] = row
        except (json.JSONDecodeError, KeyError, TypeError):
            continue
    return out


def _append(root: Path, kind: str, rows: list[dict[str, Any]]) -> None:
    """한 줄씩 붙인다.

    ponytail: 락 없음. 상한 = 한 행이 파이프 버퍼(4KB)를 넘거나 레지스트리가 네트워크
    파일시스템에 놓이면 동시 append 가 섞일 수 있다. `latest()` 가 깨진 줄을 건너뛰어
    한 행 손실로 막는다. 그 손실이 아프면 업그레이드 경로는 프로세스별 샤드
    (`mechanism.<pid>.jsonl`) + 읽을 때 병합이다 - 파일 락보다 싸고 재실행에 강하다.
    """
    if not rows:
        return
    with (root / f"{kind}.jsonl").open("a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")


def mechanism_id(h: Hypothesis) -> str:
    """기제 식별자. **사건 유형 · 채널/결과 · 배정 기제**만으로 정해진다.

    셀·날짜·종목이 들어가면 매 실행이 새 기제가 되어 track record 가 영원히 n=1 이다.
    반대로 산문(`says`)을 넣으면 같은 주장이 표현만 달라도 갈라진다 - 그래서 해시 입력은
    구조 필드로 한정한다. 배정 기제를 포함하는 것은 같은 사건이라도 `chosen` 이냐
    `mechanical` 이냐에 따라 요구되는 식별 조건이 다르기 때문이다(다른 주장이다).
    """
    et = str((h.nodes.get(h.treatment) or {}).get("event_type") or h.treatment)
    ch = str((h.nodes.get(h.outcome) or {}).get("channel") or h.outcome)
    raw = f"{et}|{ch}|{h.assignment}"
    return "M" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def _touching(h: Hypothesis, latents: list) -> list[str]:
    """이 기제의 처치·결과에 걸린 U 만. 그래프 전체 U 를 다 실으면 셀 비교가 무의미해진다."""
    ends = {h.treatment, h.outcome}
    return [u.uid for u in latents if ends & set(u.between)]


def _disposition(f: Findings, h: Hypothesis):
    """처분과 가설의 결합. P8 이 `candidate` 를 무엇으로 적는지 계약에 없어 셋 다 본다."""
    keys = {s.strip().lower() for s in (h.hid, h.cause_label, h.treatment) if s}
    for d in f.all_dispositions:
        hid = str((d.evidence or {}).get("hid") or "").strip().lower()
        if hid and hid in keys:
            return d
        if d.candidate.strip().lower() in keys:
            return d
    return None


def _num(ev: dict[str, Any], *names: str) -> float | None:
    for n in names:
        v = ev.get(n)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v)
    return None


def _discriminated(plan: DiscriminationPlan, h: Hypothesis, uids: list[str]) -> bool:
    """이 기제를 실제로 가를 관측이 하나라도 실행 가능한가.

    `common_prediction` 은 실행 가능해도 무용이다 - 두 세계가 같은 것을 예측하면 그
    관측은 아무것도 가르지 않는다. 그걸 실행 가능으로 세면 레지스트리가 검정력을 부풀린다.
    """
    for d in plan.discriminators:
        if not d.executable or d.common_prediction:
            continue
        if d.kind == "latent" and d.target in uids:
            return True
        if d.kind == "pair" and h.hid in [s.strip() for s in d.target.split("|")]:
            return True
    return False


def record(findings: Findings, graph: WorldGraph, plan: DiscriminationPlan,
           *, root: Path | str, idents: list | None = None) -> dict[str, Any]:
    """한 셀의 소환을 세 레지스트리에 남긴다.

    기제 행은 **누적 상태를 읽어 올린 새 행**이다(덮어쓰지 않는다). `version` 은 소환
    횟수가 아니라 주장이 바뀔 때만 오른다 - 배정 기제·배제 주장·식별 요구가 그대로면
    같은 주장을 또 쓴 것이고, 그건 `n_invocations` 가 셀 일이다.

    `idents` 는 P4 가 낸 `Identification` 목록이다. 주면 그 `status` 를 그대로 쓴다.
    안 주면 그래프와 소거 대장에서 재구성하는데, 그 재구성은 **뒷문 집합의 존재 여부를
    반영하지 못한다** - 조정으로 막힌 U 와 아무도 막지 않은 U 가 같은 값으로 적힌다.
    배선(`run.explain`)은 항상 넘긴다.
    """
    by_pair = {(i.src, i.dst): i for i in (idents or ())}
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    q = findings.question
    cell = f"{q.etf_instrument_id}/{q.trade_date}"
    seen = q.trade_date.isoformat()

    prev_m = latest(root, "mechanism")
    prev_a = latest(root, "amendment")
    uncleared_all = {u.uid for u in findings.uncleared_latents}

    mech_rows: list[dict[str, Any]] = []
    inst_rows: list[dict[str, Any]] = []
    for h in graph.hypotheses:
        mid = mechanism_id(h)
        uids = _touching(h, graph.latents)
        unc = [u for u in uids if u in uncleared_all]
        requires = [f"U 소거: {u}" for u in uids]
        claim = {
            "assignment": h.assignment,
            # 그래프의 간선 부재 전부가 아니라 **명시적으로 부인한 것**만 배제 주장이다.
            # 안 그린 것과 없다고 한 것을 같이 실으면 P0 이 고친 혼동이 되살아난다.
            "exclusion_claims": list(h.denies),
            "identification_requires": requires,
        }
        old = prev_m.get(mid)
        changed = old is None or any(old.get(k) != v for k, v in claim.items())
        mech_rows.append({
            "mechanism_id": mid,
            "version": (int(old["version"]) + 1 if changed else int(old["version"]))
                       if old else 1,
            **claim,
            "first_seen": old.get("first_seen", seen) if old else seen,
            "last_seen": seen,
            "n_invocations": int(old.get("n_invocations", 0)) + 1 if old else 1,
        })

        d = _disposition(findings, h)
        ev = (d.evidence or {}) if d else {}
        ident = by_pair.get((h.treatment, h.outcome))
        status = ident.status if ident is not None else (
            "not_identified" if unc else ("identified_under" if uids else "identified"))
        inst_rows.append({
            "mechanism_id": mid,
            "cell": cell,
            "verdict": d.verdict if d else "undetermined",
            "ceiling": d.ceiling if d else findings.ceiling,
            "identification_status": status,
            "uncleared_latents": unc,
            "discriminator_executable": _discriminated(plan, h, uids),
            "effect": _num(ev, "effect", "estimate", "coef") if d else None,
            "p": _num(ev, "p", "p_value", "pval") if d else None,
        })

    # ── 골격 밖 주장 ────────────────────────────────────────────────
    # claim 은 구조 식별자만 쓴다. 산문을 키로 잡으면 같은 미해결이 표현 차이로 갈라져
    # 세 번을 못 채우고, 승격 심사가 영원히 안 걸린다. 산문은 `why` 로 간다.
    pend: dict[str, tuple[str, str]] = {}
    for d in plan.discriminators:
        if d.executable and not d.common_prediction:
            continue
        why = d.why_not or ("두 세계가 같은 것을 예측한다" if d.common_prediction else "실행 불가")
        pend[f"discriminator:{d.kind}:{d.target}"] = ("unexecutable_discriminator",
                                                      f"{d.observation} — {why}")
    for u in findings.uncleared_latents:
        pend[f"latent:{u.uid}"] = ("uncleared_latent", u.says)
    for m in q.missing:
        pend[f"missing:{m}"] = ("missing_data", "원장에 없다")

    amend_rows: list[dict[str, Any]] = []
    promote: list[str] = []
    for claim_key, (kind, why) in pend.items():
        old = prev_a.get(claim_key)
        n = int(old.get("seen_in_cells", 0)) + 1 if old else 1
        row = {
            "claim": claim_key,
            "kind": kind,
            "why": why,
            "first_seen": old.get("first_seen", seen) if old else seen,
            "last_seen": seen,
            "seen_in_cells": n,
            "promote_candidate": n >= PROMOTE_AT,
        }
        amend_rows.append(row)
        if row["promote_candidate"]:
            promote.append(claim_key)

    _append(root, "mechanism", mech_rows)
    _append(root, "edge_instance", inst_rows)
    _append(root, "amendment", amend_rows)

    out = {
        "mechanism_ids": [r["mechanism_id"] for r in mech_rows],
        "invocations": len(inst_rows),
        "amendments": len(amend_rows),
        "promote_candidates": promote,
    }
    log("causal.p9.done", cell=cell, mechanisms=len(mech_rows),
        invocations=out["invocations"], amendments=out["amendments"],
        promote=len(promote))
    return out


if __name__ == "__main__":
    import tempfile
    from datetime import date

    from .contracts import Discriminator, Disposition, Latent, Question

    _q = Question(
        etf_instrument_id="091160", etf_name="반도체", trade_date=date(2026, 7, 16),
        as_of="2026-07-16T15:30:00", observed=0.031, residual=0.0421,
        route_code="R1", explanandum="r⊥[091160, 2026-07-16] = +4.21%",
        intervention="공시가 없던 세계", answer_form="구간", missing=["분봉"],
    )
    _h = Hypothesis(hid="H1", says="자사주 매입이 수급을 당겼다", treatment="BUYBACK",
                    outcome="PX", assignment="chosen", nodes={"BUYBACK": {}, "PX": {}},
                    denies=["공시 전 선행 상승"])
    _u = Latent(uid="U1", between=("BUYBACK", "PX"), says="사적 정보", source="compiled")
    _g = WorldGraph(nodes={"BUYBACK": {}, "PX": {}}, latents=[_u], hypotheses=[_h])
    _plan = DiscriminationPlan(discriminators=[
        Discriminator(kind="latent", target="U1", observation="피어 반응",
                      executable=False, why_not="분봉 없음"),
    ])
    _f = Findings(question=_q, uncleared_latents=[_u], ceiling="undetermined",
                  contributing=[Disposition(candidate="H1", verdict="contributing",
                                            why="수급", evidence={"effect": 0.02, "p": 0.03},
                                            ceiling="mechanism_compatible")])

    with tempfile.TemporaryDirectory() as _tmp:
        _root = Path(_tmp) / "없던디렉터리" / "reg"        # 파일도 디렉터리도 없는 상태
        _r1 = record(_f, _g, _plan, root=_root)
        assert _r1["invocations"] == 1
        assert latest(_root, "mechanism")[_r1["mechanism_ids"][0]]["n_invocations"] == 1
        assert not _r1["promote_candidates"]

        _r2 = record(_f, _g, _plan, root=_root)
        _m = latest(_root, "mechanism")[_r2["mechanism_ids"][0]]
        assert _m["n_invocations"] == 2, _m          # 같은 셀 두 번 -> 2
        assert _m["version"] == 1, _m                # 주장이 안 바뀌면 version 은 그대로
        assert not _r2["promote_candidates"]

        _r3 = record(_f, _g, _plan, root=_root)
        assert latest(_root, "mechanism")[_r3["mechanism_ids"][0]]["n_invocations"] == 3
        assert _r3["promote_candidates"], _r3        # 3회 -> 승격 심사 대상
        _a = latest(_root, "amendment")["latent:U1"]
        assert _a["seen_in_cells"] == 3 and _a["promote_candidate"] is True, _a
        _i = latest(_root, "edge_instance")[f"{_r3['mechanism_ids'][0]}@091160/2026-07-16"]
        assert _i["identification_status"] == "not_identified", _i
        assert _i["uncleared_latents"] == ["U1"] and _i["effect"] == 0.02, _i
        assert _i["discriminator_executable"] is False, _i

    print("p9_registry self-check ok")
