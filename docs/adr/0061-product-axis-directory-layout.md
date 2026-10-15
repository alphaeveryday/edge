# ADR-0061: 제품 축 디렉터리 배치와 src/ 제거

- 상태: 승인됨
- 날짜: 2026-10-15
- 대체: [ADR-0001](0001-monorepo-structure.md)의 `src/` 루트 배치, [ADR-0029](0029-apps-plane-grouping.md)의 apps 플레인 그룹핑. 두 문서의 나머지 결정은 유지

## 맥락
앱 디렉터리가 배포 플레인(`cloud`·`onprem`) 기준으로 묶여 있었다.
저장소에는 엔진, B2B 산출물(edgesignal), B2C 산출물(etforca)이 함께 있다.
플레인 축 구조로는 어떤 모듈이 어느 제품에 속하는지 트리에서 보이지 않는다.
B2C 앱(app-api·app-ui·portfolio)도 `cloud` 아래에 엔진·B2B 모듈과 섞여 있었다.
트랙 2 결정으로 온프렘도 증권사 클라우드 계정(BYOC)에 배포한다.
그래서 플레인 구분이 디렉터리로 드러낼 만큼의 경계 정보를 더는 담지 않는다.
워크스페이스 루트인 `src/` 한 단계는 모든 경로를 한 단계 깊게 만들 뿐 구분 정보가 없다.

## 결정
- `src/` 제거와 워크스페이스 루트 파일(`settings.gradle`·`pyproject.toml`·`pnpm-workspace.yaml` 등)의 레포 루트 승격
- 앱 디렉터리의 제품 축 그룹핑
  - `apps/engine`: 수집·분석 공용 상류 (airflow·analysis-engine-v2·data-pipeline·db-query)
  - `apps/edgesignal`: B2B 산출물 (테넌트 동기화·운영 콘솔·온프렘 모듈)
  - `apps/etforca`: B2C 산출물 (app-api·app-ui·app-landing·portfolio)
- 플레인 정보의 [apps/edgesignal/README.md](../../apps/edgesignal/README.md) 표 이관
- 깊이는 `apps/<제품>/<모듈>` 3단 고정

## 대안
- 플레인 축 유지: 제품 경계가 트리에서 보이지 않는 문제가 그대로 남아 기각
- `apps/edgesignal/{cloud,onprem}` 하위 유지: 플레인 경계를 구조로 남기지만 edgesignal만 깊이 4단이 됨. BYOC로 경계 의미가 약해져 기각
- 엔진과 제품을 루트에서 분리(`engine/`·`products/`): ADR-0001의 apps·libs 관례까지 뒤집고 깊이가 들쭉날쭉해져 기각

## 결과
- 이동은 변환 스크립트 한 번으로 수행. 커밋을 이동·치환·수동 수정으로 분리해 rename 이력 보존
- ECR 저장소·ECS 서비스·패키지 이름 같은 런타임 식별자는 불변. 변경 전후 이미지 내용 동일
- Docker 빌드 컨텍스트의 레포 루트 전환. 워크스페이스 밖 디렉터리는 `.dockerignore`로 제외
- 배포 워크플로 입력의 `plane`에서 `product`로의 교체
- 이미지 내부 경로(`/app/apps/cloud/…`)는 산출물 동일성 검증을 위해 유지. 정리는 ALPHA-1361
- 루트 정리(compose·tasks·scripts·loadtest 재배치)는 ALPHA-1359로 분리
