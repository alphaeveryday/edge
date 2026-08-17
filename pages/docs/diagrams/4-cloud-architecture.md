# 4 클라우드 아키텍처 (Cloud Architecture)

ECS 클러스터 분리·RDS·CloudFront 등 실 배포 토폴로지.

상자를 클릭하면 상위·하위 1단계가 함께 강조된다.

<p class="ep-diagram-meta">버전:
<select class="ep-version-select" aria-label="다이어그램 버전 선택">
  <option value="v2" selected>v2 (현재)</option>
  <option value="v1">v1</option>
</select> ·
<a class="ep-open-full" href="../interactive/4-ca.v2.html" data-href-v1="../interactive/4-ca.v1.html" target="_blank" rel="noopener">전체 화면으로 열기 ↗</a></p>

<div class="ep-diagram-frame">
  <iframe data-version="v2" src="../interactive/4-ca.v2.html" title="EDGE 클라우드 아키텍처 다이어그램 v2 — 인터랙티브" loading="lazy"></iframe>
  <iframe data-version="v1" src="../interactive/4-ca.v1.html" title="EDGE 클라우드 아키텍처 다이어그램 v1 — 인터랙티브" loading="lazy" hidden></iframe>
</div>

<div class="ep-version-note">
  <p data-version="v2">v2(v0.2)는 v1 의 AWS 단일 구성에 증권사 관리 환경(On-Prem)을 분리해 추가했다. 증권사 쪽은 채널·내부 통제·DB·DMZ 구역으로 나뉘어 DMZ 의 Relay Worker 만 아웃바운드로 AWS 와 통신하고, AWS 쪽은 파이프라인용·서빙용 ECS 클러스터로 갈라져 3계층 서브넷과 다중 AZ 이중화를 갖춘다.</p>
  <p data-version="v1" hidden>v1(v0.1)은 AWS 단일 환경 토폴로지다. 2개 AZ 의 VPC 에 ECS 서비스(Gateway·Widget·Tenant Console)와 RDS·ElastiCache 를 두고, 분석 파이프라인은 EventBridge 가 모는 Step Functions ECS 태스크 열로 그렸다. 증권사 관리 환경이 없는 이 Cloud-only 구조는 폐기된 방향이다.</p>
</div>

!!! note "설계 뷰 — 원본 v0.2 기준"
    이 다이어그램은 설계 시점(v0.2)의 클라우드 토폴로지다(다중 AZ 등 목표 구성 포함).
    현행 인프라의 권위는 infra/terraform 등 SSOT 에 있으며, 충돌 시 SSOT 가 우선한다.

근거 문서(설계 뷰): [cloud-architecture.md](../reference/architecture/cloud-architecture.md)

---

[← 3 시스템 아키텍처](3-system-architecture.md) · [목록](index.md)
