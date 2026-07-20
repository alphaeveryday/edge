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
ENTRYPOINT ["java", "-jar", "app.jar"]
