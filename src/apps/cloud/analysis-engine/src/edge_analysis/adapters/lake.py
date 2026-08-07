"""가격·ETF holdings 를 읽는 S3 canonical-lake 리더.

``boto3``·``pyarrow`` 는 지연 import 한다(무거운 의존의 레포 관례) — 리더는 가격/holdings
경로에서만 쓰이고, 잔잔한 조기 종료 경로에선 건드리지 않는다.
"""
from __future__ import annotations

import io
import logging
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from ..config import KST, PipelineError, ReturnsNotReadyError, Settings
from ..domain.models import Holding
from ..domain.window import CommittedMinuteWindow, MinuteBar

logger = logging.getLogger(__name__)

LAKE_PRICE_PREFIX = "canonical/market_data/price_daily"
LAKE_HOLDINGS_PREFIX = "canonical/holdings/etf_holdings"
LAKE_PRICE_MINUTE_PREFIX = "canonical/market_data/price_minute"


def minute_artifact_key(
    market: str, session_date: str, window_hhmm: str, generation: int
) -> str:
    """분봉 window artifact 키 — `data_pipeline.lake.storage.canonical_price_minute_artifact_key`
    의 전사(미러)다. 경로 규약 SSOT 는 저쪽이고, 엔진은 이미지 분리 때문에 data-pipeline
    에 의존하지 않아 문자열이 두 벌이다 — e2e 골든패스가 저 빌더로 **쓴** artifact 를
    이 키로 **읽어** 두 전사의 수렴을 고정한다(PIPELINE_ID 수렴과 같은 축).
    """
    return (
        f"{LAKE_PRICE_MINUTE_PREFIX}/market={market}/session_date={session_date}"
        f"/window={window_hhmm}/generation={generation}/bars.ndjson"
    )


def _as_date(value: str) -> date | None:
    """파티션 값을 `date` 로 — canonical 규약(`YYYY-MM-DD`)이 아니면 None.

    두 단계다. ① `fromisoformat` 이 `2026-07-1x` 같은 비날짜를 거부한다 — 길이·사전순
    검사로 대신하면 같은 길이의 오염 값이 정상 파티션보다 뒤에 정렬돼 '직전 거래일'로
    뽑힌다. ② **왕복 대조**로 기본형(`20260718`)·주차형을 거부한다 — 파이썬 3.11+ 의
    `fromisoformat` 은 그것들도 받아, 레이크 경로 빌더가 쓰지 않는 형식의 디렉터리가
    유효한 파티션으로 인정된다(경로 규약 SSOT 는 `lake/storage.py` 다).
    """
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.isoformat() == value else None


def _price(value: Any) -> float | None:
    """가격(문자열·수치)을 float 로 — 계약 위반은 None(분해가 미가격으로 제외).

    `bool` 을 **먼저** 막는다: JSON `true` 는 `float(True) == 1.0` 이라 양수·유한성
    게이트를 전부 통과해, 전일 종가 70,000 대비 -99.999% 라는 '정상 수익률'로 인증된다
    (coerce-to-passing). `isinstance(x, bool)` 없이 `float()` 만 쓰면 안 걸린다 —
    파이썬에서 bool 은 int 의 하위형이다.
    """
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def make_s3_client(settings: Settings):
    """S3 클라이언트 생성(선택적 AWS 프로파일 반영, 지연 boto3)."""
    import boto3

    if settings.aws_profile:
        session = boto3.Session(profile_name=settings.aws_profile, region_name=settings.region)
    else:
        session = boto3.Session(region_name=settings.region)
    return session.client("s3")


