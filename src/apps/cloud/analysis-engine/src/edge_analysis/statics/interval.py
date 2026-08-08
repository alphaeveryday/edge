"""ETF 요청창 설명 — 코드가 사실·블록·근거를 고정하고 LLM은 허용 문구만 채운다.

세 규율이 이 모듈의 전부다.

1. **요청창만 설명한다.** 헤더는 전일 종가부터 요청 종료까지의 ETF 누적수익이고,
   기여·폭·NAV·시장·섹터·가격 경로는 요청 시작부터 종료까지만 계산한다.
2. **요청 종료까지 알려진 정보만 쓴다.** 공시·뉴스의 `available_at`과 모든 근거의
   `as_of`는 요청 종료를 넘지 않는다.
3. **코드가 구조와 수치를 소유한다.** `BlockPlan`이 블록 순서·발생·근거를 정하고,
   LLM은 허용된 자리표시자만 채운다. 일단위 검정을 요청창 인과로 승격하지 않는다.

    python -m edge_analysis.statics.interval <ticker6> <instrument_id> <YYYY-MM-DD> \
        <HH:MM> <HH:MM>
"""
from __future__ import annotations

import datetime as dt
import math
import json
import re
import sys
from dataclasses import dataclass

from .layers import SESSION_CLOSE, SESSION_OPEN, decompose
from .premium5 import premium_5m
from .windows import Window

# 자정 τ = **시각 미상**이다. `lake.taus` 는 사이드카가 없으면 `available_at`(자정)로
# 폴백하는데, 그걸 '장 전' 으로 세면 부재가 갭 창의 알리바이를 위장한다.
UNKNOWN_TAU = dt.time(0, 0)

# 이 몫 미달 창은 층까지 가르지 않는다 — `plain.recent_window` 와 같은 규율.
# 요구창은 사람이 물었으므로 몫과 무관하게 항상 가른다.
FLOOR = 0.20

MIN_N = 20
BLOCK_ORDER = (
    "header", "nav", "contribution", "breadth", "path", "relative", "anomaly",
    "disclosure", "flow", "news", "statistics", "causal", "absence", "evidence",
)


@dataclass(frozen=True)
class ContributionFact:
    name: str
    contribution: float
    return_value: float | None = None
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class StatisticFact:
    claim: str
    n: int
    effect: float | None = None
    p: float | None = None
    evidence_ids: tuple[str, ...] = ()

@dataclass(frozen=True)
class WindowFacts:
    ticker: str
    name: str
    day: str
    window_start: str
    window_end: str
    header_return: float | None
    window_return: float
    advancers: int
    decliners: int
    market_return: float | None
    sector_name: str | None
    sector_return: float | None
    # 층 기여회계(ALPHA-871) - [3] 블록이 상대비교 대신 이 값을 말한다. 셋 다 층
    # rollup 프레임(상태축)의 %p 며, 시장+섹터+고유 = rollup.total 이 항등식이다.
    market_contribution: float | None = None
    sector_contribution: float | None = None
    idio_contribution: float | None = None
    path: str = ""

    contributions: tuple[ContributionFact, ...] = ()
    nav_gap: float | None = None
    anomaly: str | None = None
    disclosures: tuple[str, ...] = ()
    flows: tuple[str, ...] = ()
    news: tuple[str, ...] = ()
    statistics: tuple[StatisticFact, ...] = ()
    causal: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    lineage: tuple[dict, ...] = ()
    final_lines: tuple[str, ...] = ()
    event_ids: tuple[str, ...] = ()
    final_bundle_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class OutputBlock:
    key: str
    lines: tuple[str, ...]
    evidence_ids: tuple[str, ...] = ()


def _pct(value: float) -> str:
    return f"{value * 100:+.2f}%"


def _contribution_lines(items: tuple[ContributionFact, ...]) -> tuple[str, ...]:
    if not items:
        return ("구성종목 기여를 계산하지 못했습니다.",)
    up = sorted((x for x in items if x.contribution > 0),
                key=lambda x: -x.contribution)[:3]
    down = sorted((x for x in items if x.contribution < 0),
                  key=lambda x: x.contribution)[:3]
    out = []
    if up:
        out.append("상승 기여: " + " · ".join(
            f"{x.name} {_pct(x.contribution)}p" for x in up))
    if down:
        out.append("하락 기여: " + " · ".join(
            f"{x.name} {_pct(x.contribution)}p" for x in down))
    return tuple(out) or ("방향이 있는 구성종목 기여가 없습니다.",)


