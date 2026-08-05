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

# DSN 은 `duck.rdb_dsn_from_env()` **하나로** 해소한다 - 여기서 env 를 따로 읽으면
# 레이크와 근거 적재가 서로 다른 DB 를 볼 수 있고, 컨테이너엔 EDGE_RDB_DSN 대신
# PG* 여섯 개만 오므로 os.environ 만 보는 코드는 배포에서 조용히 0건이 된다.
TABLE = "analysis_evidence_bundle"

_DDL = f"""
CREATE TABLE IF NOT EXISTS public.{TABLE} (
    bundle_id   text        PRIMARY KEY,
    basis       text        NOT NULL CHECK (basis IN ('statistical', 'narrative')),
    cell        text        NOT NULL,
    trade_date  date        NOT NULL,
    layer       text,
    claim       text        NOT NULL,
    news_ids       text[]      NOT NULL DEFAULT '{{}}',
    thread_ids     text[]      NOT NULL DEFAULT '{{}}',
    stats          jsonb       NOT NULL DEFAULT '{{}}'::jsonb,
    series_lineage jsonb       NOT NULL DEFAULT '{{}}'::jsonb,
    created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS {TABLE}_cell_idx
    ON public.{TABLE} (cell, trade_date);
"""


@dataclass(frozen=True, slots=True)
class Bundle:
    """한 주장의 근거. 통계 사건은 검정값과 원문·사건 흐름 계보를 함께 가질 수 있다."""

    basis: str
    cell: str
    trade_date: str
    claim: str
    layer: str = ""
    news_ids: tuple[str, ...] = ()
    thread_ids: tuple[str, ...] = ()
    stats: dict = field(default_factory=dict)
    series_lineage: dict = field(default_factory=dict)
    # **트리거 시점의 방향**: +1 올림 · 0 무관 · -1 내림. 사용자 태그에는 노출하지
    # 않고 DB에 보존한다. 같은 하루에 역풍과 순풍이 섞여도 검산할 수 있어야 한다.
    sign: int = 0

    def __post_init__(self) -> None:
        if self.basis not in BASES:
            raise ValueError(f"basis 는 {BASES} 중 하나다: {self.basis!r}")
        if self.basis == "narrative" and not self.news_ids:
            raise ValueError("서사 근거인데 뉴스 id 가 없다 - 그건 근거가 아니다")
        if self.basis == "statistical" and not self.stats:
            raise ValueError("통계 근거인데 검정 수치가 없다 - 그건 근거가 아니다")
        if self.sign not in (-1, 0, 1):
            raise ValueError(f"방향은 -1·0·+1 중 하나다: {self.sign!r}")

    @property
    def bundle_id(self) -> str:
        """**내용 해시.** 재실행에 같은 id 가 나와야 산출물을 비교할 수 있다."""
        body = json.dumps({"b": self.basis, "c": self.cell, "d": self.trade_date,
                           "l": self.layer, "m": self.claim,
                           "n": sorted(self.news_ids), "t": sorted(self.thread_ids),
                           "s": self.stats, "x": self.series_lineage, "g": self.sign},
                          sort_keys=True, ensure_ascii=False)
        return "ev_" + hashlib.sha1(body.encode("utf-8")).hexdigest()[:16]

    @property
    def tag(self) -> str:
        """고객 문장에는 근거 조회키만 붙인다. 방향은 번들의 `sign`이 가진다."""
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
    """이 종목의 사건 원문 오브젝트셋. 재보도는 제외한다.

    제목뿐 아니라 역할별 참여자와 사건 흐름의 신규성·현재 단계를 싣는다. 모델은 내부
    코드나 `스레드`라는 말을 노출하지 않고 고객이 바로 이해하는 사실 문장으로 푼다.
    """
    from .paneltest import _base
    since = news_window(lake, day)
    promote = _PROMOTE if lake.exists.get("tau_sidecar") else ""
    news_id = "any_value(doc.source_document_id)" if promote else "''"
    published = ("coalesce(min(sc.published_kst), "
                 "min(CAST(e.available_at AS TIMESTAMP)))" if promote
                 else "min(CAST(e.available_at AS TIMESTAMP))")
    try:
        rows = lake.sql(_base(day) + f""",
            _event_args AS (
                SELECT ea.source_event_id,
                       list(ea.role_code || '=' || coalesce(en.display_name, ea.entity_id)
                            ORDER BY ea.role_code, ea.entity_id) AS arguments
                FROM rdb.public.event_argument ea
                LEFT JOIN rdb.public.entity en ON en.entity_id = ea.entity_id
                GROUP BY ea.source_event_id
            )
            SELECT e.source_event_id,
                   {news_id}                         AS news_id,
                   any_value(e.title)                AS title,
                   e.event_type_code,
                   any_value(th.thread_key)          AS thread_key,
                   any_value(th.novelty_status)      AS novelty,
                   any_value(th.current_stage)       AS current_stage,
                   any_value(a.arguments)            AS arguments,
                   {published}                       AS t
            FROM v_event e
            LEFT JOIN v_thread th ON th.source_event_id = e.source_event_id
            LEFT JOIN _event_args a ON a.source_event_id = e.source_event_id
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
    for event_id, nid, title, etype, thread, novelty, stage, arguments, t in rows:
        key = str(thread or nid or event_id or title)
        if key in seen:                     # 같은 사건 흐름에서는 첫 보도만 쓴다
            continue
        seen[key] = {"ref": f"n{len(seen) + 1}", "event_id": str(event_id or ""),
                     "news_id": str(nid or ""), "title": str(title or "")[:120],
                     "type": str(etype or "").split(".")[-1],
                     "arguments": list(arguments or ()),
                     "thread": str(thread or ""), "novelty": str(novelty or ""),
                     "stage": str(stage or ""), "t": str(t or "")[11:16]}
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
# 와 '경로를 껐어서' 를 구분할 수 없다.
#
# 껐던 이유는 뉴스가 못 미더워서가 아니라 **인용 진위 검사가 없어서**였다: 참조 존재만
# 검사하면 모델이 실재하는 id 를 가리키면서 없는 문장을 지어낼 수 있고, STORM 의 base
# 가 정확히 그 실패로 죽었다. 이제 `plain._quote_guard` 가 인용부호 구간을 기사 제목과
# 대조한다 - 검사와 스위치가 같은 커밋에 들어왔으므로 켠다.
NARRATIVE_ENABLED = True


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


def _news_lineage(objs: list[dict], refs: list[str]) -> tuple[tuple[str, ...],
                                                              tuple[str, ...]]:
    byref = {o["ref"]: o for o in objs}
    ids = tuple(byref[r]["news_id"] for r in refs
                if r in byref and byref[r].get("news_id"))
    threads = tuple(byref[r]["thread"] for r in refs
                    if r in byref and byref[r].get("thread"))
    return ids, threads


def stat_bundle(cell: str, day: str, claim: str, *, layer: str = "", sign: int = 0,
                news: list[dict] | None = None, refs: list[str] | None = None,
                series_lineage: dict | None = None, **stats) -> Bundle:
    """통계 근거. 사건 원문·흐름과 시계열 조회 계약도 같은 묶음에 둔다."""
    ids, threads = _news_lineage(news or [], refs or [])
    return Bundle(basis="statistical", cell=cell, trade_date=day, claim=claim,
                  layer=layer, news_ids=ids, thread_ids=threads,
                  stats=_plain_num(dict(stats)),
                  series_lineage=_plain_num(series_lineage or {}), sign=sign)


def news_bundle(cell: str, day: str, claim: str, objs: list[dict], refs: list[str],
                *, layer: str = "", sign: int = 0) -> Bundle:
    """서사 근거 묶음. 참조키 → 뉴스·사건 흐름 id. **모델이 고른 것만** 담는다."""
    ids, threads = _news_lineage(objs, refs)
    if not ids:
        raise ValueError(f"참조 {refs} 가 뉴스 id 로 풀리지 않는다 - 날조 또는 결손")
    return Bundle(basis="narrative", cell=cell, trade_date=day, claim=claim,
                  layer=layer, news_ids=ids, thread_ids=threads, sign=sign)


def ensure_schema(dsn: str = "") -> str:
    """**로컬 전용** 표 생성(멱등). 반환: 빈 문자열 = 성공, 아니면 사유.

    배포에서는 Flyway 가 이 표를 만든다 - 이 함수는 마이그레이션을 못 돌리는
    로컬 실험용이고, `save()` 는 이것을 부르지 않는다.

    레이크의 `rdb` 는 READ_ONLY 로 붙어 있어 쓸 수 없다 - 쓰기는 별 연결이다.
    """
    if not dsn:
        from .duck import rdb_dsn_from_env
        dsn = rdb_dsn_from_env()
    if not dsn:
        return "EDGE_RDB_DSN 도 PG* 도 없다 - 근거 묶음을 저장할 수 없다"
    try:
        import psycopg
        with psycopg.connect(dsn, connect_timeout=20) as con:
            con.execute(_DDL)
            # **이미 있는 표에는 CREATE 가 아무 일도 하지 않는다.** 실측: `sign` 을 넣은
            # 뒤 로컬 표가 구 DDL 로 만들어져 있어 적재 전량이 `UndefinedColumn` 으로
            # 죽었다(라이브 산출에 사유가 찍혔다). 마이그레이션과 같은 열을 여기서도
            # 더해 준다 - 배포는 Flyway 가 하고, 이건 그걸 못 돌리는 로컬용이다.
            con.execute("ALTER TABLE analysis_evidence_bundle "
                        "ADD COLUMN IF NOT EXISTS sign SMALLINT NOT NULL DEFAULT 0")
            con.execute("ALTER TABLE analysis_evidence_bundle ADD COLUMN IF NOT EXISTS "
                        "thread_ids TEXT[] NOT NULL DEFAULT '{}'")
            con.execute("ALTER TABLE analysis_evidence_bundle ADD COLUMN IF NOT EXISTS "
                        "series_lineage JSONB NOT NULL DEFAULT '{}'::jsonb")
            con.commit()
        return ""
    except Exception as e:                  # noqa: BLE001
        return f"{type(e).__name__}: {str(e)[:90]}"


def save(bundles: list[Bundle], dsn: str = "", *, connect=None) -> tuple[int, int, str]:
    """묶음 적재(멱등 - id 가 내용 해시라 같은 주장은 한 행). 반환 **(적재, 중복, 사유)**.

    **적재와 중복을 가른다.** `ON CONFLICT DO NOTHING` 은 조용해서, 재실행이 0행을
    써도 요청 건수를 그대로 보고하면 '오늘 근거를 남겼다' 와 '이미 다 들어 있어서
    아무것도 안 썼다' 가 산출물에서 똑같이 보인다. 실제로 들어간 행은 rowcount 가 안다.

    `connect` 는 커넥션 주입구다(기본 `psycopg.connect`). 실 DB 없이 적재·중복·실패
    세 경로를 재현할 수 없으면 이 경로는 영영 미검증으로 남는다 - 실제로 그랬다.
    """
    if not bundles:
        return 0, 0, ""
    if not dsn:
        from .duck import rdb_dsn_from_env
        dsn = rdb_dsn_from_env()
    if not dsn:
        return 0, 0, "EDGE_RDB_DSN 도 PG* 도 없다 - 저장 생략 (꼬리표는 산출물에 남는다)"
    try:
        if connect is None:
            import psycopg
            connect = psycopg.connect
        with connect(dsn, connect_timeout=20) as con:
            # **DDL 을 여기서 실행하지 않는다.** 표는 Flyway 가 소유한다
            # (V202608041100__add_analysis_evidence_bundle.sql). 코드가 매 저장마다
            # CREATE TABLE IF NOT EXISTS 를 돌리면 스키마 소유권이 두 곳이 되고,
            # 원장 마이그레이션이 컬럼을 바꿔도 코드의 낡은 DDL 이 조용히 이긴다.
            with con.cursor() as cur:
                cur.executemany(
                    f"INSERT INTO public.{TABLE} "
                    "(bundle_id, basis, cell, trade_date, layer, claim, news_ids,"
                    " thread_ids, stats, series_lineage, sign)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb,"
                    " %s::jsonb, %s) ON CONFLICT (bundle_id) DO NOTHING",
                    [(b.bundle_id, b.basis, b.cell, b.trade_date, b.layer, b.claim,
                      list(b.news_ids), list(b.thread_ids),
                      json.dumps(b.stats, ensure_ascii=False, default=str),
                      json.dumps(b.series_lineage, ensure_ascii=False, default=str),
                      b.sign)
                     for b in bundles])
                # 드라이버가 '모르겠다'(-1) 고 할 때만 요청 건수로 되돌린다 - 그때는
                # 중복을 셀 근거가 없으므로 0건 중복이라고 **주장하지 않는다**.
                got = cur.rowcount
            con.commit()
        saved = got if isinstance(got, int) and got >= 0 else len(bundles)
        return saved, len(bundles) - saved, ""
    except Exception as e:                  # noqa: BLE001 - 실패를 삼키지 않는다
        return 0, 0, f"{type(e).__name__}: {str(e)[:90]}"


def say_save(bundles: list[Bundle], dsn: str = "", *, connect=None) -> str:
    """적재하고 **산문에 붙일 한 줄**을 돌려준다. 쉬운 설명이 만들어진 직후에 부른다.

    근거 적재는 부가 산물이다 - 실패해도 셀 설명을 죽이면 안 된다(설명이 주된 산출물이고
    묶음은 그 설명을 되짚기 위한 것이다). 그렇다고 조용히 넘어가지도 않는다: 사유가
    산문에 없으면 '근거를 남긴 날' 과 '적재가 죽은 날' 이 산출물에서 똑같이 보이고,
    구분할 수 없는 부재가 이 저장소가 오래 싸워온 병이다.
    """
    if not bundles:
        return ""
    try:
        saved, skipped, why = save(bundles, dsn, connect=connect)
    except Exception as e:                  # noqa: BLE001 - 적재가 설명을 죽이지 않는다
        return f"(근거 묶음 미적재 - {type(e).__name__}: {str(e)[:90]})"
    if why:
        return f"(근거 묶음 미적재 - {why})"
    return (f"(근거 묶음 {saved}건 적재"
            + (f" · {skipped}건 중복으로 건너뜀" if skipped else "") + ")")


def say_bundles(bundles: list[Bundle]) -> str:
    """산출물 하단의 근거 목록. 꼬리표만 보고 되짚을 수 있어야 한다."""
    if not bundles:
        return ""
    out = ["── 근거 묶음 " + "─" * 46]
    for b in bundles:
        if b.basis == "narrative":
            out.append(f"  {b.bundle_id}  narrative  {b.sign:+d}  뉴스 {len(b.news_ids)}건: "
                       + ", ".join(b.news_ids[:4])
                       + (" …" if len(b.news_ids) > 4 else ""))
        else:
            out.append(f"  {b.bundle_id}  statistical {b.sign:+d}  "
                       + " · ".join(f"{k}={v}" for k, v in b.stats.items()))
    return "\n".join(out)


def _fake_con(seen: set, *, fail: str = ""):
    """실 DB 없이 `save` 를 검사하는 커넥션 팩토리. **주입구의 참조 구현**이다 -
    자체검사와 pytest 가 같은 것을 쓴다(두 벌을 두면 한 벌만 늙는다).

    `seen` 이 표 역할을 한다: 이미 있는 bundle_id 는 세지 않아 `ON CONFLICT DO NOTHING`
    의 rowcount 를 흉내낸다. `fail` 을 주면 접속에서 죽는다(적재 실패 경로).
    """
    class _Cur:
        rowcount = -1

        def executemany(self, _sql, rows):
            rows = list(rows)
            self.rowcount = sum(1 for r in rows if r[0] not in seen)
            seen.update(r[0] for r in rows)

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    class _Con:
        def cursor(self):
            return _Cur()

        def commit(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    def connect(_dsn, **_kw):
        if fail:
            raise RuntimeError(fail)
        return _Con()
    return connect


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
    # 적재 배선. **실 DB 없이 세 경로를 재현한다** - 이 검사가 없으면 '2건 적재했다'
    # 는 문장이 표가 비어 있어도 나온다(ON CONFLICT DO NOTHING 은 조용하다).
    seen: set = set()
    line = say_save([nb, sb], "dsn=fake", connect=_fake_con(seen))
    assert line == "(근거 묶음 2건 적재)", line
    line = say_save([nb, sb], "dsn=fake", connect=_fake_con(seen))
    assert "0건 적재" in line and "2건 중복" in line, line
    line = say_save([nb], "dsn=fake", connect=_fake_con(set(), fail="접속 거부"))
    assert line.startswith("(근거 묶음 미적재 - RuntimeError"), line
    print("ok")


if __name__ == "__main__":
    print(ensure_schema() or "schema ok")
    _selfcheck()
