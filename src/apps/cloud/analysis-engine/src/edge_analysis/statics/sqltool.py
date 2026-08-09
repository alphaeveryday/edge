"""읽기 전용 DuckDB SQL 툴 — LLM 자유 질의가 레이크를 만나는 계약 표면.

계약 전체 (무엇이 왜 금지인가):

1. **단일 SELECT/WITH 만.** 판정은 정규식이 아니라 DuckDB 파서(`json_serialize_sql`)다
   — SELECT 가 아닌 문장(DDL·DML·ATTACH·PRAGMA·COPY)은 직렬화 자체가 거부되고,
   문장이 여럿이면 `statements` 가 여럿으로 파싱된다. 파서가 주석·대소문자·따옴표를
   이미 소화하므로 `/*..*/DELETE` 나 `SELECT 1; DROP ...` 같은 표기 우회가 원천적으로
   없다. 문자열 grep 은 틀리고, 틀린 검사는 상한을 조용히 빠뜨린다(readonly.guard 계보).
2. **allowlist 밖 관계 참조 금지.** 파스 트리의 BASE_TABLE 노드를 전수 수집하므로
   서브쿼리·CTE 정의 안의 참조도 같은 검사를 받는다(질의가 스스로 정의한 CTE 이름만
   예외). 허용 = ``lake.bound`` 의 클램프 뷰(``v_*``, bind_day 생성) + `WHITELIST`.
3. **PIT 는 규칙이 아니라 뷰 구조가 보장한다.** 클램프 뷰는 ``available_at``/
   ``trade_date`` 절단이 **뷰 정의 안에** 있으므로(sql_surface.auto_views_sql) 질의가
   무엇을 쓰든 기준일 너머를 못 본다 — 모델에게 "PIT 절을 붙여라"라고 시키는 대신
   잊을 수 있는 규칙 자체를 없앤 자리다. 클램프 열이 없는 관계(시점 불변 차원·
   bars_5m·백필 세트)는 참조를 허용하되 결과의 ``pit_unbound`` 에 명시한다 —
   사실이 늦게 바뀌면 선견일 수 있음을 소비자가 안다.
4. **테이블 함수 금지.** ``read_parquet('s3://…')``·``postgres_query``·``glob`` 은
   allowlist 를 그대로 우회하는 뒷문이다 — FROM 에는 이름 있는 관계만 온다.
5. **행 200 · 직렬화 64KB 상한.** 상한은 SQL 을 파싱해 LIMIT 를 끼우지 않고
   **감싼다**(readonly.run_query 와 같은 이유). 초과는 자르되 ``truncated`` 에
   사유를 명시한다 — 조용한 절단은 잘린 표본을 전체로 오해하게 한다.
6. **오류는 결정론 표면.** ``{ok: False, reason}`` 하나로 돌아오고 예외 원문은
   200자로 자른다 — 에이전트 루프가 스택트레이스가 아니라 문장을 읽는다.
7. **모든 호출이 감사에 남는다.** 수락·거부 모두 ``observability.record`` 로 trace
   버퍼에 실려 프롬프트 trace 와 같은 S3 객체(``traces/``)에 질의 감사가 남는다.
"""
from __future__ import annotations

import json
from typing import Any

from ..observability import record
from .duck import BACKFILL_SETS

ROW_CAP = 200
BYTE_CAP = 64 * 1024
_REASON_CUT = 200

# 클램프 뷰 밖에서 추가로 여는 관계. 전부 클램프 열이 없으므로 pit_unbound 로 나간다.
WHITELIST: tuple[str, ...] = ("bars_5m", *BACKFILL_SETS)


def allowed_relations(lake) -> dict[str, str | None]:
    """참조 가능한 관계 → 클램프 열 (None = PIT 비보장).

    ``lake.bound`` 는 원장 표 이름 → 클램프 열이고 bind_day 가 만드는 뷰 이름은
    ``v_<표>`` 다(auto_views_sql). 목록을 손으로 적지 않는 이유는 duck._rdb 와 같다 —
    적으면 새 표의 기본값이 '안 열림'이 된다.
    """
    allow: dict[str, str | None] = {f"v_{t}": c for t, c in lake.bound.items()}
    for name in WHITELIST:
        allow.setdefault(name, None)
    return allow


def _walk(node: Any, tables: set[str], funcs: set[str], ctes: set[str]) -> None:
    """파스 트리 전수 순회 — BASE_TABLE·TABLE_FUNCTION·CTE 이름을 모은다."""
    if isinstance(node, dict):
        cte_map = (node.get("cte_map") or {}).get("map") or []
        for entry in cte_map:
            if isinstance(entry, dict) and entry.get("key"):
                ctes.add(str(entry["key"]))
        kind = node.get("type")
        if kind == "BASE_TABLE":
            tables.add(".".join(p for p in (node.get("catalog_name"),
                                            node.get("schema_name"),
                                            node.get("table_name")) if p))
        elif kind == "TABLE_FUNCTION":
            fn = node.get("function") or {}
            funcs.add(str(fn.get("function_name") or "?"))
        for v in node.values():
            _walk(v, tables, funcs, ctes)
    elif isinstance(node, list):
        for v in node:
            _walk(v, tables, funcs, ctes)


