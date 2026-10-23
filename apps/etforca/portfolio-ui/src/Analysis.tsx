import { Notice, Button, Loading } from './design-system/components';
import { useEffect, useRef, useState } from 'react';
import { request, ensureVisitorSession, type Input, type Job } from './api';
import { inputDraftKey, readInputDraft, restoreInputDraft } from './inputDraft';
import { InputForm } from './InputForm';
import { ReportView } from './ReportView';
import { FailedView, ResultView } from './ResultView';
import { Followup } from './Followup';
import { ProgressView } from './ProgressView';
import { startPresence } from './presence';
import { trackScreen } from './analytics';
import { preview, jobKey, pendingKey } from './preview';
import { useConfirmation } from './design-system/confirmation';
import type { Saved } from './workbook';

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
  const [pendingInput, setPendingInput] = useState<Input | null>(null);
  const [rejectedInput, setRejectedInput] = useState<Input | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const { ask, confirmation } = useConfirmation();
  // 처음 온 방문자는 저장한 포트폴리오도 분석도 없어 대시보드가 비어 있다 — 첫 행동 대신 빈 화면으로 빠지지 않게 한 번 묻는다.
  const checkingDashboard = useRef(false);
  async function openDashboard(e: React.MouseEvent<HTMLAnchorElement>) {
    if (jobId || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return; // 새 탭 열기 등은 그대로 둔다
    e.preventDefault();
    if (checkingDashboard.current) return;
    checkingDashboard.current = true;
    const link = e.currentTarget, href = link.href;
    let empty = false;
    try {
      // 대시보드에 보일 것(저장한 보유·최근 분석)이 하나라도 있으면 묻지 않는다. 3초 안에 확인하지 못해도 이동을 막지 않는다.
      empty = await Promise.race([
        ensureVisitorSession().then(() => Promise.all([request<Saved>('/workbook'), request<Job | null>('/analyses/latest')]))
          .then(([saved, latest]) => !saved.workbook?.assets?.length && !latest),
        new Promise<boolean>(done => setTimeout(() => done(false), 3000))]);
    } catch { /* 확인하지 못하면 이동을 막지 않는다. */ }
    checkingDashboard.current = false;
    if (!empty || await ask('아직 처음이라 저장된 포트폴리오가 없어요. 그래도 내 포트폴리오 대시보드로 갈까요?', '대시보드로 가기', link)) location.href = href;
  }
  const pendingRestored = useRef(false);
  useEffect(() => {
    if (pendingRestored.current) return;
    try {
      const pending = JSON.parse(sessionStorage.getItem(pendingKey) || 'null');
      const input = pending && JSON.parse(pending.input);
      if (typeof pending?.key === 'string' && Array.isArray(input?.holdings) && typeof input.horizon === 'string') {
        pendingRestored.current = true;
        void submit(input);
      }
    } catch { /* Malformed browser state must not submit an analysis. */ }
  }, []);
  useEffect(() => {
    if (!embedded) return;
    if (pendingRestored.current) { setRestoring(false); return; }
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
    setBusy(true); setError(''); setPendingInput(input); setRejectedInput(null); setJobId(null); setJob(null);
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
      setJob(next); setJobId(next.id); setPendingInput(null); window.scrollTo(0, 0);
    } catch (e) {
      setError((e as Error).message);
      const status = (e as { status?: number }).status;
      if (status && status >= 400 && status < 500 && status !== 408) {
        setRejectedInput(input); setPendingInput(null); pendingMemory.current = null;
        try { restoreInputDraft(input); sessionStorage.removeItem(pendingKey); } catch { /* Keep editable input in memory. */ }
        try { localStorage.removeItem(jobKey); } catch { /* Current tab remains editable. */ }
      }
    } finally { setBusy(false); }
  }
  function retry() {
    if (!job?.input) return;
    const input = { ...job.input };
    delete input.draft_id; // Editable drafts expire independently of the immutable request.
    void submit(input);
  }
  function reset() {
    setRejectedInput(job?.state === 'failed' ? job.input ?? null : null);
    if (job?.state === 'failed' && job.input) {
      try { restoreInputDraft(job.input); }
      catch { /* The server input remains editable in this tab even without draft storage. */ }
    }
    try {
      if (job?.state === 'succeeded' && readInputDraft()?.submittedJobId === jobId) sessionStorage.removeItem(inputDraftKey);
      sessionStorage.removeItem(pendingKey);
    } catch { /* Optional cleanup must not trap the visitor on a completed report. */ }
    pendingMemory.current = null;
    try { localStorage.removeItem(jobKey); } catch { /* Reset still works in this tab. */ }
    setJobId(null); setJob(null); setError('');
  }
  const Container = embedded ? 'div' : 'main';
  if (pendingInput) return <Container className="app"><article className="ans"><h1>분석 요청을 확인하고 있어요.</h1><p>입력한 내용은 보관돼 있어요. 접수 여부를 다시 확인해도 같은 요청이 중복되지 않아요.</p>{error && <Notice tone="error">{error}</Notice>}{busy ? <Loading>접수 확인 중</Loading> : <Button variant="primary" onClick={() => void submit(pendingInput)}>접수 다시 확인</Button>}</article></Container>;
  if (restoring) return <section className="dashboard-card">{error ? <><Notice tone="error">{error}</Notice><Button onClick={() => setRestoreAttempt(n => n + 1)}>다시 불러오기</Button></> : <Loading>최근 분석을 불러오고 있어요.</Loading>}</section>;
  return <Container className="app">{!embedded && <a className="dashboard-link" href={preview ? '?preview=dashboard' : '?view=dashboard'} onClick={e => void openDashboard(e)}>내 포트폴리오 ↗</a>}{confirmation}{embedded && jobId && <div className="card-heading"><h2>최근 AI 포폴 분석</h2><Button onClick={reset}>새로 분석하기</Button></div>}{jobId && <div className="nav"><div className="nav__pill"><span className="nav__logo">ETF Orca</span></div></div>}
    {storageWarning && <Notice>{storageWarning}</Notice>}
    {error && <Notice tone="error">{error}</Notice>}
    {!jobId ? <InputForm onSubmit={submit} busy={busy} initialInput={rejectedInput} /> : job?.state === 'succeeded' && job.result ? <><ResultView report={job.result} jobId={jobId} input={job.input} active={active} /><Button variant="secondary" className="secondary" onClick={reset}>새 포트폴리오 분석하기</Button></> : job?.state === 'failed' ? <>
      <FailedView failure={job.failure} partial={job.result?.partial_report} canRetry={!!job.input} busy={busy} onRetry={retry} onEdit={reset}>
        {job.result?.partial_report && Object.keys(job.result.partial_report.sections || {}).length > 0 && <ReportView report={{ ...job.result.partial_report, recommendation: null,
          completion: { tasks: [], missing_research: [], ...job.result.partial_report.completion, status: 'partial',
            missing_inputs: [...(job.result.partial_report.completion?.missing_inputs || []), '분석이 중단되어 최종 검토가 끝나지 않았어요.'] } }} />}
      </FailedView>
      <Followup jobId={jobId} />
    </> :<><ProgressView job={job} elapsed={elapsed} />{error && <Button variant="secondary" className="secondary" onClick={reset}>입력 화면으로</Button>}</>}
  </Container>;
}
