import { Notice, Button, TextInput } from './design-system/components';
import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { trackFeedback, type FeedbackRating } from './analytics';
import { request } from './api';
import { SavePortfolio } from './SavePortfolio';

const finished = new Set<string>();
const choices: [FeedbackRating, string, string][] = [['good', '😊', '좋아요'], ['okay', '😐', '그냥 그래요'], ['needs_improvement', '🛠️', '개선이 필요해요']];

export function ReportFeedback({ jobId }: { jobId: string }) {
  const anchor = useRef<HTMLDivElement>(null);
  const popup = useRef<HTMLElement>(null);
  const previousFocus = useRef<HTMLElement | null>(null);
  const [open, setOpen] = useState(false);
  const [answered, setAnswered] = useState(false);
  const [newsletterOnly, setNewsletterOnly] = useState(false);
  const [email, setEmail] = useState('');
  const [sending, setSending] = useState(false);
  const [subscription, setSubscription] = useState('');
  const [error, setError] = useState('');
  const submitted = useRef(false);
  const storageKey = `edge-portfolio-feedback:${jobId}`;
  useEffect(() => {
    if (finished.has(jobId)) return;
    try { if (localStorage.getItem(storageKey)) return; } catch { /* The in-memory guard still works without storage. */ }
    let shown = false;
    function check() {
      if (shown || finished.has(jobId) || popup.current || document.visibilityState !== 'visible' || !anchor.current || document.querySelector('dialog[open]')) return;
      if (document.activeElement?.closest('.followup')) return;
      if (!anchor.current.getClientRects().length || anchor.current.getBoundingClientRect().top > innerHeight - 64) return;
      shown = true; setOpen(true); trackFeedback('shown');
    }
    function followupStarted(event: FocusEvent) { if (event.target instanceof Element && event.target.closest('.followup')) shown = true; }
    const timer = window.setTimeout(check, 0);
    window.addEventListener('scroll', check, { passive: true });
    window.addEventListener('resize', check);
    document.addEventListener('visibilitychange', check);
    document.addEventListener('focusin', followupStarted);
    return () => { clearTimeout(timer); window.removeEventListener('scroll', check); window.removeEventListener('resize', check); document.removeEventListener('visibilitychange', check); document.removeEventListener('focusin', followupStarted); };
  }, [jobId, storageKey]);
  function remember() {
    finished.add(jobId);
    try { localStorage.setItem(storageKey, 'done'); } catch { /* Feedback must not depend on browser storage. */ }
  }
  function close() {
    if (!submitted.current && !newsletterOnly) trackFeedback('dismissed');
    if (popup.current?.contains(document.activeElement) && previousFocus.current?.isConnected) previousFocus.current.focus({ preventScroll: true });
    remember(); setOpen(false);
  }
  useLayoutEffect(() => {
    if (!open) return;
    previousFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    if (newsletterOnly) popup.current?.focus({ preventScroll: true });
    function escape(event: KeyboardEvent) { if (event.key === 'Escape' && !document.querySelector('dialog[open]')) close(); }
    function followupFocus(event: FocusEvent) { if (event.target instanceof Element && event.target.closest('.followup')) close(); }
    document.addEventListener('keydown', escape);
    document.addEventListener('focusin', followupFocus);
    return () => { document.removeEventListener('keydown', escape); document.removeEventListener('focusin', followupFocus); };
  }, [open]);
  function choose(rating: FeedbackRating) {
    if (submitted.current) return;
    submitted.current = true; remember(); trackFeedback('submitted', rating); setAnswered(true);
    popup.current?.focus({ preventScroll: true });
  }
  async function subscribe(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (sending) return;
    setSending(true); setError('');
    try {
      const result = await request<{ status: string; delivery_ready: boolean }>(`/analyses/${jobId}/newsletter`, {
        method: 'POST', body: JSON.stringify({ email: email.trim(), consent: true }),
      });
      if (result.status === 'active' || result.status === 'confirmed') {
        setSubscription((result.status === 'active' ? '이미 구독 중이에요. ' : '이메일 확인을 마쳤어요. ') + (result.delivery_ready
          ? '매일 보유 종목 소식을 보내드릴게요.' : '발송 준비가 끝나면 소식을 보내드릴게요.'));
      } else {
        setSubscription(result.delivery_ready ? '신청했어요! 확인 메일의 링크를 눌러 구독을 시작해 주세요.'
          : '신청했어요! 발송 준비가 끝나면 이메일 확인 링크를 보내드릴게요.');
      }
      setEmail('');
    } catch (e) { setError((e as Error).message); } finally { setSending(false); }
  }
  return <div ref={anchor} className="feedback-anchor">
    <SavePortfolio jobId={jobId} />
    <Button variant="secondary" className="secondary" aria-expanded={open} onClick={() => { if (open) return; setNewsletterOnly(true); setSubscription(''); setOpen(true); }}>보유 종목 뉴스레터 받기</Button>
    {open && <aside onFocusCapture={e => { if (e.relatedTarget instanceof HTMLElement && !e.currentTarget.contains(e.relatedTarget)) previousFocus.current = e.relatedTarget; }} ref={popup} tabIndex={-1} className="report-feedback" role="dialog" aria-label={newsletterOnly ? '내 ETF 소식, 매일 메일로' : '포트폴리오 저장'} aria-modal="false" data-analytics-section="feedback">
      <Button className="feedback-close" aria-label="평가 팝업 닫기" onClick={close}>×</Button>
      {!newsletterOnly && <SavePortfolio jobId={jobId} />}
      <div className="feedback-newsletter">
        <h3 id="newsletter-title">내 ETF 소식, 매일 메일로</h3>
        <p>보유 종목의 핵심 이슈와 가격이 움직인 이유를 쉽고 짧게 정리해 드려요.</p>
        {subscription ? <p role="status">{subscription}</p> : <form onSubmit={subscribe}>
          <label className="sr-only" htmlFor="newsletter-email">뉴스레터 이메일</label>
          <TextInput id="newsletter-email" type="email" autoComplete="email" placeholder="이메일 주소" maxLength={254} required value={email} onChange={e => setEmail(e.target.value)} />
          <Button variant="secondary" className="newsletter-submit" type="submit" disabled={sending}>{sending ? '신청 중…' : '매일 핵심 소식 받기'}</Button>
          <small className="newsletter-consent">신청하면 이메일·보유 종목 이용과 뉴스레터 수신에 동의합니다.</small>
          {error && <Notice tone="error">{error}</Notice>}
        </form>}
        <small>언제든 메일에서 수신 거부할 수 있어요.</small>
      </div>
      {!newsletterOnly && <><h3 id="feedback-title">이번 분석은 어떠셨나요?</h3>
      {answered ? <p role="status">의견 감사합니다!</p> : <div className="feedback-choices">{choices.map(([rating, emoji, label]) => <Button key={rating} onClick={() => choose(rating)}><span aria-hidden="true">{emoji}</span>{label}</Button>)}</div>}</>}
    </aside>}
  </div>;
}
