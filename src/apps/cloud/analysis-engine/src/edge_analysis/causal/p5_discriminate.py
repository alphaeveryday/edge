"""P5 판별 — **교란 정의를 닫는 자리. 기준은 선언이 아니라 실행 가능성이다.**

P0 의 개입 정의는 "무엇이 통제되어야 하는가"를 요구하지만, 그 목록이 충분한지는 선언을
세어서는 알 수 없다. P3 가 U 를 다섯 개 적든 하나도 안 적든 그래프는 똑같이 그럴듯하고,
`identify()` 는 적힌 것만 보고 뒷문을 센다 - 실측: 그래프에 `MOM@t-1` 한 줄을 더 그리면
`adjust=[]` 가 `adjust=['MOM@t-1']` 로 바뀐다. 선언을 세는 검사는 세계가 아니라 제안자의
지식 상태를 재고 있었다.

그래서 여기서 기준을 옮긴다: **그 U 를 가를 관측을 실제로 적을 수 있는가.** 적었으면
코드가 `sql.query()` 로 돌린다. 행이 나오면 소거 후보고, 안 나오면 그 U 는 통제되지 않은
것이며 P8 이 미소거로 확정한다. 모델의 자기 신고("이건 검정 가능하다")는 읽지 않는다 -
`executable` 은 질의가 실제로 무엇을 냈는지에서만 온다. 선언과 실행이 갈리는 자리를
`verify.gate` 가 G7b 로 잡아냈듯, 여기서도 갈린다.

실행 불가는 실패가 아니라 산출이다. "분봉을 보면 갈린다"는 답은 분봉 원장이 없으므로
`executable=False, why_not=...` 로 떨어지고, 그 문장이 다음 수집 의제가 된다. 가짜 소거
보다 정직한 미소거가 낫다 - 전자는 `confirmed` 를 열어 주고 후자는 닫는다.

무용 판정이 두 번째 장치다. 두 세계가 **같은 것**을 예측하는 관측(거래량이 는다 따위)은
아무것도 가르지 못하는데 질의는 잘 돌아가므로, `executable` 만 보면 소거로 통과한다.
`common_prediction` 이 그걸 잡고 `DiscriminationPlan.uncleared()` 가 미소거로 되돌린다.
"""
from __future__ import annotations

import re
from typing import Any

from ..config import PipelineError
from ..observability import log
from .contracts import (
    DiscriminationPlan,
    Discriminator,
    Fingerprint,
    Hypothesis,
    Identification,
    Question,
    WorldGraph,
)

MAX_TURNS = 12
# 쌍 판별을 요구하는 상위 가설 수. 3가설 -> 3쌍. 그 아래 순위까지 전수로 걸면 턴이 쌍에
# 다 먹히고 U 소거가 밀린다 - 이 모듈의 본체는 latent 쪽이다.
TOP_H = 3
NO_SQL = "SQL 표면 미주입"