def run(lake, sql: str, *, row_cap: int = ROW_CAP, byte_cap: int = BYTE_CAP) -> dict[str, Any]:
    """계약을 통과한 질의 하나를 상한 안에서 실행한다. 예외를 던지지 않는다."""
    text = (sql or "").strip()

    def reject(reason: str) -> dict[str, Any]:
        record("sqltool.rejected", sql=text, reason=reason)
        return {"ok": False, "reason": reason}

    if not text:
        return reject("빈 질의")
    # ── 1) 파서 수준 검증 — 문장 종류·개수는 DuckDB 파서가 판정한다 ──
    try:
        doc = json.loads(lake.con.execute(
            "SELECT json_serialize_sql(?)", [text]).fetchone()[0])
    except Exception as e:  # noqa: BLE001 — 파서 호출 실패도 결정론 표면으로
        return reject(f"파서 호출 실패: {type(e).__name__}: {str(e)[:_REASON_CUT]}")
    if doc.get("error"):
        # 비-SELECT(DDL·DML·PRAGMA·ATTACH·COPY)와 문법 오류가 전부 여기로 온다.
        return reject("단일 SELECT/WITH 만 받는다 — 파서: "
                      + str(doc.get("error_message", ""))[:_REASON_CUT])
    stmts = doc.get("statements") or []
    if len(stmts) != 1:
        return reject(f"문장이 {len(stmts)}개다 — 한 번에 SELECT 하나만 보내라")
    # ── 2) 참조 관계 전수 검사 — 서브쿼리·CTE 안까지 파스 트리로 ──
    tables: set[str] = set()
    funcs: set[str] = set()
    ctes: set[str] = set()
    _walk(stmts[0], tables, funcs, ctes)
    if funcs:
        return reject("테이블 함수는 쓸 수 없다 (allowlist 우회 경로): "
                      + ", ".join(sorted(funcs)))
    allow = {k.lower(): v for k, v in allowed_relations(lake).items()}
    names = {t.lower() for t in tables} - {c.lower() for c in ctes}
    unknown = sorted(n for n in names if n not in allow)
    if unknown:
        return reject("allowlist 밖 참조: " + ", ".join(unknown)
                      + " · 허용 관계: " + ", ".join(sorted(allow)))
    pit_unbound = sorted(n for n in names if allow[n] is None)
    # ── 3) 실행 — 상한은 감싸서 건다. +1 행을 더 받아 절단 여부를 안다 ──
    try:
        cur = lake.con.execute(f"SELECT * FROM ({text}) AS _sqltool LIMIT {row_cap + 1}")
        columns = [d[0] for d in (cur.description or ())]
        raw = cur.fetchall()
    except Exception as e:  # noqa: BLE001 — 실행 오류도 문장으로 돌려준다
        return reject(f"실행 실패: {type(e).__name__}: {str(e)[:_REASON_CUT]}")
    over_rows = len(raw) > row_cap
    rows: list[list[Any]] = []
    used, over_bytes = 2, False
    for r in raw[:row_cap]:
        vals = [v if isinstance(v, (type(None), bool, int, float, str)) else str(v)
                for v in r]
        used += len(json.dumps(vals, ensure_ascii=False).encode("utf-8")) + 2
        if used > byte_cap:
            over_bytes = True
            break
        rows.append(vals)
    truncated = ""
    if over_bytes:
        truncated = (f"직렬화 {byte_cap:,}바이트 상한 초과 — 앞 {len(rows)}행만 담았다"
                     + (f" (행 상한 {row_cap}도 초과)" if over_rows else ""))
    elif over_rows:
        truncated = f"행 상한 {row_cap} 초과 — 앞 {row_cap}행만 담았다"
    record("sqltool.query", sql=text, rows=len(rows), truncated=bool(truncated),
           pit_unbound=pit_unbound)
    return {"ok": True, "columns": columns, "rows": rows, "row_count": len(rows),
            "truncated": truncated, "pit_unbound": pit_unbound}


def tool_spec(lake, *, row_cap: int = ROW_CAP, byte_cap: int = BYTE_CAP,
              ) -> dict[str, Any]:
    """LLM 툴 정의 — (name·description·input_schema·call). 편입 지점이 그대로 문다.

    설명에 허용 관계를 **레이크 실물에서** 뽑아 넣는다 — 목록을 산문으로 적으면
    원장이 바뀔 때 설명만 낡는다(sql_surface SCHEMA 의 규율).
    """
    allow = allowed_relations(lake)
    clamped = sorted(n for n, c in allow.items() if c)
    unbound = sorted(n for n, c in allow.items() if c is None)
    def call(sql: str) -> dict[str, Any]:
        return run(lake, sql, row_cap=row_cap, byte_cap=byte_cap)
    description = (
        "읽기 전용 DuckDB SQL. 단일 SELECT/WITH 하나만 — DDL·DML·다중문·테이블 함수는 "
        f"거부된다. 결과는 최대 {row_cap}행·{byte_cap:,}바이트이고 잘리면 truncated 에 "
        "사유가 적힌다.\n"
        "시점 고정 뷰(기준일 너머를 볼 수 없다): " + (", ".join(clamped) or "(없음)")
        + "\n시점 비보장 관계(결과에 pit_unbound 로 표시된다): "
        + (", ".join(unbound) or "(없음)"))
    return {
        "name": "sql",
        "description": description,
        "input_schema": {
            "type": "object",
            "properties": {"sql": {"type": "string",
                                   "description": "단일 SELECT/WITH 질의문"}},
            "required": ["sql"],
        },
        "call": call,
    }


__all__ = ["BYTE_CAP", "ROW_CAP", "WHITELIST", "allowed_relations", "run", "tool_spec"]