def build_block_plan(facts: WindowFacts) -> tuple[OutputBlock, ...]:
    """요청창 사실을 고정 순서 블록으로 만든다. 다른 창은 입력 필드부터 없다."""
    header = (
        f"{facts.name} {_pct(facts.header_return)}"
        if facts.header_return is not None else
        f"{facts.name} · 전일 종가 대비 수익률 미계측"
    )
    blocks = [
        OutputBlock("header", (
            header,
            f"{facts.window_end} 기준 · 전일 종가 대비",
        )),
    ]
    if facts.nav_gap is not None:
        blocks.append(OutputBlock("nav", (f"NAV 괴리 {_pct(facts.nav_gap)}p",)))
    blocks += [
        OutputBlock("contribution", _contribution_lines(facts.contributions),
                    tuple(i for x in facts.contributions for i in x.evidence_ids)),
        OutputBlock("breadth", (
            f"구성종목 {facts.advancers + facts.decliners}종목 중 "
            f"{facts.advancers}종목 상승 · {facts.decliners}종목 하락",
        )),
        OutputBlock("path", (facts.path,)),
    ]
    # 기여회계(ALPHA-871) - 상대비교("X 대비 +n%p")는 층 회계와 다른 프레임이고
    # 프록시 상품명을 산문에 노출했다. 층 rollup 의 기여를 그대로 말한다: 항이 곧
    # 항등식(시장+섹터+고유 = 층 프레임의 구간수익)의 조각이라 검산 가능하다.
    # 프록시·지수명은 쓰지 않는다 - 섹터 소스가 바뀌어도(업종지수 1분봉 전환 예정)
    # 산문 형식이 불변이어야 한다.
    if facts.market_contribution is None:
        relative = ("층 미계측 — 시장 층이 서지 않았습니다.",)
    else:
        parts = [f"시장 요인 {_pct(facts.market_contribution)}p"]
        if facts.sector_contribution is not None:
            parts.append(f"섹터 요인 {_pct(facts.sector_contribution)}p")
        if facts.idio_contribution is not None:
            parts.append(f"고유 요인 {_pct(facts.idio_contribution)}p")
        relative = (" · ".join(parts),)
    blocks.append(OutputBlock("relative", relative))
    if facts.anomaly:
        blocks.append(OutputBlock("anomaly", (facts.anomaly,)))
    if facts.disclosures:
        blocks.append(OutputBlock("disclosure", facts.disclosures))
    if facts.flows:
        blocks.append(OutputBlock("flow", facts.flows))
    if facts.news:
        blocks.append(OutputBlock("news", facts.news))
    if facts.statistics:
        lines, refs = [], []
        for stat in facts.statistics:
            refs.extend(stat.evidence_ids)
            if stat.n < MIN_N:
                lines.append(f"표본이 부족해 판단하지 않았습니다 (n={stat.n} < {MIN_N}).")
            else:
                detail = stat.claim
                if stat.effect is not None:
                    detail += f" 효과 {_pct(stat.effect)}p"
                if stat.p is not None:
                    detail += f" · p={stat.p:.4f}"
                lines.append(detail)
        blocks.append(OutputBlock("statistics", tuple(lines), tuple(refs)))
    if facts.causal:
        blocks.append(OutputBlock("causal", facts.causal))
    if not facts.disclosures and not facts.news:
        blocks.append(OutputBlock(
            "absence", ("해당 구간에 확인된 공시·보도는 없습니다.",)))
    blocks.append(OutputBlock(
        "evidence",
        ("근거: " + " · ".join(facts.evidence),) if facts.evidence
        else ("근거: 요청창 5분봉",),
    ))
    return tuple(sorted(blocks, key=lambda block: BLOCK_ORDER.index(block.key)))

def _summary_number(match: re.Match) -> str:
    value = float(match.group(1))
    if value <= -3:
        return "큰 폭 하락"
    if value < 0:
        return "하락"
    if value >= 3:
        return "큰 폭 상승"
    if value > 0:
        return "상승"
    return "보합"


def render_block_plan(plan: tuple[OutputBlock, ...], *, summary: bool = False) -> str:
    text = "\n\n".join("\n".join(block.lines) for block in plan)
    return re.sub(r"([+-]\d+(?:\.\d+)?)%p?", _summary_number, text) if summary else text


