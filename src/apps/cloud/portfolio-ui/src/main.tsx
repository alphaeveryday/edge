import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { request, type Input, type Job } from './api';
import { InputForm } from './InputForm';
import { ReportView } from './ReportView';
import { Followup } from './Followup';
import './styles.css';
import { ProgressView } from './ProgressView';

function App() {
  const [jobId, setJobId] = useState(() => localStorage.getItem('edge-portfolio-job'));
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    if (!jobId) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    let attempts = 0;
    async function poll() {
      try {
        const next = await request<Job>('/analyses/' + jobId);
        if (stopped) return;
        setJob(next); setError(''); attempts = 0;
        if (next.state === 'succeeded' || next.state === 'failed') return;
      } catch (e) { if (!stopped) setError((e as Error).message); attempts++; }
      if (!stopped) timer = setTimeout(poll, Math.min(10000, 1500 * 2 ** attempts));
    }
    void poll();
    return () => { stopped = true; clearTimeout(timer); };
  }, [jobId]);
  useEffect(() => {
    if (job?.state !== 'running' || !job.started_at) return;
    const tick = () => setElapsed(Math.max(0, Math.floor((Date.now() - new Date(job.started_at!).getTime()) / 1000)));
    tick(); const timer = setInterval(tick, 1000); return () => clearInterval(timer);
  }, [job?.state, job?.started_at]);
  async function submit(input: Input) {
    setBusy(true); setError('');
    const serialized = JSON.stringify(input);
    let pending: { input: string; key: string } | null = null;
    try { pending = JSON.parse(sessionStorage.getItem('edge-portfolio-pending') || 'null'); } catch { /* Invalid local state is replaced. */ }
    const key = pending?.input === serialized ? pending.key : crypto.randomUUID();
    sessionStorage.setItem('edge-portfolio-pending', JSON.stringify({ input: serialized, key }));
    try {
      await request('/session');
      const next = await request<Job>('/analyses', { method: 'POST', headers: { 'Idempotency-Key': key }, body: serialized });
      localStorage.setItem('edge-portfolio-job', next.id); sessionStorage.removeItem('edge-portfolio-pending');
      setJob(next); setJobId(next.id); window.scrollTo(0, 0);
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  function reset() { localStorage.removeItem('edge-portfolio-job'); setJobId(null); setJob(null); setError(''); }
  return <main className="app">{jobId && <div className="nav"><div className="nav__pill"><span className="nav__logo">ETF Orca</span></div></div>}
    {error && <p role="alert" className="error">{error}</p>}
    {!jobId ? <InputForm onSubmit={submit} busy={busy} /> : job?.state === 'succeeded' && job.result ? <><ReportView report={job.result} /><Followup jobId={jobId} /><button className="secondary" onClick={reset}>새 포트폴리오 분석하기</button></> : job?.state === 'failed' ? <article className="ans"><h1>분석을 마치지 못했어요.</h1><p>{job.failure === 'TIMED_OUT' ? '5분 안에 근거 확인을 마치지 못했어요.' : '자료 조회 또는 결과 검증 중 문제가 생겼어요.'} 확인되지 않은 결과는 보여드리지 않아요.</p><button className="primary" onClick={reset}>다시 입력하기</button></article> : <><ProgressView job={job} elapsed={elapsed} />{error && <button className="secondary" onClick={reset}>입력 화면으로</button>}</>}
  </main>;
}
createRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>);
