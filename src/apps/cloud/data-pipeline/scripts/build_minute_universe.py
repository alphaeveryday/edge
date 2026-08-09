#!/usr/bin/env python3
"""1분 레인 universe.json 을 canonical KR holdings 에서 만든다 (ALPHA-735).

토스(초당 5회)일 때는 콜 예산 때문에 유니버스를 34종으로 줄여 뒀다. KIS 로 바꾸면서
(실측 14.8 req/s) 전 구성종목이 60초 창에 들어오므로, 유니버스를 **holdings 정본에서**
파생한다 — 손으로 유지하는 목록은 ETF 편입·제외 때마다 조용히 어긋난다.

멤버십 규칙은 ALPHA-590 그대로다: `max(as_of_date)` 파티션 하나가 아니라 **ETF 별 최신
스냅샷의 합집합**을 쓰고(부분 실패한 하루가 유니버스를 깎지 못하게), ETF 목록의 정본은
파티션이 아니라 config `krx_etf.source.etf_map` 이다. 그 로직은 수집 스텝이 이미 갖고
있어 여기서 재구현하지 않는다(`steps/ingest_price_raw`).

여기에 config `[minute_universe].sector_etf_ids` 를 **참조 계열 축**으로 얹는다 — 층 분해의
섹터 후보 ETF 다. holdings 축과 별개인 이유: `krx_etf.source.etf_map` 밖이라 그 ETF 의
구성종목을 수집하지 않고, 따라서 canonical KR holdings 에 자기 행이 없다. 안 얹으면
구간(장중) 모드에서 섹터층이 통째로 빠진다 — 일봉 경로가 쓰는 KRX 업종지수는 1분봉을
수집하지만(ALPHA-887, dataset `sector_index_minute`) 구간 모드가 읽는 5분 롤업 배선이
없고 소급도 안 돼, 아직 구간 모드가 섹터 ETF 로 대체한다.

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
    """holdings(+참조 계열) → `Universe`. 빈 결과는 만들지 않는다(fail loud).

    `sector_etf_ids` 는 층 분해의 **섹터 후보** ETF(config `[minute_universe]`)다. holdings
    에서 파생되지 않는다 — `etf_map` 밖이라 그 ETF 의 구성종목을 수집하지 않고, 따라서
    canonical KR holdings 에 자기 행이 없다. 그래서 따로 얹는다.

    **`etf_ids` 가 아니라 `sector_etf_ids` 축이다.** `etf_ids` 는 수집 축이자 **판정 축**
    이라(`Universe` 도크스트링) 얹는 순간 트리거 발화·기준선 대상이 된다. 참조 계열은
    봉만 받는다.

    **구성종목은 안 늘어난다.** 아래 차집합이 두 ETF 축을 뺀 나머지를 구성종목으로 삼으므로,
    섹터 ETF 가 다른 ETF 의 보유 종목으로 잡혀 있어도 참조 계열 축으로 흡수된다.
    """
    sectors = sorted(set(sector_etf_ids))
    judged = _kr_etf_ids(storage, expected_etfs)
    # 두 설정이 같은 코드를 다르게 말한다 — etf_map 은 "판정해라", 참조 계열은 "봉만
    # 받아라". 한쪽으로 조용히 넘기면 나머지 한쪽이 거짓이 되므로 평균내지 않고 거부한다
    # (Rule 7). 어느 쪽을 지울지는 사람이 정할 일이다.
    #
    # 대조군은 **config(etf_map) 과 holdings 둘 다**다. holdings 만 보면 그 ETF 의
    # 스냅샷이 아직 없을 때(신규 편입·KRX 런 실패·소급 상한 초과) 모순이 조용히
    # 통과한다 — 정작 그때가 사람이 목록을 손대는 시점이다.
    declared = set(expected_etfs or ()) | judged
    if both := sorted(declared & set(sectors)):
        raise SystemExit(
            f"같은 ETF 가 etf_map 과 [minute_universe].sector_etf_ids 양쪽에 있다: {both} "
            f"— 판정 대상이면 etf_map 에만, 참조 계열이면 sector_etf_ids 에만 둬라"
        )
    etf_ids = sorted(judged)
    everything = set(_kr_holdings_universe(storage, expected_etfs=expected_etfs))
    # 세 축은 서로 겹치면 안 된다(Universe 검증) — ETF·참조 계열을 뺀 나머지가 구성종목
    constituent_ids = sorted(everything - set(etf_ids) - set(sectors))
    if not etf_ids or not constituent_ids:
        raise SystemExit(
            f"holdings 에서 유니버스를 못 만들었다(판정 ETF {len(etf_ids)}종, "
            f"구성종목 {len(constituent_ids)}종) — 레이크 canonical KR holdings 를 확인하라"
        )
    # 멤버십에서 유도한다 — 같은 구성이면 같은 버전이라, 재생성이 세션 universe 충돌을
    # 만들지 않는다. 구성이 바뀌면 값이 바뀌어 그 사실이 원장에 드러난다.
    # 참조 계열은 **비어 있지 않을 때만** 넣는다: 빈 축을 무조건 넣으면 구성이 똑같은
    # 기존 universe 의 version 이 배포만으로 바뀌어 그날 재계획이 막힌다(universe_hash
    # 의 같은 규율).
    parts = [etf_ids, constituent_ids] + ([sectors] if sectors else [])
    return Universe(
        universe_version="kr-holdings-" + content_checksum(parts)[:12],
        etf_ids=tuple(etf_ids),
        constituent_ids=tuple(constituent_ids),
        sector_etf_ids=tuple(sectors),
        # extended_hours_ids 는 선언하지 않는다 — 시간외 거래 종목은 **종목별 속성**이라
        # 실측 없이는 못 채운다(추측해 넣으면 그 종목의 시간외 window 가 영구 INCOMPLETE 다).
        # 비워 두면 전 종목 정규장 390분으로 계획된다.
    )


def build_from_settings(settings, storage) -> Universe:
    """`Settings` → `Universe`. `main()` 의 배선을 여기 모은다 — 설정에서 목록을 꺼내는
    표현이 두 군데로 갈리면 한쪽만 고쳐지고, 그 갈림은 **기능이 통째로 무력화된 채
    초록으로 도는** 형태로 드러난다(빌드 테스트가 build() 만 부르면 못 잡는다)."""
    return build(storage, _krx_expected_etfs(settings), sector_ids(settings))


def sector_ids(settings) -> tuple[str, ...]:
    """config `[minute_universe].sector_etf_ids`. 섹션 미설정이면 빈 튜플."""
    return () if settings.minute_universe is None else settings.minute_universe.sector_etf_ids


def payload_of(universe: Universe) -> str:
    """universe.json 본문. **모델에서 직렬화한다** — 손으로 필드를 나열하면 축이 하나 늘
    때 조용히 빠지고, 그러면 S3 객체에 그 축이 없어 수집이 예전 집합 그대로 돈다.

    `exclude_defaults` 로 선언 없는 축(빈 extended·빈 참조 계열)은 키 자체를 안 낸다:
    universe_hash 가 빈 축을 생략하는 규율과 같은 축이고, 그래야 재생성된 파일이
    착지본과 바이트로 대조된다."""
    return json.dumps(universe.model_dump(exclude_defaults=True),
                      ensure_ascii=False, indent=2)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", help="쓸 파일 경로(없으면 stdout)")
    args = parser.parse_args()

    settings = load_settings()
    universe = build_from_settings(settings, make_storage(settings.storage))
    payload = payload_of(universe)
    # 요약은 **두 경로 모두** 낸다(stdout 경로에서 침묵하면 무엇을 만들었는지 안 보인다).
    # 참조 계열이 0 이면 그것도 그대로 보인다 — 설정이 안 실린 것이 조용히 정상으로
    # 보이면 안 된다.
    print(
        f"{args.out or '(stdout)'}: 판정 ETF {len(universe.etf_ids)}종 + 참조 계열 "
        f"{len(universe.sector_etf_ids)}종 + 구성종목 {len(universe.constituent_ids)}종 "
        f"= 수집 {len(universe.unit_ids)} unit (version={universe.universe_version})",
        file=sys.stderr,
    )
    if args.out:
        Path(args.out).write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)


if __name__ == "__main__":
    main()