SYSTEM = """너는 두 세계를 **가르는 관측**을 설계한다. 추정하지 않는다 - 무엇을 보면
갈리는지만 적는다.

다섯 종류다:
  pair        두 가설이 다르게 예측하는 관측 (Platt strong inference)
  latent      원인 가설과 **교란 U** 가 다르게 예측하는 관측
  structural  제도·규칙이 그 가설을 **불가능하게** 만든다. 질의가 필요 없다
  capacity    그 메커니즘의 물리적 수용력이 요구 규모에 **못 미친다**
  dose        **그 가설 자신의** 처치 강도가 결과와 단조인가

★ latent 가 이 단계의 본체다. 아래 U 목록에서 **하나도 빠뜨리지 마라.** 각 U 마다
소거 검정을 내거나, 못 내겠으면 `cannot` 으로 무엇이 없어서 못 가르는지 적어라.
침묵은 거부된다 - 남은 목록을 매 턴 다시 들려준다.

★ 뒤의 셋은 통계가 못 하는 기각을 위해 있다.
  `structural`  "이 크기의 움직임은 상하한가 ±30% 안에서 불가능하다" · "그 종목은 그
                기간 공매도 금지였으므로 공매도 압력 가설은 성립할 수 없다" 처럼
                **제도가 세계를 좁힌다.** 자료로 반박할 대상이 아니므로 질의를 요구하지
                않는다. `why` 대신 `observation` 에 어느 규칙인지 정확히 적어라.
  `capacity`    "이 유형 자금의 일간 순유출 상한이 X 인데 설명하려는 몫은 3X 다" -
                크기 논증이지 빈도 논증이 아니다. `sql` 로 그 상한을 재라.
  `dose`        주 가설을 **자기 증거로** 검정한다. 제안된 원인의 강도가 큰 자리에서
                결과가 오히려 작으면 그 가설은 죽는다. 이건 쌍 판별로는 절대 안 나온다.
                `target` 에 가설 hid 하나를 쓰고 `woe_db` 를 음수로 내면 자기 기각이다.

**같은 것을 예측하는 검사는 무용이다.** 두 세계가 모두 "거래량이 는다"고 말하면 그
관측은 아무것도 가르지 못한다. `predicts` 에는 두 세계를 각각 적는다 - pair 면 키가
가설 hid 둘, latent 면 원인 가설 hid 와 U 의 uid 둘이다.

## 증거의 무게 (`woe_db`, 필수)
"이 증거가 H1 과 정합하는가"가 아니다. **H1 이 참일 때 얼마나 예상되며, 경쟁 세계가
참일 때도 똑같이 예상되는가**다. 데시벨로 적어라 (Good 1985 · Fairfield-Charman 2017):

    woe_db = 10 · log10( P(관측|첫째 세계) / P(관측|둘째 세계) )

   3 dB   좋은 청력의 성인이 겨우 지각하는 차이 (JND). **미만이면 무용으로 기록된다**
   5 dB   뚜렷이 구분됨 (확률 75% -> 90% 가 약 5 dB)
  10 dB   두 배 크게 들린다. LR 10배
  25 dB   강하게 지지
  30 dB   결정적 - "자료가 분명히 말하고 있다"

정수로 적어라. 12.7 같은 소수는 거짓 정밀이다. 부호는 `predicts` 의 **첫 세계** 기준이다.
참고로 P(E|H)=0.2, P(E|~H)=0.05 는 흔히 결정적이라 불리지만 **6 dB 에 불과**하다.
`woe_because` 에 각 세계를 들여다본 서술을 적어라 - 비면 무효다.

`sql` 은 코드가 실제로 돌린다. 네 신고가 아니라 질의가 낸 것이 `executable` 을 정한다:
  · 원장에 없는 것(분봉·틱·호가·고저가·공매도잔고·교차자산)을 부르면 떨어진다.
    실패가 아니라 데이터 수집 의제다 - 그런 자리는 `cannot` 이 더 정직하다
  · **0행은 소거로 치지 않는다.** 부재를 보이는 검정이면 `count(*)` 로 감싸 1행을 내라
  · 시점은 코드가 이미 박아 뒀다. PIT 절을 쓸 필요가 없다

{brief}

{schema}

JSON 하나만:
  조회: {{"thought": "...", "sql": "SELECT ..."}}
  판별: {{"thought": "...", "discriminator": {{
        "kind": "pair"|"latent"|"structural"|"capacity"|"dose",
        "target": "H1|H2" · "U_..." · 또는 가설 hid 하나",
        "observation": "무엇을 보나",
        "predicts": {{"H1": "이 세계면 이렇게 보인다", "H2": "..."}},
        "sql": "실행할 질의 (structural 이면 생략 가능)",
        "woe_db": 10, "woe_because": "각 세계에서 이 관측이 얼마나 예상되는가"}}}}
  불가: {{"thought": "...", "cannot": "U_...", "why": "무엇이 없어서 못 가르나"}}
  끝:   {{"thought": "...", "done": true}}"""


def pairs_of(hypotheses: list[Hypothesis]) -> list[str]:
    """요구되는 가설쌍. 이름은 **그래프 순서로 정규화**한다 - H2|H1 은 H1|H2 와 같은 쌍이다."""
    top = [h.hid for h in hypotheses[:TOP_H]]
    return [f"{a}|{b}" for i, a in enumerate(top) for b in top[i + 1:]]


