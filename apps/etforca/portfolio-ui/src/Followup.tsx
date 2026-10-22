import { Button, TextInput, TextArea, Select } from './design-system/components';
import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react';
import { request, type Section, type Report } from './api';
import { ReportView } from './ReportView';
import { NewsletterSignup } from './NewsletterSignup';
import { trackAction } from './analytics';

type ChatJob = { id: string; state: string; input?: { question: string }; created_at?: string; started_at?: string | null; result: { answer: Section; revision?: Report; status?: string } | null };
type Chats = { limit: number; items: ChatJob[] };
const waiting = (chat: ChatJob) => chat.state === 'queued' || chat.state === 'running';

export function Followup({ jobId, recoveryOnly = false }: { jobId: string; recoveryOnly?: boolean }) {
  const [mode, setMode] = useState<'question' | 'recovery'>(recoveryOnly ? 'recovery' : 'question');
  const [question, setQuestion] = useState('');
  const premium = useRef<HTMLDialogElement>(null);
  const chat = useConversation(jobId, !recoveryOnly);
  const recovery = useRecovery(jobId, mode === 'recovery');
  function submit(event: FormEvent) {
    event.preventDefault();
    if (mode === 'recovery') return void recovery.ask(question);
    chat.open();
    void chat.send(question).then(sent => { if (sent) setQuestion(''); });
  }
  const remaining = chat.remaining;
  const busy = mode === 'recovery' ? recovery.busy : chat.sending;
  const answering = chat.items.some(waiting);
  const blocked = mode === 'recovery' ? recovery.blocked : remaining === 0 || answering;
  return <>
    <section className="followup" data-analytics-section="followup"><h3>AI에게 추가로 묻기</h3>
      {!recoveryOnly && <label>요청 종류 <Select aria-label="질문 종류" value={mode} disabled={busy} onChange={e => { setMode(e.target.value as 'question' | 'recovery'); recovery.reset(); }}><option value="question">추가 질문</option><option value="recovery">누락·오류 보완</option></Select></label>}
      {mode === 'recovery' && recovery.answer && <div className="chat-answer">{recovery.answer.paragraphs.map((p, i) => <p key={i}>{p}</p>)}</div>}
      {mode === 'recovery' && recovery.revision && <details className="followup-revision"><summary>보완 분석과 계산표 보기</summary><p className="notice">원본 분석은 유지되며 아래는 이 질문에 대한 별도 분석이에요.</p><ReportView key={recovery.revisionId} report={recovery.revision} /></details>}
      {mode === 'question' && chat.items.length > 0 && <Button className="chat-resume" onClick={chat.open}>이 분석에서 나눈 대화 보기 · {chat.items.length}개{chat.items.some(waiting) ? ' · 답변 작성 중' : ''}</Button>}
      <form className="report-chat" onSubmit={submit}><TextInput aria-label="추가 질문" placeholder={mode === 'recovery' ? '빠진 답변이나 보완할 조건을 알려 주세요.' : '이 추천안에서 더 궁금한 점이 있나요?'} required maxLength={1000} value={question} disabled={busy || blocked} onChange={e => setQuestion(e.target.value)} /><Button aria-label="질문 보내기" disabled={busy || blocked}>{busy ? '…' : '↑'}</Button></form>
      <p className="notice">{mode === 'recovery' ? (recovery.busy ? '자료를 확인하고 답변을 작성하고 있어요…' : '이 분석의 누락·오류 보완 2회 · 실패한 요청은 차감하지 않아요.') : remaining === 0 ? `이 분석의 추가 질문 ${chat.limit}회를 모두 사용했어요. 새 분석을 시작하면 다시 물을 수 있어요.` : answering ? '앞선 질문의 답을 쓰고 있어요. 답이 끝나면 이어서 물을 수 있어요.' : `이 분석에 대해 ${chat.limit}번까지 물을 수 있어요${remaining < chat.limit ? ` · ${remaining}번 남음` : ''} · 답을 받지 못한 질문은 세지 않아요.`}</p>
      {mode === 'recovery' && recovery.error && <p role="alert">{recovery.error}</p>}
      {mode === 'question' && chat.error && !chat.isOpen && <p role="alert">{chat.error}</p>}
      {!recoveryOnly && <ChatScreen chat={chat} />}
    </section>
    {!recoveryOnly && <>
    <Button className="premium report-premium" onClick={() => premium.current!.showModal()}><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.4" aria-hidden="true"><path d="m3 8 4-4h10l4 4-9 13zM3 8h18M7 4l5 17 5-17" /></svg>내 ETF 핵심 소식 이메일로 받기</Button><p className="report-footnote">내 보유 종목의 이슈와 가격 변동 이유를 매일 한 통으로.</p>
    <dialog className="followup-dialog" ref={premium} aria-labelledby="premium-title"><Button className="dialog-close" aria-label="닫기" onClick={() => premium.current!.close()}>×</Button><h2 id="premium-title">내 ETF 핵심 소식 이메일로 받기</h2><NewsletterSignup jobId={jobId} showPreview /></dialog>
    </>}
  </>;
}

