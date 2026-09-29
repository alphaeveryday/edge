"""KIS 종목 마스터 파일 → 지수업종 대·중·소분류 (ALPHA-1130).

원천: KIS 가 공개 배포하는 마스터 ZIP(`{base}/kospi_code.mst.zip`·`kosdaq_code.mst.zip`·`idxcode.mst.zip`).
KIS Open API(앱키·토큰)가 **아니다** — 인증 없는 정적 파일이라 KIS 공유 호출 예산(ADR-0055) 밖이다.

형식 근거는 KIS 공식 저장소(koreainvestment/open-trading-api `stocks_info/`)의 헤더·샘플이다
(2026-09-30 확인, **실파일 미확인** — 작업 지침상 내려받지 않았다):

- 종목 마스터: 줄 = [단축코드 9][표준코드 12][한글명 가변][고정폭 뒷부분]. 뒷부분 길이는 KOSPI 227·KOSDAQ 221자
  (샘플은 개행 포함 228·222 로 자른다). 뒷부분 앞 15자가 `증권그룹구분코드(2)·시가총액규모(1)·
  지수업종 대분류(4)·중분류(4)·소분류(4)` 다(헤더 `bstp_larg_div_code` 등). cp949.
- 업종코드 표(idxcode.mst): 헤더는 `[시장구분 1][업종코드 4][업종명 40]` 인데 공식 샘플은 이름을 `[3:43]` 으로
  자른다 — **둘이 어긋난다**. 헤더를 따르고, 한글 이름 비율 게이트로 어긋남을 드러낸다(이름만 비우고 코드는 남긴다).

분류 체계: KIS 지수업종(KIS 자체 `U` 번호 공간 — `0xxx` KOSPI·`1xxx` KOSDAQ 등, `sources.toml`
`[minute_sector_index]` 주석)이다. **KRX 업종지수 코드·WICS·GICS 가 아니다** — 이름이 같아도 코드로 옮기지 않는다.
`0000` 은 분류 없음이다. 원천은 **현재값만** 준다 — 과거 날짜 분류를 만들지 않는다.
"""

from __future__ import annotations

import io
import zipfile

# 파일 → (시장, 고정폭 뒷부분 길이).
MASTER_FILES = {"kospi_code.mst.zip": ("KOSPI", 227), "kosdaq_code.mst.zip": ("KOSDAQ", 221)}
SECTOR_NAME_FILE = "idxcode.mst.zip"

# 형태 게이트 — 필드가 통째로 밀린 계통적 파손을 잡는다(개별 행 이상은 행 단위로 거부).
MIN_ROWS = 500
MIN_VALID_RATIO = 0.95
MIN_HANGUL_NAME_RATIO = 0.8


def _unzip_single(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        members = [m for m in archive.namelist() if not m.endswith("/")]
        if len(members) != 1:
            raise ValueError(f"ZIP 안의 파일이 하나가 아니다: {members}")
        return archive.read(members[0]).decode("cp949")


def _hangul(text: str) -> bool:
    return any("가" <= ch <= "힣" for ch in text)


def parse_sector_names(data: bytes) -> tuple[dict[str, str], list[str]]:
    """업종코드 표 → ({코드: 이름}, 경고). 같은 코드가 다른 이름으로 두 번 나오면 그 코드는 이름을 비운다."""
    names: dict[str, str] = {}
    ambiguous: set[str] = set()
    for line in _unzip_single(data).splitlines():
        if len(line) < 6:
            continue
        code, name = line[1:5], line[5:45].strip()
        if code in names and names[code] != name:
            ambiguous.add(code)
        names[code] = name
    warnings = [f"ambiguous_sector_code:{c}" for c in sorted(ambiguous)]
    for code in ambiguous:
        names.pop(code)
    if names and sum(_hangul(n) for n in names.values()) / len(names) < MIN_HANGUL_NAME_RATIO:
        # 헤더·샘플 어긋남이 실파일에서 이름을 밀어낸 경우다. 틀린 이름을 붙이느니 비운다.
        return {}, [*warnings, "sector_name_layout_mismatch"]
    return names, warnings


def parse_master(file_name: str, data: bytes) -> tuple[list[dict], list[dict]]:
    """종목 마스터 → (행, 거부). 행: market·instrument_code·standard_code·name_kr·security_group·raw 코드 3개."""
    market, tail = MASTER_FILES[file_name]
    rows, rejects = [], []
    for number, line in enumerate(_unzip_single(data).splitlines(), start=1):
        if not line.strip():
            continue
        if len(line) <= tail + 21:
            rejects.append({"market": market, "line": number, "reasons": ["short_line"]})
            continue
        head, back = line[:-tail], line[-tail:]
        row = {"market": market, "instrument_code": head[0:9].strip(), "standard_code": head[9:21].strip(),
               "name_kr": head[21:].strip(), "security_group": back[0:2].strip(),
               "raw_large_code": back[3:7], "raw_medium_code": back[7:11], "raw_small_code": back[11:15]}
        reasons = []
        if not row["instrument_code"] or not row["instrument_code"].isalnum():
            reasons.append("bad_instrument_code")
        if len(row["standard_code"]) != 12:
            reasons.append("bad_standard_code")
        if not (len(row["security_group"]) == 2 and row["security_group"].isalpha()):
            reasons.append("bad_security_group")
        if not all(row[k].isdigit() and len(row[k]) == 4
                   for k in ("raw_large_code", "raw_medium_code", "raw_small_code")):
            reasons.append("bad_sector_code")
        if reasons:
            rejects.append({"market": market, "line": number, "instrument_code": row["instrument_code"],
                            "reasons": reasons})
        else:
            rows.append(row)
    total = len(rows) + len(rejects)
    if total < MIN_ROWS or len(rows) / total < MIN_VALID_RATIO:
        # 고정폭이 통째로 밀리면 모든 행의 코드가 조용히 틀린다 — 반쪽 파일을 적재하지 않는다.
        return [], [{"market": market, "reasons": ["master_layout_gate"], "rows": len(rows), "total": total}]
    return rows, rejects
