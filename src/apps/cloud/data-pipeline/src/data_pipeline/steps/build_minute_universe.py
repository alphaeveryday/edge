"""1분 레인 universe 정본을 canonical KR holdings 에서 재생성한다 (ALPHA-735·953).

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
구간(장중) 모드에서 섹터층이 통째로 빠진다 — 층 분해의 섹터 **정본**인 KRX 업종지수는
1분봉을 수집하지만(ALPHA-887, dataset `sector_index_minute`) 소비 배선이 아직 없어
(analysis `layers.select_sector` 의 후보는 섹터 ETF 뿐이다) 그 자리를 이 목록이 메운다.
⚠️ **"일봉 경로가 쓴다"고 적지 마라** — #657 이 일 모드를 걷어내 `layers.decompose` 는
`clock` 을 필수로 받고 `sector_index` 일봉을 읽지 않는다.

**이 스텝은 정본 객체까지 갱신한다**(ALPHA-953). 앞선 `scripts/build_minute_universe.py`
는 파일만 만들고 반영을 사람에게 넘겼는데, 그 근거는 *"갈아끼우는 순간 그날 계획이
바뀐다"* — 즉 **세션 중 교체** 위험이었다(08-11 12:07 교체가 4종의 10·11시 분봉을 영구
결손시켰다, ALPHA-936). 이 스텝은 세션 계획(start cron 07:45 KST) **전**에만 도는
자리라 그 위험이 구조적으로 없다. 세션이 이미 선 뒤에 이걸 돌리면 그 근거가 되살아난다
— 장중 실행은 하지 마라.
"""
from __future__ import annotations

import json
import logging
import math
from datetime import datetime, time

from pydantic import ValidationError

from ..lake.storage import Storage
from ..minute.models import (
    KST,
    Universe,
    content_checksum,
    read_universe_bytes,
    write_universe_bytes,
)
from ..ops.trading_calendar import is_trading_day
# 유니버스 파생은 수집 스텝이 정본이다 — 규칙을 두 벌로 만들면 한쪽만 고쳐진다.
# 공시·투자자 스텝도 같은 함수를 그대로 가져다 쓴다(ingest_raw_disclosure·investor).
from .ingest_price_raw import (
    _kr_etf_ids,
    _kr_holdings_universe,
    _krx_expected_etfs,
)

logger = logging.getLogger(__name__)

# 멤버십 방어 임계 — 직전 유니버스의 unit 중 이 비율만큼은 새 유니버스에 **그대로
# 남아야** 한다(개수가 아니라 교집합이다 — `_refuse_membership_loss`).
# 값의 근거는 정밀 실측이 아니라 **역할**이다: 정상 리밸런싱(ETF 하나가 몇 종 갈아끼우는
# 일)은 통과시키고 수집 사고(스냅샷이 통째로 낡거나 빈 수집이 착지, 남의 구성종목이
# 다른 etf_id 로 오배정)만 잡는 성긴 그물이다. `_kr_holdings_universe` 가 "ETF 별 최신
# 스냅샷 합집합"이라 원래 잘 안 흔들리므로, 큰 이탈은 그 자체가 수집을 의심할 신호다.
# ponytail: 고정 비율 하나 — 오탐/미탐이 실제로 나면 ETF 별 결손 판정으로 올려라
MIN_KEEP_RATIO = 0.9

