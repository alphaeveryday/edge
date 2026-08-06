"""프로덕션 ETF 요청창 설명 서비스 — `pipeline.py`·`window_batch.py`가 부른다.

`etfcell`(셀 러너)·`interval`(고정 블록 H·1·2·3·4/N)·`etfday`·`mkttrial`·`premium`
·`premium5`·`route`·`record`가 여기 있다. `core/`의 엔진을 소비하지만 역방향
의존은 없다 — `core/`는 이 패키지를 모른다.
"""
