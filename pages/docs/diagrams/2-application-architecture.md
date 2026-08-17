# 2 애플리케이션 아키텍처 (Application Architecture)

cloud/onprem 하이브리드 — 클라우드에서 만들고 증권사 통제 환경에서 서빙하는 구조.

상자를 클릭하면 상위·하위 1단계가 함께 강조된다.

<p class="ep-diagram-meta">버전:
<select class="ep-version-select" aria-label="다이어그램 버전 선택">
  <option value="v2" selected>v2 (현재)</option>
  <option value="v1">v1</option>
</select> ·
<a class="ep-open-full" href="../interactive/2-aa.v2.html" data-href-v1="../interactive/2-aa.v1.html" target="_blank" rel="noopener">전체 화면으로 열기 ↗</a></p>

<div class="ep-diagram-frame">
  <iframe data-version="v2" src="../interactive/2-aa.v2.html" title="EDGE 애플리케이션 아키텍처 다이어그램 v2 — 인터랙티브" loading="lazy"></iframe>
  <iframe data-version="v1" src="../interactive/2-aa.v1.html" title="EDGE 애플리케이션 아키텍처 다이어그램 v1 — 인터랙티브" loading="lazy" hidden></iframe>
</div>

<div class="ep-version-note">
  <p data-version="v2">v2(v0.2)는 v1 의 단일 환경을 금융사 통제 환경과 EDGE 환경으로 분리했다. 고객 데이터는 금융사 밖으로 나가지 않고, EDGE 가 생성한 분석이 DMZ 의 수집 중계를 통해 금융사 안으로 반입(pull)되며, 위젯도 벤더 클라우드를 직접 호출하지 않고 금융사 내부 서빙만 탄다.</p>
  <p data-version="v1" hidden>v1(v0.1)은 단일 클라우드 SaaS 로 그린 애플리케이션 구성이다. 콘솔 UI 7종이 도메인별 API 로 배선되고, 데이터 수집·처리와 종목 분석 엔진·AI 모듈이 같은 환경 안에서 위젯 화면까지 이어진다.</p>
</div>

!!! note "설계 뷰 — 원본 v0.2 기준"
    이 다이어그램은 설계 시점(v0.2)의 애플리케이션 구성이다. 현행 사실·계약의 권위는
    context·contracts 등 SSOT 에 있으며, 충돌 시 SSOT 가 우선한다.

근거 문서(설계 뷰): [application-architecture.md](../reference/architecture/application-architecture.md)

---

[← 1 정보 구조](1-information-architecture.md) · [목록](index.md) · [다음 → 3 시스템 아키텍처](3-system-architecture.md)
