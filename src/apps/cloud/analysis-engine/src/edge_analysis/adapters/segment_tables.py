"""부문 표 파서 — **공시 표에서 부문별 매출을 뽑는다. 보편 파서를 노리지 않는다.**

왜 필요한가. 노출도를 이진(상위 8종목 vs 나머지)으로 자르면 실질 자유도가 2~3으로
떨어진다. 연속 노출도(부문 매출 비중)로 회귀하면 같은 표본에서 정보량이 훨씬 커진다 -
앞서 "n=8" 로 보고했던 검정력 문제의 실제 해법이 이 파서다.

표 구조가 회사마다 다르다는 것이 이 파일의 전제다. 실측 세 형태:

    삼성전자    부문 | 주요제품 | 매출액 | 비중              (4열, 깔끔)
    SK하이닉스  사업부문 | 매출유형 | 품목 | ... | 매출액(비율)  (금액과 비율이 한 셀)
    POSCO      사업부문 | 품목 | 용도 | 3기간 × (매출액, 비율)  (부문이 ROWSPAN)

그래서 **표를 격자로 펴는 것**이 먼저다(ROWSPAN·COLSPAN 을 실제로 전개한다). 격자가
없으면 POSCO 의 열연·냉연 행에 부문 이름이 없어 조용히 빠진다 - 파싱 실패가 결손으로
위장되는 경로다.

값의 단위를 추측하지 않는다. 표면형(`surface`)을 그대로 남기고 숫자만 정규화한다.
`△110,167` 은 음수이고(공시 관행), `52,576,287(100%)` 은 금액과 비율이 붙어 있다.
합계 행은 버리지 않고 `is_total` 로 표시한다 - **부문 합 = 합계**가 파서의 자기 검사다.
"""
from __future__ import annotations

import re

_ROW = re.compile(r"<TR[^>]*>(.*?)</TR>", re.S | re.I)
_CELL = re.compile(r"<T([DH])([^>]*)>(.*?)</T\1>", re.S | re.I)
_SPAN = re.compile(r"\b(ROW|COL)SPAN\s*=\s*[\"']?(\d+)", re.I)
_TAGS = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
_NUM = re.compile(r"[△▲\-(]?\s*\d[\d,]*(?:\.\d+)?\s*\)?")
_GROUPED = re.compile(r"\d{1,3}(?:,\d{3})+")
_PCT = re.compile(r"(△?\s*-?\d+(?:\.\d+)?)\s*%")

# 부분문자열로 찾아도 되는 총계 낱말. **`계` 는 여기 없다** - `기계부문`·`설계` 처럼
# 그 음절을 품은 정상 부문명이 합계로 오인돼 `parts` 에서 빠지고, 그러면 부문 합이
# 총계보다 작아져 자기검사가 정상 표를 떨어뜨린다(관측 60% 갭).
TOTAL_WORDS = ("합계", "총계", "총 계", "소계", "합 계")
# 단독 라벨일 때만 총계로 보는 낱말. 칸 전체가 이것이어야 한다.
TOTAL_EXACT = ("계",)
SEG_WORDS = ("부문", "사업부문", "사업 부문", "세그먼트", "부  문")
REV_WORDS = ("매출액", "매출", "영업수익", "수익")
SHARE_WORDS = ("비중", "비율", "구성비")


def _text(html: str) -> str:
    return _WS.sub(" ", _TAGS.sub(" ", html or "")).strip()


def grid(table_xml: str) -> list[list[str]]:
    """표를 격자로 편다. **ROWSPAN·COLSPAN 을 실제로 전개한다.**

    전개하지 않으면 병합된 부문 이름이 첫 행에만 있고 나머지 행은 이름 없이 남는다 -
    그 행들이 조용히 빠지면 부문 합이 합계와 안 맞고, 그 불일치의 원인을 사후에 찾을 수
    없다. 격자를 먼저 만들면 결손과 파싱 실패가 구별된다.
    """
    out: list[list[str]] = []
    pending: dict[tuple[int, int], str] = {}      # (row, col) -> 값 (ROWSPAN 잔여)
    for r, row_html in enumerate(_ROW.findall(table_xml or "")):
        row: list[str] = []
        col = 0
        for _kind, attrs, body in _CELL.findall(row_html):
            while (r, col) in pending:            # 위에서 내려온 병합 칸 먼저 채운다
                row.append(pending.pop((r, col)))
                col += 1
            spans = {k.upper(): int(v) for k, v in _SPAN.findall(attrs)}
            rs, cs = spans.get("ROW", 1), spans.get("COL", 1)
            val = _text(body)
            for c in range(cs):
                row.append(val)
                for extra in range(1, rs):
                    pending[(r + extra, col + c)] = val
            col += cs
        while (r, col) in pending:                # 행 끝에 남은 병합 칸
            row.append(pending.pop((r, col)))
            col += 1
        if row:
            out.append(row)
    return out


