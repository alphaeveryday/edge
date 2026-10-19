import { Button, TextInput, Select } from './design-system/components';
import { useEffect, useRef, useState, type FormEvent } from 'react';
import { request, type Section, type Report } from './api';
import { ReportView } from './ReportView';
import { NewsletterSignup } from './NewsletterSignup';

type ChatJob = { id: string; state: string; result: { answer: Section; revision?: Report; status?: string } | null };
export function Followup({ jobId, recoveryOnly = false }: { jobId: string; recoveryOnly?: boolean }) {
  const [mode, setMode] = useState<'question' | 'recovery'>(recoveryOnly ? 'recovery' : 'question');
  const storageKey = 'edge-chat-' + jobId + (mode === 'recovery' ? '-recovery' : '');
  const [question, setQuestion] = useState('');
  const [answer, setAnswer] = useState<Section | null>(null);
  const [revision, setRevision] = useState<Report | null>(null);
  const [revisionId, setRevisionId] = useState<string | null>(null);
  const [chatId, setChatId] = useState(() => localStorage.getItem('edge-chat-' + jobId + (recoveryOnly ? '-recovery' : '')));
  const [busy, setBusy] = useState(false);
  const [blocked, setBlocked] = useState(false);
  const [error, setError] = useState('');
  const limit = useRef<HTMLDialogElement>(null);
  const premium = useRef<HTMLDialogElement>(null);
  const pending = useRef<Record<string, { question: string; key: string }>>({});
  useEffect(() => {
    if (!chatId) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    setBusy(true);
    async function poll() {
      try {
        const job = await request<ChatJob>('/analyses/' + chatId);
        if (stopped) return;
        if (job.state === 'succeeded') { setAnswer(job.result!.answer); setRevision(job.result!.revision || null); setRevisionId(job.id); setBusy(false); return; }
        if (job.state === 'failed') { setError('답변을 만들지 못했어요. 다시 질문할 수 있어요.'); setBusy(false); localStorage.removeItem(storageKey); setChatId(null); delete pending.current[storageKey]; return; }
      } catch { if (!stopped) setError('답변을 기다리고 있어요. 연결을 다시 확인할게요.'); }
      if (!stopped) timer = setTimeout(poll, 2000);
    }
    void poll(); return () => { stopped = true; clearTimeout(timer); };
  }, [chatId, jobId, storageKey]);
  function changeMode(value: 'question' | 'recovery') {
    setMode(value); setBlocked(false); setError(''); setAnswer(null); setRevision(null);
    setChatId(localStorage.getItem('edge-chat-' + jobId + (value === 'recovery' ? '-recovery' : '')));
  }
  async function ask(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError('');
    if (pending.current[storageKey]?.question !== question) pending.current[storageKey] = { question, key: crypto.randomUUID() };
    try {
      const job = await request<ChatJob>('/analyses/' + jobId + '/chat', { method: 'POST', headers: { 'Idempotency-Key': pending.current[storageKey].key }, body: JSON.stringify({ question, ...(mode === 'recovery' ? { mode } : {}) }) });
      localStorage.setItem(storageKey, job.id); setChatId(job.id); delete pending.current[storageKey];
    } catch (e) {
      if ((e as Error).message === 'CHAT_LIMIT') { setBlocked(true); limit.current!.showModal(); }
      else if ((e as Error).message === 'RECOVERY_LIMIT') { setBlocked(true); setError('이 분석의 보완 요청 2회를 모두 사용했어요. 입력 내용을 보완해 새 분석을 시작해 주세요.'); }
      else setError((e as Error).message);
      setBusy(false);
    }
  }
  return <>
    <section className="followup" data-analytics-section="followup"><h3>AI에게 추가로 묻기</h3>
      {!recoveryOnly && <label>요청 종류 <Select aria-label="질문 종류" value={mode} disabled={busy} onChange={e => changeMode(e.target.value as 'question' | 'recovery')}><option value="question">추가 질문</option><option value="recovery">누락·오류 보완</option></Select></label>}
      {answer && <div className="chat-answer">{answer.paragraphs.map((p, i) => <p key={i}>{p}</p>)}</div>}
      {revision && <details className="followup-revision"><summary>보완 분석과 계산표 보기</summary><p className="notice">원본 분석은 유지되며 아래는 이 질문에 대한 별도 분석이에요.</p><ReportView key={revisionId} report={revision} /></details>}
      <form className="report-chat" onSubmit={ask}><TextInput aria-label="추가 질문" placeholder={mode === 'recovery' ? '빠진 답변이나 보완할 조건을 알려 주세요.' : '이 추천안에서 더 궁금한 점이 있나요?'} required maxLength={1000} value={question} disabled={busy || blocked} onChange={e => setQuestion(e.target.value)} /><Button aria-label="질문 보내기" disabled={busy || blocked}>{busy ? '…' : '↑'}</Button></form><p className="notice">{busy ? '자료를 확인하고 답변을 작성하고 있어요…' : mode === 'recovery' ? '이 분석의 누락·오류 보완 2회 · 실패한 요청은 차감하지 않아요.' : '추가 질문 1회 · 누락·오류 보완은 별도예요.'}</p>
      {error && <p role="alert">{error}</p>}
    </section>
    {!recoveryOnly && <>
    <Button className="premium report-premium" onClick={() => premium.current!.showModal()}><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.4" aria-hidden="true"><path d="m3 8 4-4h10l4 4-9 13zM3 8h18M7 4l5 17 5-17" /></svg>내 ETF 핵심 소식 이메일로 받기</Button><p className="report-footnote">내 보유 종목의 이슈와 가격 변동 이유를 매일 한 통으로.</p>
    </>}
    <dialog className="followup-dialog" ref={limit} aria-labelledby="limit-title" onClose={() => { window.scrollTo(0, 0); document.querySelector('h1')?.focus(); }}><h2 id="limit-title">추가 채팅은 아직 준비 중이에요</h2><p>무료 질문 1회를 모두 사용했어요.</p><Button variant="primary" className="primary" onClick={() => limit.current!.close()}>포트폴리오로 돌아가기</Button></dialog>
    <dialog className="followup-dialog" ref={premium} aria-labelledby="premium-title"><Button className="dialog-close" aria-label="닫기" onClick={() => premium.current!.close()}>×</Button><h2 id="premium-title">내 ETF 핵심 소식 이메일로 받기</h2><NewsletterSignup jobId={jobId} showPreview /></dialog>
  </>;
}
