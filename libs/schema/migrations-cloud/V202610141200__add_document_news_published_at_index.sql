-- v2 분석의 뉴스 원천 조회가 최신 문서부터 읽고 멈출 수 있게 하는 인덱스(ALPHA-1311).
--
-- 조회(edge_analysis_v2/sources/database.py load_source)는 ETF 와 구성종목에 연결된 NEWS 문서 중
-- 발행 시각이 최신인 1,001건을 고른다. document 에 발행 시각 인덱스가 없어서, 후보가 수천 건이면 후보 문서를
-- 하나씩 읽어 정렬하고, 수만 건이면 document 전체(66만 행, 381MB)를 병렬로 훑는다.
-- 2026-10-14 dev 실측(1회씩): 후보 3,641건인 0000J0 은 4.3초 중 4.1초가 디스크 읽기였고, 후보 2만 4천 건인
-- 139280 은 document 를 통째로 훑었다. 같은 날 06:00 배치에서 40건이 함께 조회하자 5건이 15초 제한에 걸렸다.
-- 측정은 tests/loadtest/analysis-v2/README.md '뉴스 원천 조회' 절.
--
-- 열 순서는 조회의 ORDER BY (published_at DESC, document_id)와 같다. available_at 은 조회 조건이라 같이 싣는다.
-- 조회가 NEWS 만 보므로 NEWS 만 싣는다.
--
-- 표를 다시 쓰지는 않지만, 짓는 동안 document 쓰기(뉴스·공시 수집)가 기다린다. lock_timeout 은 락을 얻기까지의
-- 대기만 묶는다(다른 마이그레이션과 같은 3초). 그 안에 못 잡으면 수집을 세우지 않고 이 마이그레이션이 실패한다.
-- 그때는 다시 적용한다. 짓는 시간 자체는 묶이지 않는다.
SET LOCAL lock_timeout = '3s';

CREATE INDEX ix_document_news_published_at
    ON document (published_at DESC, document_id) INCLUDE (available_at)
    WHERE document_type = 'NEWS';
