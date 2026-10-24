-- ALPHA-743: 다스냅샷 공존(ADR-0045 결정 3) — 게시 grain 에 as_of 축 추가.
-- 구 결정(V202607211640 주석: "ETF·거래일당 하나만 노출, 새 기준시각은 구본 교체")은
-- ADR-0045 가 뒤집었다: 같은 (ticker, trade_date)의 스냅샷은 공존하고, 표시가
-- explanation_as_of 최신을 고른다("유효 최신 승리" — 무효화 시 직전 스냅샷 자동 노출).
-- 온프렘 배포는 flyway 완료 후 앱 기동(박스 원자 적용)이라 코드와 같은 PR·같은 배포다
-- (선례 V202608012000 헤더).

ALTER TABLE publication ADD COLUMN explanation_as_of TIMESTAMPTZ;

-- 백필: 게시 시점 복사 규율(etf_ticker/trade_date 와 동일) — 원천은 analysis_item.
UPDATE publication p
   SET explanation_as_of = ai.explanation_as_of
  FROM analysis_item ai
 WHERE ai.explanation_result_id = p.analysis_item_id;

ALTER TABLE publication ALTER COLUMN explanation_as_of SET NOT NULL;

-- 게시 grain = (ticker, trade_date, as_of) — cloud 게시 grain 과 동형. 같은 스냅샷의
-- 이중 게시만 막고 다스냅샷 공존을 허용한다. 이름 유지: 두 publish() 경로의
-- ON CONFLICT arbiter(tenant-console-api)·가드가 이 인덱스와 짝이다.
DROP INDEX uq_publication_published_grain;
CREATE UNIQUE INDEX uq_publication_published_grain
    ON publication (etf_ticker, trade_date, explanation_as_of)
    WHERE status = 'PUBLISHED';

COMMENT ON COLUMN publication.explanation_as_of IS
'스냅샷 기준시각 — analysis_item 에서 게시 시점 복사(ticker/trade_date 와 동일 규율). 표시 규칙 "유효 최신 승리"의 정렬 축(ADR-0045 결정 3, ALPHA-743).';
