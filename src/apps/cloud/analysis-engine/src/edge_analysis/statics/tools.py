"""도구 카탈로그 — 시맨틱 표면에 바인딩된 관측 창구.

**어휘를 프롬프트로 주지 않고 도구로 준다.** STORM 비교 실험의 결론이 근거다:
어휘 전달 방식은 병목이 아니었고 **도구 응답 어포던스**가 병목이었다. base 는
접지 없는 `EVT_KR_…` 를 지어냈고(도구가 없어서), dyn2 는 키 오조회를 '사건 없음'
으로 믿었다(응답이 부재와 오류를 구분 안 해서). 그래서 이 카탈로그의 규율은 셋:

1. **모든 답은 레이크(S3+RDB 조인 객체)의 뷰에서 나온다.** 기반 테이블 직접 접근
   금지 - 시점 클램프가 뷰 안에 있어야 우회가 불가능하다.
2. **부재와 오류를 구분해 말한다.** "그런 사건 없음"과 "키가 틀렸다"는 다른 문장이다.
3. **모든 호출이 원장에 남는다.** 시도 전량이 남아야 '무엇을 물으려다 막혔는지'가
   표면의 결함 목록이 된다 (거부된 질의와 안 던진 질의가 같은 모양이면 안 된다).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..observability import record as trace
from .paneltest import (FEATURES, Z_ANOM, flow_z, grid_screen, macro_z,
                        series_z)
from .vocab import CHANNELS, RELATIONS, SERIES_FAMILIES, TRANSFORMS

MAX_ROWS = 40

# 도구 → 그 도구가 읽는 표. 도달 지평이 셀보다 늦으면 **부르기 전에** 안다 (19R).
# 실측: 06-01 셀에서 35표 중 15표가 미도달인데 도구는 그걸 '없음'이라 답했다.
TOOL_TABLES: dict[str, tuple[str, ...]] = {
    "news": ("document",),
    "thread": ("event_thread_link",),
    "peers": ("instrument_classification",),
    "screen": ("price_daily", "source_event"),
    "series": ("price_daily",),
    "links": ("event_argument", "source_event"),
    "args": ("event_argument", "source_event"),
    "flows": ("price_daily",),   # 수급은 S3 canonical - RDB 도달성과 무관
}


@dataclass
class Catalog:
    """한 셀에 묶인 도구 묶음. 셀 좌표는 생성자가 잡고 **도구 인자로 받지 않는다** -
    모델이 셀을 바꿔 가며 표본을 찾아다니는 것이 표본 선택이기 때문이다(§17 계약)."""

    lake: object
    ticker: str
    instrument_id: str
    day: str
    types: tuple[str, ...] = ()
    calls: list[str] = field(default_factory=list)
    cache: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """뷰를 이 셀 시점으로 걸고 도달성을 잰다 - **한 번만**, 왕복 1회."""
        bind = getattr(self.lake, "bind_day", None)
        if bind:
            bind(self.day)
            self.lake.probe_day()

    # ── 도달성 ──────────────────────────────────────────────────────────
    def reach(self, name: str) -> str:
        """이 도구가 이 셀에서 답할 수 있나. 못 하면 **사유와 지평**을 준다.

        '없음'과 '아직 못 닿음'은 다른 문장이다 - 전자는 세상의 사실이고 후자는
        우리 적재의 한계다. 섞으면 '뉴스 없는 날' 같은 거짓 사실이 생긴다.
        """
        eff = getattr(self.lake, "effective", None) or {}
        for t in TOOL_TABLES.get(name, ()):
            n, h = eff.get(t, (None, None))
            if n == 0 and h and h[:10] > self.day:
                return f"미도달: {t} 은 {h[:10]} 부터 적재됐다 (이 셀보다 늦다 - 부재가 아니다)"
            if n == 0:
                return f"빈 표: {t} 이 이 셀 시점에 0행이다"
        return ""

    def coverage(self) -> str:
        """이 셀에서 무엇에 닿을 수 있나 - 바인딩율과 도달 지평."""
        cov = getattr(self.lake, "coverage", None)
        if not cov:
            return "커버리지 미상: 레이크가 보고를 안 한다"
        body = cov(effective=True)
        blocked = [f"{k}({self.reach(k).split(':')[0]})" for k in sorted(TOOL_TABLES)
                   if self.reach(k)]
        return body + ("\n  이 셀에서 막힌 도구: " + ", ".join(blocked) if blocked else "")

    def tables(self, like: str = "") -> str:
        """묶인 표 전량 - 이름·그날 행수. 여기 있는 것은 전부 peek 할 수 있다."""
        eff = getattr(self.lake, "effective", None) or {}
        rows = [(t, n) for t, (n, _) in sorted(eff.items()) if not like or like in t]
        s3 = sorted(k for k in (getattr(self.lake, "s3", None) or {})
                    if not like or like in k)
        if not rows and not s3:
            return f"묶인 표 없음{f' ({like!r} 에 맞는)' if like else ''}"
        out = [f"RDB 뷰 {len(rows)}개 (그날 행수):"]
        out += [f"  v_{t:<34} {n:>9,}" for t, n in rows]
        if s3:
            out.append(f"S3 데이터셋 {len(s3)}개 (행수는 peek 에서 - 원격이라 셀 때 비용):")
            out += [f"  {k}" for k in s3]
        return "\n".join(out)

    def peek(self, name: str) -> str:
        """표 하나의 열과 표본 3행. **탐색의 종점** - 어떤 표든 여기로 볼 수 있다.

        S3 데이터셋(`s3_…`)도 같은 창구로 본다 - 빈 것은 행 0 과 **열 목록**을
        돌려준다(스키마만 있는 축이라는 사실 자체가 답이다).
        """
        n = name.strip()
        if n.startswith("s3_"):
            return self._peek_s3(n)
        t = n.removeprefix("v_")
        cols = (getattr(self.lake, "cols", None) or {}).get(t)
        if not cols:
            return (f"그런 표 없음: {name!r}. tables() 로 목록을 봐라")
        eff = (getattr(self.lake, "effective", None) or {}).get(t, (None, None))
        head = f"v_{t} — 열 {len(cols)}: {', '.join(cols[:12])}\n  그날 행수 {eff[0]}"
        if eff[0] == 0:
            return head + f"\n  {self.reach_table(t)}"
        rows = self._q(f"SELECT * FROM v_{t} LIMIT 3")
        if isinstance(rows, str):
            return head + "\n  " + rows
        return head + "\n" + "\n".join(f"  {str(r)[:150]}" for r in rows)

    def _peek_s3(self, name: str) -> str:
        """S3 뷰 하나. 지연 바인딩이면 여기서 건다 (첫 조회 비용은 한 번만)."""
        lake = self.lake
        if name not in (getattr(lake, "s3", None) or {}):
            return (f"그런 S3 데이터셋 없음: {name!r}. 있는 것: "
                    + ", ".join(sorted(getattr(lake, "s3", None) or {})))
        if (why := getattr(lake, "bind_s3", lambda _n: "")(name)):
            return f"바인딩 실패: {why}"
        try:
            cols = [r[0] for r in lake.sql(f"DESCRIBE {name}")]
            n = lake.sql(f"SELECT count(*) FROM {name}")[0][0]
        except Exception as e:                     # noqa: BLE001
            return f"오류: {type(e).__name__}: {str(e)[:160]}"
        head = f"{name} ({lake.s3[name]}) — {n:,}행 · 열 {len(cols)}: {', '.join(cols[:14])}"
        if not n:
            return head + "\n  **스키마만 있다** - 아직 안 채워진 축이다 (적재 일감이지 설계 한계가 아니다)"
        rows = lake.sql(f"SELECT * FROM {name} LIMIT 2")
        return head + "\n" + "\n".join(f"  {str(r)[:160]}" for r in rows)

    def reach_table(self, t: str) -> str:
        eff = (getattr(self.lake, "effective", None) or {}).get(t, (None, None))
        h = eff[1]
        return (f"미도달: {h[:10]} 부터 적재됐다 (이 셀보다 늦다)"
                if h and h[:10] > self.day else "그날 0행 (진짜 부재)")

    # ── 관측 ────────────────────────────────────────────────────────────
    def cell(self) -> str:
        """이 셀이 무엇인가 - 좌표·측정 가능한 축."""
        return (f"셀 {self.ticker} {self.day}\n"
                f"  장중 접지 사건 타입: {', '.join(self.types) or '없음'}\n"
                f"  잴 수 있는 노출: {sorted(FEATURES)}")

    def events(self, type_like: str = "") -> str:
        """이 셀 종목의 장중 사건. 부재는 부재라고 답한다 (오류와 구분)."""
        hit = [t for t in self.types if type_like in t] if type_like else list(self.types)
        if not self.types:
            return "사건 없음: 이 셀에 장중 접지 사건이 하나도 없다 (조회는 성공했다)"
        if not hit:
            return (f"사건 없음: {type_like!r} 에 맞는 타입이 이 셀에 없다. "
                    f"있는 것: {', '.join(self.types)}")
        return "이 셀의 사건 타입:\n" + "\n".join(f"  {t}" for t in hit)

    def news(self, kind: str = "") -> str:
        """사건의 근거 문서 - 제목·도달 시각. 접지의 뿌리(v_event_news→v_news).

        인자는 **사건 타입 일부**다 (19R 실측: 에이전트가 타입을 넘겼는데 시그니처가
        limit 이라 조용히 버려지고 엉뚱한 기사가 돌아갔다 - 침묵하는 무시는 오답보다
        나쁘다). 안 주면 이 셀 전체.
        """
        flt = f"AND e.event_type_code LIKE '%{kind}%'" if kind else ""
        rows = self._q(f"""
            SELECT n.title, n.published_at, e.event_type_code
            FROM v_event e
            JOIN v_event_news en ON en.source_event_id = e.source_event_id
            JOIN v_news n ON n.document_id = en.document_id
            WHERE e.instrument_id = '{self.instrument_id}'
              AND e.trade_date = DATE '{self.day}' {flt}
            ORDER BY n.published_at LIMIT 6""")
        if isinstance(rows, str):
            return rows
        if not rows:
            return (f"근거 문서 없음: {kind!r} 에 걸리는 문서가 이 셀에 없다 (조회 성공)"
                    if kind else "근거 문서 없음: 사건은 있으나 문서 연결이 원장에 없다")
        return "\n".join(f"  [{r[2][:38]}] {str(r[1])[:16]} {str(r[0])[:60]}" for r in rows)

    def thread(self) -> str:
        """스레드 - 이 사건이 신규인가 후속인가. 서사 단계의 유일한 실측 축."""
        rows = self._q(f"""
            SELECT t.event_type_code, t.current_stage, t.novelty_status, count(*)
            FROM v_event e JOIN v_thread t ON t.source_event_id = e.source_event_id
            WHERE e.instrument_id = '{self.instrument_id}'
              AND e.trade_date = DATE '{self.day}'
            GROUP BY 1,2,3 ORDER BY 4 DESC LIMIT {MAX_ROWS}""")
        if isinstance(rows, str):
            return rows
        if not rows:
            return "스레드 없음: 이 셀 사건이 스레드에 안 묶여 있다 (조회 성공)"
        return "\n".join(f"  {r[0][:34]} 단계={r[1]} 신규성={r[2]} ×{r[3]}" for r in rows)

    def screen(self) -> str:
        """발견 표본 격자 - 타입 × 노출 전수. **탐색이지 확증이 아니다.**"""
        hits = [s for s in grid_screen(self.lake, self.day, list(self.types)) if "p2" in s]
        if not hits:
            return "격자 결과 없음: 발견 표본이 얇다 (확증은 다른 기간에서 한다)"
        body = "\n".join(f"  {s['type'][:38]} × {s['exposure']} n={s['n']} "
                         f"p₂={s['p2']:.3f} 방향{s['direction']}" for s in hits[:8])
        # 축의 슬롯을 못 박는다: 라이브에서 에이전트가 이 축을 **조건**에 넣어
        # 노출은 약한 축을 골랐다(18R). 격자가 재는 것은 용량-반응이고 그건 노출이다.
        return (body + "\n  ↑ 여기 × 오른쪽은 **노출** 후보다 (조건 슬롯이 아니다). "
                "조건은 다른 계열족에서 골라라 - 같으면 동어반복이라 거부된다.")

    def series(self) -> str:
        """오늘 계열 혁신 z - 계열 방아쇠의 접지. |z|≥2 인 계열족만 방아쇠 자격.

        **무엇이 움직였는지 같이 낸다** (20R): 거시·수급은 계열족 하나에 여러 계열이
        묶여 있어 z 만 주면 '거시가 튀었다'가 되고, 그건 검정 불가능한 문장이다.
        """
        zs = series_z(self.lake, self.instrument_id, self.day)
        if not zs:
            return "계열 z 미계측: 가격계열 결손 - 발화 판정 불가 (부재이지 조용함이 아니다)"
        fired = [f for f, z in zs.items() if abs(z) >= Z_ANOM]
        out = ["  " + " · ".join(f"{f} z={z:+.2f}" for f, z in sorted(zs.items())),
               f"  발화(|z|≥{Z_ANOM}): {fired or '없음 - 계열 방아쇠 자격 없음'}"]
        for fam, fn in (("거시", macro_z), ("수급", flow_z)):
            if fam not in zs:
                continue
            note = fn(self.lake, self.day) if fam == "거시" else fn(
                self.lake, self.instrument_id, self.day)
            if note[1]:
                out.append(f"  {fam} 내역: {note[1]}")
        return "\n".join(out)

    def flows(self) -> str:
        """이 종목의 **전일** 투자자별 순매수 - 수급 방아쇠·조건의 접지.

        전일인 이유: 투자자 집계는 장 마감 후 18:00 공표라 오늘 장중의 원인으로
        인용할 수 없다(동시발생이지 원인이 아니다).
        """
        rows = self._q(f"""
            SELECT iv.trade_date, iv.investor_type, iv.net_value
            FROM s3_investor_value iv
            JOIN v_instrument i ON i.ticker = iv.ticker
            WHERE i.instrument_id = '{self.instrument_id}'
              AND iv.trade_date < DATE '{self.day}'
              AND iv.trade_date >= DATE '{self.day}' - INTERVAL 7 DAY
              AND iv.investor_type IN ('foreign', 'institution_total', 'individual', 'pension')
            ORDER BY iv.trade_date DESC, abs(iv.net_value) DESC LIMIT 12""")
        if isinstance(rows, str):
            return rows
        if not rows:
            return "수급 없음: 이 종목의 전일 투자자별 집계가 원장에 없다 (조회 성공)"
        return "전일까지 투자자별 순매수 (억원):\n" + "\n".join(
            f"  {r[0]} {r[1]:<18} {r[2] / 1e8:>+12,.0f}" for r in rows)

    def peers(self, how: str = "industry") -> str:
        """같은 산업 피어. 관계 노출·위약군의 재료."""
        if how != "industry":
            return f"how={how!r} 는 아직 없다. 있는 것: 'industry'"
        rows = self._q(f"""
            SELECT count(*), max(i.industry_name)
            FROM v_instrument i
            WHERE i.industry_name = (SELECT industry_name FROM v_instrument
                                     WHERE instrument_id = '{self.instrument_id}')""")
        if isinstance(rows, str):
            return rows
        n, name = (rows[0] if rows else (0, None))
        return (f"  산업 {name or '미분류'} 피어 {n}종목"
                if n else "  산업 분류 없음 - 관계 노출 불가")

    def links(self, kind: str = "") -> str:
        """이 종목의 **타입 있는 1홉** 상대 - 경로형 가설의 접지 (19R).

        객체 타입 사이의 관계다: 산업 동일성은 속성이지 관계가 아니다. 여기 안
        보이는 상대는 오늘 이 종목과 안 엮여 있다 - 지어내면 검정에서 죽는다.
        """
        where = f"AND l.link_type = '{kind}'" if kind else "AND l.link_type IS NOT NULL"
        rows = self._q(f"""
            SELECT l.link_type, i.ticker, max(i.instrument_id), count(*) n
            FROM v_link l
            JOIN v_instrument i ON i.instrument_id =
                 CASE WHEN l.src = '{self.instrument_id}' THEN l.dst ELSE l.src END
            WHERE ('{self.instrument_id}' IN (l.src, l.dst)) {where}
            GROUP BY 1,2 ORDER BY 4 DESC LIMIT {MAX_ROWS}""")
        if isinstance(rows, str):
            return rows
        if not rows:
            return (f"링크 없음: 이 종목은 {kind or '어떤 타입으로도'} 엮인 상대가 "
                    f"원장에 없다 (조회 성공). 관계 노출 가설은 접지가 없다")
        return "타입 있는 1홉 상대:\n" + "\n".join(
            f"  [{r[0]}] {r[1]} ×{r[3]}" for r in rows)

    def args(self, type_like: str = "") -> str:
        """사건의 **아규먼트** - 이 종목이 어떤 역할·서술어·단계·신규성으로 등장했나.

        타입만 보면 거의 아무것도 안 말해준다 - 정보는 안에 있다. 그런데 방아쇠 문법은
        이미 역할·서술어·단계·신규성으로 처치를 좁힐 수 있게 열려 있었고(`refine_sql`),
        **그 값이 실제로 뭐가 있는지 볼 창구만 없었다**(21R). 어휘는 열려 있고 관측이
        막혀 있으면 모델은 추측으로 좁히고, 좁힌 결과는 표본 0 이다.

        분포는 **이력 전체**(PIT 절단 안)이고 `오늘` 열이 이 셀의 건수다 - 오늘 0 이면
        그 값으로 방아쇠를 좁혀도 이 셀에는 처치가 없다.
        """
        flt = f"AND e.event_type_code LIKE '%{type_like}%'" if type_like else ""
        rows = self._q(f"""
            SELECT e.event_type_code, e.role_code, e.predicate_code,
                   e.lifecycle_stage, e.novelty_status, count(*) n,
                   sum(CASE WHEN e.trade_date = DATE '{self.day}' THEN 1 ELSE 0 END) today_n
            FROM v_event e
            WHERE e.instrument_id = '{self.instrument_id}' {flt}
            GROUP BY 1,2,3,4,5 ORDER BY 6 DESC LIMIT {MAX_ROWS}""")
        if isinstance(rows, str):
            return rows
        if not rows:
            return (f"아규먼트 없음: 이 종목은 {type_like or '어떤 타입으로도'} 사건의 "
                    f"인자로 원장에 없다 (조회 성공)")
        return "역할·서술어·단계·신규성 분포 (이력 / 오늘):\n" + "\n".join(
            f"  [{r[1] or '역할없음'}] {r[0]} · {r[2] or '서술어없음'} · "
            f"{r[3] or '단계없음'} · {r[4] or '신규성없음'}  ×{r[5]}"
            + (f"  **오늘 {r[6]}**" if r[6] else "  오늘 0")
            for r in rows)

    def vocab(self, part: str = "") -> str:
        """닫힌 어휘. 한 번에 다 주지 않는다 - 물어본 부분만."""
        table = {"채널": sorted(CHANNELS), "계열족": sorted(SERIES_FAMILIES),
                 "변환": sorted(TRANSFORMS), "관계": sorted(RELATIONS)}
        if part in table:
            return f"{part} {len(table[part])}: {table[part]}"
        return ("어휘 부분을 골라라: " + " · ".join(f"{k}({len(v)})" for k, v in table.items())
                + f"\n  측정 가능한 (계열족,변환) 조합: {sorted(FEATURES)}")

    # ── 내부 ────────────────────────────────────────────────────────────
    def _q(self, sql: str):
        """뷰 위에서만 도는 질의. 오류는 문자열로 - **실패도 관측이다**."""
        from .paneltest import _base
        try:
            return self.lake.sql(_base(self.day, "23:59:59") + sql)
        except Exception as e:                     # noqa: BLE001 - 되먹임 대상
            return f"오류: {type(e).__name__}: {str(e)[:160]}"

    def call(self, name: str, arg: str = "") -> str:
        """이름으로 부른다. 없는 이름은 **없다고 답한다** (조용한 빈 결과 금지).

        같은 호출을 두 번 하면 캐시로 돌려주고 그 사실을 말한다 - 실측에서 모델이
        같은 도구를 반복해 턴을 태웠다. 못 닿는 도구는 **질의 전에** 사유로 막는다.
        """
        fn = getattr(self, name, None)
        if name.startswith("_") or not callable(fn) or name in ("call", "menu_names", "reach"):
            return f"그런 도구 없음: {name!r}. 있는 것: {', '.join(self.menu_names())}"
        key = f"{name}({arg})"
        if key in self.cache:
            return f"[이미 본 것 - 다른 도구를 써라]\n{self.cache[key]}"
        if (why := self.reach(name)):
            out = why
        else:
            try:
                out = str(fn(arg) if arg else fn())
            except TypeError:
                out = str(fn())
            except Exception as e:                 # noqa: BLE001
                out = f"오류: {type(e).__name__}: {str(e)[:160]}"
        self.calls.append(key)
        self.cache[key] = out
        trace("tool.call", name=name, arg=arg, out=out[:600])
        return out

    @staticmethod
    def menu_names() -> tuple[str, ...]:
        return ("cell", "coverage", "tables", "peek", "events", "news", "thread",
                "screen", "series", "peers", "links", "flows", "vocab")
