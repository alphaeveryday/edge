"""DART 사업보고서 '사업의 내용' 사업부문별 매출 파서 (euc-kr HTML → 사업부문 fact rows).

⚠️ 출처(이식): 이 파일의 파싱 로직은 팀원 **정준영**의 검증 프로토타입(alphamale)
`src/alphamale/filings/dart/segments.py`(parser_version `"segments-v2"`)의 **순수 파서 절반**을
edge 프로덕션 파이프라인으로 이식한 것이다. jy 는 별 저장소라 의존성 import 하지 않고 코드를
반입했다. theme 링킹·`segments_to_edges`(graph 투영)와 그 의존성(`alphamale.news.universe.kr`)은
edge 범위 밖(analysis-engine 소관)이라 **이식하지 않았다** — 이 모듈은 본문 str →
`(rows, stats)` 로 끝나는 순수 파서다(rcept_no·corp_code·ticker·report_date 는 raw 메타에서
정제 스텝이 조인한다).

`parse_segments(html) -> (rows, stats)`:
  rows  = [{segment_name, revenue_share_pct, revenue_krw, period, share_basis[, reported_share]}]
  stats = {tables_seen, rows, share_basis, share_sum}
4-전략 추출(metric_row·metric_column·section_amount·standard) + 헤더 승격·기간 검출 후 후보
테이블을 점수화해 best 를 고르고, share 를 정규화한다(share_basis ∈
{reported(합≈100)·rescaled·computed(금액→비중)·unreliable}). pandas.read_html(lxml) + bs4 의존.
"""

from __future__ import annotations

import re
import unicodedata
from io import StringIO
from typing import Any

import pandas as pd
from bs4 import BeautifulSoup, Tag

PARSER_VERSION = "segments-v2"

_HEADER_GROUP_A = re.compile(r"매출|부문|제품|품목|영업수익|서비스")
_HEADER_GROUP_B = re.compile(r"비중|비율|%|매출액|금액|수익")
_YEAR_RE = re.compile(r"20\d{2}\s*년(?:\s*\d+~\d+월)?")
_TERM_RE = re.compile(r"제\s*(\d+)\s*기")
_PERIOD_RE = re.compile(r"(20\d{2}\s*년(?:\s*\d+~\d+월)?|제\s*\d+\s*기)")
_SECTION_ROW_RE = re.compile(r"^\d+\.\s*")
_GENERIC_NAMES = {"제품", "상품", "기타", "제/상품", "제ㆍ상품", "-", "및"}
_AGGREGATE_NAMES = {"영업수익", "매출", "매출액", "총매출액", "순매출액", "수익"}
_RAW_MATERIAL_RE = re.compile(r"원재료|매입유형|매입처|투입액|구체적용도|생산실적|가동률|생산설비")
_SALES_CONTEXT_RE = re.compile(r"매출|영업수익|서비스|판매|수주")
_NON_SEGMENT_RE = re.compile(r"^(?:구분|비중|전년비\s*증감률|총수출|매출액|영업이익|당기순이익|연결조정(?:\s*후)?|내부매출액|총매출액|순매출액|제\s*\d+\s*기)$")


def _clean_text(value: Any) -> str:
    text = "" if value is None else str(value)
    text = unicodedata.normalize("NFKC", text).replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def _clean_segment_name(value: Any) -> str:
    text = _clean_text(value)
    text = re.sub(r"^\(단위[^)]*\)\s*", "", text)
    text = re.sub(r"\(\*\)$", "", text).strip()
    return text


def _name_key(name: str) -> str:
    return re.sub(r"\s+", "", _clean_text(name))


def _is_total_name(name: str) -> bool:
    return _name_key(name) in {"합계", "총계", "계", "소계"}


def _parse_number(value: Any) -> float | None:
    text = _clean_text(value)
    if not text or text.lower() == "nan":
        return None
    negative = "△" in text or (text.startswith("(") and text.endswith(")"))
    matches = re.findall(r"\d+(?:,\d{3})*(?:\.\d+)?", text)
    if not matches:
        return None
    number = float(matches[0].replace(",", ""))
    return -number if negative else number


