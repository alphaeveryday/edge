# syntax=docker/dockerfile:1
# 빌드 컨텍스트 = src/ 루트(멀티프로젝트). 빌드:
#   docker build -f apps/onprem/publication-api/Dockerfile -t publication-api .
# 이 모듈의 bootJar 만 만들어 서비스별 독립 이미지로 배포한다. [edge-onprem]

# --- build stage: 루트 래퍼로 해당 모듈 bootJar 빌드 ---
FROM eclipse-temurin:21-jdk AS build
WORKDIR /workspace

COPY . .
RUN chmod +x gradlew && ./gradlew :apps:onprem:publication-api:bootJar --no-daemon

# --- runtime stage: JRE 만, 비루트 실행 ---
FROM eclipse-temurin:21-jre AS runtime
WORKDIR /app

RUN useradd -r -u 1001 spring
USER spring

COPY --from=build /workspace/apps/onprem/publication-api/build/libs/*.jar app.jar

EXPOSE 8080
ENTRYPOINT ["java", "-jar", "app.jar"]
