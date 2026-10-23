import { useState } from 'react';
import { Button } from './design-system/components';
import { roles, type Job } from './api';
import { trackResult } from './analytics';
import { Brand, Glyph, type GlyphName } from './flow';

// 결과 링크: 이 브라우저의 방문자 쿠키로만 열린다(분석은 방문자별로 묶여 있다). 탭을 닫거나 저장소가 지워져도 이 링크로 돌아온다.
export const resultLink = (id: string) => `${location.origin}${location.pathname}?analysis=${encodeURIComponent(id)}`;
const state = { wait: '대기', run: '검토 중', done: '완료' } as const;

// W1·W2: 역할 도식과 물어본 것. 한 AI가 다섯 역할의 관점을 차례로 검토한다(동시에 도는 에이전트가 아니다).
export function ProgressView({ job, elapsed, offline = false }: { job: Job | null; elapsed: number; offline?: boolean }) {
  const running = job?.state === 'running', stage = running ? roles.indexOf(job.stage!) : -1;
  const status = (index: number) => stage < index ? 'wait' : stage === index ? 'run' : 'done';
  const node = (title: string, sub: string, index: number, icon: GlyphName, wide = false) => <li className="fl-agent" data-wide={wide} data-s={status(index)}>
    <span className="fl-icontile"><Glyph name={icon} /></span><span className="fl-agent__text"><b>{title}</b><small>{sub}</small></span>
    <em>{status(index) === 'done' ? <Glyph name="check" size={14} /> : status(index) === 'run' ? <i className="ds-spinner" aria-hidden="true" /> : <i className="fl-dot" aria-hidden="true" />}{state[status(index)]}</em></li>;
  const count = (job?.input?.holdings ?? []).filter(h => h.kind !== 'cash').length;
  const [copied, setCopied] = useState<'' | 'done' | 'failed'>('');
  async function copy() {
    try { await navigator.clipboard.writeText(resultLink(job!.id)); setCopied('done'); trackResult('result_link_copied', { screen: job!.state === 'running' ? 'running' : 'queued' }); }
    catch { setCopied('failed'); } // 복사를 막은 브라우저에서는 링크를 그대로 보여 직접 복사하게 한다.
  }
  return <div className="fl progress" data-surface="plain">
    {offline && <p className="fl-offline" role="status"><Glyph name="alert" size={16} />연결이 끊겼어요 · 다시 연결되면 이어서 보여 드려요</p>}
    <Brand />
    <div className="fl-hero" data-align="center"><h1 aria-live="polite">{running ? count ? <>종목 {count}개를<br />나눠서 보고 있어요</> : <>추천 포트폴리오를<br />만들고 있어요</> : '순서를 기다리고 있어요'}</h1>
      <p>{running ? `${Math.floor(elapsed / 60)}분 ${elapsed % 60}초 지났어요 · 보통 5분쯤 걸려요` : '앞선 분석이 끝나면 바로 시작해요 · 보통 5분쯤 걸려요'}</p></div>
    <section className="fl-card" data-tone="soft">
      <ul className="fl-agents" aria-label="분석 역할과 검토 흐름">
        {node('종목 분석 AI', '종목 속 기업', 1, 'search')}{node('산업 분석 AI', '산업의 흐름', 1, 'factory')}{node('경제 분석 AI', '경제와 환율', 2, 'globe')}
        <li className="fl-hand" aria-hidden="true">분석 결과 전달</li>
        {node('포트폴리오 매니저', '분석을 모아 추천안을 만들어요', 3, 'briefcase', true)}
        <li className="fl-hand" aria-hidden="true">추천안 검토</li>
        {node('리스크 매니저', '추천안의 위험과 놓친 점을 살펴요', 3, 'shield', true)}
      </ul>
      <p className="fl-caption">AI 하나가 다섯 역할을 차례로 맡아 검토해요</p>
      {/* 단계가 바뀔 때만 읽어 준다(경과 초는 읽지 않는다). */}
      <p className="fl-sr" role="status">{running ? `${['질문 정리', '종목·산업 분석', '경제 분석', '추천안 작성과 검토'][Math.max(0, stage)]} 중` : '순서를 기다리는 중'}</p>
    </section>
    <section className="fl-asked" aria-label="물어보신 것"><p><Glyph name="message" size={16} />물어보신 것에 답하고 있어요</p><b>“{job?.input?.question?.trim() || '이대로 가도 될까요?'}”</b><small>결과 맨 위에서 이 질문에 먼저 답해 드려요</small></section>
    {job && <div className="fl-cta"><p className="fl-cta__hint">창을 닫아도 분석은 계속돼요</p>
      <Button className="fl-tertiary" onClick={() => void copy()}><Glyph name="link" size={18} />결과 링크 복사해 두기</Button>
      <p className="fl-cta__hint" role="status">{copied === 'done' ? '링크를 복사했어요. 이 브라우저에서 열면 결과로 바로 가요.' : copied === 'failed' ? <>복사하지 못했어요. 이 링크를 직접 복사해 주세요: <span className="fl-link">{resultLink(job.id)}</span></> : ''}</p></div>}
  </div>;
}
