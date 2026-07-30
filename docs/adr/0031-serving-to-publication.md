# ADR-0031: serving-api를 Publication 도메인으로 리네이밍

- 상태: 승인됨
- 날짜: 2026-07-20

## 맥락
도메인 용어집은 코드(패키지·클래스·API 경로)를 영어 도메인명으로, 대외 문서를 한국어로
고정한다. 온프렘 9도메인의 영어 이름에 **"Serving"은 없다** — 대고객 제공 도메인의 이름은
**Publication(제공)**이다. 현행 `serving-api`(패키지 `com.edge.serving`)는 용어집 확정 이전에
들어온 이름이라 9도메인 규율에서 떠 있다. 이 모듈은 실제로 승인분 조회·서빙 + 노출 기록
([[0013-exposure-log-recording]])을 하는데, 이는 규제 서사상 "제공=책임 발생 시점"에 대응한다.

모듈은 아직 스캐폴드(인메모리 시드)라 리네이밍 비용이 가장 싼 시점이다.

## 결정
`serving-api` → Publication 도메인으로 통일한다.
- 모듈 디렉터리 `apps/onprem/serving-api` → `apps/onprem/publication-api` (`-api` 접미 컨벤션 유지).
- Gradle 프로젝트 `:apps:onprem:serving-api` → `:apps:onprem:publication-api`.
- 자바 패키지 `com.edge.serving` → `com.edge.publication` (짧은 도메인명 컨벤션).
- 클래스: `ServingApplication`→`PublicationApplication`, `ServingErrorStatus`→`PublicationErrorStatus`.
- `spring.application.name` `serving` → `publication`.
- Publication 도메인이 **발행 상태 관리 + 조회·서빙 + 노출 기록**을 모두 소유한다.
  도메인 *내부*에서 발행(publish, 상태 전이 1회)과 노출(serve+expose, 매 조회)은 별도
  컴포넌트로 구분한다(현행 `exposure` 서브패키지 계승).
- 문서의 "Serving API"·"Serving Cache" 표기를 "Publication API"·"Publication Cache"로 통일하고,
  `contracts/serving-api.md` → `contracts/publication-api.md`로 파일도 리네이밍한다.

**바뀌지 않는 것**:
- **API 와이어 경로는 불변** — `GET /api/v1/explanations/{ticker}`는 엔티티 기반이라 "serving"을
  담고 있지 않다. 증권사 백엔드 계약(호출자=금융사 API, 고객 해시 증권사 생성)도 그대로.
- `com.edge.tenant.*` 수직 슬라이스 재패키징은 이 ADR이 다루지 않는다(별도 결정).

## 대안
- **serving 유지** — 용어집 9도메인 규율 위반. "Serving"이 어휘에 떠 있게 둔다.
- **publication-api로 바꾸되 발행/노출 구분 없음** — "Publication"이 발행 상태만 뜻하는 걸로
  오해되어 이 API가 상태를 관리하는 것처럼 읽힌다. 발행/노출 구분을 잃는다.
- **문서는 그대로, 코드만 리네이밍** — 코드는 publication, 문서는 Serving으로 갈려 split-brain.
  용어집은 코드·문서 양쪽의 일관을 요구하므로 가변 문서까지 통일한다.

## 결과
- 온프렘 도메인 어휘가 9도메인 규율(Publication)로 정렬된다.
- 호출자(증권사 내부 API, 서버-투-서버)·고객 해시 증권사 생성 등 기존 계약은 불변.
- **승인된 ADR(0010·0013·0016·0017·0019)의 "Serving API" 표기는 불변 규칙상 수정하지 않는다** —
  이 ADR-0031이 갱신 기록으로 대체하며, 그 ADR들은 자기 시점의 이름을 보존한다.
- 열려 있는 Jira 이슈(ALPHA-422·432·433 등)의 "serving" 표기는 이 PR 범위 밖 — 후속 정리.
