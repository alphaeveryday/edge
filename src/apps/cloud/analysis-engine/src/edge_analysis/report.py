"""발화 스냅샷 대시보드 — 원장·trace 전량을 단일 HTML 로 (ALPHA-894).

사용자는 파일 하나만 본다: ``{result_prefix}/reports/dashboard.html``. 런이 끝날
때마다 전량 재조회해 같은 키에 덮어쓴다(멱등). 렌더는 결정론이다 — 입력 밖의 상태
(시계·난수·로케일)를 읽지 않으므로 같은 입력이면 같은 바이트다. 그래서 페이지에
"생성 시각"이 없다: 벽시계를 실으면 그 계약이 깨진다.

크기 천장: 최근 ``REPORT_DETAIL_DAYS`` 일(데이터의 최신 거래일 기준 — 벽시계 기준이
아니다, 결정론)은 상세 전부, 그 이전은 요약 행만 싣는다. 전환 기준은 페이지 머리에
적는다 — 조용한 절단 금지(Rule 12).

실패 정직성: trace 부재·sha256 불일치·근거 행 결손은 그 발화 항목에 결손 사유로
렌더하고 생성은 계속한다. 숨기면 봉인 배지가 있으나 마나다.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from html import escape
from pathlib import Path
from typing import Any

from .observability import log
from .statics.evidence_card import EvidenceRow, render_template
from .statics.evidence_render import render_row

# 상세/요약 전환 천장. 데이터의 최신 거래일에서 세는 일수다(오늘 기준이 아니다 —
# 같은 입력 = 같은 바이트 계약).
REPORT_DETAIL_DAYS = 30
DASHBOARD_KEY_SUFFIX = "reports/dashboard.html"

# ── 조회 (전량 — 멱등 덮어쓰기의 재료) ─────────────────────────────────────
_RESULTS_SQL = (
    "SELECT r.explanation_result_id, r.explanation_run_id, i.ticker,"
    " r.etf_instrument_id, r.trade_date, r.explanation_as_of, r.explanation_type,"
    " r.summary, r.confidence_level, r.stage_results, r.publication_status"
    " FROM explanation_result r"
    " LEFT JOIN instrument i ON i.instrument_id = r.etf_instrument_id"
    " ORDER BY r.trade_date DESC, i.ticker, r.explanation_as_of, r.explanation_result_id"
)
_EVIDENCE_SQL = (
    "SELECT explanation_result_id, ref, evidence_type, content, source, observed_at,"
    " template, basis, method, slots, n, unit, estimate, p, k, band, series"
    " FROM explanation_evidence_row ORDER BY explanation_result_id, ref"
)
_TRIALS_SQL = (
    "SELECT explanation_run_id, ticker, trade_date, stage, trigger_slot, channel,"
    " exposure, layer, verdict, n, p, reason"
    " FROM hypothesis_trial ORDER BY trial_id"
)


def _rows(conn, sql: str) -> list[tuple]:
    with conn.cursor() as cur:
        cur.execute(sql)
        return cur.fetchall()


def _jload(value: Any) -> Any:
    """JSONB 컬럼 정규화 — 드라이버에 따라 dict 또는 str 로 온다."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None
    return value


def _split_s3_uri(uri: str) -> tuple[str, str]:
    bucket, _, key = uri[len("s3://"):].partition("/")
    return bucket, key


