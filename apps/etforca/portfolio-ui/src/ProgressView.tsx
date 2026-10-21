import { roles, titles, type Job } from './api';

export function ProgressView({ job, elapsed }: { job: Job | null; elapsed: number }) {
  const stage = job?.state === 'running' ? roles.indexOf(job.stage!) : -1;
  const card = (title: string, subtitle: string, index: number, path: string, mini = false) => <li className={mini ? 'mini' : 'wide'} data-s={stage < index ? 'wait' : stage === index ? 'run' : 'done'}><svg className="ic" viewBox="0 0 24 24" aria-hidden="true"><path d={path} /></svg><b>{title}</b><span>{subtitle}</span><em>{stage === index ? '검토 중' : stage > index ? '검토 완료' : '대기'}</em></li>;
  return <article className="step progress in">
    <span className="eyebrow">포트폴리오 분석</span>
    <h2>{job?.state === 'running' ? '포트폴리오를\n검토하고 있어요' : '분석을 기다리고 있어요'}</h2>
    <div className="research-motion" data-running={stage >= 0}>
      <svg viewBox="0 0 360 110" aria-hidden="true">
        <path className="research-track" d="M40 55H310" />
        {stage >= 0 && <path className="research-flow" d={`M40 55H${40 + stage * 90}`} />}
        {roles.map((role, i) => <g key={role} className={i === stage ? 'active' : i < stage ? 'complete' : ''}>
          <circle className="research-halo" cx={40 + i * 90} cy="55" r="24" />
          <circle cx={40 + i * 90} cy="55" r="16" />
          <text x={40 + i * 90} y="60" textAnchor="middle">{i < stage ? '✓' : i + 1}</text>
        </g>)}
      </svg>
      <p role="status">{stage >= 0 ? titles[roles[stage]] : '접수 완료 · 순서대로 분석합니다'}</p>
    </div>
    <ul className="vals agents tree" aria-label="분석 역할과 검토 흐름">
      {card('종목 분석 AI', 'ETF 안의 기업', 1, 'M16.5 10.5a6 6 0 1 1-12 0a6 6 0 1 1 12 0M15 15l5 5M8 12v-2M10.5 12V8M13 12v-3', true)}
      {card('산업 분석 AI', '산업의 흐름', 1, 'M3 20V10l6 3.5V10l6 3.5V5h4v15zM3 20h18', true)}
      {card('경제 분석 AI', '경제와 환율', 2, 'M20.5 12a8.5 8.5 0 1 1-17 0a8.5 8.5 0 1 1 17 0M3.5 12h17M12 3.5c3 3 3 14 0 17M12 3.5c-3 3-3 14 0 17', true)}
      <li className={'hand' + (stage >= 3 ? ' go' : '')} aria-hidden="true"><svg viewBox="0 0 300 40" preserveAspectRatio="none"><path d="M50 0V18H250V0M150 0V40" /></svg><span>분석 결과 전달</span></li>
      {card('포트폴리오 매니저', '분석을 모아 추천안을 만들어요', 3, 'M12 3.5v8.5h8.5A8.5 8.5 0 1 1 12 3.5zM15 3.9A8.5 8.5 0 0 1 20.1 9H15z')}
      <li className={'hand' + (stage >= 3 ? ' go' : '')} aria-hidden="true"><svg viewBox="0 0 300 40" preserveAspectRatio="none"><path d="M150 0V40" /></svg><span>추천안 검토</span></li>
      {card('리스크 매니저', '추천안의 위험과 놓친 점을 확인해요', 3, 'M12 3l7.5 3v5.5c0 4.6-3 8-7.5 9.5-4.5-1.5-7.5-4.9-7.5-9.5V6zM8.8 12l2.3 2.3 4.2-4.4')}
    </ul>
    <p className="notice">한 AI가 위 역할의 관점을 순서대로 검토해요.</p>
    <p className="notice">보통 5분, 최대 10분 정도 걸려요.<br />화면을 닫아도 분석은 계속됩니다.</p>
    {job?.state === 'running' && <p className="notice">{Math.floor(elapsed / 60)}분 {elapsed % 60}초 경과</p>}
  </article>;
}
