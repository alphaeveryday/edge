# ADR-0034: 공개 엣지 호스트 단위 분리 — 서비스당 ALB 1개, 경로 라우팅 없음

- 상태: 승인됨
- 날짜: 2026-07-21

## 맥락
gateway 은퇴(ADR-0032) 이후 클라우드 공개 표면은 두 개가 예정돼 있었다: Sync 채널(온프렘 sync-agent의 mTLS outbound-Pull 목적지)과 super-admin API(운영자 콘솔). 이 둘을 한 진입점(ALB 또는 API Gateway)에서 경로(`/api/v1/sync/*` vs `/api/v1/admin/*`)로 나눌지, 진입점 자체를 나눌지 정해야 했다.

## 결정
**진입점을 호스트(도메인) 단위로 분리한다 — 서비스당 ALB 1개, 도메인=서비스 1:1, 경로 기반 라우팅 없음.**

- `sync-dev.edgesignal.dev` → sync 전용 ALB(mTLS verify) → tenant-sync-api
- `admin-api-dev.edgesignal.dev` → super-admin 전용 ALB(일반 TLS + WAF·망 제한) → super-admin-api
- 각 ALB 리스너는 default action forward 하나다. 어느 서비스로 갈지를 경로로 판별하는 지점은 없다 — DNS에서 이미 갈라진다.

결정적 근거는 프로토콜 순서다: **클라이언트 인증서 요구는 TLS 핸드셰이크 시점에 일어나고, 경로는 핸드셰이크가 끝난 뒤에야 보인다.** 어떤 장비든 "이 연결에 인증서를 요구할지"의 판별 재료는 SNI(호스트명)뿐이므로, 인증 정책의 최소 단위는 호스트다. ALB mTLS가 리스너 단위 설정인 것도, API Gateway mTLS가 custom domain 단위인 것도 같은 이유의 표현이다.

mTLS 적용은 2단계다: ALB·DNS 배선을 먼저 깔고, CA·trust store가 준비되면(ALPHA-447) `mtls_trust_store_arn` 주입으로 verify 모드를 켠다. 주입 전까지 sync 엔드포인트는 공개 도달이며(dev 스텁·시드 데이터 전제), 이 상태를 운영 데이터로 승격하지 않는다. 검증된 인증서는 `X-Amzn-Mtls-Clientcert-*` 헤더로 앱에 전달되고, 앱은 fingerprint→테넌트 바인딩만 담당한다 — 이 헤더 신뢰는 태스크 인바운드가 ALB SG로만 좁혀져(직접 도달 없음) 성립한다.

## 대안
- **단일 ALB + 경로/호스트 라우팅** — mTLS가 리스너 단위라 sync만 인증서를 요구할 수 없다. 공유하면 운영자 브라우저까지 클라이언트 인증서를 강제하게 돼 탈락. sync를 별도 포트(8443)로 빼는 우회는 "고정 FQDN:443"(온프렘 방화벽 outbound 443 전제)과 충돌.
- **API Gateway로 통합** — mTLS가 custom domain 단위라 도메인 분리는 어차피 강제된다(문제를 피하지 못함). 여기에 VPC Link 배관, 인증서→백엔드 전달의 파라미터 매핑, 페이로드 10MB 상한(sync 번들이 자랄 표면)이 얹혀 기존 ALB·Fargate 스택 대비 순증 복잡도만 남아 탈락.
- **NLB TLS passthrough + 앱 종단 mTLS** — end-to-end mTLS이지만 서버 인증서 로테이션·TLS 설정·CA 검증을 앱이 짊어지고 ACM 통합이 안 된다. 관리형(ALB verify + ACM + trust store)이 현 단계 운영 부담에 맞다.

## 결과
- ADR-0032의 재도입 예고("listener rule `/api/v1/admin/*`")는 이 결정으로 대체된다 — 경로 규칙 대신 전용 ALB 직결.
- ALB가 서비스당 1개씩 늘어나는 비용을 받아들인다. 도입 시점은 서비스별로 독립이다(sync 지금, super-admin은 ALPHA-473).
- WAF(ALPHA-297)는 super-admin ALB에만 붙는다 — sync는 인증서 trust store가 게이트이고, 기계 클라이언트라 WAF 관리형 룰의 오탐 표면이 된다.
- 와일드카드 인증서가 1레벨만 커버하므로 서브도메인은 평평하게 유지한다(`admin-api-dev` 형태 — `api.admin.…` 같은 중첩 금지).