def brief(question: Question, fingerprint: Fingerprint, graph: WorldGraph,
          idents: list[Identification], pairs: list[str]) -> str:
    """설계 패킷. **개입 정의가 맨 위다** - 무엇을 가르는 검정인지의 기준이 그것이다."""
    L = [f"설명 대상 : {question.explanandum}",
         f"개입      : {question.intervention}",
         f"답의 형태 : {question.answer_form}",
         "",
         fingerprint.brief(),
         "",
         "가설"]
    if not graph.hypotheses:
        L.append("  없음 - P2 가 가설을 하나도 세우지 못했다.")
    for h in graph.hypotheses:
        L.append(f"  [{h.hid}] {h.says}")
        L.append(f"        원인 {h.treatment} -> 결과 {h.outcome} · 배정 {h.assignment}")
        if h.predicts:
            L.append("        예측: " + " / ".join(h.predicts))
        if h.denies:
            L.append("        이러면 죽는다: " + " / ".join(h.denies))
    L += ["", "필요한 가설쌍: " + (", ".join(pairs) or "없음 - 가설이 둘 미만이다"),
          "",
          "교란 U - **전부 처분해야 한다.** 각각 소거 검정 또는 cannot"]
    if not graph.latents:
        L.append("  없음 - P3 가 공통원인을 하나도 선언하지 않았다.")
    for u in graph.latents:
        a, b = u.between
        L.append(f"  [{u.uid}]  {a} <-> {b}  ({u.source})")
        L.append(f"           {u.says}")
        if u.blocked_by:
            L.append(f"           이걸로 조건화하면 막힌다는 제안: {', '.join(u.blocked_by)}")
    L += ["", "식별 상태 - 막힌 간선"]
    blocked = [i for i in idents if i.status != "identified"]
    if not blocked:
        L.append("  없음. 그래도 U 소거는 별개다 - 뒷문이 닫혔다고 U 가 사라지지 않는다.")
    for i in blocked:
        L.append(f"  {i.src} -> {i.dst}  {i.status}  "
                 f"막은 U: {', '.join(i.blocked_by) or '-'}")
        if i.assumptions:
            L.append(f"        가정: {'; '.join(i.assumptions)}")
    return "\n".join(L)


def _cleared(d: Discriminator | None) -> bool:
    """소거로 인정되는 조건. **`uncleared()` 의 여집합이다 - 두 자리가 갈리면 안 된다.**

    `structural` 은 질의 없이도 소거한다(제도가 그 경로를 막았다는 것은 자료로 반박할
    대상이 아니다). 그 예외가 `DiscriminationPlan.uncleared` 에 있으므로 여기에도 있어야
    한다 - 없으면 루프는 미소거라 보고 원장은 소거라 보는 상태가 된다.
    """
    if d is None:
        return False
    return not d.common_prediction and (d.kind == "structural" or d.executable)


def _probe(sql, q: str) -> tuple[bool, str, str]:
    """질의를 실제로 돌린다. **`executable` 은 여기서만 정해진다 - 자기 신고가 아니다.**

    0행을 실행 가능으로 치지 않는 이유: 소거의 근거가 될 관측이 나오지 않았기 때문이다.
    부재를 보이는 검정(그런 사건이 없었다)은 `count(*)` 로 감싸면 1행이 되므로 표현할 수
    있고, 그러라고 프롬프트에 적어 뒀다. 0행을 통과시키면 아무것도 안 본 질의가 U 를
    소거한다 - 이 모듈이 막으려는 바로 그 실패다.
    """
    if sql is None:
        return False, NO_SQL, ""
    if not q:
        return False, "질의 없음 - 무엇을 볼지 적히지 않았다", ""
    try:
        rows = sql.query(q, limit=20)
    except PipelineError as exc:
        return False, f"거부: {exc}"[:300], ""
    except Exception as exc:                      # noqa: BLE001 - 되먹임 대상이다
        return False, f"오류: {type(exc).__name__}: {exc}"[:300], ""
    if not rows:
        return False, "0행 - 그 관측이 원장에 없다. 부재를 보이려면 count 로 감싸라", "0행"
    return True, "", f"{len(rows)}행. 첫 행: {rows[0]}"[:400]


