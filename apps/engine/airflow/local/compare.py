"""results/raw 스냅샷 → results/summary.md (두 경로와 dev 참조의 대사표)."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

RAW = Path(__file__).resolve().parent / "results" / "raw"


def load(name: str) -> dict:
    return json.loads((RAW / f"{name}.json").read_text())


def canon(d: dict) -> str:
    cells = []
    for td, v in sorted(d["canonical"].items()):
        ref = d["dev_canonical"].get(td, {})
        rel = ("바이트 동일" if v["file_sha"] == ref.get("file_sha")
               else "행 동일" if v["rows_sha"] == ref.get("rows_sha") else "**불일치**")
        cells.append(f"{td[11:]} {v['rows']}행 {rel}")
    return "; ".join(cells)


def ledger_line(run: dict) -> str:
    parts = []
    for task, t in sorted(run["tasks"].items()):
        short = {"INVESTOR_INTRADAY_COLLECTION_KIS": "수집", "NORMALIZE_INVESTOR_INTRADAY": "정제",
                 "LOAD_INVESTOR_INTRADAY": "적재"}[task]
        exits = ",".join(str(a["exit"]) for a in t["attempts"]) or "-"
        parts.append(f"{short} {t['outcome']}[{exits}]")
    return " · ".join(parts)


def section(name: str) -> list[str]:
    d = load(name)
    steps = Counter(i["step"] for i in d["invocations"])
    skipped_guard = sum(1 for t in d["fake_aws"]["tasks"] if t["env"].get("OPS_SKIP_IF_SUCCEEDED"))
    lines = [f"### {name}", "",
             f"- canonical vs dev: {canon(d)}",
             f"- DB 행 {d['db']['rows']} · 값 sha `{d['db']['values_sha'][:12]}` · 행+data_version sha "
             f"`{d['db']['rows_sha'][:12]}`",
             f"- 외부 호출(재생 종목 수): {d['external_calls']['calls']} / 수집 실행 {d['external_calls']['invocations']}",
             f"- ECS 실행(대역): {len(d['fake_aws']['tasks'])} (가드 env 켜짐 {skipped_guard}) · 스텝 진입 {dict(steps)}",
             f"- SNS: {[s['subject'] for s in d['fake_aws']['sns']]}",
             f"- 원장 이슈: {d['issues']}", "", "| run_key | 주체 | launch | 작업 outcome[attempt exit] |",
             "|---|---|---|---|"]
    for key, run in d["ledger"].items():
        lines.append(f"| {key[18:]} | {run['orchestrator']} | {run['launch']} | {ledger_line(run)} |")
    return lines + [""]


def main() -> None:
    names = sorted(p.stem for p in RAW.glob("*.json") if not p.stem.startswith("smoke"))
    out = ["# 장중 수급 레인 로컬 비교 결과(자동 생성: compare.py)", ""]
    pairs = [("legacy-s8", "airflow-s8"), ("legacy-partial", "airflow-partial")]
    out += ["## 경로 간 대사", "", "| 비교 | canonical 행 sha | DB 업무 값(수량) sha | DB +available_at sha | DB +data_version sha |",
            "|---|---|---|---|---|"]
    for a, b in pairs:
        if not ((RAW / f"{a}.json").exists() and (RAW / f"{b}.json").exists()):
            continue
        x, y = load(a), load(b)
        same_c = all(x["canonical"][k]["rows_sha"] == y["canonical"].get(k, {}).get("rows_sha")
                     for k in x["canonical"]) and x["canonical"].keys() == y["canonical"].keys()
        out.append(f"| {a} vs {b} | {'같음' if same_c else '다름'} | "
                   f"{'같음' if x['db']['qty_sha'] == y['db']['qty_sha'] else '다름'} | "
                   f"{'같음' if x['db']['values_sha'] == y['db']['values_sha'] else '다름'} | "
                   f"{'같음' if x['db']['rows_sha'] == y['db']['rows_sha'] else '다름'} |")
    out.append("")
    for name in names:
        out += section(name)
    (RAW.parent / "summary.md").write_text("\n".join(out))
    print("\n".join(out))


if __name__ == "__main__":
    main()
