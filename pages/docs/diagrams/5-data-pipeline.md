# 5 데이터 파이프라인 (Data Pipeline)

하루 한 번의 일 배치(v1)에서 장중 1분 레인·배치 레인·분석 엔진 v2 연결(v5)까지, 파이프라인의 발전 단계를 판별로 담았다.
단계 번호(v1~v5)는 발전 단계이고, 괄호 안은 원본 도면 파일의 판 번호다.

상자를 클릭하면 상위·하위 1단계가 함께 강조된다.

<p class="ep-diagram-meta">버전:
<select class="ep-version-select" aria-label="다이어그램 버전 선택">
  <option value="v5" selected>v5 (현재 · 도면 v3.1)</option>
  <option value="v4">v4 (도면 v2.5)</option>
  <option value="v3">v3 (도면 v2.0)</option>
  <option value="v2">v2 (도면 v0.4)</option>
  <option value="v1">v1 (도면 v0.3)</option>
</select> ·
<a class="ep-open-full" href="../interactive/5-dp.v5.html" data-href-v4="../interactive/5-dp.v4.html" data-href-v3="../interactive/5-dp.v3.html" data-href-v2="../interactive/5-dp.v2.html" data-href-v1="../interactive/5-dp.v1.html" target="_blank" rel="noopener">전체 화면으로 열기 ↗</a></p>

<div class="ep-diagram-frame">
  <iframe data-version="v5" src="../interactive/5-dp.v5.html" title="EDGE 데이터 파이프라인 v5 — 인터랙티브" loading="lazy"></iframe>
  <iframe data-version="v4" src="../interactive/5-dp.v4.html" title="EDGE 데이터 파이프라인 v4 — 인터랙티브" loading="lazy" hidden></iframe>
  <iframe data-version="v3" src="../interactive/5-dp.v3.html" title="EDGE 데이터 파이프라인 v3 — 인터랙티브" loading="lazy" hidden></iframe>
  <iframe data-version="v2" src="../interactive/5-dp.v2.html" title="EDGE 데이터 파이프라인 v2 — 인터랙티브" loading="lazy" hidden></iframe>
  <iframe data-version="v1" src="../interactive/5-dp.v1.html" title="EDGE 데이터 파이프라인 v1 — 인터랙티브" loading="lazy" hidden></iframe>
</div>

<div class="ep-version-note">
  <p data-version="v5">v5(2026-10-15)는 Airflow 도입과 분석 엔진 v2 연결. 레이크 단계(랜딩·클린·큐레이팅)를 가로축으로 두고, 1분 단위 레인(상주 ECS 워커 → RDS → outbox 릴레이 → SQS → 뉴스 이벤트 추출·가격 변동 판정)과 배치 레인(Step Functions·Airflow 의 수집·정제·적재 태스크 → 이벤트 추출)이 분석 엔진 v2 로 흘러간다. 운영 원장·메타데이터는 운영 콘솔이 읽는다.</p>
  <p data-version="v4" hidden>v4는 실행 경로와 작업 소유권. 세션 오케스트레이션·장중 1분 레인·세션 비결속 분석 레인·배치 오케스트레이션과 배치 네 영역이 같은 소스·S3·원장을 나눠 쥐게 되자, 작업마다 단일 소유자를 원장에 못박았다. 구현한 경로와 확장 목표가 한 도면에 함께 그려져 있다.</p>
  <p data-version="v3" hidden>v3는 1분 주기와 상주 worker. 장전에 세션 플래너가 그날의 window 를 한 번 계획하고, 장중에는 상주 Price·News Worker 가 window 를 받아 처리하며 Outbox Relay 가 Job SQS 로 발행한다. 일 배치 원장 대신 session·window·job·outbox 를 담은 1분 원장을 새로 뒀다. 가격 소스의 토스증권 표기는 2026-08-01 실측 당시의 사실이다.</p>
  <p data-version="v2" hidden>v2는 수집과 분석의 분리. 뉴스 레인과 가격 레인을 나란히 두고 벤더별로 task 를 쪼갠 뒤, 그 사이에 SQS 두 개를 넣어 수집 성공과 분석 완료를 서로 다른 실행 단위로 갈랐다.</p>
  <p data-version="v1" hidden>v1은 하루 한 번의 분석. EventBridge 가 단일 Step Functions 를 깨우면 Ingest → Transform → Feature → 분석이 차례로 돌고, 각 단계는 ECS Fargate task 하나다. 저장소는 S3 raw(원문 덧붙임)·canonical(멱등 병합)·feature 로 갈랐다.</p>
</div>

!!! note "설계 뷰 — 발전 단계별 원본 도면 기준"
    각 판은 그 단계의 설계 시점을 그린 것이며, 현행 사실의 권위는 ops 작업 카탈로그·context 등 SSOT 에 있다.
    원본 drawio 는 레포 밖에서 판별로 관리되고, 이 뷰어들은 `pages/scripts/build_pipeline_diagram.py` 로 원본에서 다시 만든다.
    발전 과정의 서술은 [EDGE 데이터 파이프라인 아키텍처](https://jingi723.github.io/posts/edge-pipeline-architecture/) 글에 있다.

근거 문서(설계 뷰): [system-architecture.md](../reference/architecture/system-architecture.md) 의 파이프라인 절

---

[← 4 클라우드 아키텍처](4-cloud-architecture.md) · [목록](index.md)