def _canon(target: str, order: list[str]) -> str:
    """쌍 이름을 그래프 순서로 되돌린다. 정규화 없이 키로 쓰면 H1|H2 와 H2|H1 이 두 쌍이 된다."""
    toks = {t for t in re.split(r"[|,/\s]+", target) if t}
    hids = [h for h in order if h in toks]
    return "|".join(hids) if len(hids) == 2 else ""


def _accept(rec: dict[str, Any], sql, uids: list[str],
            order: list[str]) -> tuple[tuple[str, str] | None, Discriminator | None, str]:
    """제출 하나를 값으로 굳힌다. 반환은 (키, 판별기준, 되먹임 문장)."""
    target = str(rec.get("target") or "").strip()
    kind = str(rec.get("kind") or "").strip()
    if kind not in ("pair", "latent", "structural", "capacity", "dose"):
        kind = "latent" if target in uids else "pair"
    if kind in ("latent", "structural"):
        if target not in uids and kind == "latent":
            return None, None, (f"거부: {target!r} 는 U 목록에 없다. "
                                f"목록: {', '.join(uids) or '없음'}")
    elif kind in ("capacity", "dose"):
        if target not in order:
            return None, None, (f"거부: {kind} 는 가설 하나를 겨눈다. {target!r} 는 가설이 "
                                f"아니다. 가설: {', '.join(order) or '없음'}")
    else:
        canon = _canon(target, order)
        if not canon:
            return None, None, (f"거부: {target!r} 에서 가설 둘을 못 읽었다. 'Ha|Hb' 로 "
                                f"적어라. 가설: {', '.join(order) or '없음'}")
        target = canon
    raw = rec.get("predicts")
    predicts = {str(k): str(v).strip()
                for k, v in (raw.items() if isinstance(raw, dict) else [])
                if str(v).strip()}
    # 모델의 `common` 신고를 기다리지 않고 **적힌 예측을 직접 센다.** 무용한 검정일수록
    # 스스로 무용하다고 적지 않는다. 예측이 하나뿐이거나 둘이 같은 문장이면, 그 관측이
    # 어느 세계에서 다르게 보이는지가 적히지 않은 것이므로 가르는 힘이 없다.
    #
    # 이제 그 판정을 **데시벨로** 받는다 (Good 1985 · F&C 2017). `woe_db` 가 1급 값이고
    # `common_prediction` 은 |woe| < 3 dB (JND) 에서 유도된다 - 검정 유형을 저장하지
    # 않는 것과 같은 이유다: 유형은 (관측·가설·배경가정) 삼중항의 속성이라 저장하면
    # 거짓말이 된다 (Collier 2011:825).
    db = _woe(rec.get("woe_db"))
    if bool(rec.get("common")) or len(set(predicts.values())) < 2:
        db = 0
    q = str(rec.get("sql") or "").strip()
    # 구조적 배제는 질의가 필요 없다. 제도·규칙이 그 경로를 막았다는 것은 자료로 반박할
    # 대상이 아니다 - Flash Crash 보고서가 fat finger 를 CME 가격밴드 ±12pt 로 죽인 것이
    # 통계가 아니었던 것과 같다. `executable` 요구를 여기 걸면 그 기각을 못 낸다.
    if kind == "structural":
        ok, why, preview = (True, "", "구조적 배제 - 질의 없이 성립")
    else:
        ok, why, preview = _probe(sql, q)
    d = Discriminator(kind=kind, target=target,
                      observation=str(rec.get("observation") or "").strip(),
                      predicts=predicts, sql=q, executable=ok, why_not=why,
                      woe_db=db, woe_because=str(rec.get("woe_because") or "").strip())
    note = f"{kind} {target}: " + (f"실행됨 - {preview}" if ok else f"실행 불가 - {why}")
    note += f" / WOE {db:+d} dB"
    if d.common_prediction:
        note += " / **무용** - 3 dB(JND) 미만이면 두 세계가 갈리지 않는다. 소거로 치지 않는다"
    return (kind, target), d, note


