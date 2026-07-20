# ADR-0032: gateway 은퇴 — 클라우드 엣지를 ALB 직결로

- 상태: 승인됨
- 날짜: 2026-07-21

## 맥락
gateway는 [[0006-gateway-single-edge]]의 단일 엣지였으나 그 ADR은
[[0010-hybrid-onprem-pivot]]로 대체됐다. 하이브리드 피벗이 클라우드 플레인을 비웠다:
- **console**은 온프렘으로 이동([[0010-hybrid-onprem-pivot]]·[[0031-serving-to-publication]] 계열 재편) —
  클라우드 gateway가 온프렘 console을 라우팅하는 경로는 성립하지 않는다(cloud→onprem 없음).
- **sync**는 gateway를 거치지 않는다 — 고정 FQDN:443 mTLS 단일 목적지로 별도 노출되고,
  인증서 fingerprint→테넌트 바인딩을 앱이 검증한다([[0012-sync-cert-bootstrap]]).
- 남는 건 **super-admin 하나**인데, super-admin-api·tenant-console-api는 현재 컨트롤러 없는
  **빈 스캐폴드**라 gateway의 `/api/v1/*`→`/internal/*` rewrite가 가리키는 대상이 없다(load-bearing 아님).

인프라 실측: gateway는 internal-only 스테이징(ALPHA-296)일 뿐 공개 컷오버(ALPHA-294)를
한 번도 안 했고, 엣지로 작동한 적이 없다. 공개 ALB는 gateway가 아니라 **이미 삭제된
widget-api**([[0010-hybrid-onprem-pivot]]로 코드 제거)를 임시로 앞단하고 있으며, 위젯 스택 전체
(widget_api·widget_site·전용 edge_alb·edge DNS·RDS 규칙·outputs)가 terraform에 드리프트로 남아 있다.

## 결정
gateway를 제거한다.
- **코드·배선 제거**: `src/apps/cloud/gateway/`, settings.gradle, docker-compose, deploy-gateway.yml,
  terraform `module "gateway"`.
- **공개 엣지는 super-admin이 실제 공개 도달이 필요해질 때 ALB 직결로 재도입**한다 —
  ALB listener rule(`/api/v1/admin/*` → super-admin target group) + super-admin-api가
  `/api/v1/admin/*`를 **직접 서빙**(`/internal/*` rewrite 폐기, 백엔드가 빈 스캐폴드라 비용 0).
  CORS는 API(Spring)가, 접근 제한은 망 수준(VPN/IP allowlist, [[0008-super-admin-console]])이 맡는다.
- **sync mTLS는 admin 엣지와 분리** — 앱 직접 종단(또는 전용 NLB passthrough)으로 자기 FQDN에서 종단한다.
- **죽은 위젯 엣지 스택 제거**: terraform `module "widget_api"`·`"widget_site"`·`"edge_alb"`,
  `aws_route53_record.edge`, `rds_from_widget_api` 규칙, 관련 outputs.

## 대안
- **gateway 공개 컷오버(ALPHA-294) 완성** — 백엔드 하나 앞 리버스 프록시는 과설계(Rule 2).
  온프렘(진짜 API 표면이 몰린 플레인)은 이미 gateway 없이 동작하도록 설계됐다(비대칭).
- **gateway를 idle 스테이징으로 존치** — 죽은 코드·드리프트가 누적되고, super-admin 앞 방어는
  프록시보다 망 수준 제한이 정답이라([[0008-super-admin-console]]) 존치 이득이 없다.
- **위젯 스택 terraform 존치** — 코드가 없는 서비스를 인프라가 배포하려 해 plan이 깨지거나
  죽은 ALB가 과금·503을 낸다.

## 결과
- 클라우드 실행 프로세스 1종 감소, 죽은 위젯 인프라 정리.
- `/internal/*` 경로 규약·CORS·엣지 방어는 인프라(ALB listener·SG·망 제한)와 API로 이관.
- **후속 영향**: ALPHA-294(gateway 공개 컷오버) 폐기, ALPHA-297(엣지 ALB WAFv2)은 ALB 재도입
  시점으로 이월. TODO §56의 "gateway 존치 vs ALB" 질문은 이 ADR로 해소.
- [[0006-gateway-single-edge]]는 이미 [[0010-hybrid-onprem-pivot]]로 대체됨 — 본 ADR은 그 방향을 실행에 옮긴다.
- terraform은 이 PR에서 apply하지 않는다(.tf 변경만, terraform-plan CI가 검증). 승인된 ADR 본문은 불변.
- **stateful destroy 주의**: 세트에서 빼는 리소스는 기존 state 속성으로 destroy된다(신규 config 아님).
  - **ECR 레포(edge/gateway·edge/widget-api)**: 이미지가 남아 있으면 `RepositoryNotEmpty`로 막히므로,
    이 PR은 키를 제거하지 않고 `force_delete=true`만 먼저 반영한다. 키 제거는 후속 PR에서(2단계 — TODO).
  - **widget_site S3 버킷**: static-site 모듈에 `force_destroy`가 없어 객체가 남아 있으면 `BucketNotEmpty`로
    막힐 수 있다. 위젯 CD 경로가 없어 비어 있을 것으로 보이나, apply 전 비어 있지 않으면 수동 비우기가 필요하다.
