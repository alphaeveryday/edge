# edge — 프로젝트 지침

이 프로젝트의 에이전트 지침은 AGENTS.md를 단일 출처(SSOT)로 둡니다.
Codex 등 다른 도구와 공유하기 위함입니다.

@AGENTS.md

모든 산출물(문서·UI 문구·발표 자료) 작성 시 [docs/writing-rules.md](docs/writing-rules.md)의 톤 규칙을 따른다.

## 하네스: Git 작업 사이클 · 문서 정합성

**목표:** 티켓→브랜치→PR→머지 사이클의 거버넌스 자동 준수, 코드-문서 드리프트 제거.

**트리거:** 기능·버그·문서 작업의 시작/커밋/PR/머지/Jira 티켓 처리 요청 시 `pr-cycle` 스킬을 사용하라. 변경 리뷰(diff·PR 점검, "코드리뷰 해줘") 요청 시 `edge-review` 스킬을, 코드 변경 후 문서 정합성 점검·갱신 요청 시 `docs-sync` 스킬을 사용하라 (둘 다 pr-cycle이 PR 전 게이트에서 자동 호출: edge-review → docs-sync — 빌드/테스트 확인은 edge-review 안에 포함). 단순 조회성 질문은 직접 응답 가능.

**변경 이력:**
| 날짜 | 변경 내용 | 대상 | 사유 |
|------|----------|------|------|
| 2026-07-02 | 초기 구성 — pr-cycle·docs-sync 스킬 (경량 시작, 에이전트 팀 없음) | skills/pr-cycle, skills/docs-sync | git 히스토리 반복 작업 1·2순위 스킬화 |
| 2026-07-02 | 티켓 확인을 3단계 에스컬레이션으로 확장 (스프린트→백로그→생성, 스프린트 배치 보장) | skills/pr-cycle | 백로그 미확인 시 중복 이슈 생성 우려 피드백 |
| 2026-07-04 | edge 특화 코드리뷰 스킬 신설 + PR 전 게이트에 편입 (edge-review → docs-sync 2단계; 빌드/테스트 확인은 edge-review 안에 포함) | skills/edge-review, skills/pr-cycle | 내장 /code-review 대신 AGENTS 규칙·계약(schema SSOT·신뢰경계·레이크) 특화 리뷰를 게이트에 상시화 |
| 2026-07-10 | 파인더 각도 H(검증·품질 게이트 완전성) 신설 — malformed 입력의 crash-before-gate·coerce-to-passing·unchecked-field 우회를 적대적 열거 | skills/edge-review | ALPHA-133 정제 PR에서 edge-review가 놓친 게이트 우회 6건(비달력일·NaN/inf·소수거래량·비객체행·정체성결측 등)을 Codex가 전부 잡음 — 검증 게이트 특화 각도 부재가 원인 |
| 2026-07-15 | F각도(신뢰경계) 전면 개정 — widget-api 읽기전용·gateway 3라우트 전제를 하이브리드 경계(Sync 채널 outbound-Pull·인증서-테넌트 인가·Serving Published-only·데이터 거주지)로 교체, 대체된 ADR-0006 인용 제거, widget 예시를 tenant-console-api로 교체 | skills/edge-review, skills/pr-cycle, skills/docs-sync | 코드베이스 재편(widget 삭제, ADR-0010·0016)으로 구 신뢰경계 전제가 소멸 — 하네스가 폐기된 구조를 규칙 근거로 인용 중이었음 |
| 2026-07-17 | PR 생성과 머지 사이에 Codex 리뷰 왕복 단계 신설 — `+1` 리액션/리뷰 풀링, 수용 판단(과잉 엄밀 보정·의도적 생략 고려), 수용 시 수정→`@codex review` 재요청 반복, `+1` 또는 전건 비수용 시 머지 진행 (README 사이클도 8단계로 동기화) | skills/pr-cycle, README.md | PR마다 Codex 리뷰 대응을 수동으로 반복해 온 왕복을 사이클에 편입 |
| 2026-07-21 | Phase 6(Codex 왕복)을 `codex-review-loop` 스킬로 추출 — pr-cycle은 위임(통과 여부+비수용 목록 반환받아 머지 판단). 브랜치·커밋·머지 등 트리비얼 단계는 pr-cycle과 바뀌는 이유가 같아 추출하지 않음 | skills/codex-review-loop, skills/pr-cycle | SRP — Codex 봇 프로토콜(리액션·`@codex`·풀링)은 pr-cycle과 바뀌는 이유가 독립적. 유일한 정당한 경계라 추출·재사용, pr-cycle 본문 경량화 |
| 2026-07-21 | 이슈 생성 시 담당자·story point 필수화 — 담당자 불확실 시 사용자에게 확인, story point는 백로그·완료 이슈 2~3건을 기준선으로 비교 산정(근거 보고) | skills/pr-cycle | 담당자·SP 누락 이슈가 보드 소유권·스프린트 용량 계산을 깨뜨린다는 피드백 |
| 2026-07-22 | PR 전 게이트를 로컬 수렴 루프로 개편 — edge-review(게이트 기본 `high`·위험 변경 `max`, 트리비얼은 예외)를 수용 finding 0건까지 전체 범위 재리뷰로 반복(수정 라운드 상한 3회+검증 라운드 1회, 미수렴 시 사용자 확인), docs-sync는 루프 종료 후 1회, Phase 6 봇 왕복 기대치 1라운드 + 수렴 PR 에서 초과 시 각도 보강 제안 신설 | skills/pr-cycle, README.md | 실측(최근 10개 PR): 봇 finding 대부분이 로컬에서 잡히는 성격(#191 5라운드·8건, E·G·C각도 해당)인데 게이트가 medium 1회 실행이라 커버리지 부족 + 수정분 무재리뷰가 왕복 장기화(작업당 최대 ~1시간)의 주범 — GitHub 왕복을 대기 없는 로컬 라운드로 흡수 |
