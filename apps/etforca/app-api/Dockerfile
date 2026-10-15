# syntax=docker/dockerfile:1
# 멀티프로젝트라 src/ 루트의 빌드 컨텍스트
#   docker build -f apps/cloud/app-api/Dockerfile -t app-api .
# 이 모듈 bootJar 만 담은 서비스별 독립 이미지

# --- build stage: 루트 래퍼로 모듈 bootJar 빌드 ---
FROM eclipse-temurin:21-jdk AS build
WORKDIR /workspace

COPY . .
RUN chmod +x gradlew && ./gradlew :apps:cloud:app-api:bootJar --no-daemon

# --- runtime stage: JRE 기반 비루트 실행 ---
FROM eclipse-temurin:21-jre AS runtime
WORKDIR /app

RUN useradd -r -u 1001 spring
USER spring

COPY --from=build /workspace/apps/cloud/app-api/build/libs/*.jar app.jar

# 스레드별 malloc 아레나 상한. 512MiB 태스크의 JVM 밖 네이티브 메모리 억제
ENV MALLOC_ARENA_MAX=2

# ECS target group 과 Service Connect 도 맞춘 Spring Boot 기본 포트
EXPOSE 8080
ENTRYPOINT ["java", "-jar", "app.jar"]
