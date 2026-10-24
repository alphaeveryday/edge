"""종목 별칭 명부(`src/data_pipeline/instrument_aliases.tsv`) 재생성 (ALPHA-850).

뉴스가 같은 상장사를 다르게 쓰는 이름 중 정규화(조사·법인 표기·공백·영문↔한글 읽기)로 안 풀리는
것을 명부로 관리한다. 후보는 세 곳에서 온다:
  1. 지금 명부(사람·강모델이 넣은 것 — source 를 그대로 둔다)
  2. `--krx`: 레이크 KRX 종목기본정보 최신 스냅샷의 법적 명칭·약명(`에스케이하이닉스`·`화승코퍼레이션`)
  3. `--candidates <jsonl>`: 강모델이 만든 후보 {ticker, alias, kind} (여러 번 줄 수 있다)
모든 후보는 운영 해소기로 검사한다(`entity_resolution.check_alias_candidates`) — 이미 풀리는 것은
빼고, 다른 종목 이름을 망가뜨리는 것은 거부한다. 거부 목록은 stderr 에 낸다.

사용(운영 DB 읽기 + 레이크 읽기, 쓰기는 명부 파일뿐):
    uv run --package data-pipeline python apps/engine/data-pipeline/scripts/build_instrument_aliases.py \\
        --krx --candidates aliases.jsonl --write
`--write` 가 없으면 바뀔 내용만 요약한다. DB 접속은 다른 스텝과 같은 `DATA_PIPELINE_DB__*` 설정이다.
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from data_pipeline import entity_resolution as er  # noqa: E402
from data_pipeline.config import load_settings  # noqa: E402
from data_pipeline.db import connect, db_config_from_env  # noqa: E402

HEADER = """\
# 종목 별칭 명부 (ALPHA-850) — 뉴스가 **같은 상장사 자신**을 다르게 쓰는 표기 → 그 종목 ticker.
# 정규화(조사·법인 표기·공백·영문↔한글 읽기)로 풀리는 표기는 넣지 않는다 — entity_resolution 이 푼다.
# 그룹명·비상장 자회사·브랜드·사업부·노조는 넣지 않는다(그 회사 자신이 아니다).
# 직접 고치지 말고 scripts/build_instrument_aliases.py 로 다시 만든다(검사를 거친다).
# 열: alias<TAB>ticker<TAB>source. ticker 가 마스터에 없거나 같은 표기가 두 종목에 걸리면 로드 때 버려진다.
alias\tticker\tsource
"""


# 영문 사명 끝의 법인 표기(`SeA Mechanics Co., LTD.` → `SeA Mechanics`).
_EN_LEGAL_TAIL = re.compile(r"[\s,.]*(co\.?,?\s*ltd\.?|corp(oration)?\.?|inc\.?|ltd\.?|co\.?)$", re.IGNORECASE)


def krx_names(row: dict) -> list[str]:
    """KRX 종목기본정보 1행 → 별칭 후보 이름: 약명, 법적 명칭(`…보통주`·`주식회사` 제거), 영문 사명(법인 꼬리 제거)."""
    out = []
    for name in (row.get("display_name"), row.get("legal_name")):
        if isinstance(name, str) and name.strip():
            name = name.strip().removesuffix("보통주").strip()
            out.append(name.removeprefix("주식회사").removesuffix("주식회사").strip())
    english = row.get("english_name")
    if isinstance(english, str) and english.strip():
        out.append(_EN_LEGAL_TAIL.sub("", english.strip()).strip())
    return [n for n in out if n]


def _krx_candidates(storage) -> list[tuple[str, str, str]]:
    """KRX 최신 스냅샷 보통주의 이름 후보(`krx_names`)."""
    from data_pipeline.steps.load_instruments import _latest_instrument_profile_rows

    return [(name, row["ticker"], "krx")
            for row in _latest_instrument_profile_rows(storage, "KR")
            if row.get("ticker") and row.get("share_class") == "보통주"
            for name in krx_names(row)]


def main() -> None:
    """후보를 모아 검사하고, `--write` 면 명부를 다시 쓴다."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--krx", action="store_true", help="레이크 KRX 종목기본정보에서 후보를 만든다")
    ap.add_argument("--candidates", action="append", default=[], help="강모델 후보 jsonl {ticker, alias, kind}")
    ap.add_argument("--write", action="store_true", help="명부 파일을 다시 쓴다(없으면 요약만)")
    args = ap.parse_args()

    settings = load_settings()
    with connect(db_config_from_env(settings.db)) as conn:
        master = er.load_resolution_index(conn, aliases=())

    candidates = [(alias, ticker, source) for alias, ticker, source in _current_rows()]
    if args.krx:
        from data_pipeline.lake.storage import make_storage

        candidates += _krx_candidates(make_storage(settings.storage))
    for path in args.candidates:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                candidates.append((row["alias"], row["ticker"], f"model:{row.get('kind', 'other')}"))

    accepted, rejected = er.check_alias_candidates(master, candidates)
    reasons = collections.Counter(r[3] for r in rejected)
    print(f"후보 {len(candidates)} → 채택 {len(accepted)}, 제외 {dict(reasons)}", file=sys.stderr)
    for alias, ticker, source, reason in rejected:
        if reason in ("conflict", "clash", "unknown_ticker"):
            print(f"  거부 {reason}: {alias} → {ticker} ({source})", file=sys.stderr)
    if args.write:
        body = "".join(f"{a}\t{t}\t{s}\n" for a, t, s in accepted)
        er._ALIAS_FILE.write_text(HEADER + body, encoding="utf-8", newline="\n")
        print(f"썼다: {er._ALIAS_FILE}", file=sys.stderr)


def _current_rows() -> list[tuple[str, str, str]]:
    """지금 명부의 (alias, ticker, source)."""
    out = []
    for line in er._ALIAS_FILE.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        cols = line.split("\t")
        if cols[0] != "alias":
            out.append((cols[0], cols[1], cols[2] if len(cols) > 2 else "manual"))
    return out


if __name__ == "__main__":
    main()