def _to_num(s: str) -> float | None:
    """공시 숫자. **△ 와 괄호는 음수다** - 부호를 놓치면 부문 합이 안 맞는다."""
    m = _NUM.search(s or "")
    if not m:
        return None
    raw = m.group(0)
    neg = raw.lstrip().startswith(("△", "▲", "-", "("))
    try:
        v = float(re.sub(r"[^\d.]", "", raw))
    except ValueError:
        return None
    return -v if neg else v


def _share(s: str) -> float | None:
    m = _PCT.search(s or "")
    if not m:
        return None
    t = m.group(1).replace(" ", "")
    neg = t.startswith("△")
    v = float(t.lstrip("△"))
    return (-v if neg else v) / 100.0


def _bare_share(s: str) -> float | None:
    """`%` 없는 비율 칸. **비율 열이라는 것은 머리행이 이미 정했다.**

    자릿점이 있으면(`1,234`) 비율이 아니라 금액이므로 받지 않는다 - 비율 열에 금액이 온
    표는 머리행 판정이 틀린 것이고, 그건 추측으로 메울 자리가 아니다.
    """
    t = (s or "").strip().replace(" ", "")
    if not t or _GROUPED.search(t):
        return None
    neg = t.startswith("△")
    t = t.lstrip("△-")
    try:
        v = float(t)
    except ValueError:
        return None
    return (-v if neg or (s or "").strip().startswith("-") else v) / 100.0


def _header_rows(table_xml: str, g: list[list[str]]) -> int:
    """머리행 수. **`<TH>` 를 믿되 자릿점 숫자가 있으면 데이터로 본다.**

    두 번 고쳤다. 처음엔 "매출·부문 낱말이 있고 숫자가 없는 행"으로 셌다 - POSCO 의
    `2026년 (제59기) 1분기` 가 숫자로 잡혀 머리 두 줄 중 둘째(`매출액`·`비율`)를 데이터로
    읽었고, 결과는 조용한 빈 목록이었다. 그래서 `<TH>` 를 세는 규칙으로 바꿨는데, DART
    표는 데이터 행에도 `<TH>` 를 쓰는 곳이 있어(실측 SK하이닉스) 데이터가 머리에 먹혔다 -
    `period_label` 이 `매출액 52,576,287` 로 나오고 부문 행이 0개가 됐다.

    가르는 것은 **자릿점**이다. 기간 라벨의 수(2026·59·1)에는 콤마가 없고 금액에는 있다.
    """
    n = 0
    for row_html in _ROW.findall(table_xml or ""):
        kinds = [k.upper() for k, _a, _b in _CELL.findall(row_html)]
        cells = [_text(b) for _k, _a, b in _CELL.findall(row_html)]
        if not kinds or kinds.count("H") <= kinds.count("D"):
            break
        if any(_GROUPED.search(c) for c in cells):
            break                          # 자릿점 숫자가 있으면 데이터다
        n += 1
    if n:
        return n
    for row in g[:4]:                      # TH 가 없는 표를 위한 대비책
        joined = " ".join(row)
        if any(w in joined for w in (*REV_WORDS, *SEG_WORDS, *SHARE_WORDS)) \
                and not any(_GROUPED.search(c) for c in row):
            n += 1
        else:
            break
    return n or 1


def _is_total_row(row: list[str], upto: int) -> bool:
    """합계·소계 행인가. **라벨 칸 전부를 본다** - 부문 칸만 보면 소계가 부문으로 세어진다.

    실측 POSCO: 부문 이름은 ROWSPAN 으로 채워지고 소계 여부는 품목 칸에 적힌다. 부문 칸만
    보면 소계 행이 품목 행과 함께 더해져 합이 총계의 3배가 된다(관측 오차 216%).
    """
    for c in row[:max(upto, 1)]:
        t = (c or "").replace(" ", "")
        if any(w.replace(" ", "") in t for w in TOTAL_WORDS):
            return True
        if t in TOTAL_EXACT:
            return True
    return False


