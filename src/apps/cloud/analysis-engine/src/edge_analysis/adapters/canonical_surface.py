"""canonical(S3) 자유 SELECT 표면 — **온톨로지 21표를 시점 안전하게 연다.**

## 왜 또 하나의 표면인가

`sql_surface` 는 Postgres Cloud Event Store 를 연다 — 가격·사건·수급·보유. 그것으로는
"이 종목이 왜 움직였나"의 절반밖에 못 묻는다. 재무제표·컨센서스·주주 지분·계열 관계·
임원·이사회·신용등급은 canonical 레이크에 있고, 지금까지 인과 에이전트는 **그것이
존재하는지도 몰랐다.** 후보 공간이 공시·뉴스로 쏠린 구조적 원인 중 하나다.

## 왜 Cube 가 아닌가 (중요)

Cube 는 `*_latest` 를 쓴다. 그 뷰에는 **시점 창이 없다.** 2026-07-16 을 설명하면서 그 뒤에
정정된 재무, 수정된 컨센서스, 바뀐 주주 구성을 보게 된다 - 에러가 나지 않고 **조용히
미래를 본다.** 인과 귀속에서 이보다 나쁜 실패는 없다.

그래서 여기는 `as_of_sql` 쪽을 쓴다. 클램프(`WHERE available_at <= DATE ...`)가 **뷰
정의 안에** 있으므로 질의가 우회할 수 없다 - `sql_surface` 와 같은 규율이다. 모델이
PIT 를 잊어도 미래는 애초에 안 보인다.

## 왜 매니페스트를 읽는가

`as_of_sql` 은 data-pipeline 에 있다. 여기서 import 하면 psycopg3·lxml·html5lib 가 딸려
오고(psycopg2 를 이미 쓰므로 드라이버가 둘이 된다), 다시 구현하면 정정 처리 로직이 두
벌이 되어 한 벌이 낡는다. 그래서 저쪽이 SQL 을 **생성해 파일로 내보내고** 여기는 시점만
채운다. 로직은 한 곳에 살고, 경계는 파일이며, `pit --check` 가 표류를 막는다.
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from ..config import PipelineError
from ..observability import log

MAX_ROWS = 500
# canonical 전량이 22MB 다. 스캔 비용은 사실상 0 이지만 폭주한 질의가 시간을 태우는 것은
# 막는다 - Athena 는 취소해도 스캔한 만큼 과금한다.
SCAN_LIMIT_BYTES = 512 * 1024 * 1024

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_BANNED = re.compile(
    r"(--|/\*|\*/)"
    r"|\b(insert|update|delete|drop|create|alter|truncate|grant|revoke|"
    r"call|execute|prepare|deallocate|msck|unload)\b",
    re.I,
)


class CanonicalSurface:
    """읽기 전용 자유 질의. **시점·행수·문장 종류를 코드가 정한다.**

    `runner(sql) -> list[dict]` 하나만 받는다. Athena 든 테스트 스텁이든 상관없고, 이
    클래스는 **무엇을 보여줄지와 무엇을 막을지**만 안다.
    """

    def __init__(self, runner, manifest: dict[str, Any], *, as_of: str | date,
                 ledger=None) -> None:
        day = as_of.isoformat()[:10] if isinstance(as_of, date) else str(as_of)[:10]
        if not _DATE.match(day):
            # 토큰을 문자열 치환으로 넣으므로 여기가 유일한 방어선이다. 날짜가 아니면
            # 그대로 SQL 에 박힌다 - 주입 표면이다.
            raise PipelineError(f"as_of 가 YYYY-MM-DD 가 아니다: {as_of!r}")
        self._runner = runner
        self._m = manifest
        self._as_of = day
        self._token = str(manifest.get("as_of_token") or "__AS_OF__")
        self._views = {t["name"]: t for t in manifest.get("tables") or ()}
        if not self._views:
            raise PipelineError("PIT 매니페스트에 테이블이 없다")
        self.ledger = ledger if ledger is not None else _Ledger()

    # ── 표면 설명 ────────────────────────────────────────────────────────
    def schema(self) -> str:
        """프롬프트에 실리는 표면 설명. **매니페스트에서 생성한다 - 손으로 안 쓴다.**

        손으로 쓰면 SSOT 가 둘이 되고, 컬럼이 바뀌었는데 설명이 안 바뀌면 모델이 없는
        컬럼을 부른다. 그리고 그건 "자료가 없다"로 잘못 읽힌다.
        """
        L = [f"canonical 표면 (Athena/S3, PostgreSQL 방언). **SELECT 하나만.**",
             f"모든 뷰는 {self._as_of} 시점으로 이미 잘려 있다 - PIT 절을 쓸 필요가 없고,",
             "정정된 값은 그 정정이 공개된 뒤의 시점에서만 보인다.", ""]
        for t in self._m["tables"]:
            cols = " · ".join(c["name"] for c in t["columns"])
            L.append(f"  {t['name']}")
            if t.get("note"):
                L.append(f"      {t['note']}")
            L.append(f"      {cols}")
        objs = self._m.get("objects") or []
        if objs:
            L += ["", "실체 - 무엇을 무엇으로 부르는가"]
            for o in objs:
                alt = (" · 다른 이름: " + ", ".join(o["alt_keys"])) if o.get("alt_keys") else ""
                L.append(f"  {o['kind']:<16} {o['view']}.{o['key']}{alt}")
        links = self._m.get("links") or []
        if links:
            L += ["", "관계 - **정체 키로 조인하지 마라.** 간선의 끝 컬럼은 따로다"]
            for r in links:
                f, t2 = r["from"], r["to"]
                mark = "" if r.get("resolved", True) else "  [미해소 - 문자열만 있다]"
                L.append(f"  {r['role']:<14} {r['edge']}")
                L.append(f"      {f['column']} -> {f['view']}.{f['join_on']}"
                         f"   {t2['column']} -> {t2['view']}.{t2['join_on']}{mark}")
        unbound = self._m.get("unbound") or []
        if unbound:
            L += ["", "어휘에는 있으나 **테이블이 아직 없는 것** - 물어봐야 소용없다"]
            L += [f"  {u['kind']}: {u['why']}" for u in unbound]
        L += ["", f"결과는 최대 {MAX_ROWS}행. 큰 질의는 집계로 줄여라."]
        return "\n".join(L)

    # ── 질의 ─────────────────────────────────────────────────────────────
    def query(self, sql: str, *, limit: int = MAX_ROWS) -> list[dict[str, Any]]:
        """SELECT 하나. **거부도 원장에 남는다** - 거부된 질의와 안 던진 질의는 다르다.

        가드 거부를 기록 밖에 두면 P8 커버리지 원장이 근거를 잃는다 - "그 영역을 안
        물어봤다"와 "물어봤는데 표면이 막았다"가 같은 모양(부재)이 된다. `sql_surface`
        가 이미 그렇게 하고 있고, 두 표면의 원장 계약이 갈리면 안 된다.
        """
        try:
            inner = self._guard(sql)
        except PipelineError as exc:
            self.ledger.record(sql, 0, f"거부: {exc}"[:300])
            raise
        n = max(1, min(int(limit), MAX_ROWS))
        full = f"WITH {self._ctes()}\nSELECT * FROM (\n{inner}\n) _q LIMIT {n}"
        try:
            rows = self._runner(full)
        except Exception as exc:  # noqa: BLE001 - 실패 사유가 모델에게 가야 한다
            self.ledger.record(sql, 0, f"{type(exc).__name__}: {exc}"[:300])
            raise PipelineError(f"질의 실패: {type(exc).__name__}: {exc}"[:300]) from exc
        self.ledger.record(sql, len(rows))
        return rows

    def ask(self, sql: str, *, show: int = 20) -> str:
        """모델에게 돌려줄 문자열. 예외를 문장으로 바꾼다 - 한 번 틀렸다고 세션이 죽지 않는다."""
        try:
            rows = self.query(sql)
        except PipelineError as exc:
            return f"오류: {exc}"
        if not rows:
            return "0행. 없다는 것도 관측이다 - 부재를 보이는 검정이면 count(*) 로 감싸라."
        head = list(rows[0].keys())
        out = [" | ".join(head)]
        out += [" | ".join(_cell(r.get(c)) for c in head) for r in rows[:show]]
        if len(rows) > show:
            out.append(f"... {len(rows) - show}행 더")
        return "\n".join(out)

    # ── 내부 ─────────────────────────────────────────────────────────────
    def _ctes(self) -> str:
        """시점으로 잘린 뷰 전량. **클램프가 여기 있으므로 질의가 우회할 수 없다.**"""
        return ",\n".join(
            f"{name} AS (\n{t['pit_sql'].replace(self._token, self._as_of)}\n)"
            for name, t in self._views.items())

    def _guard(self, q: str) -> str:
        s = (q or "").strip().rstrip(";").strip()
        if not s:
            raise PipelineError("빈 질의다.")
        if ";" in s:
            raise PipelineError("문장은 하나만. 세미콜론으로 이어 붙일 수 없다.")
        if not re.match(r"(?is)^\s*select\b", s):
            raise PipelineError("SELECT 로 시작해야 한다. CTE 가 필요하면 서브쿼리를 써라.")
        hit = _BANNED.search(s)
        if hit:
            raise PipelineError(f"쓸 수 없는 토큰: {hit.group()!r}. 읽기 전용 SELECT 만 받는다.")
        # 기반 테이블 직접 접근 금지. **클램프된 뷰를 우회하는 유일한 경로다** -
        # `financial_metric` 을 그냥 부르면 정정본까지 다 보인다.
        #
        # 조건은 `if base:` 하나다. `_` 가 단어문자라 `\b(financial_metric)\b` 는
        # `v_financial_metric` **안쪽을 애초에 매치하지 못한다** - 뷰 허용은 정규식이
        # 이미 보장한다. 여기에 "뷰 이름이 앞에 있으면 통과" 같은 조건을 덧대면 가드를
        # 끄는 스위치가 된다: `FROM v_financial_metric a JOIN financial_metric b` 가
        # 통과하고, 조인된 원본 쪽이 정정본(미래)까지 본다. 실측으로 걸렸다.
        bare = sorted(t["table"] for t in self._m["tables"])
        base = re.search(r"\b(" + "|".join(map(re.escape, bare)) + r")\b", s, re.I)
        if base:
            raise PipelineError(
                f"기반 테이블 {base.group(1)!r} 직접 접근 금지 - 시점 클램프를 우회한다. "
                f"v_{base.group(1)} 를 써라.")
        return s


class _Ledger:
    """던진 질의 전량. **보고된 하나가 아니라 시도 전부가 남는다.**"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def record(self, query: str, rows: int, error: str = "") -> None:
        self.calls.append({"query": query.strip()[:2000], "rows": rows, "error": error})

    @property
    def queries(self) -> list[str]:
        return [c["query"] for c in self.calls]


