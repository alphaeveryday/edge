import { useEffect, useState } from 'react';
import { Button } from './design-system/components';
import { request, ensureVisitorSession, type Input, type Job } from './api';
import { finalRows, verdictKind, type Kind } from './resultMath';
import { trackScreen } from './analytics';
import { ExampleReport } from './ExampleReport';
import { Avatar, Brand, Glyph, Quota, won, type GlyphName } from './flow';
import type { Saved } from './workbook';
import type { InputDraft } from './inputDraft';
import { preview } from './preview';

type Change = { name: string; kind?: 'cash' | 'etf' | 'stock'; instrument_id?: string; before: number; target: number };
// 판정과 사고팔 줄은 결과 화면과 같은 규칙(resultMath)으로 정한다 — 시작 화면과 결과가 다른 판정을 내지 않게.
const verdictText: Record<Kind, string> = { keep: '이대로 가도 돼요', one: '하나만 바꾸면 돼요', rebuild: '다시 짜야 해요', start: '이렇게 시작하세요' };
export function verdictOf(job: Job) {
  const report = job.result!, rows = finalRows(report), kind = verdictKind(report, rows, job.input);
  return { judged: !!kind, headline: report.verdict?.headline || (kind ? verdictText[kind] : '지난 분석을 다시 볼 수 있어요'),
    changes: rows.filter(r => r.before !== null && r.target !== null && r.before !== r.target).slice(0, 3)
      .map<Change>(r => ({ name: r.name, instrument_id: r.id, kind: r.kind === 'cash' ? 'cash' : r.kind === 'stock' ? 'stock' : 'etf', before: r.before!, target: r.target! })) };
}
// 첫 낱말에 색을 얹어 판정이 먼저 읽히게 한다(시안: '하나만' 바꾸면 돼요).
export const Headline = ({ text, mark = true }: { text: string; mark?: boolean }) => { const [first, ...rest] = text.split(' '); return <p className="fl-verdict">{mark ? <em>{first}</em> : first}{rest.length ? ' ' + rest.join(' ') : ''}</p>; };
export function ChangeRow({ change }: { change: Change }) {
  const { before, target } = change;
  const action = !target ? ['sell', '전부 팔기'] : !before ? ['buy', '새로 사기'] : target > before ? ['buy', '더 사기'] : ['sell', '줄이기'];
  return <li className="fl-change"><Avatar holding={change} /><span className="fl-change__text"><b data-gone={!target}>{change.name}</b>
    <small>{won(before)} → <em data-trade={action[0]}>{won(target)}</em> <span data-trade={action[0]}>{action[1]}</span></small></span><b className="fl-change__amount" data-gone={!target}>{won(target)}</b></li>;
}
// 받게 될 결과를 입력 전에 결과와 같은 꼴로 보인다(합성 예시 — 실제 고객 자료가 아니다).
const example: Change[] = [{ name: 'ACE 글로벌반도체TOP4 Plus', kind: 'etf', instrument_id: 'example:sell', before: 2500000, target: 0 }, { name: 'TIGER 미국S&P500', kind: 'etf', instrument_id: 'example:buy', before: 0, target: 2500000 }];
const stepsGuide: [string, string][] = [['지금 가진 것', '갖고 있는 종목과 금액만'], ['앞으로의 계획', '매달 넣을 돈, 쓸 때, 계좌'], ['궁금한 점', '먼저 답해 줬으면 하는 것, 원하는 구성 · 선택']];
const promises: [GlyphName, string, string][] = [['lock', '계좌 연결 없이', '로그인 없이 종목과 금액만 적어요'], ['shield', '상품을 팔지 않아요', '분석만 해 드려요']];
export const dashboardHref = preview ? '?preview=dashboard' : '?view=dashboard';

