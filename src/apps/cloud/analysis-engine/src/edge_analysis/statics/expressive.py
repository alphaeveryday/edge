"""표현력 측정 — **우리 어휘가 담을 수 있는 인과 가설의 비율**.

닫힌 어휘(채널 8 · 사건타입 53 · 계열족 9 · 변환 5 · 결과 2)는 *"금융 인과를
이만큼이면 담을 수 있다"* 는 **주장**이다. 20R 까지 그 주장을 한 번도 검정하지
않았다. 검정이 불가능한 구조였기 때문이다:

    hypothesize._SYSTEM:29  "아래 **닫힌 어휘**의 값만 쓸 수 있다"
    hypothesize.propose:152 _kill("접지 밖 사건타입 날조: …")

어휘를 프롬프트 첫 줄에 박아 넣으니 **어휘가 못 담는 가설은 발화조차 되지 않는다.**
게다가 어휘 밖으로 나가면 "날조"라 부른다 - 그 한 단어가 전혀 다른 둘을 합친다:
모델이 없는 사건을 지어낸 것(환각)과, 우리 어휘가 그 현상을 못 담은 것(표현력 부족).
미도달/부재를 갈랐던 것과 같은 병이 어휘 축에서 반복된 것이다.

## 측정 설계

**분모** = "우리 시스템이 실제로 보여주는 증거 위에서 나온 가설". LLM 이 아무 데서나
뽑은 상투구도, 기사 본문도 아니다 - 생성자에게 **검정 에이전트와 똑같은 도구·환경**
(FSM · Catalog · 같은 셀)을 주고 **어휘만 뺀다**. 그래야 재는 값이 "우리가 답해야 할
모집단에 대한 표현력"이 된다.

**생성자**는 어휘를 못 본다. 자유 산문으로 인과 가설을 낸다.
**채점자**는 어휘 전량을 보고 산문 하나를 튜플로 사상한다. **'사상 불가'가 1급 답이다** -
못 담는 것을 담았다고 하면 측정이 죽는다.

슬롯별 3값이고, 총점이 아니라 **어느 슬롯이 얼마나 자주 막히나**가 산출물이다:

    사상   그대로 들어간다
    대리   들어가긴 하는데 **뜻이 바뀐다** ← 가장 위험. 무엇이 바뀌었는지 필수
    불가   어떤 값으로도 못 넣는다

막힌 슬롯은 **층으로 태깅**한다. 소유자가 다르기 때문이다:
    방아쇠(사건타입 53)  → 상류 뉴스 온톨로지 - statics 는 못 고친다
    나머지               → 우리 어휘 - 고칠 수 있다
층을 안 나누면 "표현력 62%" 가 누구 일감인지 안 나온다.

**표현력과 검정가능성은 다른 축이다**(④). 어휘로 담겼는데 표본이 없어 판정불가인 것은
데이터 일감이고, 담기지 않은 것만 어휘 일감이다.

사용:  python -m edge_analysis.statics.expressive <ticker> <instrument_id> <YYYY-MM-DD> [n]
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache
from datetime import datetime, timezone
from pathlib import Path

from ..observability import record as trace
from .paneltest import FEATURES
from .vocab import CHANNELS, OUTCOME_KINDS, RELATIONS, SERIES_FAMILIES, TRANSFORMS

SLOTS = ("방아쇠", "채널", "노출", "조건", "결과", "부호")
GRADES = ("사상", "대리", "미명시", "불가")
# 미명시 ≠ 불가 (20R 첫 실측이 강요한 구분). 자유 산문이 방향을 안 밝히면 부호 슬롯이
# 빈다 - 그건 **산문이 덜 구체적**인 것이지 어휘가 못 담은 게 아니다. 어휘 탓으로 세면
# 표현력이 부당하게 낮아진다. 표현력은 어휘의 속성이지 산문의 속성이 아니다.
# 슬롯 → 그 슬롯의 어휘를 누가 소유하나. 막힘을 일감으로 바꾸는 데 이 구분이 필요하다.
OWNER = {"방아쇠": "상류(사건타입 53 - 뉴스 온톨로지)", "채널": "우리(채널 8)",
         "노출": "우리(계열족 9 × 변환 5)", "조건": "우리(계열족 9 × 변환 5)",
         "결과": "우리(결과 2)", "부호": "우리(±1)"}

_GEN = """너는 주식 인과 분석가다. 아래 셀에서 **무엇이 왜 그렇게 움직였는지** 가설을 낸다.

