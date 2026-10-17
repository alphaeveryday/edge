import { useConfirmation } from './confirmation';
import { useState } from 'react';
import { Button, TextInput, Select, TextArea, Notice, SectionHeading, Icon, chartPalette } from './components';
import './catalog.css';

export function DesignCatalog() {
  const [selected, setSelected] = useState('금액');
  const [email, setEmail] = useState('');
  const [submitted, setSubmitted] = useState(false);
  const { ask, confirmation, restoreFocus } = useConfirmation();
  return <main className="ds-catalog">
    <header><span className="eyebrow">Orca UI · 1.0</span><h1>같은 기준으로 만드는<br />편안한 금융 경험</h1><p>운영 화면에서 사용하는 실제 컴포넌트와 상태입니다. 입력한 내용은 저장하거나 발송하지 않아요.</p></header>
    <nav aria-label="디자인 시스템 목차">{[['foundation','기본 규칙'],['controls','버튼·입력'],['patterns','정보·목록'],['states','상태·팝업'],['workflow','제작 절차']].map(([id,label])=><a key={id} href={'#'+id}>{label}</a>)}</nav>
    <section id="foundation"><SectionHeading title="의미가 같은 곳에는 같은 표현" description="색상, 글자, 간격, 곡률, 움직임은 공통 토큰에서 가져옵니다." />
      <div className="ds-swatches">{['primary','text','text-secondary','text-muted','buy','sell','success','warning'].map(name=><div key={name}><i style={{ background: `var(--color-${name})` }} /><b>{name}</b></div>)}</div>
      <div className="ds-type-specimen"><strong>25,586,400원</strong><h2>내 포트폴리오를 한눈에</h2><p>얼마를 보유하고, 무엇을 바꿀지 명확하게 보여줘요.</p><small>확인이 필요한 내용만 짧게 안내해요.</small></div>
      <p className="ds-catalog-note">본문 대비 4.5:1 · 조작 영역 44px 이상 · 키보드와 글자 확대 · 동작 줄이기 지원</p>
    </section>
    <section id="controls"><SectionHeading title="행동이 분명한 버튼과 입력" />
      <div className="ds-examples"><Button variant="primary" type="button">포트폴리오 저장하기</Button><Button variant="secondary" type="button">다시 확인하기</Button><Button variant="danger" type="button">기록 삭제하기</Button><Button variant="primary" loading type="button">저장 중</Button><Button variant="primary" disabled type="button">선택 후 계속하기</Button></div>
      <form onSubmit={e=>{e.preventDefault();setSubmitted(true);}}>
        <label htmlFor="ds-email">이메일 주소<TextInput id="ds-email" type="email" value={email} onChange={e=>{setEmail(e.target.value);setSubmitted(false);}} placeholder="name@example.com" required /></label>
        <label htmlFor="ds-account">계좌<Select id="ds-account"><option>전체 계좌</option><option>ISA</option><option>IRP</option></Select></label>
        <label htmlFor="ds-question">요청사항 · 선택<TextArea id="ds-question" placeholder="궁금한 점을 적어 주세요" /></label>
        <label htmlFor="ds-error">현재 평가금액<TextInput id="ds-error" value="" readOnly aria-invalid="true" aria-describedby="ds-error-message" /></label><p id="ds-error-message" className="ds-field-error">0보다 큰 금액을 입력해 주세요.</p>
        <Button variant="primary" type="submit">예시 입력 확인하기</Button>
        {submitted&&<Notice tone="success">입력 형식을 확인했어요. 저장하거나 메일을 보내지 않았어요.</Notice>}
      </form>
    </section>
    <section id="patterns"><SectionHeading title="숫자와 다음 행동을 먼저" />
      <div className="ds-segment" aria-label="표시 단위">{['금액','비중'].map(v=><Button key={v} type="button" aria-pressed={selected===v} onClick={()=>setSelected(v)}>{v}</Button>)}</div>
      <div className="ds-example-row"><span className="ds-asset-icon"><Icon name="trendChart" size={24} /></span><span><b>미국시장 ETF 예시</b><small>ISA · 주식</small></span><span><b>{selected==='금액'?'6,880,000원':'26.9%'}</b><small className="gain">+877,000원</small></span></div>
      <div className="ds-chart-example" aria-label="예시 자산 구성">{chartPalette.map((color,i)=><span key={color} style={{background:color,flex:6-i}} />)}</div>
      <p className="ds-catalog-note">차트에는 범례와 실제 수치가 함께 있어야 해요. 매수·매도는 색상과 문구를 함께 표시해요.</p>
    </section>
    <section id="states"><SectionHeading title="기다림과 실패에도 같은 기준" />
      <Notice>입력한 내용을 확인하고 있어요.</Notice><Notice tone="error">저장하지 못했어요. 입력한 내용은 그대로예요. 다시 시도해 주세요.</Notice><Notice tone="success">포트폴리오를 저장했어요.</Notice>
      <div className="ds-empty"><Icon name="dashboard" size={36} /><h3>아직 보유 종목이 없어요</h3><p>종목을 추가하면 자산과 배당을 볼 수 있어요.</p></div>
      <Button variant="secondary" type="button" onClick={async()=>{await ask('이 내용을 저장할까요?', '확인');restoreFocus();}}>팝업 열어보기</Button>
      {confirmation}
    </section>
    <section id="workflow"><SectionHeading title="다음 화면도 같은 방식으로" /><ol><li>사용자 과제와 다음 행동을 정해요.</li><li>기존 컴포넌트와 모든 상태를 확인해요.</li><li>공통 토큰으로 모바일·PC 화면을 만들어요.</li><li>접근성·문구·데이터·회귀 검사를 통과해요.</li><li>PR 검토 후 배포하고 실제 화면을 확인해요.</li></ol><div className="ds-examples"><a href="?preview=input">입력 화면 ↗</a><a href="?preview=result">분석 결과 ↗</a><a href="?preview=dashboard">대시보드 ↗</a></div></section>
  </main>;
}
