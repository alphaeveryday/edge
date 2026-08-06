"""튜플 체계의 적층 — 셀마다 기억 0에서 시작하지 않게 한다.

P9 감사(4R)의 병이 교훈이다: 읽기가 배선되지 않은 소환 기록은 track record 가
아니다. 그래서 이 모듈은 **회상을 기록보다 먼저** 설계했고, attribute 가 그
순서로 부른다 - 오늘 결과가 자기 이력에 미리 들어가면 첫 발견이 재발견으로
보인다.

기록 대상은 둘: 격자 스크린 히트(탐색)와 튜플 게이트 판정(확증). 회상은
가설 에이전트의 **어포던스**로 들어간다 - 과거 셀들에서 어떤 (타입×노출)이
강했는지는 PIT 안전한 역사적 사실이고, STORM 교훈(다음 수를 사실로 알려라)
그대로다. append-only jsonl - 덮어쓰면 이력이 아니다.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

_FILE = "tuple_registry.jsonl"
_QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")
_NUM = re.compile(r"\d+(?:\.\d+)?")


def need_key(reason: str) -> str:
    """판정불가 사유 → **수집 단위 키**. 특정 이름·숫자를 지워 같은 결핍이 한 줄로 모인다.

    손으로 유지하는 부재 사전은 낡는다(실측: `duck.py` 주석에 "컨센서스 항목 없다"가
    적혀 있고 도구는 그걸 모른다). 사전을 **측정에서 뽑는다** - 매일 나오는 사유를
    정규화해 세면 "무엇을 채우면 몇 개가 열리나"가 추측 없이 나온다.
    """
    s = _NUM.sub("N", _QUOTED.sub("<이름>", reason))
    return s.split(" - ")[0].split("(")[0].strip()[:80]


def roadmap(root: str | Path, *, top: int = 12) -> list[dict]:
    """막힌 사유별 집계 = 데이터 수집 우선순위. `unlocks` = 그것이 열어줄 가설 수.

    행이 없으면 빈 목록이다 - 없는 로드맵을 만들지 않는다.
    """
    p = Path(root) / _FILE
    if not p.exists():
        return []
    hit: Counter[str] = Counter()
    cells: dict[str, set[str]] = {}
    for ln in p.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(ln)
        except (ValueError, TypeError):
            continue
        if r.get("kind") != "blocked":
            continue
        k = str(r.get("need") or "")
        hit[k] += 1
        cells.setdefault(k, set()).add(f"{r.get('cell')}|{r.get('day')}")
    return [{"need": k, "unlocks": n, "cells": len(cells[k])}
            for k, n in hit.most_common(top)]


def record(root: str | Path, *, day: str, cell: str,
           reports: list[tuple] | None = None,
           screens: list[dict] | None = None) -> int:
    """한 셀의 판정·스크린을 붙인다. 반환 = 기록 행수."""
    p = Path(root) / _FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    for t, r in reports or ():
        rows.append({"kind": "tuple", "day": day, "cell": cell,
                     "type": t.trigger.ident, "trigger": t.trigger.kind,
                     "channel": t.channel, "outcome": t.outcome,
                     "exposure": f"{t.exposure.ident}/{t.exposure.transform}",
                     "verdict": r.verdict, "n": r.n, "p": r.p,
                     "null_kind": getattr(r, "null_kind", ""),
                     "applied": bool(r.applies_today)})
        # **판정불가는 버리지 않고 쌓는다** - 그것이 데이터 수집 우선순위다(21R).
        # 매일 "노출 (x,y)는 아직 못 잰다" 를 뱉고 흘려보내면 로드맵을 추측으로 정한다.
        if r.verdict == "판정불가" and getattr(r, "reason", ""):
            rows.append({"kind": "blocked", "day": day, "cell": cell,
                         "type": t.trigger.ident, "channel": t.channel,
                         "exposure": f"{t.exposure.ident}/{t.exposure.transform}",
                         "need": need_key(r.reason), "reason": r.reason[:200]})
    for s in screens or ():
        if "p2" in s:
            rows.append({"kind": "screen", "day": day, "cell": cell,
                         "type": s["type"], "exposure": s["exposure"],
                         "n": s["n"], "p2": s["p2"], "direction": s["direction"]})
    with p.open("a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
    return len(rows)


def recall(root: str | Path, *, day: str, types: list[str],
           max_lines: int = 5) -> list[str]:
    """오늘 이전 셀들의 이력 → 어포던스 줄. **day 미만만** 본다 (자기 오염 금지).

    첫 소환은 빈 목록이 정직한 답이다 - 이력 날조 금지.
    """
    p = Path(root) / _FILE
    if not p.is_file():
        return []
    best_screen: dict[tuple, dict] = {}
    verdicts: dict[tuple, dict[str, int]] = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r["day"] >= day or r["type"] not in types:
            continue
        key = (r["type"], r["exposure"])
        if r["kind"] == "screen":
            if key not in best_screen or r["p2"] < best_screen[key]["p2"]:
                best_screen[key] = r
        else:
            verdicts.setdefault(key, {})
            verdicts[key][r["verdict"]] = verdicts[key].get(r["verdict"], 0) + 1
    out: list[str] = []
    for key, s in sorted(best_screen.items(), key=lambda kv: kv[1]["p2"])[:max_lines]:
        v = verdicts.get(key, {})
        vs = (" · 게이트 " + " ".join(f"{k}×{n}" for k, n in sorted(v.items()))) if v else ""
        out.append(f"{key[0]} × {key[1]}: 과거 스크린 p₂={s['p2']:.3f} "
                   f"(n={s['n']}, 방향{s['direction']}){vs}")
    return out


__all__ = ["need_key", "recall", "record", "roadmap"]
