# Tenant Console IA (증권사 On-Premise)

> 콘솔 재설계 IA 기준으로 정렬 ([../architecture/information-architecture.md](../architecture/information-architecture.md) 고객사 운영 콘솔). 이 문서가 현행 SSOT이고 뷰는 논리 개요다 — 충돌 시 이 문서 우선.

```
Tenant Console
├── Dashboard
├── Price Movement Explanations
├── Review Queue
├── Compliance Policy
└── Settings
```

> **감사 이력은 별도 메뉴가 아니다 — 데이터는 존치, 전용 열람 화면만 제거.** 구 Audit Log 브라우징 메뉴는 재설계에서 두지 않으며, 콘솔에서는 각 설명 상세("상태 변경 이력"·"컴플라이언스 검사 결과")로 확인한다. 감사 레코드 자체는 **DB에 보존**된다:
> - **콘텐츠 이력**: 콘텐츠 ID, 종목, 상태 변경/검수/정정/무효화/정책 변경 이력, 작업자, 시각, 사유
>
> 고객 단위 **노출 재현(Exposure Log)은 [ADR-0053](../adr/0053-widget-direct-serving-no-personalization.md)으로 폐지**됐다 — 재구성 근거는 게시·정정 이력과 정책 이력이다([../domain/exposure-log.md](../domain/exposure-log.md)). 상태 규칙은 [../domain/state-machine.md](../domain/state-machine.md)가 SSOT.

**Dashboard**

- 주요 현황 — 가격 변동 설명 수신 상태: 자동 제공 / 검수 대기 / 점검 차단 / 검수 반려 / 제공 중단 건수
- 제공 API 트래픽(최근 24시간): 요청 수, 에러 건수·에러율 (원천 = serving_request_metric, ALPHA-128)
- 최근 가격 변동 설명 요약: 종목, 등락률, 제공 상태, 확신도(높음/중간/보류 — 위험등급 융합 산정 폐지, [../adr/0046](../adr/0046-confidence-gate-risk-grade-abolition.md))
- 이벤트 수신 상태, 무효화 알림, 최근 장애 알림

**Price Movement Explanations**

- 목록: 종목, 시장, 등락률, 변동 요인 요약, 확신도, 상태, 생성 시각, 노출 시각
- 필터: 상태(전체 / 자동 제공 / 검수 대기 / 점검 차단 / 검수 반려 / 제공 중단), 시장(전체 / KRX — **MVP는 국내 상장 ETF 한정**, NASDAQ 등 해외는 커버리지 확장 시 [../adr/0024](../adr/0024-scope-domestic-etf.md)), 확신도(전체 / 높음 / 중간 / 보류), 종목 검색
- 상세: 종목 기본 정보, 원본 AI 문구, 컴플라이언스 통과 후 문구, 최종 노출 문구, 관련 뉴스/공시, 시세·거래량·수급 변화, 이벤트 타임라인, 신뢰도, 반대 요인, 컴플라이언스 검사 결과, 상태 변경 이력
- 관리 액션: **최종 문구 정정 / 검수로 이관 / 제공 중단(노출 중단 — 사유 필수)** (구 "정정 등록"은 CORRECTION 폐지로 삭제 — [../adr/0044](../adr/0044-correction-abolition.md))
- 판정 게이트(승인·반려)는 Review Queue 소관이다 — explanations 는 현황판+사후 운영에 한정된다(역할 분담, 사용자 결정 2026-07-29).

> 실전환 현황(ALPHA-607 읽기·613 쓰기): 읽기(목록·상세·수신 상태)와 사후 운영 쓰기(최종 문구 정정·검수 이관·제공 중단)가 온프렘 원장 전이·행위자·감사로 실전환됐다 — `ExplanationMockStore` 삭제로 콘솔 mock 이 소멸했다. 화면·IA 에 없던 approve·reject·draft(ALPHA-513 잔재)는 표면째 제거됐다. **시장·등락률(방향)**은 온프렘 원장에 아직 없어(경계면 확장 [../contracts/event-bundle-schema.md](../contracts/event-bundle-schema.md) `observed_return`·`market_code`, ALPHA-497 이연) 목록·상세·Dashboard 요약에서 **한시 생략** — materialization 후 위 컬럼을 복원한다. 권한은 [permission-matrix](permission-matrix.md) 적용.

**Review Queue** (논리적 작업함, status=REVIEW_REQUIRED 목록)

