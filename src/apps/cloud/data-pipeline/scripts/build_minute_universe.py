#!/usr/bin/env python3
"""1분 레인 universe.json 을 canonical KR holdings 에서 만든다 (ALPHA-735).

토스(초당 5회)일 때는 콜 예산 때문에 유니버스를 34종으로 줄여 뒀다. KIS 로 바꾸면서
(실측 14.8 req/s) 전 구성종목이 60초 창에 들어오므로, 유니버스를 **holdings 정본에서**
파생한다 — 손으로 유지하는 목록은 ETF 편입·제외 때마다 조용히 어긋난다.

멤버십 규칙은 ALPHA-590 그대로다: `max(as_of_date)` 파티션 하나가 아니라 **ETF 별 최신
스냅샷의 합집합**을 쓰고(부분 실패한 하루가 유니버스를 깎지 못하게), ETF 목록의 정본은
파티션이 아니라 config `krx_etf.source.etf_map` 이다. 그 로직은 수집 스텝이 이미 갖고
있어 여기서 재구현하지 않는다(`steps/ingest_price_raw`).

여기에 config `[minute_universe].sector_etf_ids` 를 **합집합으로 얹는다** — 층 분해의 섹터
후보 ETF 다. holdings 축과 별개인 이유: 그 ETF 들은 자기 분봉만 필요하고 구성종목·NAV 는
안 받으므로 holdings 에 자기 행이 없다. 안 얹으면 구간(장중) 모드에서 섹터층이 통째로
빠진다(일봉 경로가 쓰는 KRX 업종지수는 분봉이 없어 구간 모드가 섹터 ETF 로 대체한다).

**이 스크립트는 업로드하지 않는다.** 파일만 만들고, S3 반영은 운영자가 확인 후 한다 —
universe 는 세션 identity(universe_hash) 축이라 갈아끼우는 순간 그날 계획이 바뀐다.

실행(레포 루트 기준):

    cd src/apps/cloud/data-pipeline
    AWS_PROFILE=edge DATA_PIPELINE_STORAGE__BACKEND=s3 \
      DATA_PIPELINE_STORAGE__BUCKET=edge-dev-pipeline-lake \
      uv run python scripts/build_minute_universe.py --out /tmp/universe.json

    # 확인 후 반영(세션 계획 전에)
    aws s3 cp /tmp/universe.json s3://edge-dev-pipeline-lake/config/minute/universe.json

`--out` 없으면 stdout 으로 낸다.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from data_pipeline.config import load_settings  # noqa: E402
from data_pipeline.lake.storage import make_storage  # noqa: E402
from data_pipeline.minute.models import Universe, content_checksum  # noqa: E402
# 유니버스 파생은 수집 스텝이 정본이다 — 규칙을 두 벌로 만들면 한쪽만 고쳐진다.
# 공시·투자자 스텝도 같은 함수를 그대로 가져다 쓴다(ingest_raw_disclosure·investor).
from data_pipeline.steps.ingest_price_raw import (  # noqa: E402
    _kr_etf_ids,
    _kr_holdings_universe,
    _krx_expected_etfs,
)


def build(storage, expected_etfs, sector_etf_ids: tuple[str, ...] = ()) -> Universe:
    """holdings(+섹터 후보) → `Universe`. 빈 결과는 만들지 않는다(fail loud).

    `sector_etf_ids` 는 층 분해의 **섹터 후보** ETF(config `[minute_universe]`)다. holdings
    에서 파생되지 않는다 — 구성종목을 안 받으므로 holdings 에 자기 행이 없다. 그래서
    합집합으로 얹는다. 이걸 안 얹으면 구간(장중) 모드의 섹터층 후보가 계열 부재로 빠진다:
    일봉 경로가 쓰는 KRX 업종지수는 분봉이 없어 구간 모드가 섹터 ETF 로 대체하기 때문이다.

    **구성종목은 안 늘어난다.** 아래 차집합이 etf_ids 를 뺀 나머지를 구성종목으로 삼으므로,
    섹터 ETF 가 다른 ETF 의 보유 종목으로 잡혀 있어도 ETF 축으로 흡수된다(Universe 는
    두 집합이 겹치면 거부한다).
    """
    etf_ids = sorted(_kr_etf_ids(storage, expected_etfs) | set(sector_etf_ids))
    everything = set(_kr_holdings_universe(storage, expected_etfs=expected_etfs))
    # ETF 자기 티커와 구성종목은 겹치면 안 된다(Universe 검증) — ETF 쪽을 뺀 나머지가 구성종목
    constituent_ids = sorted(everything - set(etf_ids))
    if not etf_ids or not constituent_ids:
        raise SystemExit(
            f"holdings 에서 유니버스를 못 만들었다(ETF {len(etf_ids)}종, 구성종목 "
            f"{len(constituent_ids)}종) — 레이크 canonical KR holdings 를 확인하라"
        )
    return Universe(
        # 멤버십에서 유도한다 — 같은 구성이면 같은 버전이라, 재생성이 세션 universe 충돌을
        # 만들지 않는다. 구성이 바뀌면 값이 바뀌어 그 사실이 원장에 드러난다.
        universe_version="kr-holdings-" + content_checksum([etf_ids, constituent_ids])[:12],
        etf_ids=tuple(etf_ids),
        constituent_ids=tuple(constituent_ids),
        # extended_hours_ids 는 선언하지 않는다 — 시간외 거래 종목은 **종목별 속성**이라
        # 실측 없이는 못 채운다(추측해 넣으면 그 종목의 시간외 window 가 영구 INCOMPLETE 다).
        # 비워 두면 전 종목 정규장 390분으로 계획된다.
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", help="쓸 파일 경로(없으면 stdout)")
    args = parser.parse_args()

    settings = load_settings()
    universe = build(
        make_storage(settings.storage),
        _krx_expected_etfs(settings),
        () if settings.minute_universe is None else settings.minute_universe.sector_etf_ids,
    )
    payload = json.dumps(
        {
            "universe_version": universe.universe_version,
            "etf_ids": list(universe.etf_ids),
            "constituent_ids": list(universe.constituent_ids),
        },
        ensure_ascii=False, indent=2,
    )
    if args.out:
        Path(args.out).write_text(payload + "\n", encoding="utf-8")
        sector = () if settings.minute_universe is None else settings.minute_universe.sector_etf_ids
        print(
            f"{args.out}: ETF {len(universe.etf_ids)}종(섹터 후보 {len(sector)} 포함) + "
            f"구성종목 {len(universe.constituent_ids)}종 "
            f"(version={universe.universe_version})",
            file=sys.stderr,
        )
    else:
        print(payload)


if __name__ == "__main__":
    main()