// 추가 질문은 서버가 분석마다 대화로 묶어 둔다. 다른 기기·새로고침에서도 같은 대화를 보여 주고, 답을 기다리는 동안만 다시 읽는다.
function useConversation(jobId: string, enabled: boolean) {
  const [chats, setChats] = useState<Chats>({ limit: 5, items: [] });
  const [reload, setReload] = useState(0);
  const [sending, setSending] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [sendError, setSendError] = useState('');
  const [isOpen, setOpen] = useState(false);
  const pushed = useRef(false);
  const pending = useRef<{ question: string; key: string } | null>(null);
  useEffect(() => {
    if (!enabled) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    let failures = 0;
    async function load() {
      try {
        const next = await request<Chats>('/analyses/' + jobId + '/chats');
        if (stopped) return;
        if (!Array.isArray(next?.items)) throw new Error('INVALID_CHATS');
        setChats(next); setLoadError(''); failures = 0;
        if (next.items.some(waiting)) timer = setTimeout(load, 3000);
      } catch (e) {
        if (stopped || (e as { status?: number }).status === 404) return; // 대화 API가 아직 없는 서버: 빈 대화로 둔다.
        setLoadError('대화를 불러오지 못했어요. 연결을 다시 확인할게요.');
        timer = setTimeout(load, Math.min(60000, 5000 * 2 ** failures++));
      }
    }
    void load(); return () => { stopped = true; clearTimeout(timer); };
  }, [jobId, enabled, reload]);
  async function send(question: string) {
    if (!question.trim()) return false;
    setSending(true); setSendError('');
    if (pending.current?.question !== question) pending.current = { question, key: crypto.randomUUID() };
    try {
      const job = await request<ChatJob>('/analyses/' + jobId + '/chat', { method: 'POST', headers: { 'Idempotency-Key': pending.current.key }, body: JSON.stringify({ question }) });
      pending.current = null; trackAction('followup_asked');
      setChats(current => ({ ...current, items: [...current.items.filter(item => item.id !== job.id), { ...job, input: { question } }] }));
      setReload(n => n + 1);
      return true;
    } catch (e) {
      const code = (e as Error).message;
      if (code === 'CHAT_LIMIT' || code === 'CHAT_BUSY') { pending.current = null; setReload(n => n + 1); }
      setSendError(code === 'CHAT_LIMIT' ? `이 분석의 추가 질문 ${chats.limit}회를 모두 사용했어요.` : code === 'CHAT_BUSY' ? '앞선 질문의 답을 쓰고 있어요. 답이 끝나면 이어서 물어보세요.' : code);
      return false;
    } finally { setSending(false); }
  }
  const used = chats.items.filter(item => item.state !== 'failed').length;
  return { ...chats, sending, error: sendError || loadError, isOpen, send, pushed, remaining: Math.max(0, chats.limit - used),
    open: () => {
      if (isOpen) return;
      // 휴대폰 뒤로 가기가 페이지를 떠나지 않고 대화만 닫도록 한 칸을 쌓는다.
      history.pushState({ orcaChat: true }, ''); pushed.current = true; setOpen(true); trackAction('followup_chat_opened');
    }, close: () => setOpen(false) };
}