def fetch_snapshot(conn, s3) -> tuple[list[dict[str, Any]], list[tuple]]:
    """원장 3표 전량 + (상세 구간만) S3 trace 를 읽어 (발화 목록, 미연결 가설)을 만든다.

    발화 순서가 곧 렌더 순서다: 일자 내림차순 → 종목 오름차순 → 발화 시각 오름차순
    (SQL ORDER BY 가 정본). 각 항목에 ``detail``(30일 경계 판정)이 붙는다.
    미연결 가설은 run 이 확정되기 전에 죽은 런의 행이다 — 버리면 결손이 안 보인다.
    """
    results = []
    for row in _rows(conn, _RESULTS_SQL):
        (result_id, run_id, ticker, instrument_id, trade_date, as_of, etype,
         summary, confidence, stage, status) = row
        results.append({
            "result_id": str(result_id), "run_id": run_id and str(run_id),
            "ticker": str(ticker or instrument_id),
            "trade_date": trade_date if isinstance(trade_date, date)
            else date.fromisoformat(str(trade_date)),
            "as_of": str(as_of), "type": str(etype), "summary": str(summary),
            "confidence": str(confidence or ""), "status": str(status),
            "stage": _jload(stage) or {},
            "evidence": {}, "trials": [], "trace": None,
        })
    if not results:
        return results, []
    cutoff = max(r["trade_date"] for r in results) - timedelta(days=REPORT_DETAIL_DAYS - 1)
    for r in results:
        r["detail"] = r["trade_date"] >= cutoff
        r["cutoff"] = cutoff

    by_result = {r["result_id"]: r for r in results}
    for row in _rows(conn, _EVIDENCE_SQL):
        holder = by_result.get(str(row[0]))
        if holder is not None and holder["detail"]:
            holder["evidence"][int(row[1])] = row
    by_run = {r["run_id"]: r for r in results if r["run_id"]}
    orphan_trials: list[tuple] = []
    for row in _rows(conn, _TRIALS_SQL):
        holder = by_run.get(row[0] and str(row[0]))
        if holder is None:
            orphan_trials.append(row)
        elif holder["detail"]:
            holder["trials"].append(row)
    for r in results:
        if r["detail"]:
            r["trace"] = _fetch_trace(s3, (r["stage"] or {}).get("analysis_trace"))
    return results, orphan_trials


def _fetch_trace(s3, manifest: Any) -> dict[str, Any]:
    """trace 본문을 읽어 sha256 을 **재계산해 대조**한다 — 봉인 배지의 재료.

    부재·불일치도 그대로 돌려준다(Rule 12) — 렌더가 결손 사유로 싣는다.
    """
    if not manifest or not isinstance(manifest, dict) or not manifest.get("s3_uri"):
        return {"status": "부재", "reason": "trace manifest 없음(관측 부재 런)",
                "events": []}
    uri = str(manifest["s3_uri"])
    bucket, key = _split_s3_uri(uri)
    try:
        body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    except Exception as exc:  # noqa: BLE001 — 부재는 사유로 렌더한다
        return {"status": "부재", "reason": f"S3 조회 실패: {type(exc).__name__}: {exc}",
                "uri": uri, "events": []}
    actual = hashlib.sha256(body).hexdigest()
    stored = str(manifest.get("sha256") or "")
    try:
        events = json.loads(body.decode("utf-8")).get("events") or []
    except Exception:  # noqa: BLE001 — 깨진 본문도 배지는 남긴다
        events = []
    return {"status": "일치" if actual == stored else "불일치",
            "stored": stored, "actual": actual, "uri": uri, "events": events}


# ── 렌더 (결정론 — 같은 입력 = 같은 바이트) ────────────────────────────────
_CSS = """
:root{color-scheme:light}
body{font-family:'Malgun Gothic','Apple SD Gothic Neo',sans-serif;margin:0;
 background:#f5f6f8;color:#1c1e21;line-height:1.55}
main{max-width:1080px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:1.4rem}h2{font-size:1.15rem;border-bottom:2px solid #d0d4da;
 padding-bottom:4px;margin-top:32px}h3{font-size:1rem;margin:20px 0 8px}
article.utt{background:#fff;border:1px solid #dfe3e8;border-radius:8px;
 padding:16px;margin:12px 0}
.badge{display:inline-block;padding:1px 8px;border-radius:10px;font-size:.78rem;
 margin-left:6px;vertical-align:middle}
.badge.ok{background:#e3f4e6;color:#1e6b2d}.badge.bad{background:#fde5e5;color:#a11}
.badge.miss{background:#fff3d6;color:#8a6100}.badge.st{background:#e8ecf3;color:#334}
pre{background:#f2f3f5;border-radius:6px;padding:10px;overflow-x:auto;
 font-size:.85rem;white-space:pre-wrap;word-break:break-all}
pre.cards{background:#eef4ee}
table{border-collapse:collapse;width:100%;font-size:.85rem;margin:8px 0}
th,td{border:1px solid #d8dce2;padding:4px 8px;text-align:left;vertical-align:top}
th{background:#eceff3}
details{margin:6px 0;border:1px solid #e2e5ea;border-radius:6px;padding:4px 8px}
summary{cursor:pointer;font-size:.88rem}
.meta{font-size:.83rem;color:#444;background:#f8f9fa;border-radius:6px;padding:8px 12px}
.miss-note{color:#a11;font-size:.85rem}
.head-note{background:#fffbe8;border:1px solid #e8dfae;border-radius:8px;
 padding:10px 14px;font-size:.88rem}
#filter-bar{position:sticky;top:0;z-index:5;background:#fff;
 border:1px solid #dfe3e8;border-radius:8px;padding:10px 14px;margin:12px 0;
 font-size:.9rem}
#filter-bar select{font-size:.9rem;margin:0 12px 0 4px}
#f-count{color:#444}
""".strip()

