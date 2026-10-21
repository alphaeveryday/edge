"""경쟁가설 분석(ACH) 채점과 태그 결정 — 모델은 가설·증거·일치 표시를 채우고, 점수·승자·태그는 코드가 정한다.

- 점수 = 그 가설과 어긋나는(I) 증거의 무게 합. 가장 덜 어긋나는 가설이 이긴다(지지가 많은 가설이 아니다).
- 무게: high 3(공시·공식 원문) · mid 2(독립 보도 여러 건, 구체적 보도) · low 1(단일·불분명 보도, 정황).
- 태그: 이긴 가설의 종류와 차순위와의 차이로 정한다.
  event(실제 회사 사건)   → 유망주. 단 공시·공식 원문, 또는 서로 다른 매체 2곳 이상이 그 가설과 일치해야 한다
  overstated(과장·왜곡)  → 허위매물
  theme(회사 근거 없는 테마·풍문) → 허위매물. 단 차순위와 뚜렷이 갈릴 때만(차이 2 이상)
  sector·supply·other   → 판단 보류(업종 동반·수급은 주장의 참·거짓이 아니다)
  승자와 차순위의 차이가 1 미만, 또는 승자를 직접 뒷받침(C)하는 mid 이상 증거가 없음 → 판단 보류
"""
from __future__ import annotations

KINDS = {"event": "실제 회사 사건", "overstated": "사건은 있으나 과장·왜곡", "theme": "회사 근거 없는 테마·풍문",
         "sector": "업종·시장 동반 상승", "supply": "수급·신규상장·과열 매매", "other": "그 밖"}
WEIGHTS = {"high": 3, "mid": 2, "low": 1}
MARKS = ("C", "I", "N")


class AchError(ValueError):
    """채울 것이 모자라거나 형식이 틀렸다(모델에게 그대로 보여준다)."""


def score(hypotheses: list[dict], evidence: list[dict]) -> dict[str, int]:
    """가설 id → 어긋나는 증거 무게 합."""
    return {h["id"]: sum(WEIGHTS[e["weight"]] for e in evidence if e["marks"].get(h["id"]) == "I") for h in hypotheses}


def decide(hypotheses: list[dict], evidence: list[dict], *, primary: set[str], publishers: dict[str, str]) -> dict:
    """승자·차순위·태그·확신도. primary 는 사건을 확인해 주는 원문 출처 id, publishers 는 보도 출처 id → 매체(독립성 판단).
    publishers 에 없는 출처(공시 목록 등)는 매체 수에 세지 않는다."""
    if len(hypotheses) < 3:
        raise AchError("가설을 3개 이상 세우세요(실제 사건·과장·테마·업종·수급 중 이 종목에 맞는 것).")
    if not any(h["kind"] == "event" for h in hypotheses) or all(h["kind"] == "event" for h in hypotheses):
        raise AchError("실제 회사 사건 가설(event)과 그 밖의 가설을 함께 세우세요.")
    if len(evidence) < 3:
        raise AchError("증거를 3건 이상 넣으세요(공시 목록에 없다는 사실도 증거다).")
    scores = score(hypotheses, evidence)
    ranked = sorted(hypotheses, key=lambda h: (scores[h["id"]], h["id"]))
    win, runner = ranked[0], ranked[1]
    margin = scores[runner["id"]] - scores[win["id"]]
    support = [e for e in evidence if e["marks"].get(win["id"]) == "C"]
    corroborated = any(e["source"] in primary for e in support) or \
        len({publishers[e["source"]] for e in support if e["source"] in publishers}) >= 2
    backed = any(WEIGHTS[e["weight"]] >= 2 for e in support)  # 어긋남이 없다는 것만으로 이기지 않게
    if margin < 1:
        tag, why = "pending", "가장 덜 어긋나는 가설이 둘 이상이라 가르지 못했다"
    elif not backed:
        tag, why = "pending", "남은 설명을 직접 뒷받침하는 증거가 없다"
    elif win["kind"] == "event":
        tag, why = ("promising", "실제 회사 사건이 남았고 원문·복수 매체로 확인된다") if corroborated else \
                   ("pending", "실제 사건 가설이 남았으나 원문이나 서로 다른 매체 두 곳으로 확인되지 않았다")
    elif win["kind"] == "overstated":
        tag, why = "false", "사건은 있으나 내세운 주장이 원문보다 부풀려졌다"
    elif win["kind"] == "theme" and margin >= 2:
        tag, why = "false", "회사 근거 없는 테마·풍문 상승이 다른 설명보다 뚜렷이 덜 어긋난다"
    else:
        tag, why = "pending", f"남은 설명({KINDS[win['kind']]})은 주장의 참·거짓을 가르지 않는다"
    confidence = round(min(0.9, 0.5 + 0.1 * margin), 2) if tag != "pending" else round(min(0.4, 0.2 + 0.05 * margin), 2)
    return {"tag": tag, "confidence": confidence, "winner": win["id"], "runnerUp": runner["id"], "margin": margin,
            "scores": scores, "why": why}
