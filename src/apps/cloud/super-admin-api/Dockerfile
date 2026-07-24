# syntax=docker/dockerfile:1
# 빌드 컨텍스트 = src/ 루트(멀티프로젝트). 빌드:
#   docker build -f apps/cloud/super-admin-api/Dockerfile -t super-admin-api .
# 이 모듈의 bootJar 만 만들어 서비스별 독립 이미지로 배포한다.

# --- build stage: 루트 래퍼로 해당 모듈 bootJar 빌드 ---
FROM eclipse-temurin:21-jdk AS build
WORKDIR /workspace

COPY . .
RUN chmod +x gradlew && ./gradlew :apps:cloud:super-admin-api:bootJar --no-daemon

# --- runtime stage: JRE 만, 비루트 실행 ---
FROM eclipse-temurin:21-jre AS runtime
WORKDIR /app

# 비루트 사용자로 실행(Fargate 보안 기본)
RUN useradd -r -u 1001 spring
USER spring

COPY --from=build /workspace/apps/cloud/super-admin-api/build/libs/*.jar app.jar

# Spring Boot 기본 포트. ECS target group / Service Connect 도 8080 으로 맞춘다.
EXPOSE 8080
# 배포 이미지는 prod profile 로 뜬다 → application-prod.yaml 의 ECS JSON 구조적 로깅 활성(ALPHA-531).
# 로컬 compose 는 SPRING_PROFILES_ACTIVE="" 로 override 해 default(평문)로 되돌린다. IDE bootRun 은
# 이미지를 쓰지 않아 자동 default. 이미지에 박아 배포 경로(deploy-app.yml 이미지 교체)와 함께 이동시켜
# terraform task-def env 의 CD 레이스(ecs-service ignore_changes)를 피한다.
ENV SPRING_PROFILES_ACTIVE=prod
ENTRYPOINT ["java", "-jar", "app.jar"]