# 필터 바 동작 — 인라인 바닐라 JS(외부 참조 0). select change 마다 data-date·
# data-ticker 로 표시/숨김을 토글한다. 초기 로드에도 한 번 실행해 최신 일자만
# 펼친다(스크롤 천장). JS 꺼진 환경은 이 스크립트가 안 돌므로 전체가 그대로
# 보인다 — <noscript> 안내와 짝.
_FILTER_JS = """
(function(){
var fd=document.getElementById("f-date"),ft=document.getElementById("f-ticker"),
    fc=document.getElementById("f-count");
if(!fd||!ft)return;
function apply(){
  var d=fd.value,t=ft.value,n=0;
  var els=document.querySelectorAll("[data-date]");
  for(var i=0;i<els.length;i++){
    var el=els[i],tk=el.getAttribute("data-ticker");
    var show=el.getAttribute("data-date")===d&&(tk===null||!t||tk===t);
    el.style.display=show?"":"none";
    if(show&&tk!==null)n+=el.querySelectorAll("article.utt").length;
  }
  fc.textContent="발화 "+n+"건";
}
fd.addEventListener("change",apply);
ft.addEventListener("change",apply);
apply();
})();
""".strip()


def _pre(text: str, cls: str = "") -> str:
    attr = f' class="{cls}"' if cls else ""
    return f"<pre{attr}>{escape(str(text))}</pre>"


def _evidence_card(row: tuple) -> str:
    """원장 행 → 카드 텍스트(evidence_render 재사용). 결손·위반은 사유 문자열."""
    (_rid, ref, etype, content, source, observed_at,
     template, basis, method, slots, n, unit, estimate, p, k, band, series) = row
    try:
        if etype == "STAT_TEST":
            slots = _jload(slots) or {}
            series = _jload(series) or []
            detail: dict[str, Any] = {
                "basis": basis, "method": method, "n": int(n), "unit": unit,
                "estimate": float(estimate), "p": float(p)}
            if band:
                detail["band"] = band
            if k and int(k) > 1:
                detail["k"] = int(k)
            er = EvidenceRow(int(ref), "STAT_TEST",
                             content=render_template(str(template), slots),
                             source=" · ".join(str(s) for s in series),
                             time="", detail=detail)
        else:
            er = EvidenceRow(int(ref), str(etype), content=str(content),
                             source=str(source), time=str(observed_at or ""))
        return render_row(er)
    except Exception as exc:  # noqa: BLE001 — 렌더 결손도 그 자리에 사유로 남는다
        return f"[{ref}] 렌더 결손 — {type(exc).__name__}: {exc}"


def _usage_total(events: list[dict]) -> dict[str, int]:
    total: dict[str, int] = {}
    for e in events:
        usage = e.get("usage") if e.get("event") == "llm.response" else None
        if isinstance(usage, dict):
            for key, value in usage.items():
                if isinstance(value, (int, float)):
                    total[str(key)] = total.get(str(key), 0) + int(value)
    return total


