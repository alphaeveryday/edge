import { useEffect, useRef } from 'react';
import { roles, titles, type Job } from './api';
import { startGraph } from './progressGraph';

export function ProgressView({ job, elapsed }: { job: Job | null; elapsed: number }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  useEffect(() => startGraph(canvas.current!), []);
  const stage = job?.state === 'running' ? roles.indexOf(job.stage!) : -1;
  const card = (title: string, subtitle: string, index: number, path: string, mini = false) => <li className={mini ? 'mini' : 'wide'} data-s={stage < index ? 'wait' : stage === index ? 'run' : 'done'}><svg className="ic" viewBox="0 0 24 24" aria-hidden="true"><path d={path} /></svg><b>{mini ? <>{title.replace(' 분석 AI', '')}<br />분석 AI</> : title}</b><span>{subtitle}</span><em>{stage === index ? '검토 중' : stage > index ? '검토 완료' : ''}</em></li>;
  return <article className="step progress in">
    <span className="eyebrow">준비 끝</span><h2>전문가 AI가<br /><em>내 구성</em>을 봐요</h2>
    <div className="stage"><canvas ref={canvas} aria-hidden="true" /><p className="stage__t" role="status">{job?.state === 'running' && job.stage ? titles[job.stage] : '분석 준비 중'}</p></div>
    <p className="notice">약 5분 정도 걸리며, 최대 10분까지 분석해요.<br />나갔다 들어와도 분석이 계속돼요.</p>
    <ul className="vals agents tree">
      {card('개별 종목 분석 AI', 'ETF 안의 회사', 1, 'M16.5 10.5a6 6 0 1 1-12 0a6 6 0 1 1 12 0M15 15l5 5M8 12v-2M10.5 12V8M13 12v-3', true)}
      {card('산업 분석 AI', '실려 있는 산업', 1, 'M3 20V10l6 3.5V10l6 3.5V5h4v15zM3 20h18', true)}
      {card('경제 상황 분석 AI', '나라와 통화', 2, 'M20.5 12a8.5 8.5 0 1 1-17 0a8.5 8.5 0 1 1 17 0M3.5 12h17M12 3.5c3 3 3 14 0 17M12 3.5c-3 3-3 14 0 17', true)}
      <li className={'hand' + (stage >= 3 ? ' go' : '')}><svg viewBox="0 0 300 40" preserveAspectRatio="none" aria-hidden="true"><path d="M50 0V18H250V0M150 0V40" /></svg><span>분석 결과 전달</span></li>
      {card('포트폴리오 매니저', '세 분석을 모아 결론을 내요', 3, 'M12 3.5v8.5h8.5A8.5 8.5 0 1 1 12 3.5zM15 3.9A8.5 8.5 0 0 1 20.1 9H15z')}
      <li className="hand"><svg viewBox="0 0 300 40" preserveAspectRatio="none" aria-hidden="true"><path d="M150 0V40" /></svg><span>결론 전달</span></li>
      {card('리스크 매니저', '결론에 반대 의견을 내고 흠을 찾아요', 3, 'M12 3l7.5 3v5.5c0 4.6-3 8-7.5 9.5-4.5-1.5-7.5-4.9-7.5-9.5V6zM8.8 12l2.3 2.3 4.2-4.4')}
    </ul>
    <p className="notice">한 AI가 위 관점들을 순서대로 검토해요. 대기 요청이 있으면 조금 더 걸릴 수 있어요.</p>
    {job?.state === 'running' && <p className="notice">{Math.floor(elapsed / 60)}분 {elapsed % 60}초 경과</p>}
  </article>;
}
