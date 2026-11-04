# syntax=docker/dockerfile:1
# 배포 워크플로 공용 규약에 따른 레포 루트의 빌드 컨텍스트
#   docker build -f apps/etforca/app-api/batch/Dockerfile -t app-batch .
# app-api Gradle 루트를 복사해 batch 하위 프로젝트만 빌드

# --- build stage: 루트 래퍼로 batch bootJar 빌드 ---
FROM eclipse-temurin:21-jdk AS build
WORKDIR /workspace

COPY apps/etforca/app-api/ .
RUN chmod +x gradlew && ./gradlew :batch:bootJar --no-daemon

# --- runtime stage: JRE 기반 비루트 1회 실행 ---
FROM eclipse-temurin:21-jre AS runtime
WORKDIR /app

RUN useradd -r -u 1001 spring
USER spring

COPY --from=build /workspace/batch/build/libs/batch-*.jar app.jar

# app-api 와 같은 JVM 밖 네이티브 메모리 억제
ENV MALLOC_ARENA_MAX=2

# 잡 실행 뒤 종료, 잡 결과의 종료 코드가 ECS 태스크 종료 코드
ENTRYPOINT ["java", "-jar", "app.jar"]