def parse_table(table_xml: str) -> list[dict]:
    """부문 매출 행. 못 뽑으면 **빈 목록**을 낸다 - 억지로 만들지 않는다.

    규칙은 좁다. 머리행에서 부문 열과 매출 열을 찾고, 데이터 행에서 그 두 칸만 읽는다.
    매출 열이 여러 개면(기간이 여러 개) 전부 낸다 - 어느 기간인지는 머리 문자열이 말한다.
    """
    g = grid(table_xml)
    if len(g) < 2:
        return []
    hn = _header_rows(table_xml, g)
    head = ["\n".join(g[r][c] if c < len(g[r]) else "" for r in range(hn))
            for c in range(max(len(r) for r in g))]

    def _leaf(h: str) -> str:
        """열의 정체는 **가장 깊은 머리행**이 정한다.

        `매출액 (비율)` 이 COLSPAN 으로 두 칸을 덮으면 두 칸의 머리가 똑같이 그 문자열을
        물려받는다. 합친 문자열만 보면 비율 칸도 매출 열로 잡혀 **50.05 같은 퍼센트가
        매출액으로 저장된다**(실측 000500). 마지막 줄이 그 칸의 이름이다.
        """
        parts = [p for p in h.split("\n") if p.strip()]
        return parts[-1] if parts else ""

    # **부문 머리행이 없으면 포기한다.** 0번 열로 떨어뜨리면 `구분 | 매출액` 처럼 매출을
    # 종류별로 쪼갠 평범한 표가 부문 표로 채택된다 - 자기검사는 합계가 맞으니 통과시키고,
    # 그러면 부문이 아닌 것이 부문 노출도로 코호트에 흘러든다. 이 파서의 전제는 추측하지
    # 않는 것이므로, 열의 정체가 선언되지 않은 표는 채택하지 않는다.
    #
    # 공백을 지우고 맞춘다 - 실측 머리 셀이 `부  문`(자간 벌린 것)이고 격자가 그것을
    # `부 문` 으로 정규화한다. 낱말 목록에 변형을 하나씩 더하는 것으로는 못 따라간다.
    seg_col = next((i for i, h in enumerate(head)
                    if any(w.replace(" ", "") in h.replace(" ", "") for w in SEG_WORDS)), -1)
    if seg_col < 0:
        return []
    rev_cols = [i for i, h in enumerate(head)
                if any(w in h for w in REV_WORDS) and i != seg_col
                and not any(w in _leaf(h) for w in SHARE_WORDS)]
    if not rev_cols:
        # 「매출액(비율)」처럼 합성된 열이거나 머리에 낱말이 없는 표. 숫자가 있는 열 중
        # 가장 오른쪽을 쓰지 않는다 - 근거 없는 추측이므로 포기하는 쪽이 낫다.
        return []
    share_by_rev = {}
    for i in rev_cols:
        nxt = i + 1
        if nxt < len(head) and any(w in _leaf(head[nxt]) for w in SHARE_WORDS):
            share_by_rev[i] = nxt

    out: list[dict] = []
    last_seg = ""
    for row in g[hn:]:
        if seg_col < len(row) and row[seg_col].strip():
            last_seg = row[seg_col].strip()
        seg = _WS.sub(" ", last_seg)
        if not seg:
            continue
        is_total = _is_total_row(row, min(rev_cols))
        for i in rev_cols:
            if i >= len(row):
                continue
            cell = row[i]
            val = _to_num(cell)
            if val is None:
                continue
            share = _share(cell)
            if share is None and i in share_by_rev and share_by_rev[i] < len(row):
                # **비율 열의 값은 `%` 없이 오기도 한다**(실측 000500 이 `50.05`). 열의
                # 정체는 머리행이 이미 정했으므로, 기호가 없다고 버리면 합계 행 없는 표의
                # 유일한 채택 근거(비중 합 = 100%)가 통째로 죽는다.
                share = _share(row[share_by_rev[i]]) or _bare_share(row[share_by_rev[i]])
            out.append({"segment": seg, "period_label": _WS.sub(" ", head[i]).strip(),
                        "surface": cell.strip(), "value": val, "share": share,
                        "is_total": is_total})
    return out


def check(rows: list[dict], tol: float = 0.02) -> dict:
    """자기 검사. **안 맞으면 행을 놓쳤거나 부호를 틀렸다.**

    검사가 둘이다. 표 모양이 달라서 근거가 다르다 - 기준을 느슨하게 한 것이 아니다.

        by="total"  부문 합 = 합계 행         합계 행이 있는 표
        by="share"  비중 합 = 100%           합계 행이 없고 비중 열이 있는 표

    기간 라벨별로 따로 본다. 라벨이 섞이면 서로 다른 기간의 부문을 더해 우연히 맞거나
    우연히 틀린다.
    """
    by: dict[str, dict] = {}
    for r in rows:
        b = by.setdefault(r["period_label"],
                          {"parts": 0.0, "total": None, "n": 0, "share": 0.0,
                           "n_share": 0})
        if r["is_total"]:
            b["total"] = r["value"]
        else:
            b["parts"] += r["value"]
            b["n"] += 1
            if r["share"] is not None:
                b["share"] += r["share"]
                b["n_share"] += 1
    out = {}
    for label, b in by.items():
        gap = (None if b["total"] in (None, 0)
               else abs(b["parts"] - b["total"]) / abs(b["total"]))
        sgap = (None if b["n_share"] < 2 or b["n_share"] != b["n"]
                else abs(b["share"] - 1.0))
        ok, why = False, "합계 행도 비중 열도 없다"
        if gap is not None:
            ok, why = gap <= tol, "total"
        elif sgap is not None:
            ok, why = sgap <= tol, "share"
        out[label] = {"n_segments": b["n"], "parts": b["parts"], "total": b["total"],
                      "gap": gap, "share_sum": (b["share"] if b["n_share"] else None),
                      "share_gap": sgap, "ok": ok, "by": why}
    return out