def final_explanation_payload(facts: WindowFacts) -> dict:
    """최종 저장 모양. 고정 블록과 원천 조회키를 한 객체에 둔다."""
    plan = {block.key: block for block in build_block_plan(facts)}

    def block(code: str, title: str, lines: tuple[str, ...],
              systems: tuple[str, ...], refs: tuple[str, ...] = ()) -> dict:
        return {
            "block_code": code,
            "block_title": title,
            "text": "\n".join(lines),
            "source_systems": list(dict.fromkeys(systems)),
            "evidence_refs": list(dict.fromkeys(refs)),
        }

    blocks = [
        block("H", "헤더", plan["header"].lines, ("S3.bars_5m",),
              (f"bars_5m:{facts.ticker}",)),
        block("1", "기여 분해",
              plan["contribution"].lines + plan["breadth"].lines,
              ("S3.bars_5m", "RDB.etf_holding_snapshot"),
              plan["contribution"].evidence_ids),
        block("2", "시간 구간", plan["path"].lines, ("S3.bars_5m",),
              (f"bars_5m:{facts.ticker}",)),
        block("3", "요인 분해", plan["relative"].lines,
              ("S3.bars_5m", "S3.layers_daily")),
    ]
    def _lines(*keys: str) -> tuple[str, ...]:
        return tuple(line for key in keys
                     for line in (plan[key].lines if key in plan else ()))

    # 사건 계열(공시·수급·보도)은 final_lines 가 이미 접었으면 중복하지 않는다.
    # 검정 계열(statistics·causal)은 **항상 병치한다** - 판정은 코드 산출이고,
    # final_lines 뒤에 숨으면 유의·비유의 사실이 산문에서 사라진다(Rule 12).
    optional = (facts.final_lines or _lines("disclosure", "flow", "news")) \
        + _lines("statistics", "causal")
    if optional:
        lines = optional
        systems = (
            *(("RDB.source_event",) if facts.event_ids else ()),
            *(("RDB.analysis_evidence_bundle",) if facts.final_bundle_ids else ()),
        )
        refs = tuple(f"source_event:{value}" for value in facts.event_ids) + tuple(
            f"analysis_evidence_bundle:{value}" for value in facts.final_bundle_ids)
        blocks.append(block("4", "이벤트 병치", lines, systems, refs))
    else:
        blocks.append(block(
            "N", "부재 고지",
            ("해당 구간에 확인된 공시·보도는 없습니다. "
             "비교할 통계·리포트도 없습니다.",),
            ("RDB.source_event", "RDB.analysis_evidence_bundle"),
        ))
    return {
        "rendered_text": "\n\n".join(
            f"[{item['block_code']}] {item['text']}" for item in blocks),
        "blocks": blocks,
    }


def render_final_explanation(facts: WindowFacts) -> str:
    """고정 H·1·2·3 뒤에 조건부 4 또는 N을 붙인다."""
    return final_explanation_payload(facts)["rendered_text"]

# 상대 크기를 **낱말**로 말한다. 수치 없는 설명이 순위를 잃으면 아무 말도 아니게 된다.
_RANK_WORD = ("가장 크게", "두 번째로 크게", "세 번째로")

# 다른 창을 **간접 지시**하는 낱말. 창 이름·시각을 그대로 부르면("잔여1 09:00~11:00")
# 요구한 구간을 물은 사람에게 다른 구간을 설명하는 셈이 된다. 시간대 낱말은 맥락을
# 주면서도 설명 대상을 옮기지 않는다 - 요구창이 여전히 주어다.
_WHEN = ((9, 30, "장 초반"), (11, 0, "오전 중반"), (13, 0, "점심 무렵"),
         (14, 30, "오후"), (15, 30, "장 막판"))


def _when_word(t: dt.datetime) -> str:
    """앞 구간을 **간접 지시**할 때만 쓰는 시간대 낱말. 요구창 본문에는 쓰지 않는다."""
    for h, m, w in reversed(_WHEN):
        if (t.hour, t.minute) >= (h, m):
            return w
    return "장 초반"


class IntervalError(ValueError):
    """구간이 시각으로 읽히지 않는다 — 형식 오류만 던진다(범위는 자른다)."""


def _clock(t: dt.time | dt.datetime, *, half: bool = True) -> str:
    """`13:30` -> `오후 1시 30분`. 사람이 시계를 읽는 말로 **정확한 시각**을 적는다.

    '이 시간대' 같은 지시어를 쓰지 않는다 - 문단에서 지시어가 겹치면 어느 구간을
    말하는지 사라진다. 그래서 문장마다 시각이 그대로 들어간다.
    """
    h, mi = t.hour, t.minute
    ap = "오전 " if h < 12 else "오후 "
    hh = h if h <= 12 else h - 12
    return (ap if half else "") + f"{hh}시" + (f" {mi}분" if mi else "")