def _parse_pct(value: Any) -> float | None:
    number = _parse_number(value)
    return None if number is None else float(number)


def _parse_amount(value: Any) -> int | None:
    number = _parse_number(value)
    return None if number is None else int(round(number))


def _uniquify_columns(columns: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    unique: list[str] = []
    for column in columns:
        base = column or "col"
        seen[base] = seen.get(base, 0) + 1
        unique.append(base if seen[base] == 1 else f"{base}_{seen[base]}")
    return unique


def _flatten_declared_columns(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    columns: list[str] = []
    if isinstance(frame.columns, pd.MultiIndex):
        raw_columns = frame.columns.to_list()
    else:
        raw_columns = [(col,) for col in frame.columns.to_list()]
    for raw in raw_columns:
        parts = [
            text
            for text in (_clean_text(part) for part in raw)
            if text and not text.startswith("Unnamed")
        ]
        columns.append(" ".join(dict.fromkeys(parts)).strip() or "col")
    frame.columns = _uniquify_columns(columns)
    return frame


def _promote_header_rows(frame: pd.DataFrame) -> pd.DataFrame | None:
    preview = [[_clean_text(value) for value in row] for row in frame.head(3).itertuples(index=False, name=None)]
    header_rows: list[list[str]] = []
    for row in preview:
        nonempty = [value for value in row if value and value.lower() != "nan"]
        if not nonempty:
            break
        numeric = sum(_parse_number(value) is not None for value in nonempty)
        has_period = any(_PERIOD_RE.search(value) for value in nonempty)
        unit_only = all("단위" in value for value in nonempty)
        if unit_only or has_period or numeric * 2 <= len(nonempty):
            header_rows.append(row)
            continue
        break
    if not header_rows:
        return None

    promoted_rows = [row for row in header_rows if not all("단위" in value for value in row if value)]
    if not promoted_rows:
        return None

    body = frame.iloc[len(header_rows):].copy().reset_index(drop=True)
    if body.empty:
        return None

    columns: list[str] = []
    for idx in range(frame.shape[1]):
        parts: list[str] = []
        for row in promoted_rows:
            value = row[idx]
            if not value or value.lower() == "nan" or value.startswith("Unnamed") or "단위" in value:
                continue
            parts.append(value)
        columns.append(" ".join(dict.fromkeys(parts)).strip() or f"col{idx}")
    body.columns = _uniquify_columns(columns)
    return body


def _flatten_columns(frame: pd.DataFrame) -> pd.DataFrame:
    if all(re.fullmatch(r"\d+", str(column)) for column in frame.columns):
        promoted = _promote_header_rows(frame)
        if promoted is not None:
            return promoted
    return _flatten_declared_columns(frame)


def _nearby_text(table: Tag, *, limit: int = 4) -> str:
    texts: list[str] = []
    node = table
    while len(texts) < limit:
        node = node.previous_sibling
        if node is None:
            break
        if isinstance(node, Tag):
            text = _clean_text(node.get_text(" ", strip=True))
        else:
            text = _clean_text(node)
        if text:
            texts.append(text)
    texts.reverse()
    return " ".join(texts)


def _label_column(frame: pd.DataFrame) -> str:
    for column in frame.columns:
        values = [_clean_text(value) for value in frame[column].tolist()]
        values = [value for value in values if value and value.lower() != "nan"]
        if len(values) < 2:
            continue
        numeric = sum(_parse_number(value) is not None for value in values)
        if numeric * 2 <= len(values):
            return str(column)
    return str(frame.columns[0])


def _period_rank(text: str | None) -> int:
    cleaned = _clean_text(text)
    year_match = _YEAR_RE.search(cleaned)
    if year_match:
        return int(re.search(r"20\d{2}", year_match.group(0)).group(0))
    term_match = _TERM_RE.search(cleaned)
    if term_match:
        return int(term_match.group(1))
    return -1


def _extract_period_label(text: str | None) -> str | None:
    cleaned = _clean_text(text)
    year_match = _YEAR_RE.search(cleaned)
    if year_match:
        return year_match.group(0)
    term_match = _TERM_RE.search(cleaned)
    if term_match:
        return f"제{term_match.group(1)}기"
    return None


def _frame_period(context_text: str, *labels: str | None) -> str | None:
    candidates = [label for label in labels if _extract_period_label(label)]
    if candidates:
        return _extract_period_label(max(candidates, key=_period_rank))
    return _extract_period_label(context_text)


def _choose_column(columns: list[str], pattern: re.Pattern[str], *, period: str | None) -> str | None:
    matches = [(idx, column) for idx, column in enumerate(columns) if pattern.search(column)]
    if not matches:
        return None
    if period:
        same_period = [item for item in matches if period in item[1]]
        if same_period:
            matches = same_period
    return max(matches, key=lambda item: (_period_rank(item[1]), -item[0]))[1]


def _pick_segment_name(row: dict[str, str], label_column: str) -> str:
    primary = _clean_segment_name(row.get(label_column))
    if primary and primary not in _GENERIC_NAMES and not _is_total_name(primary):
        return primary
    for value in row.values():
        text = _clean_segment_name(value)
        if not text or text in _GENERIC_NAMES or _is_total_name(text):
            continue
        if _parse_number(text) is None and len(text) >= 2:
            return text
    return primary


def _value_columns(frame: pd.DataFrame, *, exclude: set[str]) -> list[str]:
    columns: list[str] = []
    for column in frame.columns:
        if column in exclude:
            continue
        values = [_clean_text(value) for value in frame[column].tolist()]
        if sum(_parse_number(value) is not None for value in values) >= 1:
            columns.append(str(column))
    return columns


def _latest_value_column(frame: pd.DataFrame, *, exclude: set[str]) -> str | None:
    columns = _value_columns(frame, exclude=exclude)
    if not columns:
        return None
    indexed = [(idx, column) for idx, column in enumerate(columns)]
    return max(indexed, key=lambda item: (_period_rank(item[1]), -item[0]))[1]


def _row_has_total_marker(row: dict[str, str], label_column: str) -> bool:
    return any(
        _is_total_name(value)
        for column, value in row.items()
        if column != label_column and _clean_text(value)
    )


def _should_skip_frame(frame: pd.DataFrame, context_text: str) -> bool:
    haystack = _clean_text(f"{context_text} {' '.join(map(str, frame.columns))}")
    return bool(_RAW_MATERIAL_RE.search(haystack) and not _SALES_CONTEXT_RE.search(haystack))


def _postprocess_segments(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    filtered = [
        row
        for row in rows
        if row.get("segment_name")
        and not _is_total_name(str(row["segment_name"]))
        and not _NON_SEGMENT_RE.match(_clean_segment_name(row["segment_name"]))
    ]
    if len(filtered) < 3:
        return filtered

    kept: list[dict[str, Any]] = []
    for idx, row in enumerate(filtered):
        others = filtered[:idx] + filtered[idx + 1:]
        share = row.get("revenue_share_pct")
        amount = row.get("revenue_krw")
        other_share = sum(float(other["revenue_share_pct"]) for other in others if other.get("revenue_share_pct") is not None)
        other_amount = sum(int(other["revenue_krw"]) for other in others if other.get("revenue_krw") is not None)
        segment_key = _name_key(str(row["segment_name"]))
        child_like = any(str(other["segment_name"]).lstrip().startswith(("-", "·")) for other in others)
        aggregate = segment_key in {_name_key(name) for name in _AGGREGATE_NAMES}
        share_matches = share is not None and other_share > 0 and abs(other_share - float(share)) <= 1.0
        amount_matches = amount is not None and other_amount > 0 and abs(other_amount - int(amount)) <= max(1.0, abs(int(amount)) * 0.02)
        if (aggregate or child_like) and (share_matches or amount_matches or (share is not None and float(share) >= 99.0)):
            continue
        kept.append(row)
    return kept or filtered


def _extract_standard_rows(frame: pd.DataFrame, context_text: str) -> list[dict[str, Any]]:
    frame = _flatten_columns(frame)
    if _should_skip_frame(frame, context_text):
        return []

    label_column = _label_column(frame)
    period = _frame_period(context_text, *frame.columns)
    columns = [str(column) for column in frame.columns]
    share_column = _choose_column(columns, re.compile(r"비중|비율|%"), period=period)
    amount_column = _choose_column(columns, re.compile(r"매출액|영업수익|금액"), period=period)
    if share_column is None:
        combined_column = _choose_column(columns, re.compile(r"매출액\s*\(?비율\)?|금액\s*\(?비율\)?"), period=period)
    else:
        combined_column = None
    if combined_column:
        share_column = combined_column
        amount_column = combined_column
    if amount_column is None and share_column is None:
        amount_column = _latest_value_column(frame, exclude={label_column})
    if amount_column is None and share_column is None:
        return []

    period = _frame_period(context_text, share_column, amount_column)
    segments: list[dict[str, Any]] = []
    for _, series in frame.iterrows():
        row = {str(column): _clean_text(value) for column, value in series.items()}
        segment_name = _pick_segment_name(row, label_column)
        if not segment_name or _is_total_name(segment_name):
            continue
        if _row_has_total_marker(row, label_column):
            continue
        if re.search(r"내부거래|제거|매출에누리", segment_name):
            continue

        revenue_share_pct = _parse_pct(row.get(share_column)) if share_column else None
        revenue_krw = _parse_amount(row.get(amount_column)) if amount_column else None
        if combined_column:
            cell = row.get(combined_column, "")
            matches = re.findall(r"\d+(?:,\d{3})*(?:\.\d+)?", cell)
            if matches:
                revenue_krw = int(round(float(matches[0].replace(",", ""))))
            if len(matches) >= 2:
                revenue_share_pct = float(matches[1].replace(",", ""))

        if revenue_share_pct is None and revenue_krw is None:
            continue
        if revenue_share_pct is not None and (revenue_share_pct <= 0 or revenue_share_pct > 100):
            continue
        segments.append(
            {
                "segment_name": segment_name,
                "revenue_share_pct": revenue_share_pct,
                "revenue_krw": revenue_krw,
                "period": period,
            }
        )
    return _postprocess_segments(segments)


def _extract_metric_row_segments(frame: pd.DataFrame, context_text: str) -> list[dict[str, Any]]:
    frame = _flatten_columns(frame)
    if _should_skip_frame(frame, context_text):
        return []

    text_columns = [str(column) for column in frame.columns if str(column) not in _value_columns(frame, exclude=set())]
    metric_column = next(
        (
            column
            for column in text_columns
            if any(_clean_text(value) in {"매출액", "순매출액", "총매출액", "영업수익"} for value in frame[column].tolist())
            and any(re.search(r"영업이익|내부매출액|연결조정", _clean_text(value)) for value in frame[column].tolist())
        ),
        None,
    )
    if metric_column is None:
        return []

    columns = [str(column) for column in frame.columns if str(column) != metric_column]
    period = _frame_period(context_text, *columns)
    amount_column = _choose_column(columns, re.compile(r"금액"), period=period)
    share_column = _choose_column(columns, re.compile(r"비중|비율|%"), period=period)
    if amount_column is None and share_column is None:
        return []

    label_candidates = [column for column in text_columns if column != metric_column]
    if not label_candidates:
        return []
    label_column = label_candidates[0]
    period = _frame_period(context_text, amount_column, share_column)
    preferred_metric = "순매출액" if any(_clean_text(value) == "순매출액" for value in frame[metric_column].tolist()) else "매출액"

    segments: list[dict[str, Any]] = []
    for _, series in frame.iterrows():
        metric_name = _clean_text(series.get(metric_column))
        if metric_name != preferred_metric:
            continue
        row = {str(column): _clean_text(value) for column, value in series.items()}
        segment_name = _pick_segment_name(row, label_column)
        if not segment_name or _is_total_name(segment_name):
            continue
        revenue_share_pct = _parse_pct(row.get(share_column)) if share_column else None
        revenue_krw = _parse_amount(row.get(amount_column)) if amount_column else None
        if revenue_share_pct is None and revenue_krw is None:
            continue
        segments.append(
            {
                "segment_name": segment_name,
                "revenue_share_pct": revenue_share_pct,
                "revenue_krw": revenue_krw,
                "period": period,
            }
        )
    return _postprocess_segments(segments)


def _extract_metric_column_segments(frame: pd.DataFrame, context_text: str) -> list[dict[str, Any]]:
    frame = _flatten_columns(frame)
    if _should_skip_frame(frame, context_text) or len(frame.columns) < 3:
        return []

    label_column = str(frame.columns[0])
    labels = [_clean_text(value) for value in frame[label_column].tolist()]
    if not any(value in {"매출액", "순매출액", "총매출액", "영업수익"} for value in labels):
        return []
    if not any(re.search(r"연결조정|연결후금액", str(column)) for column in frame.columns):
        return []

    preferred_metric = "순매출액" if "순매출액" in labels else ("매출액" if "매출액" in labels else "총매출액")
    metric_row = next((series for _, series in frame.iterrows() if _clean_text(series.get(label_column)) == preferred_metric), None)
    if metric_row is None:
        return []

    period = _frame_period(context_text, *frame.columns)
    segments: list[dict[str, Any]] = []
    for column in frame.columns[1:]:
        segment_name = _clean_segment_name(column)
        if not segment_name or re.search(r"연결조정|연결후금액|합계|총계", segment_name):
            continue
        revenue_krw = _parse_amount(metric_row.get(column))
        if revenue_krw is None or revenue_krw <= 0:
            continue
        segments.append(
            {
                "segment_name": segment_name,
                "revenue_share_pct": None,
                "revenue_krw": revenue_krw,
                "period": period,
            }
        )
    return _postprocess_segments(segments)


def _extract_section_amount_segments(frame: pd.DataFrame, context_text: str) -> list[dict[str, Any]]:
    frame = _flatten_columns(frame)
    if _should_skip_frame(frame, context_text) or len(frame.columns) < 2:
        return []

    label_column = _label_column(frame)
    label_values = [_clean_text(value) for value in frame[label_column].tolist()]
    if sum(bool(_SECTION_ROW_RE.match(value)) for value in label_values) < 2:
        return []

    amount_column = _latest_value_column(frame, exclude={label_column})
    if amount_column is None:
        return []

    period = _frame_period(context_text, amount_column)
    preferred_metric = "순매출액" if "순매출액" in label_values else ("매출액" if "매출액" in label_values else "총매출액")
    current_section: str | None = None
    segments: list[dict[str, Any]] = []
    for _, series in frame.iterrows():
        label = _clean_segment_name(series.get(label_column))
        if not label:
            continue
        if _is_total_name(label) or label == "총계":
            current_section = None
            continue
        if _SECTION_ROW_RE.match(label):
            current_section = _clean_segment_name(re.sub(r"^\d+\.\s*", "", label))
            continue
        if current_section is None or label != preferred_metric:
            continue
        revenue_krw = _parse_amount(series.get(amount_column))
        if revenue_krw is None or revenue_krw <= 0:
            continue
        segments.append(
            {
                "segment_name": current_section,
                "revenue_share_pct": None,
                "revenue_krw": revenue_krw,
                "period": period,
            }
        )
        current_section = None
    return _postprocess_segments(segments)


def _extract_from_frame(frame: pd.DataFrame, context_text: str) -> list[dict[str, Any]]:
    for extractor in (
        _extract_metric_row_segments,
        _extract_metric_column_segments,
        _extract_section_amount_segments,
        _extract_standard_rows,
    ):
        rows = extractor(frame, context_text)
        if rows:
            return rows
    return []


def _normalize_shares(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str, float]:
    share_rows = [row for row in rows if row.get("revenue_share_pct") is not None]
    if not share_rows:
        amount_rows = [row for row in rows if row.get("revenue_krw") is not None and int(row["revenue_krw"]) > 0]
        total_amount = sum(int(row["revenue_krw"]) for row in amount_rows)
        if len(amount_rows) >= 2 and total_amount > 0:
            running = 0.0
            for idx, row in enumerate(amount_rows):
                if idx == len(amount_rows) - 1:
                    share = round(100.0 - running, 2)
                else:
                    share = round((int(row["revenue_krw"]) / total_amount) * 100.0, 2)
                    running += share
                row["revenue_share_pct"] = share
            share_sum = round(sum(float(row["revenue_share_pct"]) for row in amount_rows), 2)
            share_basis = "computed"
        else:
            share_sum = 0.0
            share_basis = "unreliable"
    else:
        share_sum = round(sum(float(row["revenue_share_pct"]) for row in share_rows), 2)
        if share_sum < 50.0:
            share_basis = "unreliable"
        elif abs(share_sum - 100.0) <= 0.01:
            share_basis = "reported"
        else:
            share_basis = "rescaled"
            for row in rows:
                share = row.get("revenue_share_pct")
                if share is None:
                    continue
                row["reported_share"] = share
                row["revenue_share_pct"] = round((float(share) / share_sum) * 100.0, 2)
    for row in rows:
        row["share_basis"] = share_basis
    return rows, share_basis, share_sum


def _connection_rank(context_text: str, columns: list[str]) -> int:
    del columns
    haystack = _clean_text(context_text)
    if re.search(r"별도\s*기준|별도", haystack):
        return 0
    if re.search(r"연결\s*기준|연결실체", haystack):
        return 2
    return 1


def parse_segments(html_text: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """사업보고서 본문에서 사업부문별 매출 rows + stats 를 파싱한다.

    헤더군 A(매출·부문·제품)·B(비중·매출액·%) 를 모두 가진 표만 후보로 삼아 4-전략으로
    추출하고, share_basis·기간·연결기준 등을 점수화해 best 테이블을 고른 뒤 share 를 정규화한다.
    """
    soup = BeautifulSoup(html_text, "html.parser")
    tables = soup.find_all("table")
    candidates: list[tuple[tuple[int, int, int, int, int, int, int, float], list[dict[str, Any]], float]] = []
    for table in tables:
        context_text = _nearby_text(table)
        header_text = " ".join(
            _clean_text(cell.get_text(" ", strip=True))
            for cell in table.find_all(["th", "td"], limit=24)
        )
        haystack = f"{context_text} {header_text}"
        if not (_HEADER_GROUP_A.search(haystack) and _HEADER_GROUP_B.search(haystack)):
            continue
        for frame in pd.read_html(StringIO(str(table))):
            rows = _extract_from_frame(frame, context_text)
            if not rows:
                continue
            normalized_rows = [dict(row) for row in rows]
            normalized_rows, share_basis, share_sum = _normalize_shares(normalized_rows)
            basis_rank = {"reported": 3, "rescaled": 2, "computed": 1, "unreliable": 0}[share_basis]
            period_rank = max((_period_rank(str(row.get("period"))) for row in normalized_rows), default=-1)
            unique_names = {_name_key(str(row.get("segment_name") or "")) for row in normalized_rows if row.get("segment_name")}
            score = (
                basis_rank,
                _connection_rank(context_text, [str(column) for column in _flatten_columns(frame).columns]),
                int(2 <= len(normalized_rows) <= 12),
                int(len(unique_names) == len(normalized_rows)),
                len(unique_names),
                period_rank,
                sum(row.get("revenue_krw") is not None for row in normalized_rows),
                share_sum,
            )
            candidates.append((score, normalized_rows, share_sum))
    _, best_rows, best_share_sum = max(candidates, key=lambda item: item[0], default=((0, 0, 0, 0, 0, 0, 0, 0.0), [], 0.0))
    share_basis = str(best_rows[0]["share_basis"]) if best_rows else "unreliable"
    return best_rows, {
        "tables_seen": len(tables),
        "rows": len(best_rows),
        "share_basis": share_basis,
        "share_sum": best_share_sum,
    }


__all__ = ["parse_segments", "PARSER_VERSION"]
