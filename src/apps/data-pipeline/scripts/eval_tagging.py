"""태깅 정확도 eval — alphamale 골든 데이터 대비 (ALPHA-138).

실제 LLM 을 호출하므로 단위 테스트가 아니다(돈·네트워크·비결정). 프롬프트나 모델을 바꿨을 때
수동으로 돌려 회귀를 본다. 단위 테스트(tests/test_tagging.py)는 계약 위반 처리를 검증하고,
이 스크립트는 정확도를 잰다 — 둘은 다른 질문에 답한다.

  LLM_API_KEY=... uv run --package data-pipeline \
    python scripts/eval_tagging.py --gold <ko_gold_title.jsonl> --sample 150

env 는 analysis-engine 의 `analyze_daily.py` 가 이미 쓰는 `LLM_API_KEY`/`LLM_BASE_URL`/
`LLM_MODEL` 관례를 따른다(Rule 11) — 수집 설정 네임스페이스(`DATA_PIPELINE_*`)와 섞지 않는다.
LLM 은 수집 소스가 아니라 `load_settings()` 모델에 안 들어간다.

⚠️ **여기서 나오는 정확도는 정답률이 아니라 티처 일치율(teacher-relative fidelity)이다.**
골든 라벨(`source: ko_teacher_v1`)은 사람이 아니라 teacher LLM 이 붙였다 — alphamale 문서도
"teacher label 의 의미론적 정확도까지 증명하지는 않는다"고 명시한다. 90% 가 나와도 '기사를
90% 맞게 이해했다'가 아니라 '티처와 90% 일치한다'는 뜻이다. 티처가 틀린 곳에선 우리가
맞아도 오답으로 세어진다.

골든은 **제목만** 있다(리드 없음). 그래서 이 수치는 제목 단독 성능이고, 실제 파이프라인은
lead_text 를 함께 넣으므로 여기 수치가 하한에 가깝다.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import pathlib
import random
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from data_pipeline.tagging import extract, llm  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gold", required=True, type=pathlib.Path)
    parser.add_argument("--sample", type=int, default=150)
    parser.add_argument("--seed", type=int, default=20260715)
    parser.add_argument("--out", type=pathlib.Path)
    args = parser.parse_args()

    rows = [json.loads(line) for line in args.gold.read_text(encoding="utf-8").splitlines() if line.strip()]
    if args.sample and args.sample < len(rows):
        # 층화 없이 단순 무작위 — 골든의 doc_class 분포를 그대로 반영한 표본이라야 집계가
        # 실제 유입 분포를 닮는다. 시드 고정으로 프롬프트 비교가 같은 표본 위에서 이뤄진다.
        rows = random.Random(args.seed).sample(rows, args.sample)

    api_key = os.environ.get("LLM_API_KEY")
    if not api_key:
        print("LLM_API_KEY 미설정 — eval 은 실호출이 필요하다", file=sys.stderr)
        return 2
    complete_fn = llm.openai_compatible_complete_fn(
        api_key=api_key,
        base_url=os.environ.get("LLM_BASE_URL") or llm.DEFAULT_BASE_URL,
        model=os.environ.get("LLM_MODEL") or llm.DEFAULT_MODEL,
    )
    gate_hit = type_hit = type_total = 0
    statuses: collections.Counter = collections.Counter()
    reasons: collections.Counter = collections.Counter()
    confusion: collections.Counter = collections.Counter()
    records = []

    for i, row in enumerate(rows, 1):
        result = extract.extract_assertions(
            {"article_id": row["gold_id"], "title": row["title"], "lead_text": None,
             "published_at": row.get("published_at")},
            complete_fn=complete_fn,
        )
        statuses[result["status"]] += 1
        reasons.update(result["reasons"])
        got_class, want_class = result["doc_class"], row["doc_class"]
        if got_class == want_class:
            gate_hit += 1
        confusion[(want_class, got_class)] += 1

        # 타입 정확도는 **둘 다 EVENT 인 행에서만** 잰다 — gate 가 틀린 행까지 섞으면 타입
        # 분류 성능이 gate 성능에 오염된다(두 실패를 분리해야 어디를 고칠지 보인다).
        want_types = row.get("event_types") or []
        if want_class == "EVENT" and got_class == "EVENT" and want_types:
            type_total += 1
            if any(a["event_type_code"] == want_types[0] for a in result["assertions"]):
                type_hit += 1
        records.append({"gold_id": row["gold_id"], "title": row["title"],
                        "want_doc_class": want_class, "got_doc_class": got_class,
                        "want_type": want_types[0] if want_types else None,
                        "got_types": [a["event_type_code"] for a in result["assertions"]],
                        "status": result["status"]})
        if i % 25 == 0:
            print(f"  {i}/{len(rows)} …", file=sys.stderr, flush=True)

    n = len(rows)
    print(f"\nn={n}  (gold: {args.gold.name})")
    print(f"doc_class 티처 일치율 : {gate_hit}/{n} = {gate_hit / n:.1%}")
    if type_total:
        print(f"event_type 티처 일치율: {type_hit}/{type_total} = {type_hit / type_total:.1%}"
              f"  (둘 다 EVENT 인 행에서만)")
    else:
        print("event_type 티처 일치율: 표본 없음")
    print(f"status: {dict(statuses)}")
    if reasons:
        print(f"버려진 사유: {dict(reasons)}")
    print("\ndoc_class 혼동(want → got, 상위 8):")
    for (want, got), count in confusion.most_common(8):
        mark = "" if want == got else "  ←"
        print(f"  {want:<28} → {str(got):<28} {count:>4}{mark}")

    if args.out:
        args.out.write_text(json.dumps(records, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n행별 결과: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