def _render_llm(events: list[dict]) -> list[str]:
    """trace 이벤트 → LLM·SQL 왕복 아코디언. 원문을 자르지 않는다."""
    out: list[str] = []
    other: list[dict] = []
    for e in events:
        kind = str(e.get("event", ""))
        seq = e.get("seq", "")
        if kind == "llm.request":
            out.append(f"<details><summary>LLM 요청 #{escape(str(seq))}</summary>"
                       f"<p>system</p>{_pre(e.get('system', ''))}"
                       f"<p>user</p>{_pre(e.get('user', ''))}</details>")
        elif kind == "llm.response":
            body = json.dumps(e.get("response"), ensure_ascii=False, indent=1,
                              sort_keys=True)
            usage = e.get("usage")
            usage_html = (f"<p class=\"meta\">usage: {escape(json.dumps(usage, ensure_ascii=False, sort_keys=True))}</p>"
                          if isinstance(usage, dict) else "")
            out.append(f"<details><summary>LLM 응답 #{escape(str(seq))}"
                       f" ({escape(str(e.get('elapsed_s', '?')))}s)</summary>"
                       f"{usage_html}{_pre(body)}</details>")
        elif kind == "llm.failed":
            out.append(f"<details open><summary>LLM 실패 #{escape(str(seq))}</summary>"
                       f"{_pre(e.get('error', ''))}</details>")
        elif kind == "query.done" or kind.startswith("sql"):
            out.append(f"<details><summary>SQL {escape(str(e.get('rows', '?')))}행"
                       f" ({escape(str(e.get('ms', '?')))}ms)</summary>"
                       f"{_pre(e.get('sql', ''))}</details>")
        else:
            other.append(e)
    if other:
        lines = "\n".join(json.dumps(e, ensure_ascii=False, sort_keys=True,
                                     default=str) for e in other)
        out.append(f"<details><summary>기타 trace 이벤트 {len(other)}건</summary>"
                   f"{_pre(lines)}</details>")
    return out


_TRIAL_HEAD = ("<tr><th>단계</th><th>방아쇠</th><th>채널</th><th>노출</th>"
               "<th>층</th><th>판정</th><th>n</th><th>p</th><th>사유</th></tr>")


def _trial_row(row: tuple) -> str:
    (_run, _ticker, _day, stage, slot, channel, exposure, layer,
     verdict, n, p, reason) = row
    cells = (stage, slot, channel, exposure, layer, verdict, n, p, reason)
    return "<tr>" + "".join(
        f"<td>{escape('' if c is None else str(c))}</td>" for c in cells) + "</tr>"


def _seal_badge(trace: dict[str, Any] | None) -> str:
    if trace is None or trace.get("status") == "부재":
        return '<span class="badge miss">trace 부재</span>'
    if trace["status"] == "일치":
        return '<span class="badge ok">봉인 일치</span>'
    return '<span class="badge bad">봉인 불일치</span>'