def _span(w: Window) -> str:
    """창 하나를 부르는 말. 뒤쪽 시각은 오전/오후가 같으면 접두를 뗀다(자연스러운 한국어)."""
    if w.kind == "gap":
        return "전일 종가부터 오늘 시가까지"
    same = (w.start.hour < 12) == (w.end.hour < 12)
    return f"{_clock(w.start)}부터 {_clock(w.end, half=not same)}까지"


def _para(pairs: list[tuple[str, str]], indent: str = "  ",
          width: int = 88) -> list[str]:
    """(문장, 근거) 쌍을 **한 문단**으로 엮는다 - 글머리표가 아니라 이어지는 글이다.

    근거는 문장 바로 뒤 대괄호에 붙는다. 문장 안에 수치를 넣으면 문단이 표처럼
    읽히고, 근거를 문단 밖으로 빼면 어느 문장의 근거인지 사라진다.
    """
    words: list[str] = []
    for text, ground in pairs:
        words += text.split()
        if ground:
            words.append(f"[{ground}]")
    out, line = [], indent
    for tok in words:
        if len(line) + len(tok) + 1 > width and line.strip():
            out.append(line.rstrip())
            line = indent
        line += tok + " "
    if line.strip():
        out.append(line.rstrip())
    return out


def clamp(t0: str, t1: str) -> tuple[str, str, list[str]]:
    """`HH:MM[:SS]` 두 개를 장 안으로 자른다. **거부가 아니라 절단 + 사유.**

    장 전을 요구하면 그건 `갭` 창이 답한다(항상 따로 나온다). 장 후를 요구하면
    5분봉이 없으니 마감으로 자른다 — 없는 해상도를 있는 척하지 않되, 질문 자체를
    되돌려보내지도 않는다.
    """
    def norm(t: str) -> str:
        p = t.strip().split(":")
        if not 2 <= len(p) <= 3 or not all(x.isdigit() for x in p):
            raise IntervalError(f"시각 형식이 아니다: {t!r} (HH:MM 또는 HH:MM:SS)")
        h, m, s = (p + ["0"])[:3]
        return f"{int(h):02d}:{int(m):02d}:{int(s):02d}"

    a, b, why = norm(t0), norm(t1), []
    if a >= b:
        raise IntervalError(f"구간이 비었다: {a} >= {b}")
    if a < SESSION_OPEN:
        why.append(f"{a[:5]} 는 장 전이라 개장으로 잘랐다 — 그 앞은 `갭` 창이 답한다")
        a = SESSION_OPEN
    if b > SESSION_CLOSE:
        why.append(f"{b[:5]} 는 장 후라 마감으로 잘랐다 — 그 뒤는 5분봉이 없다")
        b = SESSION_CLOSE
    if a >= b:
        raise IntervalError(f"자른 뒤 구간이 비었다: {a} >= {b}")
    return a, b, why


def _bars(lake, ticker: str, day: str) -> list[tuple[dt.datetime, float, float]]:
    """(ts, 시가, 종가). 하루치 한 번에 읽는다 — 창마다 질의하면 6배 왕복이다."""
    return [(t, float(o), float(c)) for t, o, c in lake.sql(
        f"SELECT ts, open, close FROM bars_5m WHERE trade_date = DATE '{day}' "
        f"AND regexp_replace(symbol, '\\.(KS|KQ)$', '') = '{ticker}' "
        "AND open > 0 AND close > 0 ORDER BY ts")]


def _gap(lake, ticker: str, day: str) -> float | None:
    """ln(당일 첫 봉 시가 / 전 거래일 마지막 봉 종가).

    **5분봉 자체가 답한다.** 일봉(`s3_price_daily`)을 쓰려다 날짜 커버리지가 달라
    갭이 통째로 미계측이 됐다(실측 000660 06-01). 같은 격자·같은 소스에서 뽑으면
    장중 몫과 갭이 같은 척도가 되고, 합이 하루와 맞는다는 주장이 참이 된다.
    """
    rows = lake.sql(
        "SELECT trade_date, first(open ORDER BY ts) AS o, last(close ORDER BY ts) AS c "
        f"FROM bars_5m WHERE trade_date <= DATE '{day}' AND open > 0 AND close > 0 "
        f"AND regexp_replace(symbol, '\\.(KS|KQ)$', '') = '{ticker}' "
        "GROUP BY 1 ORDER BY 1 DESC LIMIT 2")
    if len(rows) < 2 or str(rows[0][0]) != day:
        return None
    o, pc = float(rows[0][1]), float(rows[1][2])
    return math.log(o / pc) if o > 0 and pc > 0 else None


