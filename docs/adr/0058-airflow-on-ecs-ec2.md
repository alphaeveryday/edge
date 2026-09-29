# ADR-0058: 유한 배치의 Airflow 실행 환경 — ECS on EC2 자체 운영

- 상태: 제안됨
- 날짜: 2026-09-28

## 맥락
유한 배치(SFN 5개)를 레인별로 Airflow 로 옮기기로 했고, 첫 레인(장중 수급) DAG·원장 연동이 dev 에 머지됐다(ALPHA-1088, #949·#950). DAG 를 돌릴 실행 환경이 없었다. 관리형 MWAA 는 조직 SCP 가 API 자체를 거부한다. 업무 실행은 기존 Fargate 태스크가 계속 맡으므로, 필요한 것은 실행 관리(스케줄·재시도·추적) 프로세스를 상시 띄울 자리다.

## 결정
- **일반 ECS on EC2**(ASG + Capacity Provider)로 자체 운영한다. ECS Managed Instances 가 아니다.
- 전용 클러스터 `edge-dev-airflow`, t4g.small 1대(ASG 1~2), 서비스 1개·태스크 1개에 api-server·scheduler·dag-processor(태스크 메모리 1536MiB 하나를 공유). LocalExecutor(parallelism 1), triggerer 없음. 사양은 로컬 합산 상한 검증으로 정했다 — t4g.micro 는 호스트 몫을 빼면 OOM(README "사양 검증").
- 메타DB 는 업무 DB 와 **인스턴스를 나눈다**(RDS db.t4g.micro). DAG 는 이미지에 구워 한 태그로 모든 구성요소를 교체한다. 메타DB 마이그레이션은 배포마다 단일 one-off 작업으로 서비스 교체 전에 돈다.
- UI 는 SSM 포트 포워딩으로만 접근한다(공개 진입점 없음).
- 실행 주체 전환은 이 결정과 별개다(`investor_intraday_orchestrator`, 별도 승인).

## 대안
- **MWAA** — SCP 거부로 불가.
- **ECS Fargate 상시 서비스** — 호스트 관리가 없지만, 요청 범위가 EC2 기반이었고 같은 메모리에서 상시 과금이 더 크다.
- **기존 worker 클러스터에 EC2 용량 추가** — 그 클러스터의 capacity provider 목록을 한 리소스가 통째로 소유해 기존 리소스 변경이 된다. 클러스터는 무료라 분리 비용이 없다.
- **메타DB 를 업무 DB(`edge-dev`)에 같이** — 월 약 21 USD 절감. **미결(검증 과제)**: 연결은 3~5개로 제약이 아니지만, 메모리 고갈 이력이 있는 인스턴스의 메모리·장 시작 CPU 영향은 로컬로 잴 수 없다. DB·계정을 나눠도 자원은 격리되지 않는다. 실제 on/off 측정과 중단 기준은 README "기존 RDS 재사용 평가".
- **Celery·Kubernetes executor** — 레인이 직렬이라 근거가 없다.
- **t4g.micro·t4g.medium** — micro 는 로컬 합산 상한 768MiB(호스트 몫 가정 차감)에서 OOM, medium 은 small 차감 조건(1792MiB)이 전 기준을 통과해 근거가 없다.

## 결과
- 월 약 42~44 USD 가 는다(small + 별도 RDS. 기존 RDS 재사용이 검증되면 약 20~23 — 내역은 `src/apps/cloud/airflow/README.md` "월 비용").
- 호스트 1대가 단일 장애점이다. 죽으면 스케줄이 멈추고(업무 ECS 는 끝까지 돈다), ASG 교체 뒤 EdgeStep 이 재접속하거나 보류한다. 멈춘 동안의 슬롯은 공백이다.
- 배포 때 1~3분 Airflow 가 멈춘다(한 태스크 교체). 평일 장중 배포는 워크플로가 막는다.
- AMI·Airflow 버전 갱신·메타DB 복원은 운영자 절차다(README "호스트 교체"·"배포·롤백"·"메타DB").
- SimpleAuthManager(단일 사용자)는 Airflow 가 개발용으로 분류한다. 사용자가 늘면 인증 관리자를 바꾼다.
