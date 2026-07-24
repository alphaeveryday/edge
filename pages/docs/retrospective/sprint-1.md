# Sprint 1 회고록

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
      <td>2026년 6월 24일</td>
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
          <li>분석 트랙을 외부 의존 없이 독립적으로 진행하는 구조로 두어 집중도를 유지하고 계획 작업을 대부분 소화함</li>
          <li>스코프 이탈을 스프린트 중간에 즉시 감지해 작업물을 잃지 않고 Spike로 보존함</li>
          <li>레포지토리·공통 기반 셋업을 스프린트 초반에 몰아 처리해 후반을 개발에 집중함</li>
        </ul>
      </td>
      <td>
        <ul>
          <li>M2·M3 스토리가 플래닝 때 M1에 잘못 포함돼 스코프 경계가 모호했음</li>
          <li>예비군 등 개인 일정에 따른 가용 시간 감소를 캐파에 반영하지 않아 콘솔 트랙이 전부 이월됨</li>
          <li>분석 결과(FF5·SCM)를 위젯 E2E에 붙일 인터페이스가 없어 트랙 간 연동이 비었음</li>
          <li>이슈를 막판에 일괄 완료(bulk close) 처리해 번다운이 왜곡되고 진척 파악이 어려웠음</li>
          <li>스프린트 중 추가 스토리(약 12 SP)가 많아 계획 안정성이 떨어짐</li>
          <li>Admin Console 화면 설계·API 스펙을 확정하지 않은 채 개발에 들어가 착수가 계속 밀림</li>
          <li>프로젝트 초기 세팅(구조)이 안 된 채 개발에 들어가 코드 병합이 계속 밀림</li>
        </ul>
      </td>
      <td>
        <ul>
          <li>트랙 간 의존 작업(Gateway 스텁·인터페이스)을 스프린트 초반에 먼저 처리함</li>
          <li>플래닝에서 개인 일정을 캐파에서 명시적으로 차감함</li>
          <li>분석↔위젯 응답 계약을 공동 정의하는 세션을 잡음</li>
          <li>데일리마다 Jira 상태를 갱신하고 스프린트 말 bulk close를 하지 않음</li>
          <li>스프린트 중 추가 수용 기준(WIP 한도·버퍼)을 정함</li>
          <li>다음 스프린트 Week 1에 핵심 화면 와이어프레임을 확정한 뒤 구현에 착수함</li>
          <li>프로젝트 초기 세팅(모노레포 구조)을 먼저 끝내고 개발함</li>
        </ul>
      </td>
    </tr>
  </tbody>
</table>

<h2>✅ Action Item</h2>

<p class="retro-muted">회고에서 나온 내용을 다음 스프린트에 적용하기 위한 후속 Action Item이다.</p>

<div class="retro-actions">
  <ul>
    <li>매일 데일리 전에 팀원이 Jira 상태를 최신화했는지 확인한다 (스프린트 말 bulk close 0건)</li>
    <li>스프린트 플래닝에서 각자의 가용 시간을 산정해 커밋 SP에 반영한다</li>
    <li>모노레포 모듈 구조·브랜치 전략·공통 패키지 구성을 확정하고 README에 문서화한다</li>
  </ul>
</div>

</div>

??? note "이전 Action Item"
    첫 스프린트로, 추적할 이전 스프린트의 Action Item이 없다.
