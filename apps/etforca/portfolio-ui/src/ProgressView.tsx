import { roles, titles, type Job } from './api';

const steps = ['요청 확인', '보유 구성 분석', '시장 전망 확인', '추천안 정리'];
export function ProgressView({ job, elapsed }: { job: Job | null; elapsed: number }) {
  const stage = job?.state === 'running' ? roles.indexOf(job.stage!) : -1;
  return <article className="step progress in">
    <span className="eyebrow">포트폴리오 분석</span>
    <h2>{job?.state === 'running' ? '포트폴리오를\n검토하고 있어요' : '분석을 기다리고 있어요'}</h2>
    <div className="research-motion" data-running={stage >= 0}>
      <svg viewBox="0 0 360 110" aria-hidden="true">
        <path className="research-track" d="M40 55H310" />
        {stage >= 0 && <path className="research-flow" d={`M40 55H${40 + stage * 90}`} />}
        {steps.map((_, i) => <g key={i} className={i === stage ? 'active' : i < stage ? 'complete' : ''}>
          <circle className="research-halo" cx={40 + i * 90} cy="55" r="24" />
          <circle cx={40 + i * 90} cy="55" r="16" />
          <text x={40 + i * 90} y="60" textAnchor="middle">{i < stage ? '✓' : i + 1}</text>
        </g>)}
      </svg>
      <p role="status">{stage >= 0 ? titles[roles[stage]] : '접수 완료 · 순서대로 분석합니다'}</p>
    </div>
    <ol className="progress-steps">{steps.map((label, i) => <li key={label} data-state={i < stage ? 'complete' : i === stage ? 'active' : 'waiting'} aria-current={i === stage ? 'step' : undefined}><span>{label}</span><small>{i < stage ? '완료' : i === stage ? '진행 중' : '대기'}</small></li>)}</ol>
    <p className="notice">보통 5분, 최대 10분 정도 걸려요.<br />화면을 닫아도 분석은 계속됩니다.</p>
    {job?.state === 'running' && <p className="notice">{Math.floor(elapsed / 60)}분 {elapsed % 60}초 경과</p>}
  </article>;
}
