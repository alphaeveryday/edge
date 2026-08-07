"""1분 파이프라인 공통 request/result 계약 + 세션 window 계획 (계획 §4·§6).

가격·뉴스 Worker 가 같은 계약을 쓴다. `unit_ids` 는 가격에서 instrument ID, 뉴스에서
source ID 를 담는 중립 필드다.

불변식(생성 시점에 fail-loud — config/models.py 결):
- 모든 datetime 은 timezone-aware 다. naive 는 거부한다.
- window 는 half-open `[start, end)` 이고 `start < end` 다.
- 요청 처리 중에 현재 시각으로 window 를 재계산하지 않는다. clock 은 scheduler 와
  테스트가 주입한다(계획 §4).

결정적 checksum/ID 는 v0.7 10.6절 규약을 따른다: 고정 필드 순서의 UTF-8 JSON array,
UTC RFC3339 `Z` timestamp, lowercase SHA-256.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from ..config.models import NonBlankStr
from .states import RESULT_STATUSES, WINDOW_VALID, WINDOW_VALID_EMPTY

KST = ZoneInfo("Asia/Seoul")

# KRX 정규장 09:00–15:30 (KST) = 390분. half-open 이라 마지막 window 는 [15:29, 15:30).
SESSION_OPEN = time(9, 0)
SESSION_CLOSE = time(15, 30)
WINDOWS_PER_SESSION = 390

# NXT 시간외까지 거래되는 종목은 08:00–20:00 (KST) = 720분이다 (2026-08-01 실측·결정,
# `.dev/toss-openapi-실측.md`). ⚠️ 이건 **상품군 축이 아니라 종목별 속성**이다 — ETF·지수는
# 물론 개별주 001527 도 15:30 이 마지막이었다. 그래서 클래스는 규칙이 아니라 universe 가
# 선언한다(Universe.extended_hours_ids).
EXTENDED_OPEN = time(8, 0)
EXTENDED_CLOSE = time(20, 0)
WINDOWS_PER_EXTENDED_SESSION = 720

# 종가 단일가 접수 개시. 이 시각부터 마감까지는 **주문만 받고 체결이 없다** — 그 구간
# window 는 마감이 확정된 뒤에 집는다(`scheduled_at_for`).
CLOSING_AUCTION_OPEN = time(15, 20)
# 마감 확정을 기다리는 시간. KRX 종가 단일가는 15:20~15:30 접수 후 **15:30:00 에 한 번**
# 체결되는데, 벤더 캔들에 실리기까지 시차가 있다.
FINAL_WINDOW_SETTLE_SEC = 60
# 그 지연이 걸리는 dataset. **가격 캔들 얘기**라 뉴스 세션(news_minute)에는 안 건다 —
# 같은 `plan_session` 을 쓰지만 15:30 에 기다릴 이유가 없고, 1분 지연은 뉴스 realtime
# 레인의 추출·조립을 그만큼 늦춘다.
PRICE_MINUTE_DATASET = "price_minute"


def scheduled_at_for(window_end: datetime, *, dataset: str) -> datetime:
    """window 가 claim 가능해지는 시각 — 보통 `window_end`(구간이 닫혀야 봉이 있다).

    ⚠️ **종가 단일가 접수 구간(15:20~15:30)에 걸친 window 는 예외**다 — 전부 마감 확정
    뒤(15:31)로 민다. 그 구간은 주문만 받고 체결이 없는데, 벤더는 그 사실을 **제때 말해
    주지 않는다**. 같은 좌표를 언제 묻느냐로 답이 갈린다(2026-08-05 실측, 005930):

        15:19 봉  O247500 H247500 L247000 C247000  vol 26,328   ← 실제 체결
        15:20~   그 봉이 여덟 창에 **거래량째 복제**된다        ← 접수 구간에 즉시 물은 답
        15:29 봉  O246000 …                        vol 1,382,080 ← 단일가 체결
        같은 좌표를 마감 뒤에 물으면 `vol 0`(무거래) 이 온다  ← 정직한 답

    복제 봉은 `volume != 0` 이라 4분류에서 `received`(정상)로 접히고, 5분봉 롤업이
    거래량을 합산하므로 마지막 버킷이 부푼다(08-05 실측 중앙 1.37배·최대 60배).
    08-03 의 마감 봉 종가 불일치(0005G0 수집 43,710 V=0 → 소급 43,305 = 일봉 종가)도
    같은 결함의 마지막 창 발현이었다.

    요청한 **좌표는 맞다** — 창을 늘릴 문제가 아니라 **묻는 시각**의 문제다. 창을 안
    만드는 게 아니라 claim 시각만 미루므로 원장에 구멍이 생기지 않고, KIS 는 한 콜이
    `window_end` 로 끝나는 30분치라 콜 수도 늘지 않는다.

    지연을 `scheduled_at` 에 두는 이유: worker tick 안에서 자면 `worst_tick` 이 늘어
    `session_lease_seconds` 검증(기본값 여유 **15초**)에 걸리고, lease 가 처리 중 만료되면
    그 window 의 커밋이 fence 에 거부된다. `scheduled_at` 은 claim 조건이라 tick 을
    막지 않고 lease 불변식과 무관하다.

    ⚠️ 시간외 세션(720 window)에도 이 구간이 있고 거기에도 적용된다 — 단일가 체결 시각은
    세션 길이와 무관하기 때문이다. 15:31 이후 window 는 안 걸린다. drain 은 20:05 이고
    밀린 10창은 틱당 3창(realtime 1 + recovery budget 2)씩 빠져 여유가 있다.
    """
    if window_end.tzinfo is None or window_end.tzinfo.utcoffset(window_end) is None:
        # naive 는 실행 환경 TZ 로 해석돼 **호스트마다 다른 결과**가 나온다 — KST 호스트
        # 에서는 15:30 이 마감으로 잡혀 지연되지만 UTC 호스트에선 KST 00:30 이라 안 걸린다.
        # 이 모듈의 계약(모듈 docstring)이 aware 이므로 조용히 넘기지 않는다(Rule 12).
        raise ValueError(f"naive window_end 는 받지 않는다: {window_end!r}")
    if dataset != PRICE_MINUTE_DATASET:
        return window_end
    local_end = window_end.astimezone(KST)
    # 반열림이라 접수 개시(15:20)로 **끝나는** window 는 마지막 실거래 분이다 — 안 민다.
    if not CLOSING_AUCTION_OPEN < local_end.time() <= SESSION_CLOSE:
        return window_end
    # 기준은 window_end 가 아니라 **마감 시각**이다 — 구간 전체가 같은 한 번의 체결을
    # 기다리므로 15:21 창이라고 15:22 에 물어도 답은 여전히 복제 봉이다.
    settled = local_end.replace(
        hour=SESSION_CLOSE.hour, minute=SESSION_CLOSE.minute, second=0, microsecond=0
    )
    return settled + timedelta(seconds=FINAL_WINDOW_SETTLE_SEC)

ExecutionMode = Literal["one_shot", "resident"]


# ── 결정적 직렬화·checksum (v0.7 10.6절) ──


def _json_default(value: object) -> str:
    if isinstance(value, datetime):
        # tzinfo 존재만으론 부족하다 — utcoffset() 이 None 이면 Python 기준 naive 다
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError("naive datetime 은 직렬화하지 않는다")
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, UUID):
        return str(value)
    raise TypeError(f"결정적 직렬화 불가 타입: {type(value).__name__}")


def canonical_json(payload: object) -> str:
    """고정 필드 순서(호출자가 array 순서로 고정)·UTC `Z`·공백 없는 결정적 JSON.

    NaN/Infinity 는 표준 JSON 이 아니라 거부한다(allow_nan=False) — 비정상 값에
    유효한 checksum 을 부여하지 않는다.
    """
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
        default=_json_default,
    )


def content_checksum(payload: object) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


# ── 공통 계약 (계획 §4) ──

# strict=True: '3'(str)·True(bool) 가 수량으로 조용히 강제되는 것을 막는다
Count = Annotated[int, Field(strict=True, ge=0)]
Generation = Annotated[int, Field(strict=True, ge=1)]
Sha256Hex = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class CollectionRequest(BaseModel):
    """한 window 수집 요청. scheduler/테스트가 window 와 clock 을 확정해서 넘긴다."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    dataset: NonBlankStr
    window_start: AwareDatetime
    window_end: AwareDatetime
    # ADR-0027 계열 TEXT 도메인 ID (ops·minute 원장의 stable_domain_id 와 같은 축) —
    # UUID 타입으로 좁히면 원장이 만든 `msn_<hash>` session_id 가 여기서 거부된다
    run_id: NonBlankStr
    session_id: NonBlankStr
    execution_mode: ExecutionMode
    universe_version: NonBlankStr
    unit_ids: Annotated[tuple[NonBlankStr, ...], Field(min_length=1)]
    failure_injection: str | None = None

    @model_validator(mode="after")
    def _validate(self) -> CollectionRequest:
        if self.window_start >= self.window_end:
            raise ValueError(
                f"half-open window 위반: start({self.window_start.isoformat()}) < "
                f"end({self.window_end.isoformat()}) 여야 한다"
            )
        if len(set(self.unit_ids)) != len(self.unit_ids):
            raise ValueError("unit_ids 에 중복이 있다 — 수량 계약이 깨진다")
        return self


