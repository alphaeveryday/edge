import { Notice, Button, Loading } from './design-system/components';
import { useEffect, useRef, useState } from 'react';
import { request, ensureVisitorSession, type Input, type Job } from './api';
import { inputDraftKey, readInputDraft, restoreInputDraft } from './inputDraft';
import { InputForm } from './InputForm';
import { ReportView } from './ReportView';
import { FailedView, ResultView } from './ResultView';
import { InputSave } from './InputSave';
import { Followup } from './Followup';
import { ProgressView } from './ProgressView';
import { Start } from './Start';
import { LimitScreen, LostScreen, RateScreen } from './Blocked';
import { startPresence } from './presence';
import { trackScreen } from './analytics';
import { preview, jobKey, pendingKey } from './preview';

// 결과 링크(?analysis=id)로 들어오면 그 분석을 연다. 주소에서는 지워 새로 고침·뒤로 가기가 같은 분석에 묶이지 않게 한다.
const linked = new URLSearchParams(location.search).get('analysis');
const limits = ['REQUEST_LIMIT', 'QUEUE_FULL', 'DAILY_LIMIT'];

export function Analysis({ embedded = false, active = true }: { embedded?: boolean; active?: boolean }) {
  useEffect(startPresence, []);
  const [jobId, setJobId] = useState<string | null>(() => embedded ? null : linked && /^[\w-]{1,64}$/.test(linked) ? linked : preview ? (['input', 'return'].includes(preview) ? null : 'preview') : (() => { try { return localStorage.getItem(jobKey); } catch { return null; } })());
  useEffect(() => {
    if (embedded || !linked) return;
    const url = new URL(location.href); url.searchParams.delete('analysis'); history.replaceState(null, '', url);
    if (jobId === linked) try { localStorage.setItem(jobKey, linked); } catch { /* The linked analysis still opens in this tab. */ }
  }, []);
  const [restoring, setRestoring] = useState(embedded);
  const [restoreAttempt, setRestoreAttempt] = useState(0);
  const pendingMemory = useRef<{ input: string; key: string } | null>(null);
  const [storageWarning, setStorageWarning] = useState('');
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState('');
  const [offline, setOffline] = useState(false);
  const [lost, setLost] = useState(false);
  const [busy, setBusy] = useState(false);
  const [pendingInput, setPendingInput] = useState<Input | null>(null);
  const [rejectedInput, setRejectedInput] = useState<Input | null>(null);
  const [blocked, setBlocked] = useState('');
  // 시작 화면(S1·S2)은 쓰던 입력이 없을 때만 보인다. 새로 고침은 쓰던 단계로 바로 돌아간다.
  const [entered, setEntered] = useState(() => embedded || !!readInputDraft());
  const [begin, setBegin] = useState<{ input?: Input; start?: 'fresh' | 'import'; key: number }>({ key: 0 });
  const [elapsed, setElapsed] = useState(0);
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
  const screen = lost ? 'lost' : !jobId ? blocked ? 'limit' : null : !job ? 'loading' : job.state === 'succeeded' ? 'result' : job.state;
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
        setJob(next); setError(''); setOffline(false); attempts = 0;
        if (next.state === 'succeeded' || next.state === 'failed') return;
      } catch (e) {
        if (stopped) return;
        const status = (e as { status?: number }).status;
        // 이 브라우저(방문자)의 분석이 아니거나 지워졌으면 기다려도 오지 않는다.
        if (status === 404 || status === 401) { setLost(true); return; }
        if (status) setError((e as Error).message); else setOffline(true);
        attempts++;
      }
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
    setBusy(true); setError(''); setBlocked(''); setPendingInput(input); setRejectedInput(null); setJobId(null); setJob(null);
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
      setJob(next); setJobId(next.id); setPendingInput(null); setLost(false); window.scrollTo(0, 0);
    } catch (e) {
      const { status, code } = e as { status?: number; code?: string };
      if (status && status >= 400 && status < 500 && status !== 408) {
        // 한도 화면(S3·L2·L3)은 오류 띠 대신 그 화면이 까닭을 말한다. '입력 고치기'는 같은 입력으로 돌아간다.
        if (status === 429 && code && limits.includes(code)) setBlocked(code); else setError((e as Error).message);
        setRejectedInput(input); setPendingInput(null); setEntered(true); pendingMemory.current = null;
        try { restoreInputDraft(input); sessionStorage.removeItem(pendingKey); } catch { /* Keep editable input in memory. */ }
        try { localStorage.removeItem(jobKey); } catch { /* Current tab remains editable. */ }
      } else setError((e as Error).message);
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
    // 시작 화면에서 고른 길(지난 입력·계획부터)은 그 한 번의 입력에만 쓴다 — 새 분석은 빈 입력에서 시작한다.
    setBegin({ key: begin.key + 1 }); setJobId(null); setJob(null); setError(''); setLost(false); setOffline(false); setEntered(true);
  }
  function open(next: Job) {
    try { localStorage.setItem(jobKey, next.id); } catch { /* The report still opens in this tab. */ }
    setJob(next); setJobId(next.id); setBlocked('');
  }
  const Container = embedded ? 'div' : 'main';
  if (pendingInput) return <Container className="app"><article className="ans"><h1>분석 요청을 확인하고 있어요.</h1><p>입력한 내용은 보관돼 있어요. 접수 여부를 다시 확인해도 같은 요청이 중복되지 않아요.</p>{error && <Notice tone="error">{error}</Notice>}{busy ? <Loading>접수 확인 중</Loading> : <Button variant="primary" onClick={() => void submit(pendingInput)}>접수 다시 확인</Button>}</article></Container>;
  if (restoring) return <section className="dashboard-card">{error ? <><Notice tone="error">{error}</Notice><Button onClick={() => setRestoreAttempt(n => n + 1)}>다시 불러오기</Button></> : <Loading>최근 분석을 불러오고 있어요.</Loading>}</section>;
  const result = job?.state === 'succeeded' && job.result;
  return <Container className="app">{!embedded && result && <a className="dashboard-link" href={preview ? '?preview=dashboard' : '?view=dashboard'}>내 포트폴리오 ↗</a>}{embedded && jobId && <div className="card-heading"><h2>최근 AI 포폴 분석</h2><Button onClick={reset}>새로 분석하기</Button></div>}{result && <div className="nav"><div className="nav__pill"><span className="nav__logo">ETF Orca</span></div></div>}
    {storageWarning && <Notice>{storageWarning}</Notice>}
    {error && <Notice tone="error">{error}</Notice>}
    {lost ? <LostScreen onNew={reset} />
      : !jobId ? blocked ? <LimitScreen code={blocked} busy={busy} onRetry={() => rejectedInput && void submit(rejectedInput)} onEdit={() => setBlocked('')} onOpen={open} />
        : entered ? <InputForm key={begin.key} onSubmit={submit} busy={busy} initialInput={rejectedInput ?? begin.input} start={begin.start} // 거절된 입력은 한 번만 되살린다. 처음 화면에 다녀오면 그동안 고친 초안을 이어 쓴다.
          onExit={embedded ? undefined : () => { setEntered(false); setRejectedInput(null); }} />
        : <Start draft={readInputDraft()} onBegin={input => { setBegin({ input, key: begin.key + 1 }); setEntered(true); }} onFresh={() => { setBegin({ start: 'fresh', key: begin.key + 1 }); setEntered(true); }} onImport={() => { setBegin({ start: 'import', key: begin.key + 1 }); setEntered(true); }} onOpen={open} />
      : result ? <><ResultView report={job.result!} jobId={jobId} input={job.input} active={active} /><InputSave key={jobId} jobId={jobId} screen="result" active={active} /><Button variant="secondary" className="secondary" onClick={reset}>새 포트폴리오 분석하기</Button></>
      : job?.state === 'failed' ? <>
        {job.failure === 'SEARCH_RATE_LIMIT' ? <RateScreen job={job} busy={busy} onRetry={retry} onEdit={reset} />
          : <FailedView failure={job.failure} partial={job.result?.partial_report} canRetry={!!job.input} busy={busy} onRetry={retry} onEdit={reset}>
            {job.result?.partial_report && Object.keys(job.result.partial_report.sections || {}).length > 0 && <ReportView report={{ ...job.result.partial_report, recommendation: null,
              completion: { tasks: [], missing_research: [], ...job.result.partial_report.completion, status: 'partial',
                missing_inputs: [...(job.result.partial_report.completion?.missing_inputs || []), '분석이 중단되어 최종 검토가 끝나지 않았어요.'] } }} />}
          </FailedView>}
        <InputSave key={jobId} jobId={jobId} screen="failed" active={active} />
        <Followup jobId={jobId} />
      </>
      : <><ProgressView job={job} elapsed={elapsed} offline={offline} />{error && <Button variant="secondary" className="secondary" onClick={reset}>입력 화면으로</Button>}</>}
  </Container>;
}
