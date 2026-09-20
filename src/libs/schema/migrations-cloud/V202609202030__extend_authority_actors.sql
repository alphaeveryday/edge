-- 뉴스 기관 명부의 선행 시드(ALPHA-1081). 기존 68개 기관 ID/별칭은 변경하지 않는다.
-- 명부 배포 전에 schema-migrate 완료를 확인한다. 이 PR은 명부를 활성화하지 않는다.
-- 신규 entity/actor 15쌍만 추가한다. 참조가 생긴 행은 롤백 시 삭제하지 않는다.
-- 기관명·별칭 근거와 누적 명부-시드 정합 검증은 후속 명부 PR에 포함한다.
SET LOCAL lock_timeout = '3s';

INSERT INTO entity (entity_id, entity_type, display_name, status) VALUES
    ('actor_auth_kr_motir', 'ACTOR', '산업통상부', 'ACTIVE'),
    ('actor_auth_kr_mcee', 'ACTOR', '기후에너지환경부', 'ACTIVE'),
    ('actor_auth_kr_kolas', 'ACTOR', '한국인정기구', 'ACTIVE'),
    ('actor_auth_kr_ksa', 'ACTOR', '한국표준협회', 'ACTIVE'),
    ('actor_auth_kr_kci', 'ACTOR', '한국준법진흥원', 'ACTIVE'),
    ('actor_auth_kr_keiti', 'ACTOR', '한국환경산업기술원', 'ACTIVE'),
    ('actor_auth_kr_sejong', 'ACTOR', '세종특별자치시', 'ACTIVE'),
    ('actor_auth_kr_gwacheon', 'ACTOR', '과천시', 'ACTIVE'),
    ('actor_auth_kr_gyeonggi', 'ACTOR', '경기도', 'ACTIVE'),
    ('actor_auth_kr_gangwon', 'ACTOR', '강원특별자치도', 'ACTIVE'),
    ('actor_auth_kr_suwon', 'ACTOR', '수원시', 'ACTIVE'),
    ('actor_auth_kr_hadong', 'ACTOR', '하동군', 'ACTIVE'),
    ('actor_auth_kr_seodaemun', 'ACTOR', '서울특별시 서대문구', 'ACTIVE'),
    ('actor_auth_kr_tongyeong', 'ACTOR', '통영시', 'ACTIVE'),
    ('actor_auth_us_usda', 'ACTOR', '미국 농무부', 'ACTIVE')
ON CONFLICT (entity_id) DO NOTHING;

INSERT INTO actor (actor_id, actor_type, country_code) VALUES
    ('actor_auth_kr_motir', 'GOVERNMENT', 'KR'),
    ('actor_auth_kr_mcee', 'GOVERNMENT', 'KR'),
    ('actor_auth_kr_kolas', 'GOVERNMENT', 'KR'),
    ('actor_auth_kr_ksa', 'INSTITUTION', 'KR'),
    ('actor_auth_kr_kci', 'INSTITUTION', 'KR'),
    ('actor_auth_kr_keiti', 'INSTITUTION', 'KR'),
    ('actor_auth_kr_sejong', 'GOVERNMENT', 'KR'),
    ('actor_auth_kr_gwacheon', 'GOVERNMENT', 'KR'),
    ('actor_auth_kr_gyeonggi', 'GOVERNMENT', 'KR'),
    ('actor_auth_kr_gangwon', 'GOVERNMENT', 'KR'),
    ('actor_auth_kr_suwon', 'GOVERNMENT', 'KR'),
    ('actor_auth_kr_hadong', 'GOVERNMENT', 'KR'),
    ('actor_auth_kr_seodaemun', 'GOVERNMENT', 'KR'),
    ('actor_auth_kr_tongyeong', 'GOVERNMENT', 'KR'),
    ('actor_auth_us_usda', 'GOVERNMENT', 'US')
ON CONFLICT (actor_id) DO NOTHING;