# 거래일에 교체를 허용하는 마지막 시각(KST). 이 시각 이후엔 **세션이 이미 계획됐을 수
# 있다** — 그러면 원장의 universe_hash 는 옛 값에 고정된 채 객체만 바뀌어, 재기동한
# worker·consumer 가 매 틱 blocked 로 돌면서도 안 죽는다(08-11 12:07 실증, ALPHA-936).
#
# ⚠️ **이 상수가 계약이고 크론이 그것을 지킨다** — 반대가 아니다. 장전 체인은 이 시각
# **전에** 끝나야 하고, 늦으면 스텝이 스스로 거부한다. 그게 의도다: 계획 뒤에 갈아끼우는
# 것보다 그날 재빌드를 거르는 편이 낫다(전자는 그날 분봉이 영구 결손, 후자는 신규 편입이
# 하루 늦을 뿐이다). 세션 계획(start-minute-session)은 07:45 KST 라 15분 여유다.
# 비거래일엔 세션 자체가 없으므로 시각을 안 본다.
REBUILD_CUTOFF_KST = time(7, 30)


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
    # **선언했는데 어느 축에도 안 실리는 ETF 는 거부한다**(ALPHA-927). etf_map 은 판정
    # 축을 선언하지만 `etf_ids` 는 holdings 파생이라(`_kr_etf_ids`), 선언만 있고 스냅샷이
    # 아직 없으면 그 ETF 는 `etf_ids` 도 `sector_etf_ids` 도 아니고 남의 구성종목도 아니다
    # — **universe 에서 통째로 사라진다.** 그런데도 아래 `not etf_ids` 게이트는 나머지
    # 33종이 멀쩡해 통과하고, 유일한 신호는 `_latest_kr_holdings_rows` 의 warning 한 줄에
    # exit 0 이다. 요약 줄까지 평시와 같아("판정 ETF 33종") 사람이 그 객체를 그대로 올린다.
    #
    # 이 창은 **참조 계열에서 판정 축으로 ETF 를 옮길 때 반드시 열린다** — 옮긴 직후부터
    # 첫 KRX holdings 런이 착지하기 전까지다. 그 사이에 만든 universe 를 올리면 그 ETF 는
    # 봉조차 못 받아 **옮기기 전보다 나빠진다**(참조 계열이었을 땐 봉은 받았다).
    # 그래서 겹침(Rule 7)과 같은 급으로 거부한다 — 사람이 순서를 지키게 만드는 것은
    # 문서가 아니라 여기다(Rule 12).
    # ⚠️ **1종 결손이 전면 차단으로 승격된다** — 폐지·거래정지·장기 수집실패로 어떤 ETF 가
    # `UNIVERSE_LOOKBACK_PARTITIONS` 파티션을 넘겨 결손이면 universe 재생성 자체가 막히고,
    # 탈출구는 etf_map 편집뿐이다(`--allow-missing` 류를 일부러 안 뒀다). 의도한 값이다:
    # 여기서 통과시키면 그 ETF 가 **조용히** 사라지는데, 그건 급할 때 아무도 안 본다.
    if orphan := sorted(set(expected_etfs or ()) - judged):
        raise SystemExit(
            f"etf_map 이 선언했는데 canonical KR holdings 에 없는 ETF: {orphan} — 이대로 "
            f"만들면 이 종목들이 universe 세 축 어디에도 안 실려 1분봉조차 수집되지 않는다. "
            f"순서는 ①새 sources.toml 이 든 **이미지 배포**(etf_map 은 이미지 동봉 config 가 "
            f"정본이다 — terraform 이 env 로 안 넘긴다. tasks.tf 는 자격증명만 준다) → "
            f"②그 뒤 일배치 15:40 런의 CollectKrxEtf → "
            f"NormalizeEtf 착지 → ③여기 재실행이다. ①을 건너뛰면 그 런도 옛 목록으로 "
            f"수집해 다음 날도 같은 자리에 선다. ②가 실패한 날은 trdDd 소급 수단이 없어 "
            f"다음 런을 기다려야 한다. 수집 대상에서 뺄 생각이면 etf_map 에서 지워라"
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
    """`Settings` → `Universe`. 호출부의 배선을 여기 모은다 — 설정에서 목록을 꺼내는
    표현이 두 군데로 갈리면 한쪽만 고쳐지고, 그 갈림은 **기능이 통째로 무력화된 채
    초록으로 도는** 형태로 드러난다(빌드 테스트가 build() 만 부르면 못 잡는다)."""
    expected = _krx_expected_etfs(settings)
    # `None` 은 "0종"이 아니라 **유니버스 뿌리가 부재**라는 뜻이고(`_krx_expected_etfs`),
    # 그 값을 그대로 넘기면 holdings 헬퍼가 "필터하지 않는다"로 읽어 레이크에 남은 폐지
    # ETF·옛 스냅샷까지 전부 실린 유니버스가 나온다. 아래 가드들도 `expected_etfs or ()`
    # 라 전부 빈 집합 대조가 되어 통과한다 — 즉 **전 축이 조용히 넓어진 채 초록**이다.
    # 손으로 파일만 뽑던 시절엔 사람이 요약 줄에서 걸렀지만, 이제 그 산출이 정본 객체로
    # 반영되므로 여기서 거부한다(Rule 12).
    if expected is None:
        raise SystemExit(
            "설정에 [krx_etf] 섹션이 없다 — 유니버스 뿌리(etf_map)가 부재한 채로는 "
            "만들지 않는다. 필터 없이 만들면 레이크에 남은 폐지 ETF·옛 스냅샷까지 "
            "실린다. --config 가 맞는 파일을 가리키는지 확인하라"
        )
    return build(storage, expected, sector_ids(settings))


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


def summary_of(universe: Universe) -> str:
    """만든 것 한 줄. 참조 계열이 0 이면 그것도 그대로 보인다 — 설정이 안 실린 것이
    조용히 정상으로 보이면 안 된다."""
    return (f"판정 ETF {len(universe.etf_ids)}종 + 참조 계열 "
            f"{len(universe.sector_etf_ids)}종 + 구성종목 {len(universe.constituent_ids)}종 "
            f"= 수집 {len(universe.unit_ids)} unit (version={universe.universe_version})")


def run(storage: Storage, settings, universe_uri: str, run_id: str,
        now: datetime | None = None) -> int:
    """유니버스를 다시 만들어 정본 객체에 반영한다. 변경이 없으면 쓰지 않는다.

    **대상 객체는 인자로 받는다** — 소비자(planner·worker·consumer)가 `--universe` 로
    받는 그 URI 다. 여기서 키를 상수로 박으면 terraform `var.minute_universe_uri` 가
    기본값에서 옮겨진 순간 생산자와 소비자가 다른 객체를 보게 되고, 그 상태는 양쪽 다
    exit 0 이라 **아무 데도 안 드러난다**(소비자는 옛 정본을 계속 읽는다).
    """
    existing = read_universe_bytes(universe_uri)
    before = _previous(existing, universe_uri)
    # 시간외 축은 holdings 파생이 **아니라** 사람이 실측으로 채우는 값이라, 직전 정본에서
    # 살려 오지 않으면 재생성이 조용히 지운다(아래 `_carry_extended`).
    universe = _carry_extended(before, build_from_settings(settings, storage))
    # 스크립트의 `--out` 과 **같은 바이트**여야 한다 — 손으로 만든 파일과 이 스텝의
    # 산출을 그대로 대조할 수 있어야 교체 여부를 사람이 확인한다.
    payload = (payload_of(universe) + "\n").encode("utf-8")

    if existing == payload:
        # 세대만 바꾸는 쓰기를 안 한다. universe_version 은 구성에서 유도되므로 같은
        # 구성이면 같은 값이고, 그때의 PUT 은 순수한 노이즈다.
        # ⚠️ **시각 가드보다 앞이다** — 무변경은 교체가 아니라 계획을 못 흔든다. 뒤로
        # 밀면 장중 재실행이 아무것도 안 바꾸면서 exit 1 을 내 알람만 울린다.
        logger.info("universe 무변경 — %s 를 쓰지 않는다: %s",
                    universe_uri, summary_of(universe))
        return 0

    _refuse_after_plan(now or datetime.now(KST))

    if existing is not None:
        if before is not None:
            _refuse_membership_loss(before, universe)
        # 08-11 수동 교체가 이미 하던 절차다 — 되돌릴 것이 없으면 잘못 만든 유니버스가
        # 그날의 유일한 정본이 된다.
        write_universe_bytes(f"{universe_uri}.bak-{run_id}", existing)

    write_universe_bytes(universe_uri, payload)
    logger.info("universe 갱신: %s ← %s", universe_uri, summary_of(universe))
    return 0


def _previous(existing: bytes | None, universe_uri: str) -> Universe | None:
    """직전 정본(없거나 **못 읽으면** None).

    ⚠️ **손상된 객체가 재빌드를 영구히 막으면 안 된다.** 그 상태는 소비자도 이미
    fail-loud 로 못 뜨는 상태라(`load_universe_uri`), 여기서까지 거부하면 탈출구가
    "사람이 객체를 지운다" 하나뿐인 채로 매일 아침 같은 자리에 선다. 앞으로 고치되
    **건너뛴 사실은 남긴다**(Rule 12) — 직전 객체는 백업으로 보존된다.

    잡는 예외는 **손상의 형태 셋**뿐이다. `Exception` 으로 넓히면 MemoryError·구현
    버그까지 '손상'으로 접혀, 멀쩡한 정본을 기준 없이 갈아치우고도 warning 한 줄로
    끝난다 — 가드를 끄는 가장 조용한 방법이다.
    """
    if existing is None:
        return None
    try:
        return Universe.model_validate(json.loads(existing.decode("utf-8")))
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as exc:
        logger.warning(
            "직전 universe 객체가 손상돼 멤버십 대조·시간외 축 승계를 건너뛴다(%s): %s "
            "— 백업(.bak-*)에 원본이 남는다", universe_uri, exc)
        return None


def _carry_extended(before: Universe | None, after: Universe) -> Universe:
    """시간외(NXT) 거래 종목 축을 직전 정본에서 승계한다.

    이 축은 **종목별 실측 속성**이라 holdings 에서 파생되지 않고(`build` 주석), 사람이
    채운 뒤로는 아무도 다시 안 넣는다. 재생성이 그걸 지우면 다음 세션부터 그 종목들의
    계획이 08:00–20:00 에서 390분으로 줄고, 사라진 시간외 window 는 **결손으로도 안
    잡힌다**(기대 집합 자체가 줄어든다).

    유니버스를 떠난 종목만 뺀다 — `Universe` 가 universe 밖 ID 를 거부하기 때문이고,
    뺐다는 사실은 남긴다(그 종목의 시간외 관측이 그날부터 끝난다는 뜻이다).
    """
    if before is None or not before.extended_hours_ids:
        return after
    units = set(after.unit_ids)
    kept = tuple(t for t in before.extended_hours_ids if t in units)
    if dropped := sorted(set(before.extended_hours_ids) - units):
        logger.warning("시간외 축에서 유니버스를 떠난 종목을 뺀다: %s", dropped)
    # 모델을 다시 통과시킨다 — `model_copy(update=…)` 는 검증을 건너뛰어, 축 간 겹침
    # 규칙이 깨진 조합을 그대로 직렬화한다.
    return Universe(**{**after.model_dump(), "extended_hours_ids": kept})


def _refuse_after_plan(now: datetime) -> None:
    """세션이 이미 계획됐을 수 있는 시각이면 교체를 거부한다(`REBUILD_CUTOFF_KST`).

    앞선 스크립트는 이 위험을 "업로드하지 않는다"로 피했다. 업로드를 이 스텝이 맡은
    이상 그 불변식을 **산문이 아니라 코드로** 다시 세워야 한다 — 도크스트링의 "장중에
    돌리지 마라"는 급할 때 아무도 안 읽는다.
    """
    if now.tzinfo is None or now.tzinfo.utcoffset(now) is None:
        # `astimezone` 이 naive 를 **호스트 로컬**로 해석한다 — UTC 컨테이너에서 KST
        # 16:00 을 뜻해 넘긴 값이 다음 날 01:00 이 되어 이 가드를 그냥 통과한다.
        # 원장 계약 전반의 aware-only 규약과 같은 자세다(`Universe.units_at`).
        raise ValueError(f"now 는 timezone-aware 여야 한다: {now!r}")
    local = now.astimezone(KST)
    if not is_trading_day(local.date()) or local.time() < REBUILD_CUTOFF_KST:
        return
    raise SystemExit(
        f"거래일 {local:%Y-%m-%d %H:%M} KST 는 교체 마감({REBUILD_CUTOFF_KST:%H:%M}) 뒤라 "
        f"거부한다 — 세션이 이미 이 유니버스로 계획됐을 수 있고, 그러면 원장의 "
        f"universe_hash 는 옛 값에 고정된 채 객체만 바뀌어 worker·consumer 가 매 틱 "
        f"blocked 로 돈다. 오늘 안에 꼭 갈아야 하면 scripts/build_minute_universe.py "
        f"로 파일을 뽑아 확인한 뒤 사람이 반영하고, 원장의 **universe_version 과 "
        f"universe_hash 를 둘 다** 고쳐라 — 소비자는 그 쌍을 비교한다"
        f"(price_consumer·repository)"
    )


def _refuse_membership_loss(before: Universe, after: Universe) -> None:
    """직전 유니버스의 unit 을 크게 잃으면 거부한다(`MIN_KEEP_RATIO`).

    잃은 종목을 그대로 올리면 그날 1분 레인이 쪼그라든 채 **초록으로** 돈다 — 빠진
    종목은 window 기대 집합에서도 빠져 결손으로도 안 잡힌다. 그 상태는 그날 분봉이
    다시 안 오므로 복구 불가다(ALPHA-936 실증).

    ⚠️ **개수가 아니라 남은 멤버로 센다.** 개수만 보면 30종이 빠지고 엉뚱한 30종이
    들어온 대량 교체가 '변화 없음'으로 통과한다 — 사고의 형태가 결손이 아니라 **오배정**
    (남의 구성종목이 다른 etf_id 로 착지)일 때 정확히 그 모양이 된다.

    ⚠️ **그리고 축마다 따로 센다.** 합쳐서 재면 작은 축이 통째로 사라져도 큰 축이
    비율을 떠받쳐 통과한다 — 실측 구성(460 unit 중 참조 계열 47종)에서 참조 계열이
    7종만 남아도 전체로는 91%다. 축은 크기가 아니라 **역할**로 나뉘어 있고
    (`Universe` 도크스트링 — 판정·구성종목·참조 계열), 참조 계열 전멸은 섹터층이
    통째로 빠진다는 뜻이다.

    ⚠️ **단, "남았다"의 범위는 축마다 다르다** — 축 대 축으로만 대조하면 **축 사이의
    정상적인 이동**이 소실로 잡힌다. 참조 계열 ETF 를 `etf_map` 에 넣어 판정 축으로
    옮기는 절차(ALPHA-927, `build` 주석이 순서까지 적어 둔 그것)가 그 형태다 — unit
    집합은 그대로인데 참조 계열 축만 빈다. 그래서 축마다 **정당한 착지점**을 준다
    (`_LANDING`): 잃었다는 것은 그 종목이 **맡던 역할을 잃었다**는 뜻이다.

    - 판정·구성종목 축은 유니버스 어디든 착지하면 남은 것으로 센다(수집은 계속된다).
    - **참조 계열만 좁다.** 이 축은 holdings 파생이 아니라 순수 config 선언이라
      (`[minute_universe].sector_etf_ids`), 사라지는 경우가 딱 둘이다 — 판정 축으로
      **승격**(정당)이거나 config **누락**(사고)이다. 누락이면 그 ETF 는 남의 구성종목
      으로 흡수돼 unit 집합은 멀쩡한 채 섹터층 후보만 통째로 없어진다. 착지점을
      두 ETF 축으로 좁히면 그 둘이 갈린다.

    ponytail: 이탈만 본다 — 반대 방향(오염 멤버 대량 유입)은 대칭 비율로 못 막는다.
    08-11 에 테마 ETF 4종을 더한 정상 변경이 410→460 unit(+12%)이라 어떤 대칭 상한도
    그날 거짓 차단이었다. 유입은 축이 다른 문제다(수집 용량 — 별건).
    """
    units_after = set(after.unit_ids)
    # 축 → 그 축의 멤버가 착지해도 '남은 것'으로 세는 범위(위 도크스트링)
    landing = {
        "etf_ids": units_after,
        "constituent_ids": units_after,
        "sector_etf_ids": set(after.sector_etf_ids) | set(after.etf_ids),
    }
    axes = (("판정 ETF", "etf_ids"), ("구성종목", "constituent_ids"),
            ("참조 계열", "sector_etf_ids"))
    for label, field in axes:
        was = set(getattr(before, field))
        kept = was & landing[field]
        floor = math.ceil(len(was) * MIN_KEEP_RATIO)
        if len(kept) >= floor:
            continue
        raise SystemExit(
            f"{label} 축이 직전 유니버스에서 크게 이탈해 거부한다: {len(was)} 중 "
            f"{len(kept)} 만 새 유니버스에 남았다(최소 {floor}) — 정상 리밸런싱이 아니라 "
            f"홀딩스 수집 결손·오배정, 참조 계열이면 config "
            f"`[minute_universe].sector_etf_ids` 누락을 의심하라. 이탈이 진짜면 기존 "
            f"객체를 `.bak-수동` 으로 **옮긴 뒤**(복사가 아니다 — 남겨 두면 같은 대조에 "
            f"또 걸린다) 다시 돌리고, 그 백업의 extended_hours_ids 를 새 객체에 손으로 "
            f"옮겨 담아라 — 승계할 직전 객체가 없어져 시간외 축이 빈 채로 만들어진다"
        )