const stepNames = ['지금 가진 것', '앞으로의 계획', '궁금한 점'];
export function Start({ draft, onBegin, onFresh, onImport, onOpen }: { draft: InputDraft | null; onBegin: (input?: Input) => void; onFresh: () => void; onImport: () => void; onOpen: (job: Job) => void }) {
  const [latest, setLatest] = useState<Job | null>(null);
  const [hasSaved, setHasSaved] = useState(false);
  useEffect(() => trackScreen('start'), []);
  useEffect(() => {
    let stopped = false;
    // 다시 온 방문자에게는 지난 판정을 먼저 보인다. 확인하지 못하면 처음 화면 그대로 둔다.
    void ensureVisitorSession().then(() => Promise.all([request<Job | null>('/analyses/latest').catch(() => null), request<Saved>('/workbook').catch(() => null)]))
      .then(([job, saved]) => { if (stopped) return; setLatest(job?.state === 'succeeded' && job.result ? job : null); setHasSaved(!!saved?.workbook?.assets?.length); })
      .catch(() => { /* 첫 화면은 서버 없이도 보인다. */ });
    return () => { stopped = true; };
  }, []);
  if (latest) {
    // 쓰던 입력이 있으면 지난 분석 입력으로 덮지 않고 그 입력을 잇는다(시안 S2 '작성하던 입력이 있어요').
    const restart = () => draft ? onBegin() : onBegin(latest.input);
    const { headline, changes, judged } = verdictOf(latest), day = latest.created_at ? new Date(latest.created_at) : null;
    return <div className="fl" data-surface="plain">
      <Brand><a className="fl-textlink" href={dashboardHref}>내 포트폴리오 <Glyph name="right" size={16} /></a></Brand>
      <div className="fl-hero"><h1>다시 오셨네요.<br />그동안 바뀐 게 있나요?</h1></div>
      <section className="fl-card fl-aside" data-tone="soft" aria-label="지난 분석">
        <div className="fl-card__head"><span className="fl-sublabel">지난 분석{day && ` · ${day.getMonth() + 1}월 ${day.getDate()}일`}</span><Button className="fl-textlink" onClick={() => onOpen(latest)}>다시 보기 <Glyph name="right" size={16} /></Button></div>
        <Headline text={headline} mark={judged} />
        {!!changes.length && <ul className="fl-changes">{changes.map(c => <ChangeRow key={c.instrument_id} change={c} />)}</ul>}
        <div className="fl-changed" role="group" aria-label="바꾸셨나요?"><b>바꾸셨나요?</b><Button className="fl-seg__item" onClick={restart}>바꿨어요</Button><Button className="fl-seg__item" onClick={() => onOpen(latest)}>아직이요</Button></div>
      </section>
      {draft && <section className="fl-card" data-tone="soft" aria-labelledby="draft-title"><div className="fl-draft"><Glyph name="info" /><div><h2 className="fl-card__title" id="draft-title">작성하던 입력이 있어요</h2>
        <p className="fl-card__desc">{draft.step + 1}단계 ‘{stepNames[Math.min(draft.step, 2)]}’까지 적었어요</p><Button className="fl-textlink" onClick={() => onBegin()}>이어서 쓰기 <Glyph name="right" size={16} /></Button></div></div></section>}
      <div className="fl-cta"><Quota /><Button variant="primary" className="fl-go" onClick={restart}>바뀐 포트폴리오로 다시 분석</Button>
        {hasSaved && <Button className="fl-tertiary" onClick={onImport}>저장한 포트폴리오로 분석</Button>}
        <p className="fl-cta__hint">같은 입력은 오늘 안에 다시 봐도 횟수가 줄지 않아요</p></div>
    </div>;
  }
  return <div className="fl" data-surface="plain">
    <Brand><span className="fl-pill">무료 · 가입 없이</span></Brand>
    <div className="fl-hero"><h1>내 포트폴리오,<br />이대로 가도 될까요?</h1><p>갖고 있는 종목과 금액을 넣으면 점수, 바꿀 것, 이번 달 넣을 곳까지 알려 드려요.</p></div>
    <section className="fl-card fl-aside" data-tone="soft" aria-labelledby="example-title">
      <div className="fl-card__head"><h2 className="fl-sublabel" id="example-title">이런 결과를 받아요</h2><ExampleReport /></div>
      <div className="fl-example"><p className="fl-quote">“이 구성으로 계속 들고 가도 돼요?”</p><Headline text="하나만 바꾸면 돼요" />
        <p className="fl-gain">지난 3년이었다면 <b>287만 원</b> 더 벌었어요</p>
        <ul className="fl-changes">{example.map(c => <ChangeRow key={c.instrument_id} change={c} />)}</ul></div>
    </section>
    <section className="fl-card" data-tone="soft" aria-labelledby="steps-title"><h2 className="fl-card__title" id="steps-title">세 단계면 끝나요</h2>
      <ol className="fl-steps">{stepsGuide.map(([title, sub], i) => <li key={title}><span aria-hidden="true">{i + 1}</span><b>{title}</b><small>{sub}</small></li>)}</ol></section>
    <ul className="fl-promises">{promises.map(([icon, title, sub]) => <li key={title}><span className="fl-icontile"><Glyph name={icon} /></span><span><b>{title}</b><small>{sub}</small></span></li>)}</ul>
    <div className="fl-cta"><Quota /><Button variant="primary" className="fl-go" onClick={() => onBegin()}>내 포트폴리오 넣기</Button><Button className="fl-plainbtn" onClick={onFresh}>아직 투자 전이에요</Button></div>
  </div>;
}