class CollectionResult(BaseModel):
    """한 window 수집 결과. checksum 은 데이터에서만 유도한다 — 실행 시각과 무관."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: NonBlankStr
    expected_count: Count
    succeeded_count: Count
    failed_count: Count
    retry_count: Count
    artifact_uri: NonBlankStr
    manifest_checksum: Sha256Hex
    result_checksum: Sha256Hex
    watermark_before: AwareDatetime | None
    watermark_after: AwareDatetime | None
    generation: Generation
    stage_timestamps: Annotated[dict[str, AwareDatetime], Field(min_length=1)]

    @model_validator(mode="after")
    def _validate(self) -> CollectionResult:
        if self.status not in RESULT_STATUSES:
            # 수집 결과 어휘만 허용 — 실행 축(SUCCEEDED)·원장 축(DUE/CLAIMED/MISSING)·
            # ops 관측 축(UNKNOWN)이 오면 window CHECK 저장 전 여기서 터진다
            raise ValueError(f"status {self.status!r} 는 수집 결과 어휘가 아니다")
        if self.succeeded_count + self.failed_count != self.expected_count:
            # 합이 모자라면 미분류 unit 이 조용히 사라진 것이다 — VALID 위장 금지
            raise ValueError(
                f"unit 분류 불완전: succeeded({self.succeeded_count})+failed"
                f"({self.failed_count}) != expected({self.expected_count})"
            )
        if self.failed_count > 0 and self.status in (WINDOW_VALID, WINDOW_VALID_EMPTY):
            # 실패가 있는데 VALID 면 status 만 믿는 소비자가 누락 window 를 정상 확정한다
            raise ValueError(f"failed_count={self.failed_count} 인데 status={self.status}")
        return self


# ── 세션 window 계획 ──


def plan_session_windows(
    session_date: date, *, universe: Universe | None
) -> tuple[tuple[datetime, datetime], ...]:
    """하루의 명시적 1분 half-open window 목록 (KST).

    범위는 universe 가 정한다 — 시간외 거래 종목이 하나라도 있으면 08:00–20:00(720개),
    없으면 정규장 09:00–15:30(390개)다. 계획 범위와 window 별 기대 유니버스
    (`Universe.units_at`)는 **같은 규칙**에서 나와야 한다: 계획이 더 넓으면 기대가 빈
    window 가 생기고, 좁으면 시간외 분이 아예 관측되지 않는다.

    `universe` 는 기본값 없이 **항상 명시**한다(가격 universe 가 없는 뉴스 세션은
    `None`). 기본값을 두면 시간외 종목이 있는 세션의 planner 가 인자를 빠뜨렸을 때
    390개만 계획되고, Worker 는 claim 할 행 자체가 없어 시간외 구간이 **아무 실패
    신호 없이** 누락된다.

    tz 는 KST 고정이다 — 거래시간 상수가 KST 로 정의됐고, `units_at` 도 KST 로 읽는다.
    한쪽만 다른 tz 로 부르면 계획과 기대가 통째로 어긋난다.
    """
    extended = bool(universe and universe.extended_hours_ids)
    open_at = datetime.combine(
        session_date, EXTENDED_OPEN if extended else SESSION_OPEN, tzinfo=KST
    )
    close_at = datetime.combine(
        session_date, EXTENDED_CLOSE if extended else SESSION_CLOSE, tzinfo=KST
    )
    windows: list[tuple[datetime, datetime]] = []
    cursor = open_at
    while cursor < close_at:
        windows.append((cursor, cursor + timedelta(minutes=1)))
        cursor += timedelta(minutes=1)
    return tuple(windows)


# ── universe fixture 계약 ──


class Universe(BaseModel):
    """ETF + 고유 구성종목 universe. version 은 session 의 고정 속성이다(계획 §7).

    **수집 축과 판정 축은 같지 않다.** `unit_ids`(수집)는 세 목록의 합이지만 `etf_ids`
    만이 판정 대상이다 — `price_consumer` 가 `etf_ids` 를 그대로 트리거 판정 집합으로
    받아(`price_consumer_cli`→`PriceTriggerHandler.etf_ids`) 임계 초과분을 발화시키고,
    `needs_open` 도 이 집합으로 전일 종가 결손을 센다. 그래서 "봉만 받고 싶은" 계열을
    `etf_ids` 에 얹으면 조용히 발화 대상·기준선 대상이 된다.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    universe_version: NonBlankStr
    # **판정 대상 ETF.** 여기 있는 것은 전부 트리거 판정을 받는다(위 도크스트링).
    etf_ids: Annotated[tuple[NonBlankStr, ...], Field(min_length=1)]
    constituent_ids: Annotated[tuple[NonBlankStr, ...], Field(min_length=1)]
    # 08:00–20:00 을 거래하는 unit (NXT 시간외). 나머지는 09:00–15:30 이다. 비어 있으면
    # 전 종목 정규장 — 지금까지의 390 계획과 같다. 값은 실측으로 채운다(추측 금지):
    # 잘못 넣으면 그 종목의 시간외 window 가 영구 INCOMPLETE 이고, 빠뜨리면 그 종목의
    # 시간외 분이 조용히 관측되지 않는다.
    extended_hours_ids: tuple[NonBlankStr, ...] = ()
    # **참조 계열 ETF — 봉만 받고 판정은 안 한다.** 층 분해의 섹터 후보처럼 남을 설명하는
    # 대조축으로만 쓰이는 계열이다. `etf_ids` 에 넣으면 안 되는 이유가 둘 있다:
    #   ① 트리거 — 레버리지·인버스가 임계를 넘겨 구성종목 없는 상품의 설명이 발화된다.
    #   ② 기준선 — 일봉 유니버스(holdings 파생) 밖이라 `price_daily` 행이 영영 없고,
    #      `needs_open` 이 상시 비지 않아 매 세션 시가 폴백이 걸린다(정상 세션에서
    #      첫 window 미커밋이 판정을 막지 않게 한 설계가 되돌려진다).
    sector_etf_ids: tuple[NonBlankStr, ...] = ()

    @model_validator(mode="after")
    def _validate(self) -> Universe:
        etfs, constituents = set(self.etf_ids), set(self.constituent_ids)
        sectors = set(self.sector_etf_ids)
        if (len(etfs) != len(self.etf_ids) or len(constituents) != len(self.constituent_ids)
                or len(sectors) != len(self.sector_etf_ids)):
            raise ValueError("universe 내부에 중복 ID 가 있다")
        if etfs & constituents:
            raise ValueError(f"ETF/구성종목 ID 가 겹친다: {sorted(etfs & constituents)[:5]}")
        # 참조 계열이 판정 축에도 있으면 "판정 안 한다"가 거짓이 되고, `unit_ids` 에 같은
        # 종목이 두 번 실려 완전성 판정의 기대 개수가 어긋난다 — 겹치면 거부한다.
        if overlap := sectors & (etfs | constituents):
            raise ValueError(f"참조 계열 ID 가 판정·구성종목 축과 겹친다: {sorted(overlap)[:5]}")
        extended = set(self.extended_hours_ids)
        if len(extended) != len(self.extended_hours_ids):
            raise ValueError("extended_hours_ids 에 중복 ID 가 있다")
        # universe 밖 ID 는 아무 window 에서도 수집되지 않는다 — 오타를 조용히 넘기면
        # 그 종목이 시간외에 안 잡히는 이유를 아무도 못 찾는다
        if unknown := extended - (etfs | constituents | sectors):
            raise ValueError(f"extended_hours_ids 가 universe 밖 ID 를 담았다: {sorted(unknown)[:5]}")
        return self

    @property
    def unit_ids(self) -> tuple[str, ...]:
        """**수집** 대상 전량. 판정 대상은 `etf_ids` 뿐이다(클래스 도크스트링)."""
        return self.etf_ids + self.constituent_ids + self.sector_etf_ids

    def units_at(self, window_start: datetime) -> tuple[str, ...]:
        """그 window 에 캔들이 존재해야 하는 unit — 완전성 판정의 기대 집합이다.

        정규장 안이면 전 종목, 그 밖(프리·애프터)이면 시간외 거래 종목만이다. 이걸
        시각과 무관하게 전 종목으로 두면 15:30 이 마지막인 종목이 시간외 window 마다
        missing 으로 잡혀 그 window 가 영원히 INCOMPLETE 로 남는다(2026-08-02 dev 실증).

        경계 판정은 **KST** 로 한다(거래시간 상수와 같은 축). naive datetime 은 거부한다
        — `astimezone` 이 호스트 로컬로 해석해 같은 입력이 배포 환경마다 다른 기대 집합을
        내고, 그건 조용한 누락으로 이어진다(원장 계약 전반의 aware-only 규약과 같은 결).

        거래시간 밖은 raise 다 — 조용히 빈 집합을 주면 "기대할 게 없는 window" 와
        "계획이 잘못돼 만들어진 window" 가 같은 결과가 된다.
        """
        if window_start.tzinfo is None or window_start.tzinfo.utcoffset(window_start) is None:
            raise ValueError(f"window_start 는 timezone-aware 여야 한다: {window_start!r}")
        local = window_start.astimezone(KST).time()
        if SESSION_OPEN <= local < SESSION_CLOSE:
            return self.unit_ids
        if EXTENDED_OPEN <= local < EXTENDED_CLOSE and self.extended_hours_ids:
            return self.extended_hours_ids
        raise ValueError(
            f"window {window_start.isoformat()} 는 이 universe 의 거래시간 밖이다 "
            f"— 계획되지 않아야 할 window 다"
        )

    @property
    def universe_hash(self) -> str:
        """멤버십 identity — 입력 순서에 불변(같은 구성·다른 순서 = 같은 hash).

        거래시간 클래스도 identity 에 넣는다: 멤버가 같아도 클래스가 다르면 기대
        유니버스와 window 계획이 달라진다 — 빼면 session 의 universe 충돌 가드가
        그 변경을 같은 universe 로 통과시킨다.

        단 **선언이 없으면 축 자체를 넣지 않는다** — identity 는 "이 universe 가 뜻하는
        기대"이고, 시간외 종목이 없는 universe 의 기대는 이 축이 생기기 전과 같다.
        빈 목록을 넣으면 구성이 똑같은 기존 session 이 배포만으로 다른 hash 가 돼
        UniverseConflictError 로 그날 재계획·재기동이 통째로 막힌다. 참조 계열도 같다.

        두 선택 축이 위치로만 구분되는데도 모호하지 않은 이유: **한 축만 선언된 두
        universe 는 같은 parts 를 낼 수 없다.** extended 는 멤버여야 하고(A ⊆ etfs∪const)
        참조 계열은 비멤버여야 하므로(B ∩ (etfs∪const) = ∅), etfs·const 가 같은 두
        universe 에서 A = B 는 불가능하다. (`extended_hours_ids` 자체는 참조 계열도 담을
        수 있다 — 그건 한 universe 안의 얘기라 이 논증과 무관하다.)
        """
        parts = [self.universe_version, sorted(self.etf_ids), sorted(self.constituent_ids)]
        if self.extended_hours_ids:
            parts.append(sorted(self.extended_hours_ids))
        if self.sector_etf_ids:
            parts.append(sorted(self.sector_etf_ids))
        return content_checksum(parts)


def load_universe(path: Path) -> Universe:
    return Universe.model_validate(json.loads(path.read_text(encoding="utf-8")))


def load_universe_uri(uri: str) -> Universe:
    """`load_universe` 의 URI 판 — `s3://bucket/key` 를 지원한다(ALPHA-711).

    ECS 컨테이너에는 로컬 파일 배포 축이 없다 — planner·worker·consumer 가 **같은
    S3 객체**를 가리키면 세 표면의 universe 가 한 정본에서 나온다.
    """
    if uri.lower().startswith("s3://"):  # scheme 은 대소문자 무관(RFC 3986)
        import boto3  # 지연 import — 로컬 경로만 쓰는 테스트에 AWS 의존을 안 끼운다

        bucket, _, key = uri[len("s3://"):].partition("/")
        if not bucket or not key:
            raise ValueError(f"s3 universe URI 형식 오류: {uri!r}")
        body = boto3.client("s3").get_object(Bucket=bucket, Key=key)["Body"].read()
        return Universe.model_validate(json.loads(body.decode("utf-8")))
    return load_universe(Path(uri))