def _render_utterance(r: dict[str, Any]) -> list[str]:
    stage = r["stage"] or {}
    out = [f'<article class="utt" id="{escape(r["result_id"])}">',
           f"<header><strong>{escape(r['as_of'])}</strong>"
           f'<span class="badge st">{escape(r["confidence"] or "확신도없음")}</span>'
           f'<span class="badge st">{escape(r["status"])}</span>'
           f"{_seal_badge(r['trace'])}"
           f' <code>{escape(r["result_id"])}</code></header>']
    trace = r["trace"] or {}
    if trace.get("status") == "부재":
        out.append(f'<p class="miss-note">결손: {escape(trace.get("reason", ""))}</p>')
    elif trace.get("status") == "불일치":
        out.append('<p class="miss-note">결손: trace sha256 불일치 — 원장 manifest '
                   f'<code>{escape(trace.get("stored", ""))}</code> vs 재계산 '
                   f'<code>{escape(trace.get("actual", ""))}</code></p>')

    # ① 고객 산문 블록 ↔ 근거 행
    final = stage.get("final_explanation") or {}
    blocks = final.get("blocks") or []
    used_refs: set[int] = set()
    if blocks:
        out.append("<h4>고객 설명 블록</h4>")
        for block in blocks:
            code = escape(str(block.get("block_code", "?")))
            title = escape(str(block.get("block_title", "")))
            out.append(f"<h5>[{code}] {title}</h5>")
            out.append(_pre(block.get("text", "")))
            refs = [int(x) for x in (block.get("evidence_row_refs") or ())]
            used_refs.update(refs)
            if refs:
                cards = []
                for ref in refs:
                    row = r["evidence"].get(ref)
                    cards.append(_evidence_card(row) if row is not None
                                 else f"[{ref}] 결손: 근거 행이 원장에 없다")
                out.append(_pre("\n".join(cards), cls="cards"))
    else:
        out.append('<p class="miss-note">결손: final_explanation 블록 없음 — '
                   '요약 원문으로 대신한다</p>')
        out.append(_pre(r["summary"]))
    leftover = sorted(set(r["evidence"]) - used_refs)
    if leftover:
        out.append("<h5>블록 미연결 근거 행</h5>")
        out.append(_pre("\n".join(_evidence_card(r["evidence"][ref])
                                  for ref in leftover), cls="cards"))

    # ② 가설 원장 (기각 사유 포함)
    out.append("<h4>가설 원장</h4>")
    if r["trials"]:
        out.append("<table>" + _TRIAL_HEAD
                   + "".join(_trial_row(t) for t in r["trials"]) + "</table>")
    else:
        out.append("<p class=\"meta\">가설 원장 행 없음</p>")

    # ③ LLM·SQL 왕복 원문
    out.append("<h4>LLM·SQL 왕복</h4>")
    events = trace.get("events") or []
    rendered = _render_llm(events)
    out.extend(rendered if rendered
               else ['<p class="meta">trace 이벤트 없음</p>'])

    # ④ usage 합산 · 창 좌표 · 커버리지
    usage = _usage_total(events)
    window = stage.get("window") or {}
    meta = ["<div class=\"meta\">"]
    meta.append("usage 합산: " + (escape(json.dumps(usage, ensure_ascii=False,
                                                   sort_keys=True))
                                 if usage else "기록 없음(결측)"))
    coords = {key: window.get(key) for key in (
        "analysis_start", "requested_start", "requested_end", "committed_windows",
        "state_bars", "requested_bars", "window_min_coverage",
        "price_weight_coverage", "dropped_units") if key in window}
    meta.append("<br>창 좌표·커버리지: "
                + (escape(json.dumps(coords, ensure_ascii=False, sort_keys=True))
                   if coords else "기록 없음(결측)"))
    meta.append("</div>")
    out.append("".join(meta))
    out.append("</article>")
    return out


