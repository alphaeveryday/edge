"""공시를 1분 레인 어휘에 넣으면서 함께 들어가는 가드 (ALPHA-875).

격자 폭(720)의 배선 반례는 `test_session_cli.py` 에 있다 — 순수 함수(`plan_session_windows`)
와 상수(`EXTENDED_HOURS_DATASETS`)만 보는 테스트는 그 둘을 잇는 CLI 를 안 지나기 때문이다
(iNAV 가 그 교훈을 남겼다). 여기 남는 것은 어휘 자체의 축이다.
"""

import pytest

from data_pipeline.minute.session_ops import _resolve
from data_pipeline.minute.states import (
    DATASET_DISCLOSURE_MINUTE,
    EXTENDED_HOURS_DATASETS,
    MINUTE_DATASETS,
    SCALED_DATASETS,
    SOURCE_GROUPS_BY_DATASET,
    UNIVERSE_DATASETS,
)


def test_어휘에_있어도_상주_서비스를_소유하지_않는다():
    """iNAV 와 같은 축이다 — start/stop 이 올리고 내리는 서비스 목록은 dataset 별이 아니라
    **공용**이라, 공시 세션을 지목해 stop 을 부르면 phase 게이트는 그 세션만 보고(claim 0
    → 즉시 통과) 큐·outbox 게이트는 전역이라 **살아 있는 price-worker 가 내려간다**.

    공시 생산자는 뉴스와 같은 형태로 붙는다(공용 목록이 아닌 자기 서비스 목록 + 세션이 선
    날만 스케일) — 그래서 이 dataset 이 `SCALED_DATASETS` 에 들어갈 일은 없다.
    """
    assert DATASET_DISCLOSURE_MINUTE in MINUTE_DATASETS      # 어휘엔 있고
    assert DATASET_DISCLOSURE_MINUTE not in SCALED_DATASETS  # 스케일 권한은 없다

    with pytest.raises(SystemExit, match="상주 서비스를 소유하지 않는다"):
        _resolve(DATASET_DISCLOSURE_MINUTE, "dart")


def test_공시는_소스_단위라_universe_축이_아니다():
    """유니버스는 공시에서 **기대 집합이 아니라 필터**다 — 목록 질의가 창 전체를 훑고
    (ALPHA-714) 유니버스는 그중 우리 종목을 고르는 데만 쓰인다. `UNIVERSE_DATASETS` 에
    넣으면 `plan-minute-session` 이 `--universe` 를 요구해, universe 파일 승인 없이는
    공시 세션이 아예 안 서고 격자도 universe 선언에 묶인다.
    """
    assert DATASET_DISCLOSURE_MINUTE not in UNIVERSE_DATASETS
    # 격자를 넓히는 판단은 universe 가 아니라 이 표가 진다(DART 접수 07:30~18:00)
    assert DATASET_DISCLOSURE_MINUTE in EXTENDED_HOURS_DATASETS


def test_공시_소스는_dart_하나다():
    """국내 전자공시의 원 접수처가 하나다 — 어휘 밖 값으로 세션이 서면 그 소스를 처리하는
    어댑터 배선이 없어 dataset 오타와 같은 모양으로 하루가 조용히 안 돈다."""
    assert SOURCE_GROUPS_BY_DATASET[DATASET_DISCLOSURE_MINUTE] == frozenset({"dart"})