def load_manifest(path: str | Path) -> dict[str, Any]:
    """생성 매니페스트를 읽는다. **없으면 조용히 비우지 않는다.**"""
    p = Path(path)
    if not p.exists():
        raise PipelineError(
            f"PIT 매니페스트가 없다: {p}. "
            "data-pipeline 에서 `py -m data_pipeline.semantic.pit --out <경로>` 로 생성한다")
    m = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(m, dict) or not m.get("tables"):
        raise PipelineError(f"PIT 매니페스트가 비었거나 모양이 다르다: {p}")
    return m


def athena_runner(*, database: str, output: str, profile: str = "",
                  region: str = "ap-northeast-2", timeout: int = 120):
    """Athena 실행기. boto3 는 **지연 import** 한다 - 이 경로를 안 타는 런이 많다."""
    import time

    import boto3

    session = boto3.Session(profile_name=profile or None, region_name=region)
    client = session.client("athena")

    def run(sql: str) -> list[dict[str, Any]]:
        qid = client.start_query_execution(
            QueryString=sql,
            QueryExecutionContext={"Database": database},
            ResultConfiguration={"OutputLocation": output})["QueryExecutionId"]
        deadline = time.time() + timeout
        while True:
            got = client.get_query_execution(QueryExecutionId=qid)["QueryExecution"]
            state = got["Status"]["State"]
            if state in ("SUCCEEDED", "FAILED", "CANCELLED"):
                break
            if time.time() > deadline:
                client.stop_query_execution(QueryExecutionId=qid)
                raise PipelineError(f"Athena 시간 초과 ({timeout}s): {qid}")
            time.sleep(0.4)
        scanned = got.get("Statistics", {}).get("DataScannedInBytes", 0)
        log("canonical.athena", qid=qid, state=state, scanned=scanned)
        if state != "SUCCEEDED":
            raise PipelineError(
                f"Athena {state}: {got['Status'].get('StateChangeReason', '')}"[:300])
        if scanned > SCAN_LIMIT_BYTES:
            log("canonical.scan_large", qid=qid, scanned=scanned)
        pages = client.get_paginator("get_query_results").paginate(QueryExecutionId=qid)
        out: list[dict[str, Any]] = []
        head: list[str] = []
        for i, page in enumerate(pages):
            rows = page["ResultSet"]["Rows"]
            if i == 0:
                head = [c.get("VarCharValue", "") for c in rows[0]["Data"]]
                rows = rows[1:]
            for r in rows:
                out.append(dict(zip(head, (c.get("VarCharValue") for c in r["Data"]))))
        return out

    return run


