"""업종지수를 1분 레인 다섯 번째 dataset 으로 넣으면서 함께 들어가는 가드 (ALPHA-887).

`test_inav_wiring.py` 가 형식 선례다. 이 dataset 은 **하이브리드**라 가드가 하나 더 있다:
코드 형상은 iNAV(window artifact·unit 집합·`MinuteWorkerLoop`)인데 universe 축은
뉴스·공시(소스 단위, `--universe` 거부)다. 그 조합이 처음이라 한 축만 맞추면 다른 축이
조용히 어긋난다 — 이 파일이 네 축을 각각 못박는다.
"""

from datetime import date

import pytest

from data_pipeline.lake.storage import (
    canonical_etf_inav_minute_prefix,
    canonical_price_minute_prefix,
    canonical_sector_index_minute_artifact_key,
    canonical_sector_index_minute_prefix,
)
from data_pipeline.minute.models import Universe, plan_session_windows
from data_pipeline.minute.session_ops import _resolve
from data_pipeline.minute.states import (
    DATASET_SECTOR_INDEX_MINUTE,
    EXTENDED_HOURS_DATASETS,
    MINUTE_DATASETS,
    SCALED_DATASETS,
    SOURCE_GROUPS_BY_DATASET,
    UNIVERSE_DATASETS,
)


def test_어휘에_있어도_서비스를_소유하지_않으면_stop_대상이_아니다():
    """어휘와 스케일 권한은 **다른 축**이다(iNAV 와 같은 가드).

    start/stop 이 올리고 내리는 서비스 목록은 dataset 별이 아니라 공용이다. 업종지수
    세션을 하나 계획한 뒤 stop 을 부르면 phase 게이트는 그 세션만 보고 통과하는데
    큐·outbox 게이트는 전역이라 **살아 있는 price-worker 가 내려간다**.
    """
    assert DATASET_SECTOR_INDEX_MINUTE in MINUTE_DATASETS      # 어휘엔 있고
    assert DATASET_SECTOR_INDEX_MINUTE not in SCALED_DATASETS  # 스케일 권한은 없다

    with pytest.raises(SystemExit, match="상주 서비스를 소유하지 않는다"):
        _resolve(DATASET_SECTOR_INDEX_MINUTE, "kis")


def test_기대_집합이_universe_가_아니다():
    """🔴 지수 45종은 universe.json 에 **없다** — ETF 명부에도 구성종목에도 없다.

    `UNIVERSE_DATASETS` 에 넣으면 planner 가 `--universe` 를 요구하고, 그 파일로 세운
    기대 집합에는 업종코드가 한 줄도 없어 매 window 가 빈 성공(VALID_EMPTY)으로
    확정된다. 정본은 `[minute_sector_index.index_map]` 이다.
    """
    assert DATASET_SECTOR_INDEX_MINUTE not in UNIVERSE_DATASETS


def test_격자는_시간외로_넓히지_않는다():
    """정규장 390 window 다.

    이 TR 은 정규장 지수만 준다 — 시간외로 넓히면 매 거래일 330 window 가 아무도 못
    채운 채 DUE 로 남고, 이 소스는 소급이 불가라(소급 TR 은 일봉으로 degrade) 영구
    결손이다. iNAV 를 시간외에서 막은 것과 결론은 같고 근거만 다르다.
    """
    universe = Universe(
        universe_version="v1", etf_ids=("069500",), constituent_ids=("005930",),
        extended_hours_ids=("069500",),
    )
    day = date(2026, 8, 10)

    extended = plan_session_windows(day, universe=universe, extended_hours=True)
    # 이 dataset 의 실제 격자 — universe 없이 세운다(planner 가 그렇게 부른다)
    regular = plan_session_windows(day, universe=None, extended_hours=False)

    assert len(extended) == 720   # 08:00~20:00
    assert len(regular) == 390    # 09:00~15:30
    assert regular[0][0].hour == 9
    assert DATASET_SECTOR_INDEX_MINUTE not in EXTENDED_HOURS_DATASETS


def test_소스는_KIS_하나다():
    """KRX 는 지수 분봉 API 를 안 열고(일봉만), 토스에는 지수 축이 없다."""
    assert SOURCE_GROUPS_BY_DATASET[DATASET_SECTOR_INDEX_MINUTE] == frozenset({"kis"})


def test_canonical_키가_프리픽스에서_자라고_남의_dataset_과_갈린다():
    """prefix 는 스캔 축, key 는 쓰기 축 — 조립이 두 곳이면 한쪽만 옮겨진다.

    분봉·iNAV 와 섞으면 그 window 가 "종목은 다 왔는데 지수는 안 왔다"를 표현할 수 없어
    매번 INCOMPLETE 다(기대 집합이 다르기 때문이다).
    """
    prefix = canonical_sector_index_minute_prefix("KR", "2026-08-10")
    key = canonical_sector_index_minute_artifact_key("KR", "2026-08-10", "0931", 1)

    assert key.startswith(prefix)
    assert prefix != canonical_price_minute_prefix("KR", "2026-08-10")
    assert prefix != canonical_etf_inav_minute_prefix("KR", "2026-08-10")
    # 정정은 새 generation → 새 key 라 원본을 안 덮는다. run_id 는 없다(재실행 no-op).
    assert key != canonical_sector_index_minute_artifact_key("KR", "2026-08-10", "0931", 2)
    assert key == canonical_sector_index_minute_artifact_key("KR", "2026-08-10", "0931", 1)
    # ⚠️ **window 축을 명시로 못박는다** — 이 단언이 없으면 키에서 `window=` 세그먼트를
    # 통째로 빼도 위 단언들이 전건 통과한다(iNAV 에서 실제로 그랬다). 그러면 하루
    # 390 window 가 한 키를 다툰다.
    assert "window=0931" in key
    assert key != canonical_sector_index_minute_artifact_key("KR", "2026-08-10", "0932", 1)
