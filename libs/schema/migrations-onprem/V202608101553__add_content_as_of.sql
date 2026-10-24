-- ALPHA-918: 콘텐츠 기준시각 — 산문이 서술하는 요청 창의 끝을 표시·서빙용으로 신설.
-- explanation_as_of(생성 벽시계)는 스냅샷 grain 축이라 불변이고, 이 컬럼은 표시 전용이다.
-- 구형 수신분·시드·EOD 레인은 값이 없어 NULL 허용·백필 없음(소비자는 explanation_as_of 폴백)
-- — analysis_item.etf_ticker 의 "현행 번들에 없어 NULL 허용" 선례와 같은 결.

ALTER TABLE analysis_item ADD COLUMN content_as_of TIMESTAMPTZ;

COMMENT ON COLUMN analysis_item.content_as_of IS
'콘텐츠 기준시각 — 산문이 서술하는 요청 창의 끝(KST). 번들 content_as_of(stage_results 창 파생) 저장.
결측(구형 수신분·시드·EOD·합성 실패)은 NULL — 표시층은 explanation_as_of 로 폴백한다. (ALPHA-918)';

ALTER TABLE publication ADD COLUMN content_as_of TIMESTAMPTZ;

COMMENT ON COLUMN publication.content_as_of IS
'콘텐츠 기준시각 — 게시 시점에 analysis_item.content_as_of 를 복사(publication-api 가 원장 재조인 없이 서빙,
explanation_as_of 복사와 같은 규율). NULL 허용 — 원장 결측이 그대로 전파된다. (ALPHA-918)';