def render_dashboard(results: list[dict[str, Any]],
                     orphan_trials: list[tuple] = ()) -> str:
    """발화 목록 → 단일 HTML(인라인 CSS·JS만, 외부 참조 0, 한글).

    상단 고정 필터 바(일자 내림차순·종목 가나다순)가 상세 구간을 일자·종목으로
    토글한다(ALPHA-894 후속). 기본 선택은 최신 일자 — 초기 스크롤이 하루치로
    준다. JS 꺼짐이면 스크립트가 안 돌아 전체가 보인다(<noscript> 안내).
    """
    parts = ["<!doctype html>", '<html lang="ko"><head><meta charset="utf-8">',
             '<meta name="viewport" content="width=device-width,initial-scale=1">',
             "<title>발화 스냅샷 대시보드</title>",
             f"<style>{_CSS}</style></head><body><main>",
             "<h1>발화 스냅샷 대시보드</h1>"]
    if not results:
        parts.append("<p>원장에 발화가 없다.</p></main></body></html>")
        return "\n".join(parts)
    cutoff = results[0]["cutoff"]
    parts.append(
        '<div class="head-note">상세 기준: 데이터 최신 거래일부터 최근 '
        f"{REPORT_DETAIL_DAYS}일({cutoff.isoformat()} 이후)은 상세 전부, 그 이전 "
        "발화는 요약 행만 싣는다 — 상세는 원장(explanation_result·"
        "explanation_evidence_row·hypothesis_trial)과 S3 traces/ 에 있다. 정렬: "
        "일자 내림차순 → 종목 오름차순 → 발화 시각 오름차순.</div>")
    detail = [r for r in results if r["detail"]]
    old = [r for r in results if not r["detail"]]
    if detail:
        dates = sorted({r["trade_date"] for r in detail}, reverse=True)
        tickers = sorted({r["ticker"] for r in detail})
        parts.append(
            '<div id="filter-bar"><label>일자 <select id="f-date">'
            + "".join(f'<option value="{d.isoformat()}">{d.isoformat()}</option>'
                      for d in dates)
            + '</select></label><label>종목 <select id="f-ticker">'
            '<option value="">전체</option>'
            + "".join(f'<option value="{escape(t)}">{escape(t)}</option>'
                      for t in tickers)
            + '</select></label><span id="f-count"></span></div>')
        parts.append('<noscript><p class="head-note">필터는 JS 가 필요하다 — '
                     '전체 목록은 아래에 그대로 있다.</p></noscript>')
    day = ticker = None
    open_section = False
    for r in detail:
        new_day = r["trade_date"] != day
        if new_day or r["ticker"] != ticker:
            if open_section:
                parts.append("</section>")
            if new_day:
                day = r["trade_date"]
                parts.append(f'<h2 data-date="{day.isoformat()}">'
                             f"{day.isoformat()}</h2>")
            ticker = r["ticker"]
            parts.append(f'<section data-date="{day.isoformat()}"'
                         f' data-ticker="{escape(ticker)}">')
            parts.append(f"<h3>{escape(ticker)}</h3>")
            open_section = True
        parts.extend(_render_utterance(r))
    if open_section:
        parts.append("</section>")
    if old:
        parts.append(f"<h2>요약 구간 ({cutoff.isoformat()} 이전)</h2>")
        parts.append("<p class=\"meta\">상세는 원장·trace 에 있다.</p>")
        parts.append("<table><tr><th>일자</th><th>종목</th><th>확신도</th>"
                     "<th>게시상태</th><th>결과 id</th></tr>")
        for r in old:
            parts.append(
                f"<tr><td>{r['trade_date'].isoformat()}</td>"
                f"<td>{escape(r['ticker'])}</td>"
                f"<td>{escape(r['confidence'])}</td><td>{escape(r['status'])}</td>"
                f"<td><code>{escape(r['result_id'])}</code></td></tr>")
        parts.append("</table>")
    if orphan_trials:
        # run 확정 전에 죽은 런의 가설 행 — 어느 발화에도 못 붙지만 결손을 숨기지
        # 않는다(Rule 12).
        parts.append("<h2>런 미연결 가설 시행</h2>")
        parts.append("<table>" + _TRIAL_HEAD
                     + "".join(_trial_row(t) for t in orphan_trials) + "</table>")
    parts.append("</main>")
    if detail:
        parts.append(f"<script>{_FILTER_JS}</script>")
    parts.append("</body></html>")
    return "\n".join(parts)


def regenerate_dashboard(*, conn, s3, result_prefix: str,
                         out_dir: str | None = None) -> str | None:
    """전량 재조회 → 렌더 → 같은 키 덮어쓰기(멱등). 반환은 산출 위치.

    ``out_dir`` 이 있으면 로컬 파일로 쓴다(수동 재생성·검수용). 없으면
    ``{result_prefix}/reports/dashboard.html`` 로 올린다.
    """
    if conn is None:
        raise RuntimeError("대시보드 재생성에 원장 커넥션이 없다")
    html = render_dashboard(*fetch_snapshot(conn, s3))
    body = html.encode("utf-8")
    if out_dir is not None:
        target = Path(out_dir) / "dashboard.html"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
        log("report.regenerated", path=str(target), bytes=len(body))
        return str(target)
    if not result_prefix.startswith("s3://"):
        raise RuntimeError(f"result prefix 가 s3:// 가 아니다: {result_prefix!r}")
    bucket, key_prefix = _split_s3_uri(result_prefix)
    key = f"{key_prefix.rstrip('/')}/{DASHBOARD_KEY_SUFFIX}"
    s3.put_object(Bucket=bucket, Key=key, Body=body,
                  ContentType="text/html; charset=utf-8")
    location = f"s3://{bucket}/{key}"
    log("report.regenerated", s3=location, bytes=len(body))
    return location
