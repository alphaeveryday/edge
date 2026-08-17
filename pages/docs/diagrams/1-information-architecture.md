# 1 정보 구조 (Information Architecture)

콘솔·위젯 등 사용자 표면의 정보 구조. 상자를 클릭하면 상위·하위 1단계가 함께 강조된다.

<p class="ep-diagram-meta">버전:
<select class="ep-version-select" aria-label="다이어그램 버전 선택">
  <option value="v2" selected>v2 (현재)</option>
  <option value="v1">v1</option>
</select> ·
<a class="ep-open-full" href="../interactive/1-ia.v2.html" data-href-v1="../interactive/1-ia.v1.html" target="_blank" rel="noopener">전체 화면으로 열기 ↗</a></p>

<div class="ep-diagram-frame">
  <iframe data-version="v2" src="../interactive/1-ia.v2.html" title="EDGE 정보 구조 다이어그램 v2 — 인터랙티브" loading="lazy"></iframe>
  <iframe data-version="v1" src="../interactive/1-ia.v1.html" title="EDGE 정보 구조 다이어그램 v1 — 인터랙티브" loading="lazy" hidden></iframe>
</div>

<div class="ep-version-note">
  <p data-version="v2">v2(v0.2)는 v1 의 단일 콘솔을 슈퍼 어드민 콘솔과 고객사 운영 콘솔로 분리했다. 하이브리드 전환으로 검수·제공 관리가 고객사 몫이 되면서 고객사 콘솔은 가격 변동 설명의 검수·기준 관리 중심으로, 슈퍼 어드민 콘솔은 테넌트·분석 파이프라인 운영 중심으로 재편됐고, 위젯은 종목별 가격 변동 설명 하나로 수렴했다.</p>
  <p data-version="v1" hidden>v1(v0.1)은 단일 SaaS 콘솔을 전제로 그린 정보 구조다. EDGE Console 아래 인증/온보딩과 조직(고객사) 콘솔이 나란히 놓이고 — 조직 콘솔이 대시보드·애플리케이션·컴플라이언스·구성원·설정을 담는다 — 위젯은 종목 분석 정보 화면 하나로 두었다. 이 Cloud-only 구조는 폐기된 방향이다.</p>
</div>

!!! note "설계 뷰 — 원본 v0.2 기준"
    이 다이어그램은 설계 시점(v0.2)의 정보 구조다. 현행 화면·메뉴의 권위는
    [콘솔 IA SSOT](../reference/console-ia/super-admin-console.md)에 있으며, 충돌 시 SSOT 가 우선한다.

근거 문서(설계 뷰): [information-architecture.md](../reference/architecture/information-architecture.md)

---

[목록](index.md) · [다음 → 2 애플리케이션 아키텍처](2-application-architecture.md)