def _woe(raw: Any) -> int:
    """데시벨을 정수로. **1 dB 미만 해상도는 거짓 정밀이다** (Good 1985).

    ±60 dB 를 넘으면 사실상 결정론 주장(LR 10^6)이므로 잘라 낸다 - 반증 불가한 주장을
    수치로 위장하는 자리다.
    """
    try:
        v = int(round(float(raw)))
    except (TypeError, ValueError):
        return 0
    return max(-60, min(60, v))


def _put(got: dict[tuple[str, str], Discriminator], key: tuple[str, str],
         d: Discriminator) -> str:
    """소거된 자리를 못 쓴 제출로 덮지 않는다 - 뒤 턴이 앞 턴보다 나쁜 경우가 실제로 있다."""
    old = got.get(key)
    if old is not None and _cleared(old) and not _cleared(d):
        return " (앞 턴의 소거 검정을 유지했다 - 덮어쓰지 않는다)"
    got[key] = d
    return ""


def _user(trace: list[tuple[str, str]], silent: list[str], weak: list[str],
          left_pairs: list[str], turn: int, force: bool) -> str:
    """매 턴 **남은 목록을 다시 들려준다.** 한 번 요구하고 마는 프롬프트는 U 를 흘린다."""
    L: list[str] = []
    for c, o in trace[-6:]:
        L += [f">>> {c[:600]}", o[:900], ""]
    if silent:
        L.append("소거 검정도 cannot 도 아직 없는 U - **이번 턴에 처분해라**: "
                 + ", ".join(silent))
    if weak:
        L.append("제출은 받았으나 미소거인 U (실행 불가 또는 무용). 더 나은 검정이 있으면 "
                 "내라: " + ", ".join(weak))
    if left_pairs:
        L.append("아직 판별기준이 없는 가설쌍: " + ", ".join(left_pairs))
    if not (silent or weak or left_pairs):
        L.append("남은 것이 없다. done 을 내라.")
    L.append(f"[{turn}/{MAX_TURNS}턴]")
    L.append("마지막이다. 남은 U 는 cannot 으로라도 처분해라 - 침묵만 안 된다." if force
             else "조회(sql)·판별(discriminator)·불가(cannot)·끝(done) 중 하나.")
    return "\n".join(L)


def _handled(got: dict[tuple[str, str], Discriminator], uid: str) -> Discriminator | None:
    """이 U 가 처분됐나. **`structural` 도 처분이다.**

    `for_latent()`·`uncleared()` 는 `structural` 을 U 처분으로 인정하는데 루프의 미처분
    계산이 `('latent', uid)` 키만 보면 세 자리가 어긋난다: 모델이 구조적 배제로 U 를
    처분해도 루프가 못 봐서 `done` 을 상한까지 거부하고(왕복 낭비), 상한에서 `mute` 가
    "12턴 안에 아무것도 안 나왔다"는 **거짓 행**을 P9 대장에 남긴다. 실제로 재현됐다.
    """
    return got.get(("latent", uid)) or got.get(("structural", uid))


