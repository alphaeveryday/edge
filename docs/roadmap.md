# 향후 확장 가능 영역 (MVP 이후)

- analysis_type 확장: MARKET_BRIEFING, DISCLOSURE_SUMMARY 등
- 시장 커버리지 확장: 미국 ETF — 물리 스키마는 선반영됨(V202607150001, instrument 모델 시장 무관), 수집원(SEC 공시·해외 뉴스·미국 시세)만 추가하면 됨. MVP는 국내 ETF 한정 ([adr/0024](adr/0024-scope-domestic-etf.md))
- 상품 커버리지 확장: 개별 종목(주식) 단위 설명
- 노출 콜백 API (조회≠노출 정밀화)
- 정정 처리의 테넌트 정책 분기 (위험등급별 자동 반영 허용)
- 번들 서명 검증의 완전 구현 (목표 계약 — [contracts/sync-protocol.md](contracts/sync-protocol.md))
- Tenant Console 운영 알림용 제한적 SSE