def _window_ret(bars: list[tuple[dt.datetime, float, float]],
                w: Window) -> tuple[float | None, int]:
    """창 안 봉의 시가→종가 로그수익과 봉 수. 봉이 없으면 (None, 0) — 0 이 아니다."""
    seg = [b for b in bars if w.start <= b[0] < w.end]
    if not seg:
        return None, 0
    return math.log(seg[-1][2] / seg[0][1]), len(seg)


def _context(w: Window, ret: float,
             before: list[tuple[str, float, str]]) -> list[tuple[str, str]]:
    """요구창 앞에서 **더 큰** 움직임이 있었으면 그것을 간접적으로 얹는다.

    사용자 규약: 정보 접근은 이전 시간 전부 가능하고 **설명 대상만** 요구 구간이다.
    앞 구간이 더 합리적인 설명이면 쓸 수 있지만 **직접 호명은 금지** - 창 이름이나
    시각을 부르면 물은 구간이 아니라 다른 구간을 설명하는 답이 된다.
    """
    big = [(when, r) for when, r, _k in before if abs(r) > abs(ret) * 1.5]
    if not big:
        return []
    when, r = max(big, key=lambda x: abs(x[1]))
    same = (r > 0) == (ret > 0)
    if same:
        return [(f"{when}부터 이어진 흐름이 그대로 이어진 구간이에요."
                 " 앞선 움직임이 더 컸고, 여기는 그 연장선이에요.",
                 f"앞 구간 몫 {r * 100:+.2f}%p")]
    return [(f"{when}에 반대 방향으로 더 크게 움직인 뒤였어요."
             " 그 되돌림으로 읽는 편이 자연스러워요.",
             f"앞 구간 몫 {r * 100:+.2f}%p")]


def _reasons(w: Window, ret: float, rank: int, roll, inside: list[str],
             after: int) -> list[tuple[str, str]]:
    """창 하나의 (문장, 근거). 문장에는 **정확한 시각**이 들어가고 수치는 근거로 간다."""
    up = "올랐" if ret > 0 else "내렸"
    span = _span(w)
    out: list[tuple[str, str]] = []
    if w.kind == "gap":
        out.append((f"{span} 정보가 한꺼번에 반영되면서 시가가 전일 종가보다 {up}어요."
                    " 장이 열려 있지 않은 구간이라 더 잘게 나눌 수 없고,"
                    " 그래서 안에서 무엇이 먼저였는지는 가릴 수 없어요.",
                    f"갭 {ret * 100:+.3f}%p"))
    else:
        word = _RANK_WORD[rank] if rank < len(_RANK_WORD) else "비교적 작게"
        out.append((f"{span} {word} {up}어요."
                    " 같은 날의 다른 구간과 견줘 본 순서예요.",
                    f"구간 몫 {ret * 100:+.3f}%p · 하루에서 {rank + 1}번째"))
    if roll is not None and roll.layers:
        big = max(roll.layers, key=lambda x: abs(x.contribution))
        same = (big.contribution > 0) == (ret > 0)
        out.append(
            (f"이 움직임은 {big.kind} 흐름과 같은 방향이었어요.",
             f"{big.kind} {big.code} {big.contribution * 100:+.3f}%p")
            if same else
            (f"{big.kind} 흐름은 반대로 갔는데도 {up}어요."
             " 시장을 따라간 게 아니라는 뜻이에요.",
             f"{big.kind} {big.code} {big.contribution * 100:+.3f}%p"))
        if abs(roll.idio) > abs(big.contribution):
            out.append(("설명이 붙는 공통 흐름보다 이 종목만의 움직임이 더 컸어요."
                        " 무엇이 그 몫을 만들었는지는 아직 가려지지 않았어요.",
                        f"고유 {roll.idio * 100:+.3f}%p"))
    if inside:
        out.append((f"{span} 사이에 공시나 보도가 있었어요."
                    " 다만 그것이 이 움직임을 만들었는지는 확인되지 않았어요 -"
                    " 같은 시간에 있었다는 것과 원인이라는 것은 다른 말이에요.",
                    f"사건 {len(inside)}건"))
    elif w.kind != "gap":
        out.append((f"{span} 사이에는 새로 알려진 소식이 없었어요."
                    " 그래서 움직임의 이유를 소식에서 찾을 수는 없어요."
                    + (" 오늘 나온 소식은 모두 이 구간보다 뒤였어요." if after else ""),
                    "구간 내 사건 0건" + (f" · 이후 {after}건" if after else "")))
    return out