type Conversation = ReturnType<typeof useConversation>;
// 질문하면 분석 화면 위에 대화 화면이 열린다. 뒤로 가기(버튼·브라우저·Esc)는 같은 분석 결과 위치로 돌려보낸다.
function ChatScreen({ chat }: { chat: Conversation }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const end = useRef<HTMLDivElement>(null);
  const [draft, setDraft] = useState('');
  const [now, setNow] = useState(() => Date.now());
  const answering = chat.items.some(waiting);
  useEffect(() => {
    const element = dialog.current!;
    if (!chat.isOpen) { if (element.open) element.close(); return; }
    if (!element.open) element.showModal();
    const back = () => { chat.pushed.current = false; element.close(); };
    addEventListener('popstate', back);
    return () => removeEventListener('popstate', back);
  }, [chat.isOpen]);
  // 3초마다 다시 읽어도 읽던 자리를 끌어내리지 않게, 대화가 바뀔 때만 맨 아래로 간다.
  const shape = chat.items.map(item => item.id + item.state).join();
  useEffect(() => { if (chat.isOpen) end.current?.scrollIntoView({ block: 'end' }); }, [chat.isOpen, shape]);
  useEffect(() => {
    if (!answering) return;
    const timer = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(timer);
  }, [answering]);
  function closed() {
    chat.close();
    if (chat.pushed.current) { chat.pushed.current = false; history.back(); }
  }
  async function submit(event?: FormEvent) {
    event?.preventDefault();
    if (await chat.send(draft)) setDraft('');
  }
  function enter(event: KeyboardEvent<HTMLTextAreaElement>) {
    // 한글 조합 중 Enter 는 글자 확정이다. 보내지 않는다.
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); if (draft.trim() && !blocked) void submit(); }
  }
  const blocked = chat.sending || answering || chat.remaining === 0;
  return <dialog ref={dialog} className="chat-screen" aria-labelledby="chat-title" data-analytics-section="followup_chat" onClose={closed}>
    <header className="chat-screen__bar">
      <Button className="chat-screen__back" onClick={() => dialog.current!.close()}><span aria-hidden="true">←</span> 분석 결과로 돌아가기</Button>
      <h2 id="chat-title">AI에게 묻기</h2>
      <span className="chat-screen__count">{chat.remaining}/{chat.limit}회 남음</span>
    </header>
    <div className="chat-screen__messages" role="log">
      <p className="chat-screen__intro">방금 받은 분석을 기준으로 답해요. 앞에서 나눈 대화도 이어서 기억해요.</p>
      {chat.items.map(item => <div className="chat-turn" key={item.id}>
        <p className="chat-bubble chat-bubble--me">{item.input?.question}</p>
        {item.state === 'succeeded' && item.result ? <div className="chat-bubble chat-bubble--ai">
          {item.result.answer.paragraphs.map((p, i) => <p key={i}>{p}</p>)}
          {item.result.revision && <details className="followup-revision"><summary>이 답의 계산표와 근거 보기</summary><p className="notice">원본 분석은 그대로이고, 아래는 이 질문에 맞춰 다시 계산한 결과예요.</p><ReportView report={item.result.revision} /></details>}
        </div> : item.state === 'failed' ? <div className="chat-bubble chat-bubble--ai chat-bubble--failed">
          <p>답변을 만들지 못했어요. 이 질문은 횟수에서 빠졌어요.</p>
          {item.input && <Button disabled={blocked} onClick={() => void chat.send(item.input!.question)}>같은 질문 다시 보내기</Button>}
        </div> : <div className="chat-bubble chat-bubble--ai chat-bubble--waiting">
          <span className="chat-dots" aria-hidden="true"><i /><i /><i /></span>
          <p>계좌 자료와 근거를 다시 확인하며 답을 쓰고 있어요<span aria-hidden="true">{elapsed(item, now)}</span></p>
          <small>보통 2~5분 걸려요. 분석 결과로 돌아가도 답은 계속 만들어져요.</small>
        </div>}
      </div>)}
      {chat.error && <p className="chat-screen__error" role="alert">{chat.error}</p>}
      <div ref={end} />
    </div>
    <form className="chat-screen__composer" onSubmit={submit}>
      <TextArea aria-label="이어서 질문" rows={1} maxLength={1000} value={draft} disabled={chat.remaining === 0} onKeyDown={enter} onChange={e => setDraft(e.target.value)}
        placeholder={chat.remaining === 0 ? '이 분석의 질문을 모두 사용했어요. 새 분석에서 다시 물을 수 있어요.' : answering ? '답변이 끝나면 이어서 물을 수 있어요.' : '이어서 물어보세요'} />
      <Button variant="primary" aria-label="이어서 질문 보내기" disabled={blocked || !draft.trim()}>↑</Button>
    </form>
  </dialog>;
}

