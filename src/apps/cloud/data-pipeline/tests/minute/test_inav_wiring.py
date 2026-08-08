"""iNAV 를 1분 레인 어휘에 다시 넣으면서 함께 들어가는 두 가드 (ALPHA-851).

ALPHA-845 는 이 어휘를 **뺐다** — 넣는 순간 `start/stop-minute-session` 이 열려 공유
서비스를 내릴 수 있었기 때문이다. 삭제가 아니라 가드로 푸는 것이 이 파일의 내용이고,
가드가 없으면 어휘를 넣으면 안 된다.
"""

import pytest

from data_pipeline.lake.storage import (
    canonical_etf_inav_minute_artifact_key,
    canonical_etf_inav_minute_prefix,
    canonical_price_minute_prefix,
)
from data_pipeline.minute.models import Universe, plan_session_windows
from data_pipeline.minute.session_ops import _resolve
from data_pipeline.minute.states import (
    DATASET_ETF_INAV_MINUTE,
    DATASET_PRICE_MINUTE,
    EXTENDED_HOURS_DATASETS,
    MINUTE_DATASETS,
    SCALED_DATASETS,
    SOURCE_GROUPS_BY_DATASET,
)
from datetime import date


def test_어휘에_있어도_서비스를_소유하지_않으면_stop_대상이_아니다():
    """어휘(`MINUTE_DATASETS`)와 스케일 권한은 **다른 축**이다.

    start/stop 이 올리고 내리는 서비스 목록은 dataset 별이 아니라 공용이다. iNAV 세션을
    하나 계획한 뒤 stop 을 부르면 phase 게이트는 그 세션만 보고(claim 0 → 즉시 통과)
    큐·outbox 게이트는 전역이라 **살아 있는 price-worker 가 내려간다**. 도움말 산문은
    게이트가 아니다(Rule 12) — `rollup.py` 의 상수 리젝트가 같은 선례다.
    """
    assert DATASET_ETF_INAV_MINUTE in MINUTE_DATASETS      # 어휘엔 있고
    assert DATASET_ETF_INAV_MINUTE not in SCALED_DATASETS  # 스케일 권한은 없다

    with pytest.raises(SystemExit, match="상주 서비스를 소유하지 않는다"):
        _resolve(DATASET_ETF_INAV_MINUTE, "kis")


def test_배선된_dataset_은_그대로_통과한다():
    """가드가 기존 경로를 막으면 그날 가격 레인이 통째로 선다."""
    assert _resolve(DATASET_PRICE_MINUTE, "kis") == (DATASET_PRICE_MINUTE, "kis")


def test_iNAV_격자는_시간외로_넓히지_않는다():
    """어댑터의 수집 하한이 09:00 이고 그 하한은 **앞으로 못 내린다**(파티션 `ingest_date`
    가 UTC 스탬프라 09:00 KST 미만은 파티션이 전날로 붙는다). 격자만 08:00 로 넓히면
    매 거래일 60 window 가 아무도 못 채운 채 DUE 로 남고, iNAV 는 소급이 불가라 영구
    결손이다."""
    universe = Universe(
        universe_version="v1", etf_ids=("069500",), constituent_ids=("005930",),
        extended_hours_ids=("069500",),  # 시간외 종목이 있어도
    )
    day = date(2026, 8, 10)

    price = plan_session_windows(day, universe=universe, extended_hours=True)
    inav = plan_session_windows(day, universe=universe, extended_hours=False)

    assert len(price) == 720   # 08:00~20:00
    assert len(inav) == 390    # 09:00~15:30
    assert inav[0][0].hour == 9
    assert DATASET_ETF_INAV_MINUTE not in EXTENDED_HOURS_DATASETS


def test_iNAV_소스는_KIS_하나다():
    """토스 분봉 API 에는 NAV 축이 없다(`1m`·`1d` 캔들만)."""
    assert SOURCE_GROUPS_BY_DATASET[DATASET_ETF_INAV_MINUTE] == frozenset({"kis"})


def test_canonical_키가_프리픽스에서_자라고_분봉과_갈린다():
    """prefix 는 스캔 축, key 는 쓰기 축 — 조립이 두 곳이면 한쪽만 옮겨진다. 그리고
    분봉과 섞으면 그 window 가 "봉은 다 왔는데 NAV 는 ETF 것만" 을 표현할 수 없어
    매번 INCOMPLETE 다(구성종목에는 NAV 가 없다)."""
    prefix = canonical_etf_inav_minute_prefix("KR", "2026-08-10")
    key = canonical_etf_inav_minute_artifact_key("KR", "2026-08-10", "0931", 1)

    assert key.startswith(prefix)
    assert prefix != canonical_price_minute_prefix("KR", "2026-08-10")
    # 정정은 새 generation → 새 key 라 원본을 안 덮는다. run_id 는 없다(재실행 no-op).
    assert key != canonical_etf_inav_minute_artifact_key("KR", "2026-08-10", "0931", 2)
    assert key == canonical_etf_inav_minute_artifact_key("KR", "2026-08-10", "0931", 1)
    # ⚠️ **window 축을 명시로 못박는다.** 위 단언은 전부 한 window 안에서만 비교해서,
    # 키에서 `window=` 세그먼트를 통째로 빼도 전건 통과했다. 그러면 하루 390 window 가
    # 한 키를 다투고 09:32 가 09:31 위에 ArtifactImmutabilityError 를 내거나 덮어쓴다.
    assert "window=0931" in key
    assert key != canonical_etf_inav_minute_artifact_key("KR", "2026-08-10", "0932", 1)
