"""근거 묶음 — 주장마다 **무엇에 근거했는지**를 DB 에 남긴다.

## 왜 필요한가

쉬운 설명(토스식)에는 수치가 없다. 수치가 없으면 나중에 그 문장이 통계에서 나온
것인지 기사 서사에서 나온 것인지 구분할 수 없고, 구분할 수 없으면 **서사를 검정
결과처럼 읽는다**. 그래서 주장 단위로 꼬리표를 붙인다:

    {statistical, ev_...}   패널·시행이 받친다. 묶음에 그 검정의 수치가 들어 있다
    {narrative,   ev_...}   기사 서사가 받친다. 묶음에 조회한 뉴스 id 목록이 있다

## 서사 경로는 **통계가 전멸했을 때만** 쓴다

가설이 하나라도 성립하면 서사는 필요 없다 - 검정된 것을 말하면 된다. 전부 기각·
판정불가일 때만 '그럼에도 사람이 납득할 설명' 을 서사로 낸다. 판단은 코드가 한다
(`narrative_allowed`) - 모델이 고르면 편한 쪽(서사)으로 도망간다.

## id 는 코드가 만든다

모델은 **어느 근거를 썼는지만 고른다**(오브젝트셋의 참조키). 묶음 id 는 내용
해시라 재실행에 같다 - 모델이 id 를 지어내면 접지가 무너진다(프로젝트 규약).

## 뉴스 창: 월요일이면 주말까지 거슬러 올라간다

금요일 장 마감 뒤에 나온 뉴스가 월요일 가격을 만든다. 직전 거래일 **다음날부터**
당일까지가 정당한 구간이다(휴장 포함). 재보도(`DUPLICATE_REBROADCAST`)는 새 정보가
없으므로 뺀다 - 같은 이야기가 열 번 실렸다고 열 배 원인이 되지 않는다.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field

BASES = ("statistical", "narrative")


def _plain_num(v):
    """numpy 스칼라·배열을 순수 파이썬으로. **jsonb 에 `np.float64(...)` 문자열이
    들어가면 근거를 다시 읽을 수 없다**(실측: explained 가 그렇게 저장됐다)."""
    if hasattr(v, "item") and not isinstance(v, (str, bytes)):
        try:
            return v.item()
        except Exception:                   # noqa: BLE001
            pass
    if isinstance(v, (list, tuple)):
        return [_plain_num(x) for x in v]
    if isinstance(v, dict):
        return {k: _plain_num(x) for k, x in v.items()}
    return v
DSN_ENV = "EDGE_RDB_DSN"
TABLE = "analysis_evidence_bundle"

_DDL = f"""
CREATE TABLE IF NOT EXISTS public.{TABLE} (
    bundle_id   text        PRIMARY KEY,
    basis       text        NOT NULL CHECK (basis IN ('statistical', 'narrative')),
    cell        text        NOT NULL,
    trade_date  date        NOT NULL,
    layer       text,
    claim       text        NOT NULL,
    news_ids    text[]      NOT NULL DEFAULT '{{}}',
    stats       jsonb       NOT NULL DEFAULT '{{}}'::jsonb,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS {TABLE}_cell_idx
    ON public.{TABLE} (cell, trade_date);
"""


@dataclass(frozen=True, slots=True)
class Bundle:
    """한 주장의 근거. `basis` 에 따라 한쪽만 채워진다 - 섞이면 무엇이 근거인지 흐려진다."""

    basis: str
    cell: str
    trade_date: str
    claim: str
    layer: str = ""
    news_ids: tuple[str, ...] = ()
    stats: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.basis not in BASES:
            raise ValueError(f"basis 는 {BASES} 중 하나다: {self.basis!r}")
        if self.basis == "narrative" and not self.news_ids:
            raise ValueError("서사 근거인데 뉴스 id 가 없다 - 그건 근거가 아니다")
        if self.basis == "statistical" and not self.stats:
            raise ValueError("통계 근거인데 검정 수치가 없다 - 그건 근거가 아니다")

    @property
    def bundle_id(self) -> str:
        """**내용 해시.** 재실행에 같은 id 가 나와야 산출물을 비교할 수 있다."""
        body = json.dumps({"b": self.basis, "c": self.cell, "d": self.trade_date,
                           "l": self.layer, "m": self.claim,
                           "n": sorted(self.news_ids),
                           "s": self.stats}, sort_keys=True, ensure_ascii=False)
        return "ev_" + hashlib.sha1(body.encode("utf-8")).hexdigest()[:16]

    @property
    def tag(self) -> str:
        """주장 뒤에 붙는 꼬리표."""
        return f"{{{self.basis}, {self.bundle_id}}}"


def news_window(lake, day: str) -> str:
    """뉴스 조회 시작일(배타). 직전 거래일 - 그 다음날부터 당일까지가 정당한 구간.

    월요일이면 금요일이 나오므로 주말이 자동으로 포함된다. 요일을 보고 분기하지
    않는다 - 휴장은 주말만이 아니고(설·추석·임시휴장), 달력 규칙을 코드에 박으면
    공휴일마다 틀린다. **거래일 원장이 답을 안다.**
    """
    rows = lake.sql("SELECT max(CAST(ts AS DATE)) FROM bars_5m "
                    f"WHERE CAST(ts AS DATE) < DATE '{day}'")
    return str(rows[0][0]) if rows and rows[0][0] else day


def news_objectset(lake, instrument_id: str, day: str, *, limit: int = 30) -> list[dict]:
    """이 종목의 뉴스 오브젝트셋. **재보도 제외.** 모델은 제목을 옮겨 쓰지 않고
    참조키(`ref`)로 가리킨다 - 그래서 산문의 접지를 코드가 검사할 수 있다.

    스레드 하나에 여러 기사가 붙으면 **가장 이른 것**만 남긴다(첫 보도가 원인이고
    나머지는 반향이다). 시각은 사이드카가 복원한 발행시각이 먼저다.
    """
    from .paneltest import _base
    since = news_window(lake, day)
    promote = _PROMOTE if lake.exists.get("tau_sidecar") else ""
    try:
        # `v_event`·`v_thread` 는 뷰가 아니라 `_base(day)` 가 만드는 CTE 다.
        rows = lake.sql(_base(day) + f"""
            SELECT any_value(doc.source_document_id) AS news_id,
                   any_value(e.title)                AS title,
                   e.event_type_code,
                   any_value(th.thread_key)          AS thread_key,
                   any_value(th.novelty_status)      AS novelty,
                   coalesce(min(sc.published_kst),
                            min(CAST(e.available_at AS TIMESTAMP))) AS t
            FROM v_event e
            LEFT JOIN v_thread th ON th.source_event_id = e.source_event_id
            {promote}
            WHERE e.instrument_id = '{instrument_id}'
              AND e.trade_date > DATE '{since}' AND e.trade_date <= DATE '{day}'
              AND coalesce(th.novelty_status, '') <> 'DUPLICATE_REBROADCAST'
            GROUP BY e.source_event_id, e.event_type_code
            ORDER BY t
        """)
    except Exception as e:                  # noqa: BLE001 - 부재를 사유로 올린다
        return [{"ref": "!오류", "title": f"{type(e).__name__}: {str(e)[:70]}",
                 "news_id": "", "type": "", "thread": "", "t": ""}]
    seen: dict[str, dict] = {}
    for nid, title, etype, thread, _nov, t in rows:
        key = str(thread or nid or title)
        if key in seen:                     # 스레드의 첫 보도만 - 반향은 원인이 아니다
            continue
        seen[key] = {"ref": f"n{len(seen) + 1}", "news_id": str(nid or ""),
                     "title": str(title or "")[:120],
                     "type": str(etype or "").split(".")[-1],
                     "thread": str(thread or ""), "t": str(t or "")[11:16]}
        if len(seen) >= limit:
            break
    return list(seen.values())


_PROMOTE = """
LEFT JOIN rdb.public.event_evidence ev ON ev.source_event_id = e.source_event_id
LEFT JOIN rdb.public.document_assertion da ON da.assertion_id = ev.assertion_id
LEFT JOIN rdb.public.document doc ON doc.document_id = da.document_id
LEFT JOIN tau_sidecar sc ON sc.article_id = doc.source_document_id
"""


# 서사 경로 스위치. **끈 것을 산출물이 말한다** - 조용히 빠지면 '뉴스가 없어서'
# 와 '경로를 껐어서' 를 구분할 수 없다. 되돌리려면 이 한 줄을 True 로.
NARRATIVE_ENABLED = False


def narrative_allowed(*, credible: int, applied_edges: int) -> tuple[bool, str]:
    """서사 경로 허가. **통계가 전멸했을 때만** 허가한다 - 판단은 코드가 한다.

    반환 (허가, 사유). 사유는 산출물에 그대로 실려 왜 서사를 썼는지/안 썼는지 남긴다.
    """
    if not NARRATIVE_ENABLED:
        return False, ("서사 경로 **비활성**(NARRATIVE_ENABLED=False) - 통계 근거만 "
                       "쓴다. 뉴스가 없어서가 아니다")
    if credible > 0:
        return False, (f"검정을 통과한 함의 {credible}건이 있다 - 서사가 필요 없다. "
                       "검정된 것을 말한다")
    if applied_edges > 0:
        return False, (f"오늘 적용된 성립 엣지 {applied_edges}건이 있다 - "
                       "서사가 필요 없다")
    return True, ("가설이 전부 기각·판정불가다 - 서사로 설명하고 "
                  "**모든 주장에 narrative 꼬리표를 붙인다**")


def stat_bundle(cell: str, day: str, claim: str, *, layer: str = "",
                **stats) -> Bundle:
    """통계 근거 묶음. `stats` 에 이 주장에 쓴 **가설의 검정 결과**가 들어간다."""
    return Bundle("statistical", cell, day, claim, layer, (), _plain_num(dict(stats)))


def news_bundle(cell: str, day: str, claim: str, objs: list[dict], refs: list[str],
                *, layer: str = "") -> Bundle:
    """서사 근거 묶음. 참조키 → 뉴스 id 목록. **모델이 고른 것만** 담는다."""
    byref = {o["ref"]: o for o in objs}
    ids = tuple(byref[r]["news_id"] for r in refs
                if r in byref and byref[r]["news_id"])
    if not ids:
        raise ValueError(f"참조 {refs} 가 뉴스 id 로 풀리지 않는다 - 날조 또는 결손")
    return Bundle("narrative", cell, day, claim, layer, ids, {})


def ensure_schema(dsn: str = "") -> str:
    """표를 만든다(멱등). 반환: 빈 문자열 = 성공, 아니면 사유.

    레이크의 `rdb` 는 READ_ONLY 로 붙어 있어 쓸 수 없다 - 쓰기는 별 연결이다.
    """
    dsn = dsn or os.environ.get(DSN_ENV, "")
    if not dsn:
        return f"{DSN_ENV} 없음 - 근거 묶음을 저장할 수 없다"
    try:
        import psycopg
        with psycopg.connect(dsn, connect_timeout=20) as con:
            con.execute(_DDL)
            con.commit()
        return ""
    except Exception as e:                  # noqa: BLE001
        return f"{type(e).__name__}: {str(e)[:90]}"


def save(bundles: list[Bundle], dsn: str = "") -> tuple[int, str]:
    """묶음 적재(멱등 - id 가 내용 해시라 같은 주장은 한 행). 반환 (건수, 사유)."""
    if not bundles:
        return 0, ""
    dsn = dsn or os.environ.get(DSN_ENV, "")
    if not dsn:
        return 0, f"{DSN_ENV} 없음 - 저장 생략 (꼬리표는 산출물에 남는다)"
    try:
        import psycopg
        with psycopg.connect(dsn, connect_timeout=20) as con:
            con.execute(_DDL)
            with con.cursor() as cur:
                cur.executemany(
                    f"INSERT INTO public.{TABLE} "
                    "(bundle_id, basis, cell, trade_date, layer, claim, news_ids, stats)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb) "
                    "ON CONFLICT (bundle_id) DO NOTHING",
                    [(b.bundle_id, b.basis, b.cell, b.trade_date, b.layer, b.claim,
                      list(b.news_ids),
                      json.dumps(b.stats, ensure_ascii=False, default=str))
                     for b in bundles])
            con.commit()
        return len(bundles), ""
    except Exception as e:                  # noqa: BLE001 - 실패를 삼키지 않는다
        return 0, f"{type(e).__name__}: {str(e)[:90]}"


def say_bundles(bundles: list[Bundle]) -> str:
    """산출물 하단의 근거 목록. 꼬리표만 보고 되짚을 수 있어야 한다."""
    if not bundles:
        return ""
    out = ["── 근거 묶음 " + "─" * 46]
    for b in bundles:
        if b.basis == "narrative":
            out.append(f"  {b.bundle_id}  narrative   뉴스 {len(b.news_ids)}건: "
                       + ", ".join(b.news_ids[:4])
                       + (" …" if len(b.news_ids) > 4 else ""))
        else:
            out.append(f"  {b.bundle_id}  statistical "
                       + " · ".join(f"{k}={v}" for k, v in b.stats.items()))
    return "\n".join(out)


def _selfcheck() -> None:
    objs = [{"ref": "n1", "news_id": "NEWS_A", "title": "수주", "type": "SIGNING",
             "thread": "t1", "t": "09:10"},
            {"ref": "n2", "news_id": "NEWS_B", "title": "증설", "type": "CAPACITY",
             "thread": "t2", "t": "13:20"}]
    nb = news_bundle("091160", "2026-07-31", "수주 소식이 있었어요", objs, ["n1", "n2"])
    assert nb.news_ids == ("NEWS_A", "NEWS_B") and nb.basis == "narrative"
    assert nb.bundle_id.startswith("ev_") and len(nb.bundle_id) == 19
    # 재실행 결정론: 같은 내용 → 같은 id
    assert nb.bundle_id == news_bundle(
        "091160", "2026-07-31", "수주 소식이 있었어요", objs, ["n2", "n1"]).bundle_id
    assert "narrative" in nb.tag and nb.bundle_id in nb.tag

    sb = stat_bundle("091160", "2026-07-31", "우선협상 단계가 올렸어요",
                     layer="고유", etype="CONTRACT.SIGNING", stage="PREFERRED_BIDDER",
                     n_pairs=138, att=0.0113, p=0.004)
    assert sb.basis == "statistical" and sb.stats["p"] == 0.004
    assert "statistical" in sb.tag

    # 근거 없는 묶음은 만들 수 없다
    for kw in ({"basis": "narrative"}, {"basis": "statistical"}):
        try:
            Bundle(cell="c", trade_date="d", claim="m", **kw)
        except ValueError:
            continue
        raise AssertionError(f"근거 없는 {kw} 묶음을 통과시켰다")
    try:
        news_bundle("c", "d", "m", objs, ["없는참조"])
    except ValueError:
        pass
    else:
        raise AssertionError("풀리지 않는 참조를 통과시켰다")

    # 서사 경로는 통계가 전멸했을 때만
    assert not narrative_allowed(credible=1, applied_edges=0)[0]
    assert not narrative_allowed(credible=0, applied_edges=2)[0]
    off, why = narrative_allowed(credible=0, applied_edges=0)
    assert not off and "비활성" in why, "끈 것을 사유로 말해야 한다"
    # `python -m` 으로 돌면 이 모듈은 __main__ 이라 `statics.evidence` 를 패치해도
    # 안 먹는다(별 인스턴스). 자기 globals 를 건드린다.
    globals()["NARRATIVE_ENABLED"] = True
    try:
        ok, why2 = narrative_allowed(credible=0, applied_edges=0)
        assert ok and "전부 기각" in why2
    finally:
        globals()["NARRATIVE_ENABLED"] = False

    s = say_bundles([nb, sb])
    assert "NEWS_A" in s and "p=0.004" in s
    print("ok")


if __name__ == "__main__":
    print(ensure_schema() or "schema ok")
    _selfcheck()
