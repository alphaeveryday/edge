import { Notice, Button } from './design-system/components';
import { useEffect, useRef, useState } from 'react';
import { request, ensureVisitorSession, type Input, type Job } from './api';
import { inputDraftKey, readInputDraft } from './inputDraft';
import { InputForm } from './InputForm';
import { ReportView } from './ReportView';
import { Followup } from './Followup';
import { ProgressView } from './ProgressView';
import { startPresence } from './presence';
import { trackScreen } from './analytics';
import { preview, jobKey, pendingKey } from './preview';

export function Analysis({ embedded = false, active = true }: { embedded?: boolean; active?: boolean }) {
  useEffect(startPresence, []);
  const [jobId, setJobId] = useState<string | null>(() => embedded ? null : preview ? (preview === 'input' ? null : 'preview') : (() => { try { return localStorage.getItem(jobKey); } catch { return null; } })());
  const [restoring, setRestoring] = useState(embedded);
  const [restoreAttempt, setRestoreAttempt] = useState(0);
  const pendingMemory = useRef<{ input: string; key: string } | null>(null);
  const [storageWarning, setStorageWarning] = useState('');
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    if (!embedded) return;
    let stopped = false;
    setRestoring(true); setError('');
    void ensureVisitorSession().then(() => request<Job | null>('/analyses/latest')).then(next => {
      if (!stopped) { setJob(next); setJobId(next?.id ?? null); setRestoring(false); }
    }).catch(e => { if (!stopped) setError((e as Error).message); });
    return () => { stopped = true; };
  }, [embedded, restoreAttempt]);
  const screen = !jobId ? null : !job ? 'loading' : job.state === 'succeeded' ? 'result' : job.state;
  useEffect(() => screen && active ? trackScreen(screen) : undefined, [screen, active]);
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
    let pending = pendingMemory.current;
    try { pending ??= JSON.parse(sessionStorage.getItem(pendingKey) || 'null'); } catch { /* Invalid local state is replaced. */ }
    const key = pending?.input === serialized ? pending.key : crypto.randomUUID();
    pendingMemory.current = { input: serialized, key };
    try { sessionStorage.setItem(pendingKey, JSON.stringify(pendingMemory.current)); } catch { /* Keep retries idempotent in memory when storage is unavailable. */ }
    try {
      await ensureVisitorSession();
      const next = await request<Job>('/analyses', { method: 'POST', headers: { 'Idempotency-Key': key }, body: serialized });
      try { const draft = readInputDraft(); if (draft?.draftId === input.draft_id) sessionStorage.setItem(inputDraftKey, JSON.stringify({ ...draft, submittedJobId: next.id })); } catch { /* Optional draft storage must not interrupt an accepted analysis. */ }
      try { localStorage.setItem(jobKey, next.id); } catch { setStorageWarning('이 브라우저에서는 분석 결과를 자동 복원할 수 없어요. 결과를 확인할 때까지 이 화면을 유지해 주세요.'); }
      pendingMemory.current = null;
      try { sessionStorage.removeItem(pendingKey); } catch { /* The accepted job remains available in this tab. */ }
      setJob(next); setJobId(next.id); window.scrollTo(0, 0);
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  function reset() { try { if (job?.state === 'succeeded' && readInputDraft()?.submittedJobId === jobId) sessionStorage.removeItem(inputDraftKey); } catch { /* Continue to input when browser storage is unavailable. */ } try { localStorage.removeItem(jobKey); } catch { /* Reset still works in this tab. */ } setJobId(null); setJob(null); setError(''); }
  const Container = embedded ? 'div' : 'main';
  if (restoring) return <section className="dashboard-card">{error ? <><Notice tone="error">{error}</Notice><Button onClick={() => setRestoreAttempt(n => n + 1)}>다시 불러오기</Button></> : <p role="status">최근 분석을 불러오고 있어요.</p>}</section>;
  return <Container className="app">{!embedded && <a className="dashboard-link" href={preview ? '?preview=dashboard' : '?view=dashboard'}>내 포트폴리오 ↗</a>}{embedded && jobId && <div className="card-heading"><h2>최근 AI 포폴 분석</h2><Button onClick={reset}>새로 분석하기</Button></div>}{jobId && <div className="nav"><div className="nav__pill"><span className="nav__logo">ETF Orca</span></div></div>}
    {storageWarning && <Notice>{storageWarning}</Notice>}
    {error && <Notice tone="error">{error}</Notice>}
    {!jobId ? <InputForm onSubmit={submit} busy={busy} /> : job?.state === 'succeeded' && job.result ? <><ReportView report={job.result} feedbackJobId={jobId} /><Followup jobId={jobId} /><Button variant="secondary" className="secondary" onClick={reset}>새 포트폴리오 분석하기</Button></> : job?.state === 'failed' ? <><article className="ans"><h1>분석을 마치지 못했어요.</h1><p>{job.failure === 'SEARCH_RATE_LIMIT' ? '외부 검색 서비스의 요청 제한으로 자료를 모두 확인하지 못했어요. 잠시 후 다시 시도해 주세요.' : job.failure === 'TIMED_OUT' ? '분석 또는 자료 조회의 제한 시간 안에 근거 확인을 마치지 못했어요. 전체 분석은 최대 10분까지 진행해요.' : '자료 조회 또는 결과 검증 중 문제가 생겼어요.'} 중단된 분석은 최종 추천으로 사용하지 마세요.</p><Button variant="primary" className="primary" onClick={reset}>다시 입력하기</Button></article>
      {job.result?.partial_report && Object.keys(job.result.partial_report.sections || {}).length > 0 && <details><summary>중단 전 작성된 분석 보기</summary><ReportView report={{ ...job.result.partial_report, recommendation: null,
        completion: { tasks: [], missing_research: [], ...job.result.partial_report.completion, status: 'partial',
          missing_inputs: [...(job.result.partial_report.completion?.missing_inputs || []), '분석이 중단되어 최종 검토가 끝나지 않았어요.'] } }} /></details>}
      <Followup jobId={jobId} recoveryOnly />
    </> : <><ProgressView job={job} elapsed={elapsed} />{error && <Button variant="secondary" className="secondary" onClick={reset}>입력 화면으로</Button>}</>}
  </Container>;
}
