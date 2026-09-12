# app-api

B2C 앱의 API 서버(Cloud). 현재는 스캐폴드 단계 — 웹·actuator 만 배선했고
DB·도메인 표면은 미구현이다.

네이밍은 역할 기반 `-api` 컨벤션(super-admin-api·tenant-sync-api)을 따른다.
consumer 는 메시징 컨슈머 용어와 충돌해 쓰지 않는다.

## 실행·확인

```bash
# src/ 에서
./gradlew :apps:cloud:app-api:bootRun
curl localhost:8080/actuator/health

./gradlew :apps:cloud:app-api:build
```
