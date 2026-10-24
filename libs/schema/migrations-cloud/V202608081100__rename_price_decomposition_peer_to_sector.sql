-- price_decomposition 의 `peer_*` 어휘를 `sector_*` 로 바꾼다 (ALPHA-853).
--
-- 왜: 이 표는 **"대상 종목 vs 동종 피어 바스켓"** 모형으로 설계됐는데, 우리가 기록할
-- 것은 **"ETF vs 시장 + 섹터"** 다. `peer_orthogonal_return` 에 섹터 잔차를 넣으면 값은
-- 들어가지만 **이름이 거짓말을 한다** — 나중에 이 표를 읽는 사람은 피어 바스켓 분해로
-- 읽는다. 어휘가 모형과 갈리면 그 표는 문서가 아니라 함정이 된다.
--
-- 왜 지금: 이 표들은 **정의만 있고 생산자가 없다**(2026-08-07 전수 확인). 저장소에서
-- 나오는 곳은 마이그레이션 SQL · physical-erd · FK 보호 주석뿐이고, Java 소비자도 0건이다
-- (`JdbcAnalysisRepository` 등에 `peer_*`·`benchmark_series_id` 참조 없음). 행이 0 이라
-- 이관도 백필도 없다 — 순수 개명이다. 값이 쌓인 뒤에는 같은 작업이 훨씬 비싸진다.
--
-- expand-contract 를 안 쓰는 이유가 그것이다. 그 절차는 **읽는 쪽이 있을 때** 옛 이름과
-- 새 이름을 함께 두어 무중단 전환을 만드는 것인데, 여기엔 읽는 쪽이 없다. 없는 소비자를
-- 위해 두 벌을 유지하면 다음 사람이 어느 쪽이 진짜인지 묻게 된다.

ALTER TABLE price_decomposition RENAME COLUMN benchmark_series_id   TO sector_series_id;
ALTER TABLE price_decomposition RENAME COLUMN peer_raw_return       TO sector_raw_return;
ALTER TABLE price_decomposition RENAME COLUMN peer_orthogonal_return TO sector_orthogonal_return;
ALTER TABLE price_decomposition RENAME COLUMN peer_explained_return TO sector_explained_return;

ALTER INDEX ix_price_decomposition_benchmark RENAME TO ix_price_decomposition_sector_series;

-- 제약명도 같이 옮긴다. 인덱스만 고치면 개명 후 스키마에서 FK 를 찾은 사람이
-- `fk_..._benchmark` 를 보고 "benchmark 라는 다른 개념이 있나" 를 묻는다 — 이 변경이
-- 없애려던 "이름이 거짓말을 한다" 상태가 절반 남는 것이다.
ALTER TABLE price_decomposition
    RENAME CONSTRAINT fk_price_decomposition_benchmark TO fk_price_decomposition_sector_series;

COMMENT ON TABLE price_decomposition IS
'가격관찰을 시장·섹터 층으로 분해한 계산 결과. 가격관찰과 1:N 관계를 가진다. 섹터 항은 시장에 직교화된 잔차다(중복 청구 금지).';

COMMENT ON COLUMN price_decomposition.sector_series_id IS
'섹터 층으로 쓴 계열의 식별자. 무엇과 비교했는지가 값과 함께 남아야 사후에 재현된다.';
COMMENT ON COLUMN price_decomposition.sector_raw_return IS
'섹터 계열의 원수익률(직교화 전). 시장과 겹친 부분을 아직 품고 있어 이 값만으로 기여를 청구하면 시장 몫을 이중계상한다 — 배분은 sector_orthogonal_return 이 한다.';
COMMENT ON COLUMN price_decomposition.sector_orthogonal_return IS
'시장에 직교화한 섹터 수익률. 원계열(sector_raw_return)과 갈라 두는 이유는 "시장이 민 건지 섹터가 민 건지"의 배분이 이 값으로만 결정되기 때문이다.';
COMMENT ON COLUMN price_decomposition.sector_explained_return IS
'섹터 층이 설명한 몫 = β_섹터 × sector_orthogonal_return. market_explained_return 과 더해도 중복이 없다(직교화의 목적).';

-- 피어 목록 표는 통째로 버린다. 우리 모형에서 섹터는 **층 하나**이고 순위·가중치를 갖는
-- 구성원 목록이 아니다. 남겨 두면 영영 안 채워지는 표가 ERD 에 남아 모형을 오해시킨다.
-- 행 0 · 이 표를 참조하는 FK 0 (나가는 FK 2건뿐) · 코드 소비자 0.
--
-- ⚠️ **되돌리기는 대칭이 아니다.** RENAME 4건과 제약·인덱스 개명은 역으로 적으면 되지만
-- DROP 은 역이 없다 — 복원하려면 `V202607150001__replace_analysis_mart_with_etf_explanation
-- _schema.sql` 의 924~946(CREATE TABLE + PK + CHECK 2 + UNIQUE 1)과 1565~1574(FK 2건 +
-- 인덱스 + COMMENT)를 그대로 다시 적어야 한다. 데이터 손실은 없다(행 0).
DROP TABLE IF EXISTS price_decomposition_peer;
