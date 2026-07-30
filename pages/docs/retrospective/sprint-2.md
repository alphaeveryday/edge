# Sprint 2 회고록

<style>
.retro-page {
  max-width: 960px;
}
.retro-page h2 {
  margin-top: 2.2rem;
}
.retro-muted {
  color: #44546f;
}
.retro-info {
  display: flex;
  gap: 0.85rem;
  align-items: flex-start;
  margin: 1.2rem 0 1.6rem;
  padding: 0.9rem 1rem;
  border-radius: 2px;
  background: #deebff;
  color: #172b4d;
}
.retro-info-icon {
  display: inline-flex;
  width: 1.25rem;
  height: 1.25rem;
  align-items: center;
  justify-content: center;
  flex: 0 0 auto;
  border-radius: 999px;
  background: #0c66e4;
  color: #fff;
  font-weight: 700;
  line-height: 1;
}
.retro-page table {
  width: 100%;
  border-collapse: collapse;
  table-layout: fixed;
}
.retro-page th,
.retro-page td {
  border: 1px solid #dfe1e6;
  padding: 0.8rem;
  vertical-align: top;
}
.retro-board td {
  padding: 0.75rem 0.55rem;
}
.retro-meta th {
  width: 30%;
  background: #f4f5f7;
  color: #172b4d;
  text-align: left;
}
.retro-meta td {
  color: #44546f;
}
.retro-board th {
  color: #172b4d;
  text-align: left;
}
.retro-board .keep {
  background: #e6fcff;
}
.retro-board .problem {
  background: #ffebe6;
}
.retro-board .try {
  background: #e3fcef;
}
.retro-page .retro-board ul {
  margin: 0;
  padding-left: 0;
  list-style: none;
}
.retro-page .retro-board li {
  margin-left: 0;
  position: relative;
  padding-left: 0.9rem;
}
.retro-board li::before {
  content: "•";
  position: absolute;
  left: 0;
}
.retro-actions ul {
  margin: 0;
  padding-left: 1.1rem;
}
.retro-board li + li,
.retro-actions li + li {
  margin-top: 0.55rem;
}
.retro-actions li {
  list-style: none;
  position: relative;
  padding-left: 1.75rem;
}
.retro-actions li::before {
  content: "";
  position: absolute;
  left: 0;
  top: 0.2rem;
  display: inline-flex;
  width: 1rem;
  height: 1rem;
  align-items: center;
  justify-content: center;
  border: 1px solid #c1c7d0;
  border-radius: 2px;
  background: #fff;
  color: #0c66e4;
  font-size: 0.78rem;
  font-weight: 700;
  line-height: 1;
}
.retro-actions.done li::before {
  content: "✓";
}
</style>

<div class="retro-page">

<h2>📋 개요</h2>

<p class="retro-muted">
회고 플레이에 대한 설명을 따라 지난 작업을 돌아보고 개선의 기회를 식별한다.
</p>

<table class="retro-meta">
  <tbody>
    <tr>
      <th>날짜</th>
      <td>2026년 7월 1일</td>
    </tr>
    <tr>
      <th>팀</th>
      <td>Alpha Everyday</td>
    </tr>
    <tr>
      <th>참여자</th>
      <td>김진기, 조영서, 정준영</td>
    </tr>
  </tbody>
</table>

<h2>💭 KPT 회고</h2>

<div class="retro-info">
  <span class="retro-info-icon">i</span>
  <span>KEEP, PROBLEM, TRY 항목을 아래 표에 정리한다. 이 항목을 바탕으로 다음 스프린트에서 개선할 Action Item을 정한다.</span>
</div>

<table class="retro-board">
  <thead>
    <tr>
      <th class="keep">KEEP</th>
      <th class="problem">PROBLEM</th>
      <th class="try">TRY</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td>
        <ul>
          <li>화면 확정 전 팀이 함께 리뷰해 피드백을 빠르게 반영하고, AI로 시안을 빠르게 뽑아 반복 주기를 줄임</li>
          <li>스프린트 초반에 아키텍처를 먼저 공유해 트랙 간 인터페이스 이해를 맞춰 병렬 작업 충돌이 적었음</li>
          <li>개발 환경·설계를 스프린트 내 대부분 확정해 다음 스프린트 구현 착수 장벽을 낮춤</li>
        </ul>
      </td>
      <td>
        <ul>
          <li>데이터베이스 지식 부족으로 ERD 설계가 늦어져 블로커가 됨</li>
          <li>인프라 배포가 안 돼 통일된 실행 환경을 갖추지 못함</li>
          <li>착수 전 완료 기준을 불명확하게 잡아 시간 산정을 못 하고 스프린트 이슈를 완료하지 못함</li>
          <li>유저 가치 검증 없이 스토리를 만들어 우선순위 낮은 기능에 공수를 소모함</li>
          <li>각자 당일 작업 계획을 팀에 공유하지 않아 서로의 진행 방향이 보이지 않음</li>
          <li>데일리 스크럼을 꾸준히 쓰지 않아 한 일이 공유되지 않고, 이를 뒤늦게 확인하는 중복 비용이 발생함</li>
        </ul>
      </td>
      <td>
        <ul>
          <li>기획 멘토에게 유저 스토리 작성법을 물어봄</li>
          <li>데이터베이스 정규화 예제를 풀어 지식 공백을 메움</li>
          <li>중요한 기능은 개발 전 문제를 문서화(왜·어떻게)한 뒤 해결함</li>
          <li>이슈를 더 작은 서브태스크로 나누고 완료 기준을 명확히 함</li>
          <li>인프라 세팅을 먼저 끝내고 개발함</li>
          <li>데일리 스크럼을 매 영업일 작성함</li>
        </ul>
      </td>
    </tr>
  </tbody>
</table>

<h2>✅ Action Item</h2>

<p class="retro-muted">회고에서 나온 내용을 다음 스프린트에 적용하기 위한 후속 Action Item이다.</p>

<div class="retro-actions">
  <ul>
    <li>데일리 스크럼을 매 영업일 작성한다</li>
    <li>이슈를 더 작은 서브태스크로 나누고 완료 기준을 명확히 한다</li>
  </ul>
</div>

</div>

??? note "이전 Action Item"
    [Sprint 1](sprint-1.md)에서 정한 Action Item의 이행 여부를 점검한다.

    - ❌ 매일 데일리 전 Jira 상태 최신화 확인 (bulk close 0건) — 데일리 스크럼을 꾸준히 진행하지 못해 미달성
    - ✅ 플래닝에서 각자의 가용 시간을 산정해 커밋 SP에 반영
    - ✅ 모노레포 모듈 구조·브랜치 전략·공통 패키지 구성 확정 + README 문서화
