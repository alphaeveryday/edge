# syntax=docker/dockerfile:1
# 배포 워크플로 공용 규약에 따른 레포 루트의 빌드 컨텍스트
#   docker build -f apps/etforca/app-api/Dockerfile -t app-api .
# 자체 Gradle 루트라 이 디렉터리만 복사

# --- build stage: 모듈 자체 래퍼로 bootJar 빌드 ---
FROM eclipse-temurin:21-jdk AS build
WORKDIR /workspace

COPY apps/etforca/app-api/ .
RUN chmod +x gradlew && ./gradlew bootJar --no-daemon

# --- runtime stage: JRE 기반 비루트 실행 ---
FROM eclipse-temurin:21-jre AS runtime
WORKDIR /app

RUN useradd -r -u 1001 spring
USER spring

COPY --from=build /workspace/build/libs/*.jar app.jar

# 스레드별 malloc 아레나 상한. 512MiB 태스크의 JVM 밖 네이티브 메모리 억제
ENV MALLOC_ARENA_MAX=2

# ECS target group 과 Service Connect 도 맞춘 Spring Boot 기본 포트
EXPOSE 8080
ENTRYPOINT ["java", "-jar", "app.jar"]
