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
from .paneltest import FEATURES, Z_ANOM, grid_screen, series_z, split_date
from .vocab import CHANNELS, SERIES_FAMILIES, TRANSFORMS

MAX_ROWS = 40


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

    # ── 관측 ────────────────────────────────────────────────────────────
    def cell(self) -> str:
        """이 셀이 무엇인가 - 좌표·분할 경계·측정 가능한 축."""
        cut = split_date(self.lake, self.day)
        return (f"셀 {self.ticker} {self.day}\n"
                f"  발견/확증 분할 경계: {cut or '이력 부족 - 분할 없음'}\n"
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

    def news(self, limit: int = 5) -> str:
        """사건의 근거 문서 - 제목·도달 시각. 접지의 뿌리(v_event_news→v_news)."""
        rows = self._q(f"""
            SELECT n.title, n.published_at, e.event_type_code
            FROM v_event e
            JOIN v_event_news en ON en.source_event_id = e.source_event_id
            JOIN v_news n ON n.document_id = en.document_id
            WHERE e.instrument_id = '{self.instrument_id}'
              AND e.trade_date = DATE '{self.day}'
            ORDER BY n.published_at LIMIT {min(limit, MAX_ROWS)}""")
        if isinstance(rows, str):
            return rows
        if not rows:
            return "근거 문서 없음: 사건은 있으나 문서 연결이 원장에 없다 (조회 성공)"
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
        # 축의 슬롯을 못 박는다: 라이브에서 에이전트가 이 축을 **취약성**에 넣어
        # 노출은 약한 축을 골랐다(18R). 격자가 재는 것은 용량-반응이고 그건 노출이다.
        return (body + "\n  ↑ 여기 × 오른쪽은 **노출** 후보다 (취약성 슬롯이 아니다). "
                "취약성은 다른 계열족에서 골라라 - 같으면 동어반복이라 거부된다.")

    def series(self) -> str:
        """오늘 계열 혁신 z - 계열 방아쇠의 접지. |z|≥2 인 계열족만 방아쇠 자격."""
        zs = series_z(self.lake, self.instrument_id, self.day)
        if not zs:
            return "계열 z 미계측: 가격계열 결손 - 발화 판정 불가 (부재이지 조용함이 아니다)"
        fired = [f for f, z in zs.items() if abs(z) >= Z_ANOM]
        body = " · ".join(f"{f} z={z:+.2f}" for f, z in sorted(zs.items()))
        return f"  {body}\n  발화(|z|≥{Z_ANOM}): {fired or '없음 - 계열 방아쇠 자격 없음'}"

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

    def vocab(self, part: str = "") -> str:
        """닫힌 어휘. 한 번에 다 주지 않는다 - 물어본 부분만."""
        table = {"채널": sorted(CHANNELS), "계열족": sorted(SERIES_FAMILIES),
                 "변환": sorted(TRANSFORMS)}
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
        """이름으로 부른다. 없는 이름은 **없다고 답한다** (조용한 빈 결과 금지)."""
        fn = getattr(self, name, None)
        if name.startswith("_") or not callable(fn) or name in ("call", "menu_names"):
            return f"그런 도구 없음: {name!r}. 있는 것: {', '.join(self.menu_names())}"
        try:
            out = str(fn(arg) if arg else fn())
        except TypeError:
            out = str(fn())
        except Exception as e:                     # noqa: BLE001
            out = f"오류: {type(e).__name__}: {str(e)[:160]}"
        self.calls.append(f"{name}({arg})")
        trace("tool.call", name=name, arg=arg, out=out[:600])
        return out

    @staticmethod
    def menu_names() -> tuple[str, ...]:
        return ("cell", "events", "news", "thread", "screen", "series", "peers", "vocab")