def _etypes(lake, eids: list[str]) -> list[str]:
    """오늘 실제로 있었던 사건타입. **우리가 고르지 않는다** - 있는 것만 센다."""
    if not eids or lake.exists.get("rdb") is not True:
        return []
    lit = ",".join(repr(str(e)) for e in eids)
    rows = lake.sql("SELECT DISTINCT event_type_code FROM rdb.public.source_event "
                    f"WHERE source_event_id IN ({lit})")
    return sorted({str(r[0]) for r in rows if r[0]})


def _literal(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _mapping(value) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _event_lines(lake, event_ids: tuple[str, ...],
                 event_times: dict[str, dt.datetime]) -> tuple[str, ...]:
    """PIT 통과 사건의 (시각, 요지) 라인 - disclosure 블록 계약.

    입력은 `window_facts` 가 이미 클램프한 사건뿐이다(τ ∈ [창 시작, 창 끝]) -
    여기서 미래 사건이 들어오면 그 자체가 PIT 위반이다. 제목 조회가 실패하거나
    비면 id 라인으로 폴백한다 - 사건이 있었다는 사실을 조회 실패가 지우면 안 된다.
    """
    if not event_ids:
        return ()
    ids = ",".join(_literal(eid) for eid in event_ids)
    try:
        titles = {str(r[0]): str(r[1]) for r in lake.sql(
            "SELECT source_event_id, title FROM rdb.public.source_event "
            f"WHERE source_event_id IN ({ids})") if r[1]}
    except Exception:  # noqa: BLE001 - 제목 부재는 id 폴백으로 말한다
        titles = {}
    out = []
    for eid in event_ids:
        title = titles.get(eid)
        if not title:
            out.append(f"요청창 사건 {eid}")
            continue
        tau = event_times.get(eid)
        out.append(f"{tau:%H:%M}, {title.rstrip('.')}" if tau else title)
    return tuple(out)


def _final_lines(lake, ticker: str, day: str, event_ids: tuple[str, ...],
                 event_times: dict[str, dt.datetime], window_return: float,
                 market_return: float | None,
                 evidence_bundle_ids: list[str] | None = None) -> tuple[str, ...]:
    """PIT 사건·재무·통계 사실을 사용자용 네 문장으로 접는다."""
    if not event_ids:
        return ()
    ids = ",".join(_literal(eid) for eid in event_ids)
    try:
        events = lake.sql(
            "SELECT source_event_id, title FROM rdb.public.source_event "
            f"WHERE source_event_id IN ({ids}) ORDER BY available_at LIMIT 1")
    except Exception:  # noqa: BLE001 - 최종 문장은 아래의 관측 가능한 사실만 쓴다
        events = []
    if not events:
        return ()
    event_id, title = str(events[0][0]), str(events[0][1] or "공시")
    tau = event_times.get(event_id)
    first = f"{tau:%H:%M}, {title.rstrip('.')}가 있었습니다." if tau else f"{title.rstrip('.')}가 있었습니다."

    lines = [first]
    try:
        supply = lake.sql(
            "SELECT amount_krw, ratio_pct FROM s3_supply_fact "
            f"WHERE ticker={_literal(ticker.split('.')[0])} "
            f"AND report_date=DATE {_literal(day)} ORDER BY rcept_no DESC LIMIT 1")
    except Exception:  # noqa: BLE001 - 재무 부재를 0으로 만들지 않는다
        supply = []
    if supply and supply[0][0] is not None and supply[0][1] is not None:
        amount = float(supply[0][0]) / 100_000_000
        amount_text = f"{amount:,.0f}" if amount >= 1 else f"{amount:.1f}"
        lines.append(
            f"계약금액 {amount_text}억원, 최근 연매출 대비 "
            f"{float(supply[0][1]):.1f}% 규모입니다.")

    try:
        bundles = lake.sql(
            "SELECT bundle_id, stats FROM rdb.public.analysis_evidence_bundle "
            f"WHERE cell={_literal(ticker.split('.')[0])} "
            f"AND trade_date=DATE {_literal(day)} AND basis='statistical' "
            "ORDER BY created_at DESC LIMIT 1")
    except Exception:  # noqa: BLE001 - 통계 부재를 결과 없음으로 명시한다
        bundles = []
    if bundles and evidence_bundle_ids is not None:
        evidence_bundle_ids.append(str(bundles[0][0]))
    stats = _mapping(bundles[0][1]) if bundles else {}
    n = stats.get("n", stats.get("n_days", stats.get("n_pairs")))
    mean = stats.get("mean_excess", stats.get("att", stats.get("effect")))
    if isinstance(n, (int, float)) and int(n) >= MIN_N and isinstance(mean, (int, float)):
        lines.append(
            f"시장 요인을 제거한 기준으로, 조건이 비슷한 과거 {int(n)}건의 공시 당일 "
            f"초과수익률은 평균 {float(mean) * 100:+.1f}%였습니다.")
        if market_return is not None:
            today = window_return - market_return
            position = str(stats.get("position") or "").strip()
            tail = f"과거 분포의 {position}입니다." if position else "과거 분포 내 위치는 계산하지 못했습니다."
            lines.append(
                f"오늘 이 종목의 초과수익률은 {today * 100:+.1f}%로, {tail}")
    return tuple(lines)


# "호출자가 층 분해를 모른다" 를 뜻하는 기본값. `None` 을 쓰면 **분해 실패**(라우팅도
# 못 얻었다)와 구분되지 않고, 그 둘을 겹치면 실패한 창을 여기서 재시도하게 된다.
ROLL_UNSET = object()


def window_facts(lake, ticker: str, instrument_id: str, day: str,
                 t0: str, t1: str, *, roll=ROLL_UNSET) -> WindowFacts:
    """원천에서 요청창 하나의 사실만 계산한다.

    ``roll`` 은 호출자가 같은 창으로 이미 뽑은 층 분해다. 넘어오면 재질의하지 않는다 -
    `pipeline` 은 라우팅에서 이 분해로 route_code 를 정해 원장에 넣으므로, 여기서 다시
    질의하면 그 사이 분봉 canonical 이 정정될 때 **한 explanation 안에서** 라우팅 근거와
    산문 근거가 갈린다.

    ⚠️ ``None`` 은 "안 넘겼다"가 아니라 **"라우팅도 못 얻었다"** 는 사실이다 - 그때도
    재질의하지 않는다. 재질의하면 라우팅 시점엔 없던 재료가 그새 착지했을 때 원장은
    `PRICE_ONLY`(미상)인데 산문에는 층 근거가 실린다 - 이 함수가 막으려는 갈림 그대로다.
    호출자가 분해를 아예 모르는 경우(`window_batch`·`explain` 단독 호출)만 기본값
    ``ROLL_UNSET`` 로 남아 직접 계산한다.

    ⚠️ 그중 `window_batch` 는 **이미 있는 route 에 설명을 매단다** — 그 route 를 정한
    분해가 저장돼 있지 않아 여기서 넘길 것이 없고, 그래서 같은 갈림이 그 경로엔 남아
    있다(원장의 route_code 는 옛 분해, 산문은 지금 분해). 닫으려면 route 와 함께 그
    분해를 영속해야 한다 — 이 함수 밖의 일이다.

    ⚠️ 남는 천장: 봉(`_bars`)·괴리(`premium_5m`)는 **여기서 다시 읽는다**. 주입된 분해와
    그 둘 사이에도 정정이 끼어들 수 있어 층↔봉 스냅샷은 아직 한 벌이 아니다. 이 변경이
    닫는 것은 원장 route_code ↔ 산문 근거의 갈림이고, 전 사실을 한 스냅샷으로 묶는 것은
    별개 작업이다(창 하나를 통째로 고정하는 설계가 필요하다).
    """
    a, b, _why = clamp(t0, t1)
    date = dt.date.fromisoformat(day)
    start = dt.datetime.combine(date, dt.time.fromisoformat(a))
    end = dt.datetime.combine(date, dt.time.fromisoformat(b))
    asked = Window("요구창", start, end, "asked")
    open_to_end = Window(
        "헤더", dt.datetime.combine(date, dt.time.fromisoformat(SESSION_OPEN)),
        end, "asked")
    bars = _bars(lake, ticker, day)
    window_return, _ = _window_ret(bars, asked)
    intraday, _ = _window_ret(bars, open_to_end)
    gap = _gap(lake, ticker, day)
    header_return = None if gap is None or intraday is None else gap + intraday
    premium, _premium_note = premium_5m(
        lake, ticker, day, window_start=a, window_end=b)

    if roll is ROLL_UNSET:
        roll = decompose(lake, ticker, day, clock=(a, b))
    layers = tuple(getattr(roll, "layers", ())) if roll is not None else ()
    names = tuple(getattr(roll, "names", ())) if roll is not None else ()
    contributions = tuple(
        ContributionFact(
            str(getattr(item, "label", "") or getattr(item, "ticker", "")),
            float(item.contribution),
            float(item.ret),
            (f"bars_5m:{getattr(item, 'ticker', ticker)}",),
        )
        for item in names if getattr(item, "contribution", None) is not None
    )
    market = next((x for x in layers if x.kind == "시장"), None)
    sector = next((x for x in layers if x.kind == "섹터"), None)
    event_times = {
        str(eid): tau for tau, eid in lake.taus(instrument_id, day)
        if tau is not None and tau.time() != UNKNOWN_TAU and start <= tau <= end
    }
    event_ids = tuple(event_times)
    if window_return is None:
        # 이 파일의 도메인 예외를 쓴다(`clamp` 과 같은 축). `ValueError` 하위라 기존
        # `except` 는 그대로 동작하고, 호출자가 "이 모듈이 낸 부재" 와 "예상 못 한 버그" 를
        # 가를 수 있게 된다 — 판정불가 산문이 그 구분으로 문구를 정한다.
        raise IntervalError(f"요청창 {a[:5]}~{b[:5]} 5분 수익률을 계산하지 못했습니다")
    measured = window_return
    direction = "상승" if measured > 0 else "하락" if measured < 0 else "보합"
    evidence = ["구성종목 실시간 시세"]
    # 산문에 프록시·지수 상품명을 싣지 않는다(ALPHA-871) - 실체는 lineage 와 층
    # rollup 에 남는다. 이름 오염(layers_daily 41/80)의 사용자 노출 경로도 이걸로 닫힌다.
    if market is not None:
        evidence.append("시장 계열")
    if sector is not None:
        evidence.append("섹터 계열")
    if event_ids:
        evidence.append("요청창 사건")
    lineage = (
        {
            "view": "bars_5m",
            "fields": ("ts", "open", "close"),
            "entity": ticker,
            "window_start": a[:5],
            "window_end": b[:5],
            "as_of": b[:5],
        },
        {
            "view": "layers",
            "fields": ("ret", "contribution", "weight"),
            "entity": ticker,
            "window_start": a[:5],
            "window_end": b[:5],
            "as_of": b[:5],
        },
    )
    final_bundle_ids: list[str] = []
    final_lines = _final_lines(
        lake, ticker, day, event_ids, event_times, measured,
        None if market is None else float(market.ret),
        evidence_bundle_ids=final_bundle_ids,
    )
    return WindowFacts(
        ticker=ticker,
        name=str(getattr(roll, "etf_name", "") or ticker),
        day=day,
        window_start=a[:5],
        window_end=b[:5],
        header_return=header_return,
        window_return=measured,
        advancers=sum(1 for item in names if float(item.ret) > 0),
        decliners=sum(1 for item in names if float(item.ret) < 0),
        market_return=None if market is None else float(market.ret),
        sector_name=None if sector is None else str(sector.name),
        sector_return=None if sector is None else float(sector.ret),
        # `getattr` 인 이유: 주입 rollup 은 형이 강제되지 않는다(레거시 대역·테스트
        # fake). 기여가 없으면 층 미계측으로 접는다 - 지어내지 않는다.
        market_contribution=(float(market.contribution)
                             if market is not None
                             and getattr(market, "contribution", None) is not None
                             else None),
        sector_contribution=(float(sector.contribution)
                             if sector is not None
                             and getattr(sector, "contribution", None) is not None
                             else None),
        idio_contribution=(float(roll.idio)
                           if roll is not None and getattr(roll, "idio", None) is not None
                           else None),
        path=f"{a[:5]}부터 {b[:5]}까지 {direction}했습니다.",
        contributions=contributions,
        nav_gap=None if premium is None else float(premium.premium_move),
        disclosures=_event_lines(lake, event_ids, event_times),
        evidence=tuple(evidence),
        lineage=lineage,
        final_lines=final_lines,
        event_ids=event_ids,
        final_bundle_ids=tuple(final_bundle_ids),
    )


def explain(lake, ticker: str, instrument_id: str, day: str,
            t0: str, t1: str, *, tools: bool = True,
            summary: bool = False) -> str:
    """요청창 하나만 계산해 고정 블록으로 설명한다."""
    del tools  # 호환 인자. 일단위 도구 관측은 요청창 설명에 섞지 않는다.
    return render_block_plan(
        build_block_plan(window_facts(lake, ticker, instrument_id, day, t0, t1)),
        summary=summary,
    )


def main() -> None:
    if len(sys.argv) != 6:
        raise SystemExit(__doc__)
    from .duck import CausalLake
    print(explain(CausalLake(), *sys.argv[1:]))


if __name__ == "__main__":       # pragma: no cover
    main()