형식 제약은 없다. 정해진 어휘도 없다. **네가 자연스럽다고 생각하는 말로** 인과를 써라 -
무엇이 방아쇠였고, 어떤 경로로, 어떤 종목이 더 크게 반응했을지, 방향은 어느 쪽인지.
도구로 본 것에 근거해라. 도구가 안 보여준 사건을 지어내지 마라.

가설 {n}개. JSON 하나만: {{"hypotheses": ["한 문단짜리 인과 가설", ...]}}

관측 기록:
{seen}"""

_SCORE = """너는 **환원 채점자**다. 자유 산문 가설 하나를 아래 닫힌 어휘의 튜플로 사상한다.

**값은 아래 목록에서 그대로 복사한다. 새로 쓰면 그 슬롯은 자동으로 불가 처리된다.**
(코드가 목록 대조로 검산한다 - 그럴듯한 문구를 지어내도 통과 못 한다)

슬롯별로 넣을 수 있는 값:
  방아쇠 = 아래 사건타입 코드 하나  또는  계열족 하나
     **사건타입 어휘 전량** (이 셀에 그 사건이 있었는지는 별개 - 어휘에 있으면 "사상"):
     {event_types}
     계열족: {families}
  채널   = {channels} 중 하나
  노출   = "계열족/변환" 형식 하나 (예: "가격잔차/누적")  또는  관계 {relations} 중 하나
  조건 = "계열족/변환" 형식 하나 (예: "수급/누적")
     계열족 {families}
     변환   {transforms}
     ※ 계열족만 쓰면 안 된다. "수급" (X) → "수급/누적" (O)
  결과   = {outcomes} 중 하나
  부호   = "+1" 또는 "-1"

슬롯마다 넷 중 하나:
  "사상"    위 목록의 값으로 그 뜻이 그대로 들어간다
  "대리"    목록의 값에 넣긴 하는데 **뜻이 바뀐다**. changed 에 무엇이 바뀌었는지 필수
            (예: 주어가 경쟁사→자사 / 원인과 결과가 뒤집힘 / 범위가 넓어짐)
            **서로 다른 메커니즘을 한 값에 몰아넣는 것도 대리다.** '수급 쏠림'과
            '경쟁사 신제품'을 둘 다 S주식수 로 찍었다면 최소한 하나는 대리다
  "미명시"  **원문이 그 슬롯을 안 말했다.** 어휘 탓이 아니다 - 이걸 '불가'로 쓰면
            어휘를 부당하게 깎는다
  "불가"    위 목록의 **어떤 값으로도** 못 넣는다. want 에 필요했던 개념을 한 줄로

**두 가지를 섞지 마라 (이걸 틀리면 측정이 죽는다):**
  · 어휘에 값이 **있는데** 지금 패널이 그 축을 못 재는 것 → **"사상"** 이다.
    검정가능성은 표현력과 다른 축이고 따로 잰다. 예: 계열족 '수급' 은 어휘에 있다.
    참고로 패널이 실제 재는 것은 {measurable} 뿐이지만 **이 목록은 어휘가 아니다.**
  · 위 목록에 값이 **없는** 것 → 그때만 "불가".

**방아쇠를 계열족으로 도피하지 마라.** 계열족 방아쇠는 원문이 *계열 충격*(거래량이
튀었다·가격잔차가 이상하다)을 말했을 때만 쓴다. 원문이 **특정 사건**(경쟁사 신제품
발표, 옵션 만기)을 말했는데 그게 사건타입 목록에 없으면 그건 **"불가"** 다 -
계열족으로 바꿔 넣으면 다른 가설이 된다.

