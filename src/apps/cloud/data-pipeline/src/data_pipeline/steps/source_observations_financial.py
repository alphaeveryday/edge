"""DART 재무 지표·보고서 판본 원천 관측의 데이터셋 규칙 (ALPHA-1130) — 구성종목 기간 파생·정규화·판본·수집·명세.

저장·정제·적재 공통 경로는 `source_observations` 에 있다. 계약 정본은 docs/design/etf-data-storage-plan.md §10.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

from ..lake import Storage, canonical_etf_holdings_partition, canonical_financial_metric_partition, raw_run_manifest_key
from ..parse import krx_short_code
from ..sources import dart_fundamental
from .source_observations import (
    _PROVENANCE,
    KST,
    Column,
    DatasetSpec,
    RawObject,
    ensure_same_request,
    existing_raw_manifest,
    record_already_collected,
    write_raw_run,
)

LIST_LOOKBACK_DAYS = 400   # 접수번호→접수일 대조와 Q4 유도(같은 해 3분기) 입력을 덮는 목록 소급 폭
REGULAR_FILING_DAYS = 14   # 정기 수집이 다시 보는 접수일 창(늦은 게시·정정 흡수)


def constituents_between(storage: Storage, etf_ids: list[str], start: date, end: date) -> tuple[list[str], dict]:
    """기간 [start, end] 에 유효했던 구성종목 합집합 — start 시점에 유효한 스냅샷(start 이하 최신) + 기간 안 스냅샷.

    현재 구성을 과거에 소급하지 않는다. start 이전에 스냅샷이 하나도 없으면 그 앞 기간의 구성은 **모른다** —
    `uncovered_before` 로 드러내고 추정하지 않는다.
    """
    from .ingest_price_raw import _is_calendar_date, _read_parquet_rows

    marker = canonical_etf_holdings_partition("KR", "")
    dates = sorted({d for key in storage.list_keys(marker)
                    if _is_calendar_date(d := key[len(marker):].split("/", 1)[0])})

    def rows_of(as_of: str) -> list[dict]:
        """그 날짜 파티션에서 대상 ETF 들의 구성종목 행."""
        prefix = canonical_etf_holdings_partition("KR", as_of)
        return [row for key in storage.list_keys(prefix + "/") if key.endswith(".parquet")
                for row in _read_parquet_rows(storage.get_bytes(key)) if row.get("etf_id") in etf_ids]

    tickers: set[str] = set()
    used: set[str] = set()
    # start 시점에 유효한 스냅샷은 **ETF 마다** 다르다 — 다른 ETF 만 갱신된 날짜를 대상 ETF 의 스냅샷으로 쓰지 않는다.
    pending = set(etf_ids)
    for as_of in reversed([d for d in dates if d <= start.isoformat()]):
        if not pending:
            break
        found = [row for row in rows_of(as_of) if row["etf_id"] in pending]
        if found:
            used.add(as_of)
            pending -= {row["etf_id"] for row in found}
            tickers.update(c for row in found if (c := krx_short_code(row.get("constituent_ticker"))))
    for as_of in (d for d in dates if start.isoformat() < d <= end.isoformat()):
        found = rows_of(as_of)
        if found:
            used.add(as_of)
            tickers.update(c for row in found if (c := krx_short_code(row.get("constituent_ticker"))))
    ordered = sorted(used)
    # start 에 유효한 스냅샷이 없는 ETF 가 있으면 그 앞 기간의 구성은 모른다 — 추정하지 않고 드러낸다.
    coverage = {"snapshots": ordered,
                "uncovered_before": None if not pending else (ordered[0] if ordered else end.isoformat()),
                "etfs_without_snapshot_at_start": sorted(pending)}
    return sorted(tickers), coverage


def _kst_midnight_after(day: str) -> str:
    """접수일 다음날 00:00 KST(UTC ISO). 공개 시각을 모르는 날짜 자료의 보수적 가시 경계 — 시각을 지어내지 않는다."""
    moment = datetime.combine(date.fromisoformat(day) + timedelta(days=1), datetime.min.time(), tzinfo=KST)
    return moment.astimezone(timezone.utc).isoformat()


_REPORT_PERIOD = {"11013": "Q1", "11012": "Q2", "11014": "Q3", "11011": "Q4"}   # 사업보고서 실행이 Q4 행을 만든다


def _normalize_financial(objects: list[dict], raw_manifest: dict) -> tuple[list[dict], list[dict], list[dict]]:
    """재무제표 응답 → 지표 행 + 거부 + **보고서 판본**(회사·연도·보고서·기준·실행마다 한 줄).

    판본은 지표 행과 독립이다 — 이번 실행이 그 보고서를 정상으로 확인했는데 쓸 지표가 없어도(CONFIRMED, metrics=[])
    한 줄이 남아 조회가 옛 값을 "최신"으로 내지 않고, 공급자 오류·응답 파손(UNCONFIRMED)은 기존 확정값을 무효화하지
    않으면서 "최근 확인 실패"로 드러난다. 수집·정제 중이거나 중단된 실행은 판본이 없다(아무것도 바꾸지 않는다).
    """
    corps = raw_manifest["request_scope"]["corps"]
    rcept_dates: dict[str, str] = {}
    statements, shares = {}, {}
    rows = []
    # 수집이 계획에서 뺀 보고서(비12월 결산)는 여기서 거부로 드러낸다 — 조용한 누락이 성공이 되지 않게.
    rejects = list(raw_manifest["request_scope"].get("unsupported_reports", []))
    versions: dict[tuple, dict] = {}
    share_entries: dict[tuple, dict] = {}
    manifest_key = raw_run_manifest_key(FINANCIAL.dataset, raw_manifest["run_id"])
    for entry in raw_manifest["objects"]:
        request = entry.get("request") or {}
        kind, corp_code = request.get("kind"), request.get("corp_code")
        if kind == "shares":
            share_entries[(corp_code, request["bsns_year"], request["reprt_code"])] = entry
        elif kind == "statement" and corp_code in corps:
            year, code, fs_div = request["bsns_year"], request["reprt_code"], request["fs_div"]
            versions[(corp_code, year, code, fs_div)] = {
                "corp_code": corp_code, "instrument_code": corps[corp_code]["stock_code"], "fiscal_year": int(year),
                "reprt_code": code, "report_period": _REPORT_PERIOD[code], "fs_basis": fs_div,
                # ok·empty(013 = 그 기준의 재무제표 없음)는 확인된 응답이다. error 는 판본을 확정하지 못한 것.
                "status": "CONFIRMED" if entry["status"] in ("ok", "empty") else "UNCONFIRMED",
                "rcept_no": None, "rcept_date": None, "metrics": [], "rejected": [],
                "detail": {"statement": entry["status"], "statement_detail": entry.get("detail"),
                           "shares": None, "shares_detail": None},
                "received_at": entry["fetched_at"],
                # 본문이 없는 실패는 raw manifest 가 그 요청의 증거다.
                "raw_key": entry.get("key") or manifest_key,
                "raw_sha256": entry.get("sha256") or raw_manifest["manifest_sha256"]}

    share_rejects: dict[tuple, str] = {}

    def unconfirmed(target: tuple, reason: str) -> None:
        """이 실행이 그 보고서를 확정하지 못했다고 표시한다(응답 파손·다른 보고서 응답·추출 실패)."""
        if target in versions:
            versions[target]["status"] = "UNCONFIRMED"
            versions[target]["detail"]["statement_detail"] = reason

    def reject_response(kind: str, target: tuple, reason: str) -> None:
        """응답 하나를 정제가 거부했다 — 재무제표면 판본 미확정, 주식총수면 분모 미확정으로 남긴다."""
        if kind == "statement":
            unconfirmed(target, reason)
        elif kind == "shares":
            share_rejects[target[:3]] = reason

    for obj in objects:
        request = obj["request"]
        kind = request["kind"]
        target = (request.get("corp_code"), request.get("bsns_year"), request.get("reprt_code"), request.get("fs_div"))
        body = json.loads(obj["body"].decode("utf-8"))
        items = body.get("list") if isinstance(body, dict) else None
        if not isinstance(items, list):
            rejects.append({"raw_key": obj["key"], "reasons": ["unexpected_shape"]})
            reject_response(kind, target, "unexpected_shape")
            continue
        malformed = sum(1 for item in items if not isinstance(item, dict))
        if malformed:
            rejects.append({"raw_key": obj["key"], "reasons": ["malformed_list_row"], "rows": malformed})
            # 파손 행이 섞인 재무제표·주식총수 응답은 확인된 응답이 아니다 — 남은 행으로 지표·분모를 만들지 않는다
            # (만들면 미확정 값이 확정 판본에 실리거나 "확정된 부재"가 된다). 목록은 접수일 사전에만 쓰므로 남은 행을 쓴다.
            reject_response(kind, target, "malformed_list_row")
            if kind != "list":
                continue
        items = [item for item in items if isinstance(item, dict)]
        if kind == "list":
            for item in items:
                rcept_no = item.get("rcept_no")
                if not (isinstance(rcept_no, str) and dart_fundamental.RCEPT_NO.fullmatch(rcept_no)):
                    # 접수번호가 문자열이 아니면 사전 키로도 못 쓴다 — 그 행만 거부한다(정제 전체를 멈추지 않게).
                    rejects.append({"raw_key": obj["key"], "reasons": ["bad_rcept_no"]})
                    continue
                try:
                    day = datetime.strptime(str(item.get("rcept_dt")), "%Y%m%d").date().isoformat()
                except ValueError:
                    continue            # 접수일을 못 읽으면 그 접수번호는 "모름" — 수신 기준으로 떨어진다
                rcept_dates[rcept_no] = day
            continue
        # 요청한 회사·연도·보고서의 응답인지 본다 — 다른 기간의 값을 요청 기간으로 라벨하지 않게.
        if any(ln.get("corp_code") not in (None, request["corp_code"])
               or str(ln.get("bsns_year") or request["bsns_year"]) != request["bsns_year"]
               or str(ln.get("reprt_code") or request["reprt_code"]) != request["reprt_code"]
               for ln in items):
            rejects.append({**{k: request.get(k) for k in ("corp_code", "bsns_year", "reprt_code")},
                            "raw_key": obj["key"], "reasons": ["response_identity_mismatch"]})
            reject_response(kind, target, "response_identity_mismatch")
            continue
        target = (request["corp_code"], request["bsns_year"], request["reprt_code"])
        if kind == "statement":
            statements[(*target, request["fs_div"])] = {**obj, "body_json": {**body, "list": items}}
        else:
            shares[target] = {**obj, "body_json": {**body, "list": items}}

    def finish(row: dict, sources: list[dict]) -> dict:
        """수신·가시시각·근거 키를 채운다. 접수일을 모두 확인한 행만 공개일 기준 가시시각을 갖는다."""
        received = max(src["fetched_at"] for src in sources)
        # 입력 전부의 접수일을 확인해야 공개일 기준이다 — 접수번호 없는 입력은 "모름"으로 센다.
        release = [rcept_dates.get(i.get("rcept_no")) for i in row["inputs"]]
        row.update({"received_at": received, "raw_key": sources[0]["key"], "raw_sha256": sources[0]["sha256"],
                    "inputs": json.dumps(row["inputs"], ensure_ascii=False, sort_keys=True)})
        if release and all(release):
            row["rcept_date"] = max(release)
            row["available_at"] = min(received, _kst_midnight_after(row["rcept_date"]),
                                      key=datetime.fromisoformat)
            row["availability_basis"] = "provider_release_date"
        else:
            # 접수일을 목록에서 확인 못 한 판본 — 공개일을 추정하지 않고 실제 수신부터 보이게 한다.
            row.update({"rcept_date": None, "available_at": received, "availability_basis": "received"})
        return row

    by_report: dict[tuple, list[tuple[dict, list[dict]]]] = {}
    for (corp_code, year, code, fs_div), statement in sorted(statements.items()):
        share = shares.get((corp_code, year, code))
        version = versions.get((corp_code, year, code, fs_div))
        if version is not None:
            share_entry = share_entries.get((corp_code, year, code))
            rejected = share_rejects.get((corp_code, year, code))
            # 수집은 ok 였어도 정제가 거부한 분모 응답은 "확인된 응답"이 아니다 — 분모 부재를 확정으로 읽지 않게.
            version["detail"].update({
                "shares": "error" if rejected else (share_entry["status"] if share_entry else "missing"),
                "shares_detail": rejected or (share_entry.get("detail") if share_entry else None)})
            if share is not None:
                # 분모 응답을 쓴 판본의 수신시각은 두 응답 중 늦은 쪽 — bps 행과 같은 규칙.
                version["received_at"] = max(version["received_at"], share["fetched_at"])
            numbers = {ln.get("rcept_no") for ln in statement["body_json"]["list"]
                       if dart_fundamental.RCEPT_NO.fullmatch(str(ln.get("rcept_no")))}
            if len(numbers) > 1:
                # 한 재무제표 응답은 접수번호 하나다 — 원본·정정 줄이 섞이면 어느 공개일의 값인지 정할 수 없다.
                rejects.append({"corp_code": corp_code, "bsns_year": year, "reprt_code": code, "fs_basis": fs_div,
                                "raw_key": statement["key"], "reasons": ["mixed_rcept_no"]})
                unconfirmed((corp_code, year, code, fs_div), "mixed_rcept_no")
                continue
            version["rcept_no"] = next(iter(numbers), None)
        try:
            extracted, bad = dart_fundamental.extract(
                {"corp_code": corp_code, "stock_code": corps[corp_code]["stock_code"]},
                year, code, fs_div, statement, share["body_json"] if share else None)
        except Exception as exc:
            # 한 보고서의 응답 파손이 다른 회사·보고서의 정제를 막지 않게 그 보고서만 거부한다.
            rejects.append({"corp_code": corp_code, "bsns_year": year, "reprt_code": code, "fs_basis": fs_div,
                            "raw_key": statement["key"], "reasons": ["extract_error"], "error": type(exc).__name__})
            unconfirmed((corp_code, year, code, fs_div), f"extract_error:{type(exc).__name__}")
            continue
        rejects.extend({**b, "raw_key": statement["key"]} for b in bad)
        # 재무제표 줄의 접수번호 파손(metric 없는 거부)은 응답 파손 — 남은 줄로 만든 지표를 확정 판본에 싣지 않는다.
        if any("bad_rcept_no" in (b.get("reasons") or []) and b.get("metric") is None for b in bad):
            unconfirmed((corp_code, year, code, fs_div), "bad_rcept_no")
            continue
        if version is not None:
            version["metrics"].extend(f'{r["metric"]}/{r["period_kind"]}/{r["fiscal_period"]}' for r in extracted)
            version["rejected"].extend({"metric": b.get("metric"), "reasons": b.get("reasons")} for b in bad)
        for row in extracted:
            # 두 BPS 지표의 분모는 주식총수 응답이다 — 그 수신시각이 행의 수신시각에 들어가야 한다.
            sources = [statement] + ([share] if share and row["metric"] in ("bps", "bps_total_shares") else [])
            for item in row["inputs"]:
                item["raw_key"] = statement["key"] if "account_id" in item else (share or statement)["key"]
            by_report.setdefault((corp_code, year, fs_div), []).append((row, sources))
    for (corp_code, year, fs_div), items in by_report.items():
        fy = [r for r, _ in items if r["fiscal_period"] == "FY"]
        if fy:
            q3_version = versions.get((corp_code, year, "11014", fs_div))
            if q3_version is None or q3_version["status"] == "UNCONFIRMED":
                # Q4 유도 입력(9M)을 이 실행이 확정하지 못했다(응답 실패, 또는 수집이 Q3 요청 전에 멈춰 판본 자체가 없음 —
                # 수집은 FY 를 계획하면 Q3 도 함께 요청하므로 부재는 중단이다). 사업보고서 판본을 "Q4 없음"으로 확정하면
                # 옛 확정 Q4 가 과거 조회에서 NULL 로 바뀐다. 판본을 미확정으로 두고 이 실행의 FY·Q4 행은 싣지 않는다.
                unconfirmed((corp_code, year, "11011", fs_div), "q4_input_unconfirmed")
                items = [(r, s) for r, s in items if r["fiscal_period"] not in ("FY", "Q4")]
                rows.extend(finish(dict(row), sources) for row, sources in items)
                continue
            q3 = [r for r, _ in items if r["fiscal_period"] == "Q3"]
            derived, bad = dart_fundamental.derive_q4(fy, q3)
            rejects.extend(bad)
            annual = versions.get((corp_code, year, "11011", fs_div))
            if annual is not None:      # Q4 유도 행·거부는 사업보고서 판본의 것이다
                annual["metrics"].extend(f'{r["metric"]}/{r["period_kind"]}/Q4' for r in derived)
                annual["rejected"].extend({"metric": b.get("metric"), "reasons": b.get("reasons")} for b in bad)
            fy_src = {r["metric"]: src for r, src in items if r["fiscal_period"] == "FY"}
            q3_src = {r["metric"]: src for r, src in items if r["fiscal_period"] == "Q3"}
            items = items + [(r, fy_src[r["metric"]] + q3_src[r["metric"]]) for r in derived]
        rows.extend(finish(dict(row), sources) for row, sources in items)
    # 판본은 자기 지표가 다 보일 때부터 보인다 — 유도 Q4 처럼 다른 보고서(정정 Q3)의 공개일이 섞인 지표가 있으면
    # 판본 가시시각을 그 뒤로 미룬다. 앞당기면 정정 전 기준시각에서 새 판본이 뽑히고 지표는 걸러져 NULL 이 된다.
    owned: dict[tuple, list[dict]] = {}
    code_of = {"Q1": "11013", "Q2": "11012", "Q3": "11014", "Q4": "11011", "FY": "11011"}
    for row in rows:
        owned.setdefault((row["corp_code"], str(row["fiscal_year"]), code_of[row["fiscal_period"]], row["fs_basis"]),
                         []).append(row)
    version_rows = []
    for (corp_code, year, code, fs_div), version in versions.items():
        mine = owned.get((corp_code, year, code, fs_div), [])
        if mine:
            version["received_at"] = max([version["received_at"], *(r["received_at"] for r in mine)],
                                         key=datetime.fromisoformat)
            if any(r["availability_basis"] == "received" for r in mine):
                version["rcept_no_for_release"] = None            # 입력 하나라도 접수일을 모르면 수신 기준
            else:
                version["release_day"] = max(r["rcept_date"] for r in mine)
        if version["detail"]["shares"] is None:
            # 재무제표가 empty(013)·오류라 추출 루프에 안 들어간 판본도 분모 응답 상태는 있어야 한다 —
            # 없으면 조회가 정상 확인된 부재를 "분모 미확정"으로 읽는다.
            share_entry = share_entries.get((corp_code, year, code))
            rejected = share_rejects.get((corp_code, year, code))
            version["detail"].update({
                "shares": "error" if rejected else (share_entry["status"] if share_entry else "missing"),
                "shares_detail": rejected or (share_entry.get("detail") if share_entry else None)})
        # 표 자체의 파손(접수번호·기준일·종류별 합·합계 숫자)은 자본 계정·재무제표 유무와 무관하게 분모 미확정이다 —
        # 손익 지표와 판본은 유지하고 BPS 만 BPS_UNCONFIRMED 로 읽히게. 한 규칙(share_table_problem)을 _bps 와 같이 쓴다.
        share = shares.get((corp_code, year, code))
        if share is not None and version["detail"]["shares"] == "ok":
            problem = dart_fundamental.share_table_problem(share["body_json"], dart_fundamental._period_end(year, code))
            if problem:
                version["detail"].update({"shares": "error", "shares_detail": problem})
        day = rcept_dates.get(version["rcept_no"]) if version["rcept_no"] else None
        if "rcept_no_for_release" in version:
            day = None
        elif version.get("release_day") and day:
            day = max(day, version["release_day"])
        version.pop("rcept_no_for_release", None)
        version.pop("release_day", None)
        received = version["received_at"]
        # 확정 못 한 시도는 수신시각부터만 보인다 — 응답에 섞인 접수번호로 실패를 공개일로 소급하면
        # 과거 기준시각 조회에 아직 일어나지 않은 실패가 나타난다.
        if day and version["status"] == "CONFIRMED":
            version.update({"rcept_date": day, "available_at": min(received, _kst_midnight_after(day),
                                                                     key=datetime.fromisoformat),
                            "availability_basis": "provider_release_date"})
        else:
            version.update({"available_at": received, "availability_basis": "received"})
        version_rows.append({**version, **{k: json.dumps(version[k], ensure_ascii=False, sort_keys=True)
                                           for k in ("metrics", "rejected", "detail")}})
    return rows, rejects, version_rows


FINANCIAL_VERSION = DatasetSpec(
    dataset="financial_report_version",
    columns=(Column("corp_code", "str"), Column("instrument_code", "str"), Column("fiscal_year", "int"),
             Column("reprt_code", "str"), Column("report_period", "str"), Column("fs_basis", "str"),
             Column("status", "str"), Column("rcept_no", "str"), Column("rcept_date", "date"),
             Column("metrics", "str"), Column("rejected", "str"), Column("detail", "str"), *_PROVENANCE),
    key=("corp_code", "fiscal_year", "reprt_code", "fs_basis"),
    partition=lambda r: "",                      # 현재 상태 파티션 없음 — 실행별 artifact·DB 에만
    normalize=lambda objects, manifest: ([], []),   # _normalize_financial 이 지표와 함께 만든다
    table="financial_report_version",
    collection_vendor="dart",
)

FINANCIAL = DatasetSpec(
    dataset="financial_metric",
    columns=(Column("corp_code", "str"), Column("instrument_code", "str"), Column("fiscal_year", "int"),
             Column("fiscal_period", "str"), Column("period_end", "date"), Column("metric", "str"),
             Column("period_kind", "str"), Column("fs_basis", "str"), Column("derivation", "str"),
             Column("value", "str"), Column("unit", "str"), Column("formula", "str"), Column("inputs", "str"),
             Column("rcept_no", "str"), Column("rcept_date", "date"), *_PROVENANCE),
    key=("corp_code", "fiscal_year", "fiscal_period", "metric", "period_kind", "fs_basis"),
    partition=lambda r: canonical_financial_metric_partition("KR", r["period_end"]),
    normalize=_normalize_financial,
    table="financial_metric",
    collection_vendor="dart",
    companion=FINANCIAL_VERSION,
)


def filing_window(today_kst: date, from_date: str | None, to_date: str | None) -> tuple[date, date]:
    """정기 수집은 접수일 (오늘 − 14일 ~ 오늘), 백필은 명시한 접수일 기간(오늘 이후 불가)."""
    if from_date is None and to_date is None:
        return today_kst - timedelta(days=REGULAR_FILING_DAYS), today_kst
    if from_date is None or to_date is None:
        raise SystemExit("백필은 --from 과 --to 를 함께 준다")
    start, end = date.fromisoformat(from_date), date.fromisoformat(to_date)
    if end > today_kst or start > end:
        raise SystemExit(f"접수일 기간이 유효하지 않다: {start}~{end} (오늘 {today_kst} 이후 불가)")
    return start, end


def collect_financial(storage: Storage, source, run_id: str, *, etf_ids: list[str],
                      from_date: str | None, to_date: str | None, now: datetime | None = None) -> int:
    """구성종목(기간별 스냅샷) × 창 안에 접수된 정기보고서 → 목록·전체 재무제표(CFS·OFS)·주식총수 응답을 그대로 남긴다."""
    from ..sources.http import StopFetch

    producer = "ingest_raw_financial_metric"
    requested = {"from": from_date, "to": to_date, "etf_ids": sorted(etf_ids)}
    done = existing_raw_manifest(storage, FINANCIAL.dataset, run_id)
    if done is not None:
        ensure_same_request(FINANCIAL.dataset, run_id, done, requested)
        return record_already_collected(storage, FINANCIAL, run_id, producer, done)
    started_at = now or datetime.now(timezone.utc)
    start, end = filing_window(started_at.astimezone(KST).date(), from_date, to_date)
    tickers, coverage = constituents_between(storage, etf_ids, start, end)
    scope = {"etf_ids": etf_ids, "filing_window": {"from": start.isoformat(), "to": end.isoformat()},
             "constituents": tickers, "holdings_coverage": coverage, "corps": {}, "unmapped": [],
             "mode": "backfill" if from_date else "regular", "requested": requested}
    if not tickers:
        # 그 기간의 구성종목 스냅샷이 없다 — 현재 구성으로 대신하지 않는다.
        return write_raw_run(storage, FINANCIAL, run_id, producer=producer, objects=[
            RawObject("dart", "market", "KR", "universe", "json", None, {"kind": "universe"}, "error",
                      "no_holdings_snapshot", started_at.isoformat())],
            started_at=started_at, request_scope=scope)
    objects: list[RawObject] = []

    def keep(kind: str, stem: str, result) -> None:
        """DART 응답 하나를 raw 객체로 남긴다(상태·요청 서술 포함). 중단 응답이면 남긴 뒤 수집을 멈춘다."""
        objects.append(RawObject("dart", "market", "KR", stem, "json", result.body,
                                 {"kind": kind, **result.request}, result.status, result.detail, result.fetched_at))
        if getattr(result, "stop", False):
            raise StopFetch(f"DART {result.detail}")
    try:
        corp_map = source.corp_map()
    except Exception as exc:
        # 키·한도(StopFetch)뿐 아니라 재시도 소진·파싱 실패도 수집 실패로 기록한다 — 예외가 새면 raw manifest 도
        # collection_log 도 없어 품질 경로에서 안 보이고 재처리 입력도 없다.
        corp_map = {}
        objects.append(RawObject("dart", "market", "KR", "corpcode", "json", None, {"kind": "corp_map"}, "error",
                                 str(exc)[:200], datetime.now(timezone.utc).isoformat()))
    try:
        for ticker in tickers if corp_map else []:
            corp = corp_map.get(ticker)
            if corp is None:
                scope["unmapped"].append(ticker)       # 우선주·비신고 종목 — DART 공시 주체가 아니다
                continue
            corp_code = corp["corp_code"]
            scope["corps"][corp_code] = {"stock_code": ticker, "corp_name": corp.get("corp_name")}
            try:
                pages = source.filings(corp_code, start - timedelta(days=LIST_LOOKBACK_DAYS), end)
                for number, page in enumerate(pages, start=1):
                    keep("list", f"{corp_code}-list-p{number}", page)
                listed = []
                for page in (p for p in pages if p.status == "ok"):
                    body = json.loads(page.body.decode("utf-8"))
                    items = body.get("list") if isinstance(body, dict) else None
                    listed.extend(items if isinstance(items, list) else [])
                # 결산월은 사업보고서가 정하고(plan_reports), 3분기 정정은 Q4 재유도에 그 해 사업보고서가 필요하다. 창 안
                # 분기보고서의 사업보고서가 목록 소급 폭(LIST_LOOKBACK_DAYS) 밖이면 그 해의 다음 해 목록을 한 번 더 받는다
                # (회사당 해당 연도 수만큼) — 계획 전에 받아 결산월 확인과 Q4 재계획을 한 번에 한다.
                lo, hi = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
                in_window = {p[0] for row in listed if isinstance(row, dict)
                             and isinstance(row.get("rcept_dt"), str) and lo <= row["rcept_dt"] <= hi
                             and (p := dart_fundamental.report_of(row.get("report_nm"))) and p[1] != "11011"}
                annual_years = {p[0] for row in listed if isinstance(row, dict)
                                and (p := dart_fundamental.report_of(row.get("report_nm"))) and p[1] == "11011"}
                for year in sorted(in_window - annual_years):
                    fy_start = date(int(year) + 1, 1, 1)
                    if fy_start > end:
                        continue                      # 사업보고서가 아직 나올 수 없는 해
                    extra = source.filings(corp_code, fy_start, min(date(int(year) + 1, 12, 31), end))
                    for number, page in enumerate(extra, start=1):
                        keep("list", f"{corp_code}-list-fy{year}-p{number}", page)
                    for page in (p for p in extra if p.status == "ok"):
                        body = json.loads(page.body.decode("utf-8"))
                        items = body.get("list") if isinstance(body, dict) else None
                        listed.extend(items if isinstance(items, list) else [])
                targets, unsupported = dart_fundamental.plan_reports(listed, start, end)
                scope.setdefault("unsupported_reports", []).extend(unsupported)
                for year, code in sorted(targets):
                    for fs_div in ("CFS", "OFS"):
                        keep("statement", f"{corp_code}-{year}-{code}-{fs_div}",
                             source.statement(corp_code, year, code, fs_div))
                    keep("shares", f"{corp_code}-{year}-{code}-shares", source.shares(corp_code, year, code))
            except StopFetch:
                raise
            except Exception as exc:
                # 한 회사의 응답 파손이 다른 회사 수집과 이미 받은 raw 저장을 막지 않게 그 회사만 실패로 남긴다.
                objects.append(RawObject("dart", "market", "KR", f"{corp_code}-error", "json", None,
                                         {"kind": "corp", "corp_code": corp_code}, "error", type(exc).__name__,
                                         datetime.now(timezone.utc).isoformat()))
    except StopFetch as exc:
        # 키·한도·점검 — 남은 호출을 멈추고 받은 것까지만 남긴다(부분 실패로 드러난다).
        objects.append(RawObject("dart", "market", "KR", "stopped", "json", None, {"kind": "stop"}, "error",
                                 str(exc)[:200], datetime.now(timezone.utc).isoformat()))
    return write_raw_run(storage, FINANCIAL, run_id, producer=producer, objects=objects,
                         started_at=started_at, request_scope=scope)
