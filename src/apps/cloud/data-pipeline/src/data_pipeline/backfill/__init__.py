"""백필 — **포워드 수집과 완전히 격리된 재구축 경로.**

포워드(`steps/ingest_*`)는 매일 도는 프로덕션이고 백필은 과거를 다시 쌓는 일이다. 둘을
섞으면 롤백이 불가능해진다 - 어느 파티션이 어느 경로에서 왔는지 사후에 가릴 수 없기
때문이다. 그래서 이 패키지는 **쓰기 좌표 셋으로 격리**한다.

    source=dartlab      포워드는 source=dart. 벤더 축이 달라 파티션이 절대 겹치지 않는다
    run_id=backfill-*   포워드는 run_id 접두사가 다르다. 롤백은 run_id 파티션 삭제다
    draft/ 접두사       승격 전에는 draft/ 아래에만 쓴다(설정). 승격은 접두사 이동이다

그리고 **데이터가 전소해도 다시 쌓을 수 있어야 한다.** 그 조건은 외부 입력이 전부
재접근 가능하고 로컬 상태에 의존하지 않는 것이다. 이 패키지의 외부 입력은 하나다 -
HuggingFace 공개 데이터셋. 종목 유니버스조차 그 데이터셋의 파일 목록에서 얻는다(로컬
종목 마스터를 안 읽는다). 매니페스트도 레이크에 쓴다 - 로컬 디스크에 두면 그것이
전소했을 때 재개가 불가능하다.

    py -m data_pipeline.backfill.run financial --limit 50
    py -m data_pipeline.backfill.run verify --run-id backfill-dartlab-financial-20260731

의존을 늘리지 않았다. `dartlab` 패키지(다운로드·캐시·CLI·AI 에이전트 포함)를 쓰지 않고
공개 parquet URL 을 직접 읽는다 - 백필이 서드파티 런타임에 매이면 재구축 가능성이 그
패키지의 수명에 매인다.
"""

from .financial import backfill_financial
from .hf import HfDataset
from .manifest import Manifest

__all__ = ["HfDataset", "Manifest", "backfill_financial"]