원문이 그 슬롯을 안 말했으면 채워 넣지 마라. **"미명시"** 다. 슬롯을 다 채워
완성처럼 보이게 만드는 것이 이 측정에서 가장 나쁜 행동이다.

JSON 하나만:
{{"slots": {{"방아쇠": {{"grade": "사상|대리|미명시|불가", "value": "목록에서 복사한 값",
              "changed": "", "want": ""}},
            "채널": {{...}}, "노출": {{...}}, "조건": {{...}}, "결과": {{...}}, "부호": {{...}}}},
  "lost": "이 사상에서 원문의 무엇이 사라졌나 (없으면 빈 문자열)"}}

가설:
{prose}"""


@lru_cache(maxsize=2)
def all_event_types(lake) -> tuple[str, ...]:
    """**어휘 전체** 사건타입 53종. 셀 접지 목록과 다르다 (20R 실측이 강요한 구분).

    채점자에게 셀 접지 목록만 주면 "이 셀에 없음"을 "어휘에 없음"으로 보고한다 -
    실측: 표현력 측정이 '증권사 목표주가 하향 (사건타입 목록에 없음)' 이라 찍었는데
    `MARKET_INFO.ANALYST.TARGET_PRICE_CHANGE` 는 어휘에 31건 있다. 그 셀에 없었을 뿐이다.
    미도달을 부재로 보고하는 병이 어휘 축에서 재발한 것이고, 어휘 구멍을 부풀린다.
    """
    try:
        return tuple(r[0] for r in lake.sql(
            "SELECT * FROM postgres_query('rdb', 'SELECT DISTINCT event_type_code "
            "FROM source_event WHERE event_status = ''ACTIVE'' ORDER BY 1')"))
    except Exception:                       # noqa: BLE001 - 못 읽으면 셀 목록으로 폴백
        return ()


def in_vocab(slot: str, value: str, event_types) -> bool:
    """사상됐다는 값이 **정말 어휘 안에 있나**. 어휘가 닫혀 있으니 코드가 검사한다.

    20R 실측이 강요한 검산: 채점자가 "사상"이라 주장하면서 방아쇠 슬롯에 채널을,
    노출 슬롯에 티커를, 조건에 계열족만(변환 없이) 넣었다. 그대로 믿으니 환원율이
    100% 로 부풀었다 - 자기 신고를 무검증으로 세는 병, 이 프로젝트가 계속 데인 그것.
    사람 기준 표본 없이도 **이 실패 유형만은** 코드가 잡는다.
    """
    v = (value or "").strip()
    if not v:
        return False
    if slot == "방아쇠":
        return v in set(event_types) or v in SERIES_FAMILIES
    if slot == "채널":
        return v in CHANNELS
    if slot in ("노출", "조건"):
        if v in RELATIONS:
            return slot == "노출"        # 관계는 노출 슬롯에서만 의미가 있다
        parts = [x.strip() for x in v.replace("×", "/").replace(",", "/").split("/")]
        return (len(parts) == 2 and parts[0] in SERIES_FAMILIES
                and parts[1] in TRANSFORMS)
    if slot == "결과":
        return v in OUTCOME_KINDS
    if slot == "부호":
        return v.replace("+", "").strip() in ("1", "-1")
    return False


@dataclass(frozen=True, slots=True)
class Reduction:
    """가설 하나의 환원 결과. 등급은 슬롯 판정에서 **유도**된다 - 별도 주장이 아니다."""

    prose: str
    slots: dict[str, dict]
    lost: str = ""

    @property
    def grade(self) -> str:
        """완전 → 대리 → 부분 → 불가. 방아쇠가 안 잡히면 뼈대가 없는 것이다."""
        g = {s: self.slots.get(s, {}).get("grade", "불가") for s in SLOTS}
        if g["방아쇠"] == "불가":
            return "불가"
        # 미명시는 중립 - 원문이 안 말한 것을 어휘 탓으로 세지 않는다.
        said = [v for v in g.values() if v != "미명시"]
        if all(v == "사상" for v in said):
            return "완전"
        if any(v == "불가" for v in said):
            return "부분"
        return "대리"

    @property
    def blocked(self) -> list[str]:
        return [s for s in SLOTS if self.slots.get(s, {}).get("grade") == "불가"]

    @property
    def ungrounded(self) -> bool:
        """방아쇠가 어휘엔 있으나 이 셀엔 없다. **어휘 구멍이 아니다** - 셀 축이다."""
        return bool(self.slots.get("방아쇠", {}).get("ungrounded"))

    @property
    def bogus(self) -> list[str]:
        """채점자가 어휘 밖 값을 사상이라 우긴 슬롯. **측정기 건강 지표**다."""
        return [s for s in SLOTS if self.slots.get(s, {}).get("bogus")]

    @property
    def unsaid(self) -> list[str]:
        """원문이 안 말한 슬롯. **어휘 일감이 아니다** - 산문의 구체성 문제다."""
        return [s for s in SLOTS if self.slots.get(s, {}).get("grade") == "미명시"]

    @property
    def proxied(self) -> list[str]:
        return [s for s in SLOTS if self.slots.get(s, {}).get("grade") == "대리"]

    def measurable(self) -> bool | None:
        """사상된 노출이 **실제로 패널에서 재지나**. 표현력과 다른 축(④).

        None = 노출이 사상 안 됐다 (검정가능성을 물을 수 없다).
        """
        v = self.slots.get("노출", {})
        if v.get("grade") in ("불가", "미명시"):
            return None
        parts = [p.strip() for p in str(v.get("value", "")).replace("×", "/").split("/")]
        return len(parts) == 2 and (parts[0], parts[1]) in FEATURES


@dataclass
class Survey:
    """한 셀의 표현력 조사. 총점이 아니라 **막힘 분포**가 산출물이다."""

    cell: str
    items: list[Reduction] = field(default_factory=list)

    def report(self) -> str:
        if not self.items:
            return f"[{self.cell}] 가설 0개 - 측정 불가"
        n = len(self.items)
        gr = Counter(r.grade for r in self.items)
        blocked = Counter(s for r in self.items for s in r.blocked)
        proxied = Counter(s for r in self.items for s in r.proxied)
        able = Counter(r.measurable() for r in self.items)
        out = [f"[{self.cell}] 자유 가설 {n}개의 환원",
               "  등급: " + " · ".join(f"{k} {gr.get(k, 0)}"
                                     for k in ("완전", "대리", "부분", "불가")),
               f"  환원율(완전+대리) {100 * (gr['완전'] + gr['대리']) / n:.0f}%"
               f" · 뼈대라도 서는 비율 {100 * (n - gr['불가']) / n:.0f}%"]
        if blocked:
            out.append("  막힌 슬롯 (어휘 일감):")
            out += [f"    {s} ×{c}  → {OWNER[s]}" for s, c in blocked.most_common()]
        ung = sum(1 for r in self.items if r.ungrounded)
        if ung:
            out.append(f"  방아쇠가 어휘엔 있으나 이 셀 미접지 {ung}건 — **어휘 구멍이 아니다**"
                       " (셀 선택·데이터 축)")
        unsaid = Counter(s for r in self.items for s in r.unsaid)
        if unsaid:
            out.append("  원문 미명시 (어휘 일감 아님 - 산문이 덜 구체적):")
            out += [f"    {s} ×{c}" for s, c in unsaid.most_common()]
        if proxied:
            # 대리는 조용한 실패다 - 사상됐다고 세면 표현력이 부풀려진다.
            out.append("  뜻이 바뀐 슬롯 (대리 - 조용한 실패):")
            out += [f"    {s} ×{c}" for s, c in proxied.most_common()]
        # 의미 붕괴 탐지 - 코드가 볼 수 있는 마지막 신호. 서로 다른 메커니즘 n개가
        # 한 값으로 수렴하면 사상이 아니라 뭉갬이다(20R 실측: 채널 S주식수 5/5).
        if n >= 3:
            for slot in SLOTS:
                vals = [r.slots.get(slot, {}).get("value", "") for r in self.items
                        if r.slots.get(slot, {}).get("grade") in ("사상", "대리")]
                if len(vals) >= 3:
                    top, c = Counter(vals).most_common(1)[0]
                    if c / len(vals) > 0.8:
                        out.append(f"  ⚠ 의미 붕괴 의심: {slot} 이 {c}/{len(vals)} 로 "
                                   f"{top!r} 에 몰렸다 — 서로 다른 메커니즘을 한 값에 "
                                   "뭉갠 것일 수 있다 (사상이 아니라 대리)")
        bogus = Counter(s for r in self.items for s in r.bogus)
        if bogus:
            out.append(f"  ⚠ 채점자 허위사상 {sum(bogus.values())}건 (어휘 밖 값을 사상이라 주장) "
                       "— 측정기 건강 지표, 이 값들은 불가로 강등했다:")
            out += [f"    {s} ×{c}" for s, c in bogus.most_common()]
        out.append(f"  검정가능(사상된 노출이 패널 피처): {able.get(True, 0)}"
                   f" · 어휘엔 있으나 못 잼: {able.get(False, 0)}"
                   f" · 노출 미사상: {able.get(None, 0)}"
                   "   ← 표현력과 다른 축, 앞은 데이터 일감")
        want = [(s, v.get("want", "")) for r in self.items for s, v in r.slots.items()
                if v.get("grade") == "불가" and v.get("want")]
        if want:
            out.append("  필요했던 개념 (어휘 확장 후보 - 사람의 스키마 변경 대상):")
            out += [f"    [{s}] {w[:88]}" for s, w in want[:8]]
        lost = [r.lost for r in self.items if r.lost]
        if lost:
            out.append("  사상에서 사라진 것:")
            out += [f"    {x[:96]}" for x in lost[:5]]
        return "\n".join(out)


def generate(ask, machine, *, facts: str, n: int = 4) -> list[str]:
    """자유 산문 가설. **어휘를 안 준다** - 주면 자기검열이 일어나 분모가 오염된다."""
    from .hypothesize import explore
    seen = explore(ask, machine, facts=facts)
    try:
        out = ask("너는 분석가다. JSON 으로만 답한다.", _GEN.format(n=n, seen=seen))
    except Exception as e:                          # noqa: BLE001 - 실패도 관측
        trace("expressive.generate_failed", why=f"{type(e).__name__}: {e}")
        return []
    hyps = [str(h).strip() for h in (out.get("hypotheses") or []) if str(h).strip()]
    for h in hyps:
        trace("expressive.free_hypothesis", prose=h[:600])
    return hyps


def score(ask, prose: str, *, event_types: list[str],
          vocab_types: tuple[str, ...] = ()) -> Reduction | None:
    """산문 → 튜플 사상. 생성자와 **다른 호출**이다 (자기 채점은 낙관 편향).

    `event_types` = 이 셀에 접지된 타입 · `vocab_types` = 어휘 전체 53종.
    **둘은 다르다**: 어휘엔 있는데 이 셀에 없는 타입은 '어휘 구멍'이 아니라
    '이 셀 미접지'다 - 전자는 스키마 일감이고 후자는 데이터/셀 선택 문제다.
    """
    known = tuple(vocab_types) or tuple(event_types)
    sys_p = _SCORE.format(channels=sorted(CHANNELS), event_types=known,
                          families=sorted(SERIES_FAMILIES), transforms=sorted(TRANSFORMS),
                          relations=sorted(RELATIONS), outcomes=sorted(OUTCOME_KINDS),
                          measurable=sorted(FEATURES), prose=prose)
    try:
        out = ask("너는 환원 채점자다. JSON 으로만 답한다. '불가'를 두려워하지 마라.", sys_p)
    except Exception as e:                          # noqa: BLE001
        trace("expressive.score_failed", why=f"{type(e).__name__}: {e}")
        return None
    slots = out.get("slots") or {}
    clean = {}
    for s in SLOTS:
        v = slots.get(s) or {}
        g = str(v.get("grade", "")).strip()
        # 등급이 어휘 밖이면 **불가로 떨어뜨린다** - 모르는 판정을 성공으로 세지 않는다.
        clean[s] = {"grade": g if g in GRADES else "불가",
                    "value": str(v.get("value", ""))[:80],
                    "changed": str(v.get("changed", ""))[:160],
                    "want": str(v.get("want", ""))[:160]}
        # 대리인데 무엇이 바뀌었는지 안 쓰면 대리 자격이 없다 - 조용한 성공 금지.
        if clean[s]["grade"] == "대리" and not clean[s]["changed"]:
            clean[s]["grade"] = "불가"
            clean[s]["want"] = clean[s]["want"] or "대리 사유 미기재 - 뜻 보존 확인 불가"
        # 사상 주장은 **어휘 대조로 검산**한다. 통과 못 하면 사상이 아니다.
        if clean[s]["grade"] in ("사상", "대리") and not in_vocab(s, clean[s]["value"], known):
            clean[s] = {"grade": "불가", "value": clean[s]["value"], "changed": "",
                        "bogus": True,
                        "want": f"채점자 허위사상 - 어휘 밖 값: {clean[s]['value'][:56]!r}"}
    # 어휘엔 있으나 이 셀에 접지 안 된 방아쇠 - 표현력 구멍이 아니다(다른 축).
    tv = clean["방아쇠"]
    if tv["grade"] in ("사상", "대리") and tv["value"] not in set(event_types):
        tv["ungrounded"] = True
    r = Reduction(prose=prose, slots=clean, lost=str(out.get("lost", ""))[:240])
    trace("expressive.reduction", grade=r.grade, blocked=r.blocked, proxied=r.proxied,
          measurable=r.measurable(), lost=r.lost)
    return r


def survey_cell(lake, ask, ticker: str, instrument_id: str, day: str,
                n: int = 4) -> Survey:
    """한 셀에서 자유 가설을 받아 전부 채점한다. 검정 에이전트와 **같은 도구·환경**."""
    from .attribute import load_cell
    from .fsm import Machine
    from .tools import Catalog

    # 검정 에이전트와 **같은 경로**로 셀을 연다 - 분모가 다르면 측정이 다른 것을 잰다.
    shares, labels, _after = load_cell(lake, ticker, instrument_id, day)
    types = sorted({labels[e] for s in shares for e in s.window.event_ids if e in labels})
    total = sum(s.log_ret for s in shares)
    facts = (f"셀 {ticker} {day} · 하루 {total * 100:+.2f}%p\n"
             + "\n".join(f"  {s.window.name} {s.window.start:%H:%M}-{s.window.end:%H:%M} "
                         f"{s.log_ret * 100:+.2f}%p" for s in shares))
    cat = Catalog(lake=lake, ticker=ticker, instrument_id=instrument_id, day=day,
                  types=tuple(types))
    sv = Survey(cell=f"{ticker}/{day}")
    for prose in generate(ask, Machine(cat), facts=facts, n=n):
        r = score(ask, prose, event_types=types, vocab_types=all_event_types(lake))
        if r is not None:
            sv.items.append(r)
    return sv


def append_ledger(root: str | Path, sv: Survey) -> int:
    """셀 배치 집계용 원장. 한 줄 = 가설 하나 - 셀을 넘어 합칠 수 있어야 한다."""
    p = Path(root) / "expressive.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        for r in sv.items:
            f.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(),
                                "cell": sv.cell, "grade": r.grade,
                                "blocked": r.blocked, "proxied": r.proxied,
                                "measurable": r.measurable(), "lost": r.lost,
                                "slots": r.slots, "prose": r.prose},
                               ensure_ascii=False) + "\n")
    return len(sv.items)


if __name__ == "__main__":       # pragma: no cover
    import os

    from ..adapters.llm import DeepSeekClient
    from .duck import CausalLake

    if len(sys.argv) < 4:
        sys.exit(__doc__)
    client = DeepSeekClient(os.environ["DEEPSEEK_API_KEY"],
                            model=os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-pro"))
    survey = survey_cell(CausalLake(), client.complete_json, *sys.argv[1:4],
                         n=int(sys.argv[4]) if len(sys.argv) > 4 else 4)
    print(survey.report())
    append_ledger(os.environ.get("CAUSAL_BACKFILL_DIR", ".tmp/causal-backfill"), survey)