class LakeReader:
    """S3 레이크에서 종가 대비 등락과 ETF holdings 를 읽는다."""

    def __init__(self, s3, bucket: str) -> None:
        """주어진 S3 클라이언트와 버킷으로 리더를 만든다."""
        self._s3 = s3
        self._bucket = bucket

    def _list_pages(self, prefix: str, *, delimiter: str | None = None):
        """`list_objects_v2` 응답을 **끝까지** 흘린다.

        첫 페이지만 읽으면 잘린 목록의 마지막이 '최신'으로 인증된다 —
        `load_prev_closes` 는 그걸 직전 거래일 종가(분해의 분모)로 쓰고, `load_returns`
        는 당일 파티션을 못 찾아 조용히 빈 dict 를 돌려준다. 둘 다 오류 없이 틀린 값이
        된다. CommonPrefixes 도 1,000개에서 잘리고, KR 거래일 ~245/년이라 4년치면
        도달한다(dev 현재 18개).

        절단됐다면서 토큰이 없거나 안 움직이면 같은 페이지를 영원히 다시 받는다 —
        상주 소비자가 메모리를 먹으며 조용히 멈춘다. 부분 목록으로 끊는 편이 낫다
        (호출부의 빈/부분 결과 게이트가 잡는다).
        """
        token: str | None = None
        while True:
            kwargs: dict[str, Any] = {"Bucket": self._bucket, "Prefix": prefix}
            if delimiter:
                kwargs["Delimiter"] = delimiter
            if token:
                kwargs["ContinuationToken"] = token
            resp = self._s3.list_objects_v2(**kwargs)
            yield resp
            if not resp.get("IsTruncated"):
                return
            nxt = resp.get("NextContinuationToken")
            if not nxt or nxt == token:
                logger.warning(
                    "S3 나열이 절단됐는데 토큰이 전진하지 않는다 — 부분 목록으로 중단: %s",
                    prefix)
                return
            token = nxt

    def _partition_values(self, base: str, key: str) -> list[str]:
        """``base`` 바로 아래 ``key=`` 파티션 값들(정렬)."""
        out: list[str] = []
        for resp in self._list_pages(base, delimiter="/"):
            for common in resp.get("CommonPrefixes", []):
                seg = common.get("Prefix", "").rstrip("/").split("/")[-1]
                if seg.startswith(f"{key}="):
                    out.append(seg[len(key) + 1:])
        return sorted(out)

    def _read_parquet_prefix(self, prefix: str, columns: list[str]) -> list[dict[str, Any]]:
        """``prefix`` 하위 parquet 을 전부 읽어 행 dict 리스트로 반환한다."""
        import pyarrow.parquet as pq

        rows: list[dict[str, Any]] = []
        for resp in self._list_pages(prefix):
            for obj in resp.get("Contents", []):
                if not obj["Key"].endswith(".parquet"):
                    continue
                body = self._s3.get_object(Bucket=self._bucket, Key=obj["Key"])["Body"].read()
                rows.extend(pq.read_table(io.BytesIO(body), columns=columns).to_pylist())
        return rows

    def load_returns(self, market: str, trade_date: date) -> dict[str, float | None]:
        """티커별 종가 대비 등락. 직전 파티션을 D-1 로 쓴다."""
        base = f"{LAKE_PRICE_PREFIX}/market={market}/"
        dates = self._partition_values(base, "trade_date")
        today = trade_date.isoformat()
        if today not in dates:
            return {}
        idx = dates.index(today)
        prev = dates[idx - 1] if idx > 0 else None
        cur = {
            str(r["ticker"]): r["close"]
            for r in self._read_parquet_prefix(f"{base}trade_date={today}/", ["ticker", "close"])
            if r.get("close") is not None
        }
        prv = (
            {
                str(r["ticker"]): r["close"]
                for r in self._read_parquet_prefix(f"{base}trade_date={prev}/", ["ticker", "close"])
                if r.get("close") is not None
            }
            if prev
            else {}
        )
        returns: dict[str, float | None] = {}
        for ticker, close in cur.items():
            prev_close = prv.get(ticker)
            returns[ticker] = (close / prev_close - 1.0) if prev_close and prev_close > 0 else None
        return returns

    def _read_minute_bars(
        self, market: str, session_date: str, window_hhmm: str, generation: int,
        expected_checksum: str | None,
    ) -> dict[str, dict[str, Any]]:
        """분봉 window artifact(NDJSON) 를 unit_id → 레코드 dict 로 읽는다.

        artifact 부재(NoSuchKey)는 빈 dict — window 커밋 직후의 짧은 지연이 정상
        경로라 호출부가 ReturnsNotReady 로 접어 재시도한다. **checksum 대조는 소비자
        쪽 계약**이다(price_consumer 와 동형) — 원장 checksum 은 커밋된 바이트의
        sha256 이고, 어긋난 바이트로 분해하면 트리거 근거와 다른 가격이 설명으로
        영속된다(동시 PUT 경합·운영 실수, ALPHA-704). 불일치는 읽기 일관성 지연일
        수 있어 재시도 축(ReturnsNotReady)으로 접고, 지속되면 예산이 DLQ 로 드러낸다.
        형상 밖 행(비객체·unit_id 결손)은 건너뛴다 — canonical 진입 차단의 정본
        게이트는 워커 검증 경계(ALPHA-679)고, 한 행이 분해 전체를 죽이면 안 된다.
        """
        import hashlib
        import json

        from botocore.exceptions import ClientError

        from ..config import ReturnsNotReadyError

        key = minute_artifact_key(market, session_date, window_hhmm, generation)
        try:
            body = self._s3.get_object(Bucket=self._bucket, Key=key)["Body"].read()
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
                return {}
            raise
        if (expected_checksum is not None
                and hashlib.sha256(body).hexdigest() != expected_checksum):
            raise ReturnsNotReadyError(f"분봉 artifact checksum 불일치: {key}")
        rows: dict[str, dict[str, Any]] = {}
        for line in body.decode("utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            if isinstance(record, dict) and record.get("unit_id"):
                rows[str(record["unit_id"])] = record
        return rows

    def load_committed_minute_bars(
        self, market: str, windows: list[CommittedMinuteWindow],
    ) -> tuple[MinuteBar, ...]:
        """원장이 확정한 generation/checksum의 1분봉을 strict하게 읽는다.

        구 단건 reader와 달리 404·형상 위반·중복을 관대하게 접지 않는다. 이 결과는
        시간축 전체의 입력이므로 한 행을 버리면 구성종목별 봉 수가 갈리고, 그 상태로
        5분 집계하면 정상처럼 보이는 다른 지평의 수익률이 만들어진다.
        """
        import hashlib
        import json

        from botocore.exceptions import ClientError

        market = str(market).strip()
        if not market:
            raise ValueError("market이 비었다")
        if not windows:
            return ()
        if len({window.session_id for window in windows}) != 1:
            raise PipelineError("커밋 minute windows의 session_id가 다르다")
        if list(windows) != sorted(windows, key=lambda window: window.start):
            raise PipelineError("커밋 minute windows가 시간 오름차순이 아니다")
        if len({window.start for window in windows}) != len(windows):
            raise PipelineError("커밋 minute windows에 중복 시각이 있다")
        if len({window.start.date() for window in windows}) != 1:
            raise PipelineError("커밋 minute windows가 여러 session_date에 걸쳐 있다")

        result: list[MinuteBar] = []
        for source in windows:
            session_date = source.start.date().isoformat()
            window_hhmm = source.start.strftime("%H%M")
            key = minute_artifact_key(market, session_date, window_hhmm, source.generation)
            try:
                body = self._s3.get_object(Bucket=self._bucket, Key=key)["Body"].read()
            except ClientError as error:
                if error.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
                    raise ReturnsNotReadyError(f"분봉 artifact 미착지: {key}") from error
                raise
            if hashlib.sha256(body).hexdigest() != source.checksum:
                raise ReturnsNotReadyError(f"분봉 artifact checksum 불일치: {key}")
            try:
                text = body.decode("utf-8")
            except UnicodeDecodeError as error:
                raise PipelineError(f"분봉 artifact UTF-8 위반: {key}") from error

            seen: set[str] = set()
            bars: list[MinuteBar] = []
            for line_number, line in enumerate(text.splitlines(), 1):
                if not line.strip():
                    raise PipelineError(
                        f"분봉 artifact 빈 NDJSON 행: {key} line={line_number}")
                try:
                    record = json.loads(line)
                    if not isinstance(record, dict):
                        raise ValueError("레코드가 객체가 아니다")
                    unit_id = str(record["unit_id"]).strip()
                    if not unit_id:
                        raise ValueError("unit_id가 비었다")
                    if unit_id in seen:
                        raise ValueError(f"unit_id 중복: {unit_id}")
                    stamp = datetime.fromisoformat(str(record["ts"]))
                    if stamp.tzinfo is None or stamp.astimezone(KST) != source.start:
                        raise ValueError(
                            f"ts가 원장 window와 다르다: {record['ts']!r}")
                    values = []
                    for field in ("open", "high", "low", "close", "volume"):
                        value = record[field]
                        if isinstance(value, bool):
                            raise ValueError(f"{field}가 bool이다")
                        values.append(Decimal(str(value)))
                    bar = MinuteBar(source, unit_id, *values)
                except (KeyError, TypeError, ValueError, InvalidOperation, json.JSONDecodeError) as error:
                    raise PipelineError(
                        f"분봉 artifact 형상 위반: {key} line={line_number} — {error}") from error
                seen.add(unit_id)
                bars.append(bar)
            result.extend(sorted(bars, key=lambda bar: bar.unit_id))
        return tuple(result)

    def load_prev_closes(self, market: str, trade_date: date) -> dict[str, float]:
        """직전 거래일 종가 — 분봉 분해의 분모(ALPHA-747).

        '직전'은 `trade_date` **미만** 최신 파티션이다. 당일 파티션을 기준으로 인덱스를
        세면(daily `load_returns` 방식) 장중엔 당일 파티션이 없어 항상 빈 dict 가 된다 —
        분봉 분해는 그 파티션이 생기기 전에 도는 것이 정상 경로다.

        형 변환 실패(비수치·None·bool)는 그 티커만 뺀다 — 한 행이 분해 전체를 죽이면
        안 되고, 빠진 티커는 분해에서 미가격으로 잡혀 coverage 에 드러난다.

        파티션 값이 **실제 날짜로 파싱되는 것**만 보고, **파싱한 날짜로 정렬**한다.
        문자열로 고르면 두 방향으로 틀린다: 길이 검사만으론 `2026-07-1x`(열 글자)가
        `2026-07-19` 뒤에 오고, 파싱만 하고 문자열로 정렬하면 `fromisoformat` 이 받는
        기본형(`20260718`)이 `2026-07-19` 뒤로 정렬돼 더 **오래된** 날이 뽑힌다.
        어느 쪽도 오류 없이 틀린 분모가 된다.
        """
        base = f"{LAKE_PRICE_PREFIX}/market={market}/"
        earlier = sorted(
            (parsed, d) for d in self._partition_values(base, "trade_date")
            if (parsed := _as_date(d)) is not None and parsed < trade_date
        )
        if not earlier:
            return {}
        closes: dict[str, float] = {}
        for row in self._read_parquet_prefix(
                f"{base}trade_date={earlier[-1][1]}/", ["ticker", "close"]):
            price = _price(row.get("close"))
            if price is not None and row.get("ticker") is not None:
                closes[str(row["ticker"])] = price
        return closes

    def load_minute_returns(
        self,
        market: str,
        session_date: str,
        trigger_window_hhmm: str,
        trigger_generation: int,
        trigger_checksum: str | None,
        prev_closes: dict[str, float],
    ) -> dict[str, float | None]:
        """unit 별 장중 수익률 — **전일 종가** 대비 트리거 window 의 close.

        ETF 트리거 판정(ALPHA-745 `intraday-anchor-v2`)이 전일 종가를 앵커로 쓰므로
        분해도 같은 축이어야 한다 — 분모가 갈리면 트리거가 설명하는 움직임과 분해가
        설명하는 움직임이 다르다(ALPHA-747). 갭(전일 종가→시가)이 기여에 포함된다.
        가격 계약 위반(0·음수·비수치)은 그 unit 만 None 으로 접는다 — 분해는 None 을
        미가격으로 제외한다(daily `load_returns` 와 같은 계약).
        """
        import math

        closes = self._read_minute_bars(
            market, session_date, trigger_window_hhmm, trigger_generation, trigger_checksum)
        if not closes:
            return {}
        returns: dict[str, float | None] = {}
        for unit_id, record in closes.items():
            base_price = prev_closes.get(unit_id)
            close_price = _price(record.get("close"))
            if close_price is None:
                returns[unit_id] = None
                continue
            # isfinite — float("Infinity"/"nan") 은 양수 비교를 통과하거나(inf) 전부
            # False(nan)라, 유한성 없이 접으면 손상 레코드가 수익률 inf 로 위장된다.
            # 결과값도 검사한다: 유한 피연산자끼리도 나눗셈이 오버플로할 수 있다
            # (예: base 1e-300) — 피연산자 게이트만으론 inf 가 분해에 실린다.
            ret = (
                (close_price / base_price - 1.0)
                if base_price is not None and math.isfinite(base_price)
                and math.isfinite(close_price)
                and base_price > 0 and close_price > 0
                else None
            )
            returns[unit_id] = ret if ret is not None and math.isfinite(ret) else None
        return returns

    def load_holdings(
        self, etf_id: str, market: str, trade_date: date
    ) -> tuple[list[Holding], str | None]:
        """한 ETF 의 구성종목 비중(fraction).

        대상 ETF 행 존재 기준으로 고른다: trade_date 이하 최신 as_of, 없으면 가장 이른
        미래 스냅샷 — 파이프라인 트리거 writer(ALPHA-418)와 같은 규칙이라 발화한 트리거와
        그 설명이 같은 holdings 로 분해된다.
        """
        base = f"{LAKE_HOLDINGS_PREFIX}/market={market}/"
        dates = self._partition_values(base, "as_of_date")
        target = trade_date.isoformat()
        eligible = [x for x in dates if x <= target]
        future = [x for x in dates if x > target]
        for chosen in [*reversed(eligible), *future]:
            rows = self._read_parquet_prefix(
                f"{base}as_of_date={chosen}/",
                ["etf_id", "constituent_ticker", "constituent_name", "weight_pct"],
            )
            holdings = [
                Holding(
                    ticker=str(r["constituent_ticker"]),
                    name=r.get("constituent_name"),
                    weight=float(r["weight_pct"] or 0.0) / 100.0,
                )
                for r in rows
                if str(r.get("etf_id")) == etf_id and r.get("constituent_ticker")
            ]
            if holdings:
                return holdings, chosen
        return [], None
