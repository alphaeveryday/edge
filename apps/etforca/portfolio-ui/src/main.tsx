import { Notice, Button, Select } from './design-system/components';
import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { request, ensureVisitorSession, type Input, type Job } from './api';
import { InputForm } from './InputForm';
import { ReportView } from './ReportView';
import { Followup } from './Followup';
import './design-system/fonts.css';
import './design-system/tokens.css';
import './styles.css';
import './design-system/components.css';
import { ProgressView } from './ProgressView';
import { startPresence } from './presence';
import { trackScreen } from './analytics';
import { NewsletterConfirmation } from './NewsletterConfirmation';
import { Dashboard } from './Dashboard';
import { DesignCatalog } from './design-system/Catalog';
import { preview, jobKey, pendingKey } from './preview';

function App() {
  useEffect(startPresence, []);
  const [jobId, setJobId] = useState(() => preview ? (preview === 'input' ? null : 'preview') : localStorage.getItem(jobKey));
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const screen = !jobId ? null : !job ? 'loading' : job.state === 'succeeded' ? 'result' : job.state;
  useEffect(() => screen ? trackScreen(screen) : undefined, [screen]);
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
    try { pending = JSON.parse(sessionStorage.getItem(pendingKey) || 'null'); } catch { /* Invalid local state is replaced. */ }
    const key = pending?.input === serialized ? pending.key : crypto.randomUUID();
    sessionStorage.setItem(pendingKey, JSON.stringify({ input: serialized, key }));
    try {
      await ensureVisitorSession();
      const next = await request<Job>('/analyses', { method: 'POST', headers: { 'Idempotency-Key': key }, body: serialized });
      localStorage.setItem(jobKey, next.id); sessionStorage.removeItem(pendingKey);
      setJob(next); setJobId(next.id); window.scrollTo(0, 0);
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  function reset() { localStorage.removeItem(jobKey); setJobId(null); setJob(null); setError(''); }
  return <main className="app"><a className="dashboard-link" href={preview ? '?preview=dashboard' : '?view=dashboard'}>내 포트폴리오 ↗</a>{jobId && <div className="nav"><div className="nav__pill"><span className="nav__logo">ETF Orca</span></div></div>}
    {error && <Notice tone="error">{error}</Notice>}
    {!jobId ? <InputForm onSubmit={submit} busy={busy} /> : job?.state === 'succeeded' && job.result ? <><ReportView report={job.result} feedbackJobId={jobId} /><Followup jobId={jobId} /><Button variant="secondary" className="secondary" onClick={reset}>새 포트폴리오 분석하기</Button></> : job?.state === 'failed' ? <><article className="ans"><h1>분석을 마치지 못했어요.</h1><p>{job.failure === 'SEARCH_RATE_LIMIT' ? '외부 검색 서비스의 요청 제한으로 자료를 모두 확인하지 못했어요. 잠시 후 다시 시도해 주세요.' : job.failure === 'TIMED_OUT' ? '분석 또는 자료 조회의 제한 시간 안에 근거 확인을 마치지 못했어요. 전체 분석은 최대 10분까지 진행해요.' : '자료 조회 또는 결과 검증 중 문제가 생겼어요.'} 중단된 분석은 최종 추천으로 사용하지 마세요.</p><Button variant="primary" className="primary" onClick={reset}>다시 입력하기</Button></article>
      {job.result?.partial_report && Object.keys(job.result.partial_report.sections || {}).length > 0 && <details><summary>중단 전 작성된 분석 보기</summary><ReportView report={{ ...job.result.partial_report, recommendation: null,
        completion: { tasks: [], missing_research: [], ...job.result.partial_report.completion, status: 'partial',
          missing_inputs: [...(job.result.partial_report.completion?.missing_inputs || []), '분석이 중단되어 최종 검토가 끝나지 않았어요.'] } }} /></details>}
      <Followup jobId={jobId} recoveryOnly />
    </> : <><ProgressView job={job} elapsed={elapsed} />{error && <Button variant="secondary" className="secondary" onClick={reset}>입력 화면으로</Button>}</>}
  </main>;
}
const confirmationToken = new URLSearchParams(location.hash.slice(1)).get('newsletter-confirm');
// Keep the bearer token out of subsequent navigation, analytics and screenshots of the address bar.
if (confirmationToken) history.replaceState(null, '', location.pathname);
function Entry() {
  const [token, setToken] = useState(confirmationToken);
  useEffect(() => {
    function confirmLink() {
      const next = new URLSearchParams(location.hash.slice(1)).get('newsletter-confirm');
      if (!next) return;
      history.replaceState(null, '', location.pathname);
      setToken(next);
    }
    window.addEventListener('hashchange', confirmLink);
    return () => window.removeEventListener('hashchange', confirmLink);
  }, []);
  const dashboard = new URLSearchParams(location.search).get('view') === 'dashboard' || preview === 'dashboard' || preview === 'empty';
  return <>{preview && <div className="preview-bar"><b>테스트 화면 · 실제 분석·메일 발송 없음 · {import.meta.env.VITE_BUILD_VERSION}</b><label>화면<Select value={preview} onChange={e => { location.href = '?preview=' + e.target.value; }}>{[['input', '입력'], ['queued', '접수'], ['running', '분석 중'], ['result', '결과·저장 팝업'], ['partial', '일부 자료 없음'], ['failed', '분석 실패'], ['dashboard', '대시보드'], ['empty', '빈 대시보드'], ['newsletter', '구독 확인'], ['design-system', '디자인 시스템']].map(([v, label]) => <option key={v} value={v}>{label}</option>)}</Select></label><Button onClick={() => { for (const key of ['edge-preview-workbook', 'edge-preview-empty-workbook', 'edge-preview-job', 'edge-preview-input', 'edge-chat-preview', 'edge-chat-preview-recovery', 'edge-portfolio-feedback:preview']) localStorage.removeItem(key); location.reload(); }}>테스트 초기화</Button></div>}{preview === 'design-system' && !token ? <DesignCatalog /> : token || preview === 'newsletter' ? <NewsletterConfirmation key={token} token={token || 'preview'} /> : dashboard ? <Dashboard /> : <App />}</>;
}
createRoot(document.getElementById('root')!).render(<React.StrictMode><Entry /></React.StrictMode>);
