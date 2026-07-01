# 스키마 마이그레이션 실행기 이미지 — Flyway CLI + 이 저장소의 마이그레이션 SQL.
#
# 왜 이미지인가: GitHub-hosted 러너는 VPC 밖이라 private RDS 에 못 붙는다. 그래서 배포 파이프라인은
# 이 이미지를 ECS one-off task 로 VPC 내부에서 실행해 Flyway 를 RDS 에 적용한다.
# SQL 을 이미지에 구워 넣어 런타임 의존성 다운로드 없이 자립 실행한다(private 서브넷에서 RDS 만 접근).
#
# 접속값은 런타임 env 로 주입: FLYWAY_URL / FLYWAY_USER / FLYWAY_PASSWORD.
# 정책 플래그는 flyway.conf 에 있고 build.gradle 의 flyway{} 블록과 미러한다.
#
# 태그(버전)는 build.gradle 의 Flyway 플러그인 버전과 일치시킨다.
FROM flyway/flyway:10.21.0

# 마이그레이션 SQL — libs/schema 가 SSOT. (빌드 컨텍스트는 src/libs/schema)
COPY migrations/ /flyway/sql/

# 정책 설정(locations·baselineOnMigrate·cleanDisabled·validateMigrationNaming)
COPY flyway.conf /flyway/conf/flyway.conf

# 이미지 ENTRYPOINT 는 flyway. 기본 명령은 migrate(ECS task 도 command=["migrate"] 로 명시).
CMD ["migrate"]