function elapsed(chat: ChatJob, now: number) {
  const start = Date.parse(chat.started_at || chat.created_at || '');
  if (!start) return '…';
  const seconds = Math.max(0, Math.floor((now - start) / 1000));
  return ` · ${seconds >= 60 ? `${Math.floor(seconds / 60)}분 ` : ''}${seconds % 60}초째`;
}

// 누락·오류 보완은 대화가 아니라 원래 분석을 고치는 별도 요청이라 기존처럼 한 번에 하나만 보여 준다.
function useRecovery(jobId: string, active: boolean) {
  const storageKey = 'edge-chat-' + jobId + '-recovery';
  const [answer, setAnswer] = useState<Section | null>(null);
  const [revision, setRevision] = useState<Report | null>(null);
  const [revisionId, setRevisionId] = useState<string | null>(null);
  const [chatId, setChatId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [blocked, setBlocked] = useState(false);
  const [error, setError] = useState('');
  const pending = useRef<{ question: string; key: string } | null>(null);
  useEffect(() => { setChatId(active ? localStorage.getItem(storageKey) : null); }, [active, storageKey]);
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
        if (job.state === 'failed') { setError('답변을 만들지 못했어요. 다시 질문할 수 있어요.'); setBusy(false); localStorage.removeItem(storageKey); setChatId(null); pending.current = null; return; }
      } catch { if (!stopped) setError('답변을 기다리고 있어요. 연결을 다시 확인할게요.'); }
      if (!stopped) timer = setTimeout(poll, 2000);
    }
    void poll(); return () => { stopped = true; clearTimeout(timer); setBusy(false); };
  }, [chatId, storageKey]);
  async function ask(question: string) {
    setBusy(true); setError('');
    if (pending.current?.question !== question) pending.current = { question, key: crypto.randomUUID() };
    try {
      const job = await request<ChatJob>('/analyses/' + jobId + '/chat', { method: 'POST', headers: { 'Idempotency-Key': pending.current.key }, body: JSON.stringify({ question, mode: 'recovery' }) });
      localStorage.setItem(storageKey, job.id); setChatId(job.id); pending.current = null;
    } catch (e) {
      if ((e as Error).message === 'RECOVERY_LIMIT') { setBlocked(true); setError('이 분석의 보완 요청 2회를 모두 사용했어요. 입력 내용을 보완해 새 분석을 시작해 주세요.'); }
      else setError((e as Error).message);
      setBusy(false);
    }
  }
  return { answer, revision, revisionId, busy, blocked, error, ask, reset: () => { setBlocked(false); setError(''); setAnswer(null); setRevision(null); } };
}
