import { Button } from './design-system/components';
import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { trackFeedback, type FeedbackRating } from './analytics';
import { NewsletterSignup } from './NewsletterSignup';
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
  return <div ref={anchor} className="feedback-anchor">
    <SavePortfolio jobId={jobId} />
    <Button variant="secondary" className="secondary" aria-expanded={open} onClick={() => { if (open) return; setNewsletterOnly(true); setOpen(true); }}>내 ETF 핵심 소식 이메일로 받기</Button>
    {open && <aside onFocusCapture={e => { if (e.relatedTarget instanceof HTMLElement && !e.currentTarget.contains(e.relatedTarget)) previousFocus.current = e.relatedTarget; }} ref={popup} tabIndex={-1} className="report-feedback" role="dialog" aria-label={newsletterOnly ? '내 ETF 소식, 매일 메일로' : '포트폴리오 저장'} aria-modal="false" data-analytics-section="feedback">
      <Button className="feedback-close" aria-label="평가 팝업 닫기" onClick={close}>×</Button>
      {!newsletterOnly && <SavePortfolio jobId={jobId} />}
      <NewsletterSignup jobId={jobId} showPreview={newsletterOnly} />
      {!newsletterOnly && <><h3 id="feedback-title">이번 분석은 어떠셨나요?</h3>
      {answered ? <p role="status">의견 감사합니다!</p> : <div className="feedback-choices">{choices.map(([rating, emoji, label]) => <Button key={rating} onClick={() => choose(rating)}><span aria-hidden="true">{emoji}</span>{label}</Button>)}</div>}</>}
    </aside>}
  </div>;
}