- 목록: 종목, 등락률, 변동 요인 요약, 확신도, 검수 사유, 생성 시각
- 필터: 검수 사유(전체 / 단일 출처 / 금칙어 / 단정 표현), 종목 검색
- 상세: 원본 AI 문구, 수정 가능한 최종 문구, 근거 데이터, 위반/경고 사유, 검수 의견. **검수 사유는 운영자가 설정한 기준의 문구를 그대로 쓴다**(ALPHA-774) — 확신도를 `중간 이하`로 걸었으면 사유도 `확신도 중간 이하`다. 기준의 출처는 **판정 당시 정책 버전**이고(`screening_check.policy_version_id`), 현재 설정이 아니다 — 정책은 불변 버전이라 오늘 값으로 과거 판정을 라벨링하면 감사 재현이 어긋난다. 실측값은 사유 옆에 부기하고(`출처 1건`), 판정기 원값은 tooltip 으로 남는다. 컴플라이언스 검사 결과 표에는 **정책 버전 열**이 있어 어느 기준으로 걸렸는지 드러난다
- 액션: **임시 저장 / 승인 / 수정 후 승인 / 반려 / 차단**

**Compliance Policy**

- 금칙어/금지 표현: 목록(표현·처리 방식·활성 여부), 등록. 탭 순서는 **처리 기준 → 금칙어**다(ALPHA-765) — 처리 기준 표가 정책의 전경이고 금칙어는 그 표 한 항목의 상세라, 진입도 전경부터다. **심각도(위험 등급)는 은퇴했다**(ALPHA-760) — 결과를 정하는 축은 처리 방식뿐이라 판정에 쓰이지 않는 등급이 강도를 정하는 것처럼 읽혔다
- 처리 기준: **활성 정책에서 파생한 한 표**(점검 항목 · 설정 · 결과) — 행마다 무엇이 걸리면 어떤 상태가 되는지를 보여준다(ALPHA-756). 결과 어휘는 원장 상태 라벨(자동 제공 · 검수 대기 · 점검 차단)을 그대로 쓰고, 설정 값은 걸리는 쪽 극성이다("1개 이하"·"보류 이하"). 항목은 금칙어(처리 방식별 2행, 상세는 금칙어 탭) · 출처 수 · 확신도 · 원인 미확인(엔진 고정, 확신도 무관 상시 검수, [../adr/0046](../adr/0046-confidence-gate-risk-grade-abolition.md)) · 금칙어 밖 룰 인스턴스(있을 때만). **자동 제공 스위치는 카드 헤더 토글**이다 — 끄면 어디에도 걸리지 않은 설명까지 검수로 가고, 금칙어·원인 미확인은 스위치와 무관하게 적용된다(평가기 순서: 룰 → UNCERTAIN → 스위치 → 게이트). **온보딩 기본은 자동 노출 ON — 점검(금칙어·기준)에 걸린 것만 검수한다**(2026-07-27 결정, ALPHA-438). 전건 검수(0%) 운영은 테넌트 선택지다(policy_version.auto_publish_enabled — 콘솔에서 끌 수 있다). 첫 발행 전에는 판정 자체가 진행되지 않아(정책 부재 = 진행 중단) 화면이 "발행 전"으로 구분한다
- 정책 버전 이력: 발행 버전 목록(버전·발행 시각·발행자·활성 여부·기준 요약) — 정책은 불변 버전(ADR-0018)이라 모든 변경이 새 버전 발행이고, 이력이 곧 감사 추적이다
- 기본 안내 문구 예시: "본 내용은 공개 정보 기반의 변동 요인 후보이며 투자 권유가 아닙니다."
- **이해상충 대응 (확정, 2026-07-14)**: 계열사 종목·자사 IPO 주관 종목 등 이해상충 소지 종목은 별도 룰 타입 신설 없이 Settings의 노출 범위 제외(종목·섹터 단위)로 통제한다. 증권사 현업 검증에서 준법감시 필수 검토 항목으로 확인된 요구사항 ([../adr/0023-customer-validation.md](../adr/0023-customer-validation.md))

**Settings**

- 노출(제공) 범위: 시장·종목별 제공 ON·OFF, 특정 ETF·섹터 제외, MTS/HTS/Internal 채널별 ON·OFF. **MVP 커버리지는 국내 상장 ETF 한정**([../adr/0024](../adr/0024-scope-domestic-etf.md)) — 미국 ETF(NASDAQ 등) ON·OFF는 시장 커버리지 확장 시 추가 ([../roadmap.md](../roadmap.md))
- Cloud Sync: 연결 상태, 동기화 이력, 인증서 fingerprint/만료일/교체 ([../contracts/sync-auth.md](../contracts/sync-auth.md) 참조)
- Users & Roles: 사용자 목록/등록/비활성화 (관리자 직접 등록 — 초대·재설정 메일 흐름 없음, [../adr/0025](../adr/0025-onprem-auth-hybrid.md)), 역할 부여 — **Tenant Admin / Compliance Reviewer / Operator / Read Only**. 역할별 기능 권한은 [permission-matrix.md](permission-matrix.md)가 SSOT
