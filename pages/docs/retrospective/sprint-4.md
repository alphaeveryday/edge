# Sprint 4 회고록

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
      <td>2026년 7월 15일</td>
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
      <td>얇은 E2E 슬라이스를 실데이터 파이프라인과 cloud/onprem 2아티팩트 아키텍처로 재편하고, 두 트랙의 계약 경계(Event Bundle·Tenant Sync API)를 확정한다.</td>
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
          <li>가용 시간을 고려한 스프린트 플래닝으로 이월 이슈가 적었음</li>
          <li>개발과 병행해 비즈니스 활동을 진행함 (유진투자증권 HTS 개발자 컨택)</li>
        </ul>
      </td>
      <td>
        <ul>
          <li>인프라 관리 방법이 여러 갈래로 나뉘어 있어 일관된 관리가 되지 않음</li>
          <li>팀원 간 코드 담당 경계를 명확히 하지 않아 파이프라인 개발에 딜레이가 생김</li>
          <li>팀이 합의한 명확한 기획이 없어 멘토님들의 의견을 취사선택할 기준이 없었음</li>
          <li>AI 산출물을 비판 없이 한 번에 수용해 결함을 뒤늦게 발견함</li>
        </ul>
      </td>
      <td>
        <ul>
          <li>팀이 공유하는 명확한 기획을 기준으로 멘토님들의 의견을 수용하거나 수용하지 않음</li>
          <li>AI 산출물은 한 번에 받아들이지 않고 다른 에이전트의 비판(교차 검증)을 거쳐 수용함</li>
        </ul>
      </td>
    </tr>
  </tbody>
</table>

<h2>✅ Action Item</h2>

<p class="retro-muted">회고에서 나온 내용을 다음 스프린트에 적용하기 위한 후속 Action Item이다.</p>

<div class="retro-actions">
  <ul>
    <li>팀이 공유하는 명확한 기획을 기준으로 멘토님들의 의견을 수용하거나 수용하지 않음</li>
    <li>AI 산출물은 한 번에 받아들이지 않고 다른 에이전트의 비판(교차 검증)을 거쳐 수용함</li>
  </ul>
</div>

</div>

??? note "이전 Action Item"
    [Sprint 3](sprint-3.md)에서 정한 Action Item의 이행 여부를 점검한다.

    <div class="retro-actions done">
      <ul>
        <li>퇴근 시간을 21시로 지키고, 하루에 산정한 이슈를 그날 안에 모두 처리</li>
        <li>한 가지 작업에 가용 시간을 정하고 그 이상은 쓰지 않음</li>
      </ul>
    </div>
