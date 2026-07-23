# Sprint 5 회고록

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
      <td>2026년 7월 22일</td>
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
      <td>수집→정제→분석 전 경로를 하나의 파이프라인으로 통합해, 다중 ETF 설명을 매일 자동으로 산출·적재하는 end-to-end 자동화를 완성한다.</td>
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
          <li>가용 시간을 먼저 확인하고 우선순위·작업량을 조정해 계획한 핵심 이슈를 스프린트 내에 완료함</li>
          <li>개발과 병행해 비즈니스 활동을 진행함 (컨퍼런스 참여로 도메인 지식 습득·현업자 컨택)</li>
        </ul>
      </td>
      <td>
        <ul>
          <li>식사가 불규칙한 날에는 허기·피로로 집중력이 떨어지고 작업 흐름이 끊김</li>
          <li>주장·평가 기준을 먼저 세우지 않고 AI에 생성을 맡겨, 제안을 반복 수정하느라 직접 구조를 잡는 것보다 시간이 더 들었음</li>
          <li>멘토링 직후 피드백의 의도·담당자·기한을 정리하지 않아, 나중에 배경을 다시 복원하고 재논의하느라 합의가 길어짐</li>
        </ul>
      </td>
      <td>
        <ul>
          <li>점심 11:00~12:00·저녁 18:00~19:00을 식사 시간으로 캘린더에 확보하고, 못 지키면 대체 시간을 미리 정함</li>
          <li>AI에 요청하기 전 핵심 주장·평가 기준을 먼저 작성하고, 근거를 설명하고 반론에 답할 수 있는 제안만 채택함</li>
          <li>멘토링 종료 직후 피드백을 즉시 반영/추가 검토/반영하지 않음/확인 필요로 분류하고, 각 항목에 근거·담당자·기한을 기록함</li>
        </ul>
      </td>
    </tr>
  </tbody>
</table>

<h2>✅ Action Item</h2>

<p class="retro-muted">회고에서 나온 내용을 다음 스프린트에 적용하기 위한 후속 Action Item이다.</p>

<div class="retro-actions">
  <ul>
    <li>점심 11:00~12:00·저녁 18:00~19:00을 캘린더에 식사 시간으로 확보하고, 못 지키면 대체 식사 시간을 미리 정함</li>
    <li>AI에 요청하기 전 핵심 주장·평가 기준을 먼저 작성하고, 설명·반론이 가능한 제안만 채택함</li>
    <li>멘토링 직후 피드백을 즉시 반영/추가 검토/반영하지 않음/확인 필요로 분류하고 근거·담당자·기한을 기록함</li>
  </ul>
</div>

</div>

??? note "이전 Action Item"
    [Sprint 4](sprint-4.md)에서 정한 Action Item의 이행 여부를 점검한다.

    <div class="retro-actions done">
      <ul>
        <li>팀이 공유하는 명확한 기획을 기준으로 멘토님들의 의견을 수용하거나 수용하지 않음</li>
        <li>AI 산출물은 한 번에 받아들이지 않고 다른 에이전트의 비판(교차 검증)을 거쳐 수용함</li>
      </ul>
    </div>
