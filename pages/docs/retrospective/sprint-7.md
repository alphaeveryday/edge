# Sprint 7 회고록

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
      <td>2026년 8월 5일</td>
    </tr>
    <tr>
      <th>팀</th>
      <td>Alpha Everyday</td>
    </tr>
    <tr>
      <th>참여자</th>
      <td>김진기, 조영서, 정준영</td>
    </tr>
    <tr>
      <th>스프린트 골</th>
      <td>장중 1분 주기 파이프라인을 수집→트리거 판정→설명 생성→게시·회수까지 관통시키고, 게시 계약을 일 단위 교체 모델에서 실시간 스냅샷·무효화 단독 모델로 재편한다.</td>
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
          <li>GitHub repository ruleset을 설정하고, 설정 전까지는 머지 전 테스트 통과 여부를 직접 확인함</li>
          <li>멘토링 직후 피드백을 즉시 반영/추가 검토/반영하지 않음/확인 필요로 분류하고, 각 항목에 근거·담당자·기한을 기록함</li>
        </ul>
      </td>
      <td>
        <ul>
          <li>빠른 개발에 집중해 기술 부채를 관리하지 못했고, 그 여파로 배포에 문제가 발생함</li>
          <li>데일리 스크럼 진행이 미흡해 팀원별로 어떤 작업을 진행 중인지 공유되지 않음</li>
          <li>팀원 간 생각의 싱크를 맞추지 않고 개발을 진행해, 문제를 발견하기까지 걸리는 시간이 늘어남</li>
        </ul>
      </td>
      <td>
        <ul>
          <li>팀 내부 개발 프로세스를 따라 작은 PR 단위로 나누고, PR에 포함된 작업을 PR 안에서 검증함</li>
          <li>매일 9시 30분에 메신저로 스프린트 진행 알림이 가도록 스케줄링하고, 잘 보이는 곳에 포스트잇을 부착함</li>
          <li>개발에 착수하기 전, 구체화된 계획이 기존 기획과 방향이 다르면 ADR로 제안함</li>
        </ul>
      </td>
    </tr>
  </tbody>
</table>

<h2>✅ Action Item</h2>

<p class="retro-muted">회고에서 나온 내용을 다음 스프린트에 적용하기 위한 후속 Action Item이다.</p>

<div class="retro-actions">
  <ul>
    <li>팀 내부 개발 프로세스를 따라 작은 PR 단위로 나누고, PR에 포함된 작업을 PR 안에서 검증함</li>
    <li>매일 9시 30분에 메신저로 스프린트 진행 알림이 가도록 스케줄링하고, 잘 보이는 곳에 포스트잇을 부착함</li>
    <li>개발에 착수하기 전, 구체화된 계획이 기존 기획과 방향이 다르면 ADR로 제안함</li>
  </ul>
</div>

</div>

??? note "이전 Action Item"
    [Sprint 6](sprint-6.md)에서 정한 Action Item의 이행 여부를 점검한다.

    <div class="retro-actions done">
      <ul>
        <li>GitHub repository ruleset으로 테스트 통과를 머지 필수 조건으로 강제하고, 설정 전까지는 머지 전 체크 상태를 직접 확인함</li>
      </ul>
    </div>

    미달성 — 데일리 스크럼 진행 미흡으로 이번 스프린트 PROBLEM에 다시 올라왔고, TRY의 9시 30분 알림 스케줄링으로 재시도한다.

    <div class="retro-actions">
      <ul>
        <li>매일 아침 9시 30분에 데일리 스크럼을 고정 진행함 (진행 담당: 정준영)</li>
      </ul>
    </div>
