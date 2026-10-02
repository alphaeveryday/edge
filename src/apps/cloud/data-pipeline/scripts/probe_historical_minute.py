"""과거 분봉 TR 이 그 날짜·그 분의 체결 행을 주는지 소량으로 확인한다. **조회만** — 저장·원장 변경 없음.

미수집(MISSING) 창을 소급 TR 로 회수하기 전에 돌린다(README "미수집 창 회수", ALPHA-1153).
어댑터는 벤더가 주지 않은 분을 직전가 flat·거래량 0 으로 채우므로, 대상 분이 통째로 없어도 본
실행은 그 창을 전 종목 무거래(VALID_EMPTY)로 확정한다 — 회수된 것처럼 보인다. 그래서 채우기 전의
**원시 행**을 여기서 먼저 본다.

    AWS_PROFILE=edge KIS_TOKEN_CACHE_PARAM=/edge-dev-data-pipeline/kis/access-token \\
      uv run --package data-pipeline python apps/cloud/data-pipeline/scripts/probe_historical_minute.py \\
        --date 2026-10-02 --symbols 005930,000660,069500 --windows 1432,1434

- 종목은 그 시간대에 **매 분 체결되는** 대형주·대표 ETF 로 고른다. 저유동 종목은 무거래 분이 정상이라
  이 판정에 못 쓴다.
- 종목당 최대 4콜(하루치 페이징). `KIS_TOKEN_CACHE_PARAM` 을 주면 워커와 같은 토큰을 쓴다(없으면 새로
  발급한다 — 발급은 분당 1회 한도다).
- exit: 0=모든 종목의 모든 대상 분에 체결 행이 있다 / 1=체결 행이 없는 분이 있다(회수를 시작하지
  않는다) / 2=조회 자체를 못 했다.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime

from data_pipeline.sources.http import PoliteClient
from data_pipeline.sources.kis_minute import KST, KisHistoricalMinuteClient


def minutes_without_trade(candles, windows: list[str]) -> list[str]:
    """`windows`(창 시작 KST HHMM) 중 그 종목의 원시 봉에 체결 행이 없는 분."""
    traded = {c.window_start.astimezone(KST).strftime("%H%M") for c in candles if c.traded}
    return [window for window in windows if window not in traded]


def main(argv: list[str] | None = None) -> int:
    """종목별 한 줄(JSON)을 내고 exit code 로 판정한다 — 0·1·2 의 뜻은 모듈 도크스트링."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--date", required=True, help="지난 거래일 YYYY-MM-DD",
                        type=lambda text: datetime.strptime(text, "%Y-%m-%d").date())  # 틀리면 exit 2
    parser.add_argument("--symbols", required=True, help="KRX 단축코드 쉼표 목록")
    parser.add_argument("--windows", required=True, help="창 시작 KST HHMM 쉼표 목록")
    parser.add_argument("--secret-id", default="edge-dev-data-pipeline/kis/oauth",
                        help="app_key·app_secret 을 담은 Secrets Manager 시크릿")
    args = parser.parse_args(argv)
    windows = args.windows.split(",")

    absent_anywhere = False
    try:
        import boto3  # 지연 import — 판정 함수만 쓰는 테스트는 AWS 를 안 건드린다

        secret = json.loads(boto3.client("secretsmanager").get_secret_value(
            SecretId=args.secret_id)["SecretString"])
        client = KisHistoricalMinuteClient(secret["app_key"], secret["app_secret"],
                                           PoliteClient(min_interval=0.25), session_date=args.date)
        for symbol in args.symbols.split(","):
            candles = client._fetch_day(symbol)  # 합성(`fill_no_trade_minutes`) 전의 그날 원시 봉
            absent = minutes_without_trade(candles, windows)
            absent_anywhere = absent_anywhere or bool(absent)
            print(json.dumps({"symbol": symbol, "day_rows": len(candles),
                              "target_minutes": len(windows), "without_trade": absent}))
    except Exception as error:  # noqa: BLE001 — 자격증명·시크릿·조회 어느 실패든 "확인 못 함"(2)이다
        # 이 스크립트의 출력에는 예외 문자열을 싣지 않는다(URL·자격증명이 들어갈 수 있다) — 종류만 낸다.
        # 하위 모듈(kis_auth 등)의 경고 로그는 워커에서와 같은 것이 stderr 로 나간다.
        print(json.dumps({"error": type(error).__name__}))
        return 2
    return 1 if absent_anywhere else 0


if __name__ == "__main__":
    raise SystemExit(main())
