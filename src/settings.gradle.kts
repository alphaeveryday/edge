rootProject.name = "edge"

// JVM 워크스페이스 루트 (ADR-0001 — 런타임별 루트 분리).
// 현재는 DB 스키마 SSOT 모듈만 포함한다. 앱(gateway·widget-api 등)은 구현될 때 추가한다.
include(":libs:schema")
