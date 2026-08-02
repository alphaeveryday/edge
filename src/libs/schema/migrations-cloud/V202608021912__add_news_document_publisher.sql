-- 뉴스 문서의 언론사(publisher) 승격 (ALPHA-695).
--
-- 언론사는 정규화(normalize_news)가 벤더별 필드(BigKinds PROVIDER·토스 site)를 표준행
-- `publisher` 로 통일해 canonical 까지 살아 오는데, 적재(load_documents)가 담지 않아
-- 원장에서 사라졌다 — 콘솔 문서 목록의 "출처"가 전 행 수집 벤더(bigkinds)로만 나오는
-- 원인. 품질 게이트가 missing_publisher 를 non-blocking 경고로 두므로 컬럼도 nullable.
-- canonical 에 값이 이미 있어 과거분은 해당 파티션 재적재로 소급 백필된다.

SET search_path TO public;

ALTER TABLE news_document ADD COLUMN publisher TEXT;
