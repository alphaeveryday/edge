# Tenant Console IA (증권사 On-Premise)

```
Tenant Console
├── Dashboard
├── Price Movement Explanations
├── Review Queue
├── Compliance Policy
├── Audit Log
└── Settings
```

**Dashboard**: 이벤트 수신 상태, 자동 노출/검수 대기/차단 건수, 정정·무효화 알림, 최근 장애 알림, 최근 생성된 설명

**Price Movement Explanations**

- 목록: 종목, 시장, 등락률, 변동 요인 요약, 위험 등급, 상태, 생성 시각, 노출 시각
- 상세: 종목 기본 정보, 원본 AI 문구, 컴플라이언스 통과 후 문구, 최종 노출 문구, 관련 뉴스/공시, 시세·거래량·수급 변화, 이벤트 타임라인, 신뢰도, 반대 요인, 컴플라이언스 검사 결과, 상태 변경 이력, **노출 중단**, **정정 등록**

**Review Queue** (논리적 작업함, status=REVIEW_REQUIRED 목록)

- 목록: 종목, 등락률, 변동 요인 요약, 위험 등급, 검수 사유, 생성 시각
- 상세: 원본 AI 문구, 수정 가능한 최종 문구, 근거 데이터, 위반/경고 사유, 검수 의견 + 액션: **승인 / 수정 후 승인 / 반려 / 차단**

**Compliance Policy**

- 금칙어/금지 표현: 목록, 심각도, 처리 방식
- 처리 기준: 자동 노출 기준, 검수 필요 기준, 차단 기준, 기본 안내 문구. **자동 노출(AUTO_PUBLISHED) 비율은 테넌트 설정값이며 0%(전건 검수)부터 시작 가능** — 보수적 증권사는 전건 REVIEW_REQUIRED로 운영을 시작하고, 검수 데이터 축적 후 자동 노출 기준을 점진 완화하는 경로를 표준 온보딩 시나리오로 제시한다
- 기본 안내 문구 예시: "본 내용은 공개 정보 기반의 변동 요인 후보이며 투자 권유가 아닙니다."
- **이해상충 대응 (확정, 2026-07-14)**: 계열사 종목·자사 IPO 주관 종목 등 이해상충 소지 종목은 별도 룰 타입 신설 없이 Settings의 노출 범위 제외(종목·섹터 단위)로 통제한다. 증권사 현업 검증에서 준법감시 필수 검토 항목으로 확인된 요구사항 ([../adr/0023-customer-validation.md](../adr/0023-customer-validation.md))

**Audit Log**

- 콘텐츠 이력: 콘텐츠 ID, 종목, 상태 변경/검수/정정/무효화/정책 변경 이력, 작업자, 시각, 사유
- 노출 재현: 콘텐츠 ID, 종목, 고객 식별 해시, 기간, 채널, 실제 노출 문구, 노출 시각/채널, 근거 데이터, 컴플라이언스 검사 결과, 검수 결과

**Settings**

- Cloud Sync: [../contracts/sync-auth.md](../contracts/sync-auth.md) 참조 (연결 상태, 동기화 이력, 인증서 fingerprint/만료일/교체)
- 노출 범위: 특정 ETF·섹터 제외, MTS/HTS/Internal 채널별 ON·OFF. **MVP 커버리지는 국내 상장 ETF 한정**([../adr/0024](../adr/0024-scope-domestic-etf.md)) — 미국 ETF ON·OFF는 시장 커버리지 확장 시 추가 ([../roadmap.md](../roadmap.md))
- Users & Roles: 사용자 목록/초대/비활성화, 역할 부여 — **Tenant Admin / Compliance Reviewer / Operator / Read Only**
