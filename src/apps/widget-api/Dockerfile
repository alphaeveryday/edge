# syntax=docker/dockerfile:1

# --- build stage: 래퍼로 실행 가능한 boot jar 빌드 ---
FROM eclipse-temurin:21-jdk AS build
WORKDIR /app

# 빌드 설정 먼저 복사 → 소스만 바뀔 때 의존성 레이어 캐시 재사용
COPY gradlew settings.gradle build.gradle ./
COPY gradle ./gradle
RUN chmod +x gradlew

COPY src ./src
# bootJar 만 빌드한다(plain jar 미생성 → build/libs 에 jar 1개만 남음)
RUN ./gradlew bootJar --no-daemon

# --- runtime stage: JRE 만, 비루트 실행 ---
FROM eclipse-temurin:21-jre AS runtime
WORKDIR /app

# 비루트 사용자로 실행(Fargate 보안 기본)
RUN useradd -r -u 1001 spring
USER spring

COPY --from=build /app/build/libs/*.jar app.jar

# Spring Boot 기본 포트. ECS target group / Service Connect 도 8080 으로 맞춘다.
EXPOSE 8080
ENTRYPOINT ["java", "-jar", "app.jar"]