def _cell(v: Any) -> str:
    if v is None:
        return "∅"
    if isinstance(v, float):
        return f"{v:.6g}"
    s = str(v)
    return s if len(s) <= 60 else s[:57] + "..."


class Surfaces:
    """두 표면을 하나로 보여준다. **P2·P3·P5 시그니처를 안 바꾼다.**

    에이전트가 보는 것은 표면 하나다. 어느 저장소에 사는지는 그쪽 문제가 아니고, 알게
    하면 "어느 표면에 물어야 하나"라는 없는 문제를 풀게 된다.

    라우팅은 **뷰 이름**으로 결정론이다 - 이름 공간이 겹치지 않는다(Postgres 쪽은 사건·
    가격·수급, canonical 쪽은 재무·지배구조). 겹치면 생성 시점에 터뜨린다: 조용히 한쪽을
    고르면 같은 이름이 날마다 다른 표를 가리킬 수 있다.

    한쪽이 없으면(canonical 미배선) 그쪽 어휘는 **"없다"가 아니라 아예 안 실린다** -
    커버리지 원장이 `unavailable` 로 적을 근거를 `missing` 이 준다.
    """

    def __init__(self, primary, canonical: CanonicalSurface | None = None) -> None:
        self._p = primary
        self._c = canonical
        self._own: frozenset[str] = frozenset()
        if canonical is not None:
            self._own = frozenset(canonical._views)
            clash = self._own & _view_names(primary.schema())
            if clash:
                raise PipelineError(
                    f"두 표면의 뷰 이름이 겹친다: {sorted(clash)}. 같은 이름이 다른 표를 "
                    "가리키면 질의가 조용히 틀린 곳을 읽는다")

    @property
    def ledger(self):
        return self._p.ledger

    @property
    def missing(self) -> list[str]:
        """이 런에서 안 열린 어휘. **P8 커버리지 원장의 입력이다.**"""
        return [] if self._c is not None else [
            "canonical(재무·컨센서스·주주지분·계열·임원·이사회·신용등급) - 미배선"]

    def schema(self) -> str:
        if self._c is None:
            return self._p.schema()
        return self._p.schema() + "\n\n" + self._c.schema()

    def _pick(self, sql: str):
        """어느 표면인가. canonical 뷰 이름이 하나라도 보이면 그쪽이다."""
        if self._c is None:
            return self._p
        low = sql.lower()
        return self._c if any(re.search(rf"\b{v}\b", low) for v in self._own) else self._p

    def query(self, sql: str, *, limit: int = MAX_ROWS) -> list[dict[str, Any]]:
        return self._pick(sql).query(sql, limit=limit)

    def ask(self, sql: str, *, show: int = 20) -> str:
        return self._pick(sql).ask(sql, show=show)


def _view_names(schema: str) -> frozenset[str]:
    return frozenset(re.findall(r"\bv_[a-z0-9_]+", schema))


__all__ = ["MAX_ROWS", "CanonicalSurface", "Surfaces", "athena_runner", "load_manifest"]
