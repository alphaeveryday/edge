# Sprint 3 회고록

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
      <td>2026년 7월 8일</td>
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
          <li>매일 다 같이 일하는 시간을 맞춰 작업해 블로커를 빠르게 논의하고 결정함</li>
          <li>하네스를 구축해 반복되는 작업을 자동화하고, 절약한 시간을 핵심 작업에 집중함</li>
          <li>데일리 스크럼을 진행해 서로의 진행 상황을 공유하고, 진행 방향이 어긋나거나 작업이 중복되는 것을 줄임</li>
        </ul>
      </td>
      <td>
        <ul>
          <li>스프린트 플래닝에서 작업 우선순위를 고려하지 않아 블로커가 생겨 스프린트를 완료하지 못함</li>
          <li>개발 시간과 설계 시간을 분배하지 못해, 산정한 이슈를 다 처리하지 못하고 이월함</li>
          <li>빈번히 퇴근 시간이 늦어져 작업 효율이 떨어짐</li>
        </ul>
      </td>
      <td>
        <ul>
          <li>스프린트 플래닝에 설계 이슈와 개발 이슈를 함께 산정함</li>
          <li>한 가지 작업에 가용 시간을 정하고 그 이상은 쓰지 않음</li>
          <li>퇴근 시간을 21시로 지키고, 하루에 산정한 이슈를 그날 안에 모두 처리함</li>
        </ul>
      </td>
    </tr>
  </tbody>
</table>

<h2>✅ Action Item</h2>

<p class="retro-muted">회고에서 나온 내용을 다음 스프린트에 적용하기 위한 후속 Action Item이다.</p>

<div class="retro-actions">
  <ul>
    <li>퇴근 시간을 21시로 지키고, 하루에 산정한 이슈를 그날 안에 모두 처리한다</li>
    <li>한 가지 작업에 가용 시간을 정하고 그 이상은 쓰지 않는다</li>
  </ul>
</div>

</div>

??? note "이전 Action Item"
    [Sprint 2](sprint-2.md)에서 정한 Action Item의 이행 여부를 점검한다.

    <div class="retro-actions done">
      <ul>
        <li>데일리 스크럼 작성</li>
        <li>이슈를 더 작은 서브테스크로 나누고, 완료 기준을 명확히 한다</li>
      </ul>
    </div>
