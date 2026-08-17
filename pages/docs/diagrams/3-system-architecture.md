# 3 시스템 아키텍처 (System Architecture)

데이터 수집 → 분석 → 게시 → 테넌트 팬아웃 → 스크리닝 → 서빙의 모듈 구성.

상자를 클릭하면 상위·하위 1단계가 함께 강조된다.

<p class="ep-diagram-meta">버전:
<select class="ep-version-select" aria-label="다이어그램 버전 선택">
  <option value="v2" selected>v2 (현재)</option>
  <option value="v1">v1</option>
</select> ·
<a class="ep-open-full" href="../interactive/3-sa.v2.html" data-href-v1="../interactive/3-sa.v1.html" target="_blank" rel="noopener">전체 화면으로 열기 ↗</a></p>

<div class="ep-diagram-frame">
  <iframe data-version="v2" src="../interactive/3-sa.v2.html" title="EDGE 시스템 아키텍처 다이어그램 v2 — 인터랙티브" loading="lazy"></iframe>
  <iframe data-version="v1" src="../interactive/3-sa.v1.html" title="EDGE 시스템 아키텍처 다이어그램 v1 — 인터랙티브" loading="lazy" hidden></iframe>
</div>

<div class="ep-version-note">
  <p data-version="v2">v2(v0.2)는 v1 의 도메인 수직 분해를 환경 분리 위에 다시 얹었다. 금융사 쪽은 Publication·검수·정책 중심의 서비스군과 Intake·Screening·Relay Worker 로, EDGE 쪽은 분석 파이프라인 중심으로 재편되고, 두 환경은 DMZ 의 Relay Worker → API Gateway 경로로만 통신한다.</p>
  <p data-version="v1" hidden>v1(v0.1)은 도메인별 마이크로서비스 분해다. Client → Gateway → Services 아래 도메인마다 Queue·Worker·Cache·Database 를 수직으로 세우고, 분석은 Scheduler 가 모는 Ingestion → Processing → Analysis Worker 열로 두었다.</p>
</div>

!!! note "설계 뷰 — 원본 v0.2 기준"
    이 다이어그램은 설계 시점(v0.2)의 시스템 구성이다. 현행 사실·계약의 권위는
    context·contracts 등 SSOT 에 있으며, 충돌 시 SSOT 가 우선한다.

근거 문서(설계 뷰): [system-architecture.md](../reference/architecture/system-architecture.md)

---

[← 2 애플리케이션 아키텍처](2-application-architecture.md) · [목록](index.md) · [다음 → 4 클라우드 아키텍처](4-cloud-architecture.md)
