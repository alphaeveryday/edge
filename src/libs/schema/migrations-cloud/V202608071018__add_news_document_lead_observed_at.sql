-- 리드 본문의 관측 시각 — 두 생산자 사이의 승자 축 (ALPHA-696).
--
-- `news_document.lead_text` 에는 생산자가 둘이다: 배치 `load_documents`(레이크 canonical
-- 을 읽어 적재)와 1분 `PgNewsCanonicalWriter`(벤더 행을 PG 에 직접 upsert, ALPHA-691).
-- 배치는 리드가 비어 있지 않을 때 **시각 조건 없이** 덮는다. 그래서 1분 경로가 반영한
-- 정정 본문(T2)을 아직 T1 만 있는 레이크 값으로 되돌린다 — 그러면 원장은 새 지문(fp2)을
-- 확정했는데 Consumer 는 T1 을 읽어 ALPHA-691 이 고치려던 P1 이 재현된다.
--
-- 이 컬럼은 **지금 저장된 리드 상태를 누가 언제 관측했는가**를 담는다. 소비 계약은 셋이고
-- 셋 다 후속 코드 PR 이 SQL 로 강제한다(주석 합의는 다음 사람이 못 본다 — 티켓 요구사항):
--
-- ① **1분 경로는 쓰기 가드가 없다.** 내용은 언제나 이번 관측이 이긴다. 시각으로 내용
--    쓰기를 막으면 위의 P1 이 그대로 돌아온다(`canonical_news.py` docstring 이 "한때
--    그렇게 했다가 되돌렸다"고 기록한 그것). 비대칭이 의도다.
--
-- ② **1분 경로가 시각을 찍는 조건 = "리드 상태가 실제로 움직였을 때".**
--    * 충돌 갱신 갈래(`ON CONFLICT DO UPDATE … WHERE lead_text IS DISTINCT FROM`)에서
--      리드가 바뀌면 찍는다. 여기엔 **비어 있던 리드가 붙는 것**과 **있던 리드가 지워지는
--      것**이 다 들어간다 — 둘 다 지금 알게 된 사실이다.
--    * 행을 새로 만드는 **INSERT 갈래에서 리드가 NULL 이면 시각도 NULL 로 둔다.** 그
--      갈래는 `WHERE` 절이 막아 주지 않으므로 여기서 명시하지 않으면 "빈 리드에 최신
--      시각"이 무조건 박힌다.
--    * ⚠️ 그 둘을 `SET lead_observed_at = EXCLUDED.lead_observed_at` 로 대칭 구현하지
--      마라. INSERT 갈래를 `CASE WHEN lead IS NULL THEN NULL END` 로 짜고 충돌 갈래가
--      `EXCLUDED` 를 물려받으면, **리드를 지우는 정정에서 시각이 NULL 로 지워져** 아래
--      ③의 첫 절이 열리고 배치가 옛 리드를 복원한다 — 예외절을 안 넣고도 ③이 경고한
--      고착이 그대로 난다. 충돌 갈래는 `EXCLUDED` 가 아니라 **관측 시각 자체**를 쓴다.
--    * "매 관측마다 갱신"이 아니다. 1분 레인이 같은 T1 을 장중 내내 재관측하며 시각만
--      밀어 올리면, 레인이 못 본 진짜 정정(기사가 목록 1페이지에서 밀려난 경우)을 배치가
--      영영 못 싣는다 — 축이 하려던 일이 무효가 된다.
--    ⭐ 이 비대칭은 새 규칙이 아니다. `canonical_news.py` 가 `available_at` 에 이미 쓰고
--    있다 — "NULL 리드를 처음 만드는 건 변경이 아니고, 실제 리드가 처음 붙는 건 변경이다".
--
-- ③ **배치는 자기 관측이 더 새로울 때만 덮는다. 예외절은 두지 않는다.**
--        news_document.lead_observed_at IS NULL
--     OR news_document.lead_observed_at <= <배치 canonical fetched_at>
--    `fetched_at` 이 결손이면 배치는 신선도를 **주장하지 않는다** — 첫 절(미주장 자리)로만
--    쓰고 `lead_observed_at` 은 NULL 로 남긴다. `published_at` 폴백은 금지다(아래 이유).
--    ⚠️ 여기에 `OR lead_text IS NULL` 같은 "빈 자리는 언제나 채운다" 예외를 **넣지 마라.**
--    ②에 따르면 시각이 찍힌 채 리드가 NULL 인 상태는 **오직 1분 경로가 있던 리드를
--    의도적으로 지웠을 때**만 생기는데, 그게 정확히 배치가 덮으면 안 되는 상태다. 예외를
--    넣으면 배치가 T1 을 복원하고 시각을 자기 `fetched_at` 으로 되돌려, 이후 모든 배치
--    런이 비교를 통과하는 **자기강화 고착**이 된다. 예외 없이도 ②의 INSERT 갈래 규칙이
--    "아무도 안 쓴 빈 자리"를 첫 절로 흘려보내므로 ALPHA-628 의 리드 승격은 안 막힌다.
--    (언론사 승격은 **별도 컬럼·별도 문장**이다 — `publisher` UPSERT 에는 이 가드를
--    달지 마라. 무관한 컬럼의 시각으로 ALPHA-695 를 막게 된다.)
--    ⭐ **배치 쪽은 구조적으로 단조다** — `저장값 <= 자기 값`일 때만 쓰므로 뒤로 가는
--    경로가 없다. 1분 경로는 벽시계를 그냥 찍으므로 그 보장이 없다(옆 축 `available_at`
--    은 `GREATEST` 로 명시 가드를 건다). 도달 가능한 역행 경로는 리뷰가 못 찾았지만
--    (fence 검증·recovery 재조회·READ COMMITTED 재평가로 각각 반증) 시계 skew 는 남는다
--    — 후속 PR 에서 `GREATEST(news_document.lead_observed_at, %s)` 한 단어면 축 전체가
--    구조적으로 단조가 되고 ②·③ 무엇도 안 깨진다(앞으로 가는 건 그대로 통과).
--
-- ⚠️ `document.available_at` 을 쓰지 않는 이유: 그 값은 배치에서
-- `fetched_at or published_at` 이라 수집 시각과 발행 시각이 섞이고, `fetched_at` 결손
-- 시 **미래 발행일**이 실린다. 미래 시각이 한 번 박히면 이후 모든 갱신이 조용히 영구
-- 차단된다. 신선도 축과 PIT 조회 축(as-of)을 한 컬럼에 겹치지 않기 위해 분리한다.
--
-- nullable: 기존 행은 누가 언제 썼는지 알 수 없다. NULL = "아직 아무도 이 리드 상태를
-- 관측했다고 주장하지 않은 자리" 로 읽고 양쪽 생산자가 통과시킨다 — 소급 백필은 하지
-- 않는다(레이크의 fetched_at 을 되읽어 채우면 1분 경로가 쓴 행에까지 배치의 시각을 박는다).
--
-- ⚠️ **이름을 바꾸지 마라 — `created_at`·`available_at`·`published_at` 계열로 바꾸면
-- 조용히 동작이 바뀐다.** `analysis-engine` 의 `sql_surface.auto_views_sql` 은 `CLAMP_COLS`
-- 에 든 이름을 PIT 클램프 축으로 집는다. `news_document` 엔 지금 클램프 후보 컬럼이 하나도
-- 없어 `v_news_document` 가 무클램프 뷰인데, 그런 이름을 쓰면 LLM SQL 표면이 보는 행 집합이
-- 바뀐다. 그걸 강제하는 게이트는 없다.
--
-- 후속 코드 PR 이 이어받는 것 셋:
-- * `load_documents` 의 후보 dict 에 **원시 `fetched_at` 을 따로 싣는다**. 지금은
--   `available_at = fetched_at or published_at` 으로 이미 접힌 뒤라 원시값이 없다.
-- * `fetched_at` 결손으로 신선도를 주장하지 못한 행 수를 quality_log 에 센다 — 그런
--   벤더가 많으면 이 축이 조용히 무력화되는데(첫 절이 늘 참) 볼 계기가 없다.
-- * ⚠️ 알려진 거래 하나: `canonical_news.py` docstring 이 인정한 "지연 커밋이 최신 본문을
--   덮는" 상태(낡은 리드 + 새 시각)의 치유가 **배치 재수집에 걸리게 된다**. 그 기사가
--   이후 뉴스 레인 수집에 다시 잡히면 새 `fetched_at` 이 앞서 배치가 치유하고, **안
--   잡히면 못 한다**. ⚠️ 치유 지연 상한을 "장 끝나면 그날 안"으로 잡지 마라 —
--   `LoadDocuments` 는 시장 SFN 이 아니라 **뉴스 레인**이고(`ops/catalog.py` 의
--   `pipeline_type="news"`, 시장 SFN 의 `market_excluded_states`), 슬롯은 평일
--   15:00·15:30·23:50 KST 라 **셋 중 둘이 정규장 안**이다. 15:20 에 난 지연 커밋은
--   23:50 런을 기다린다(주말이 끼면 다음 영업일). 1분 경로를 승자로 정한 대가이고,
--   사후 판별은 ALPHA-689 의 `prompt_input_checksum` 이 한다.

SET search_path TO public;

-- 락 예산 (README "락 예산", ALPHA-799) — `news_document` 는 장중에 쓰이는 표다: 1분 뉴스
-- 레인이 매분 upsert 하고(`PgNewsCanonicalWriter`), 분석엔진은 autocommit=False 라 이 표에
-- 잡은 ACCESS SHARE 가 통계+LLM 구간(건당 5~10분)을 지나서야 풀린다. `schema-migrate` 는
-- 장 시간 게이트 없이 dev push 마다 즉시 도니, 무한 대기하는 DDL 이 끼면 그 뒤의 매분
-- 커밋이 전부 줄을 선다(head-of-line blocking). DEFAULT 없는 nullable 컬럼이라 보유 시간은
-- 상수고, 묶어야 하는 것은 **획득 대기**다.
--
-- ⚠️ 장중 착지에서 빨간불은 **상한이 일한 것이지 결함이 아니다** — 3초 안에 `55P03` 으로
-- 죽고, 통째로 롤백돼 `flyway_schema_history` 에 기록도 안 남아 재실행이 깨끗하다.
-- 되돌리는 마이그레이션(`DROP COLUMN`)도 ACCESS EXCLUSIVE 라 이 가드를 같이 복사해야 한다.
--
-- `SET LOCAL` 인 이유: `lock_timeout` 같은 동작값을 맨 `SET` 으로 두면 Flyway 의 한
-- 커넥션에 남아 뒤따르는 남의 마이그레이션까지 물들인다(README 실측). 위의
-- `SET search_path` 는 값이 Flyway 기본과 같아 이 문제가 없는 기존 관례다.
SET LOCAL lock_timeout = '3s';

ALTER TABLE news_document ADD COLUMN lead_observed_at TIMESTAMPTZ;

COMMENT ON COLUMN news_document.lead_observed_at IS
    '지금 저장된 lead_text 상태를 관측한 시각 (ALPHA-696). 1분 경로는 처리 시각을 '
    '리드 상태가 실제로 움직였을 때 찍는다 — 리드가 붙는 것도 지워지는 것도 움직임이고, '
    '갱신 갈래뿐 아니라 리드가 있는 새 행(INSERT 갈래)도 찍는다. 리드가 NULL 인 새 행에만 '
    '안 찍는다(= 아무도 주장하지 않은 빈 자리). '
    '배치는 IS NULL 이거나 저장값 <= 자기 canonical fetched_at 일 때만 덮는다 — '
    '"빈 자리는 언제나 채운다" 예외를 넣으면 리드를 지우는 정정이 되돌아간다. '
    'fetched_at 이 결손이면 배치는 신선도를 주장하지 않는다: IS NULL 절로만 쓰고 '
    '이 컬럼은 NULL 로 남긴다. document.available_at(= fetched_at or published_at) '
    '을 대신 쓰지 마라 — 미래 발행일이 박히면 이 행의 리드 승격이 영구 차단된다. '
    'NULL 은 미주장.';