def design(client, sql, *, question: Question, fingerprint: Fingerprint,
           graph: WorldGraph, idents: list[Identification]) -> DiscriminationPlan:
    """가를 관측을 설계하고 **코드가 돌려서** 실행 가능성을 정한다.

    `done` 을 미처분 U 가 남은 채 내면 거부하고 남은 목록을 돌려준다. 한 번 요구하고 마는
    설계에서는 모델이 쉬운 U 두 개만 처리하고 done 을 내며, 그 침묵이 소거와 구별되지
    않는다 - 부재가 성공으로 읽히는 옛 구멍과 같은 모양이다.

    상한(12턴)까지 끝내 침묵한 U 는 값으로 굳혀서 담는다. 없는 것을 없다고 적은 행이
    아무 행도 없는 것보다 낫다 - P9 대장에 "12턴 동안 못 냈다"가 남아야 다음 수집이 그
    자리를 안다.
    """
    uids = [u.uid for u in graph.latents]
    order = [h.hid for h in graph.hypotheses]
    pairs = pairs_of(graph.hypotheses)
    if not uids and not pairs:
        # 가를 것이 없다. 부를 이유도 없다.
        log("causal.p5.done", turns=0, n=0, uncleared=0)
        return DiscriminationPlan()

    system = SYSTEM.format(
        brief=brief(question, fingerprint, graph, idents, pairs),
        schema=(sql.schema() if sql is not None else
                f"{NO_SQL} - 조회 없이 설계해라. 낸 질의는 전부 실행 불가로 기록된다."))
    got: dict[tuple[str, str], Discriminator] = {}
    trace: list[tuple[str, str]] = []
    # **표면에 실제로 닿은 질의만 담는다.** `sql=None` 이면 아무것도 안 던진 것이고,
    # 모델이 적어 낸 문자열은 `Discriminator.sql` 에 그대로 남아 사라지지 않는다.
    queries: list[str] = []

    for turn in range(1, MAX_TURNS + 1):
        silent = [u for u in uids if _handled(got, u) is None]
        weak = [u for u in uids
                if u not in silent and not _cleared(_handled(got, u))]
        left_pairs = [p for p in pairs if ("pair", p) not in got]
        out = client.complete_json(
            system, _user(trace, silent, weak, left_pairs, turn, force=(turn == MAX_TURNS)))
        acted = False

        rec = out.get("discriminator")
        if isinstance(rec, dict):
            acted = True
            key, d, note = _accept(rec, sql, uids, order)
            if d is not None and key is not None:
                if d.sql and sql is not None:
                    queries.append(d.sql)
                note += _put(got, key, d)
            trace.append(("(판별 제출)", note))

        uid = str(out.get("cannot") or "").strip()
        if uid:
            acted = True
            if uid in uids:
                why = str(out.get("why") or "").strip() or "사유 미기재"
                # cannot 은 처분이지 소거가 아니다. `executable=False` 로 남아
                # `uncleared()` 가 미소거로 집어 P8 이 확정한다.
                note = _put(got, ("latent", uid), Discriminator(
                    kind="latent", target=uid, observation="가를 관측을 적지 못했다",
                    why_not=why))
                trace.append(("(cannot)", f"{uid} 미소거로 기록. 사유: {why}{note}"))
            else:
                trace.append(("(cannot)", f"거부: {uid!r} 는 U 목록에 없다. "
                                          f"목록: {', '.join(uids) or '없음'}"))

        q = str(out.get("sql") or "").strip()
        if q:
            acted = True
            if sql is None:
                trace.append((q, f"{NO_SQL} - 조회 없이 설계해라."))
            else:
                queries.append(q)
                trace.append((q, sql.ask(q)))

        if out.get("done"):
            acted = True
            left = [u for u in uids if _handled(got, u) is None]
            if left and turn < MAX_TURNS:
                trace.append(("(완료 시도)", "**거부** - 소거 검정도 cannot 도 없는 U 가 "
                                          f"남았다: {', '.join(left)}"))
            else:
                break

        if not acted:
            trace.append(("", "오류: sql·discriminator·cannot·done 중 하나를 내라."))

    # 상한까지 침묵한 U 를 값으로 굳힌다. 행이 없으면 P9 대장에서 "안 물어본 것"과
    # "물어봤는데 못 냈다"가 같은 모양(부재)이 된다.
    mute = [u for u in uids if _handled(got, u) is None]
    for uid in mute:
        got[("latent", uid)] = Discriminator(
            kind="latent", target=uid, observation="가를 관측을 적지 못했다",
            why_not=f"{MAX_TURNS}턴 안에 소거 검정도 cannot 도 나오지 않았다")

    plan = DiscriminationPlan(discriminators=list(got.values()), queries=queries)
    log("causal.p5.done", turns=turn, n=len(plan.discriminators),
        pair=sum(1 for d in plan.discriminators if d.kind == "pair"),
        cleared=sum(1 for d in plan.discriminators if _cleared(d)),
        uncleared=len(plan.uncleared(graph.latents)), mute=len(mute),
        queries=len(queries))
    return plan


__all__ = ["MAX_TURNS", "NO_SQL", "SYSTEM", "brief", "design", "pairs_of"]
