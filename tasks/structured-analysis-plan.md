# 구조화 분석 도구 전환 구현 계획

승인 설계: `docs/superpowers/specs/2026-08-09-structured-analysis-tool-surface-design.md`

## PR 1 — 출력 조회 표면 격리

1. 자동 뷰 계획에 입력/출력 정책을 추가한다.
   - 산출물 테이블은 뷰 계획, `lake.bound`, SQL allowlist, coverage에서 제외한다.
   - 금지 테이블을 추가해도 기본 비공개가 유지되는 정책 테스트를 둔다.
2. 기존 정상 입력 뷰와 PIT 클램프가 유지되는 회귀 테스트를 실행한다.
3. analysis-engine 전체 테스트와 이미지 빌드를 실행하고 PR 증거를 작성한다.

완료 주장: 분석 산출물은 LLM 조회 관계로 노출되지 않는다.

## PR 2 — ObjectSet 런타임 기반

1. ObjectSet 계약을 analysis-engine 이미지에 포함되는 경량 모듈로 편입한다.
2. 생성·필터·설명·affordance·관계 이동·관측 도구를 구조화 schema로 제공한다.
3. `as_of`, lineage, PIT gap 불변식과 SQL 비노출 테스트를 추가한다.
4. P2 가설 탐색 호출부를 ObjectSet으로 전환하고 SQL-shaped 모델 응답이 실행되지 않음을
   검증한다. SQL 구현 파일은 후속 shadow/삭제 PR을 위해 남긴다.
5. 기존 경로와 고정 fixture differential을 실행한다.

완료 주장: LLM은 SQL 없이 시점 고정 객체 집합을 합성할 수 있다.

### 배포 체크포인트 A — PR 1·2

- 두 PR이 `dev`에 병합된 배포 이미지를 빌드·배포한다.
- AWS profile `work`로 canary 분석을 실행한다.
- trace의 LLM SQL 0건, 금지 관계 0건, ObjectSet lineage 존재를 확인한다.
- 실패 시 이전 ECS task definition으로 롤백한다.

## PR 3 — 뉴스 thread/event/argument 도구

1. thread, event, argument, evidence handle과 탐색 도구를 구현한다.
2. 사건 타입별 role/cardinality/object kind/measure schema를 제공한다.
3. 미해소 argument, 정정 thread, 미래 기사 차단 fixture를 검증한다.

완료 주장: 뉴스 관계는 실제 사건 argument와 선언된 schema에만 근거한다.

## PR 4 — 나머지 LLM 단계 구조화 도구 컷오버

1. P3·P5와 나머지 LLM 입력에서 SQL을 제거하고 구조화 도구만 주입한다(P2는 PR2 완료).
2. SQL 필드와 verifier에 없는 수치를 결정적 schema/lineage guard로 거부한다.
3. 고정 평가 셋과 shadow replay로 신규 경로를 비교한다.

완료 주장: LLM은 SQL을 실행할 수 없고 최종 수치는 verifier까지 역추적된다.

### 배포 체크포인트 B — PR 3·4

- 병합된 `dev` 이미지를 배포해 뉴스 포함 canary를 실행한다.
- thread→event→argument→evidence trace와 미해소 표면을 검토한다.
- SQL 응답 0건, unsupported numeric claim 0건, abstention 사례를 확인한다.

## PR 5 — SQL 레거시 제거

1. 배포 로그에서 레거시 사용량 0을 확인한다.
2. LLM용 `sql_surface`, `sqltool`, SQL prompt/retry/allowlist를 삭제한다.
3. 코드·테스트·이미지 참조 0과 전체 회귀를 검증한다.

완료 주장: 배포 산출물에 LLM 자유 SQL 경로가 존재하지 않는다.

### 최종 배포 체크포인트 C — PR 5

- 삭제 PR 단독으로 새 이미지를 배포한다.
- canary와 실제 task definition/image digest를 기록한다.
- 실패 시 이전 task definition으로 롤백한다.

## 검증 규율

- 각 동작 변경은 RED→GREEN 순서로 구현한다.
- 각 PR은 focused tests, 전체 analysis-engine tests, image build를 수행한다.
- 실제 AWS 검증을 실행하지 못한 PR은 `PARTIALLY VERIFIED` 이하로 보고한다.
- DB drop과 과거 데이터 삭제는 이 계획에 포함하지 않는다.
