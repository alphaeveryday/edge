import { useEffect, useRef, useState } from 'react';
import { trackFeedback, type FeedbackRating } from './analytics';

const finished = new Set<string>();
const choices: [FeedbackRating, string, string][] = [['good', '😊', '좋아요'], ['okay', '😐', '그냥 그래요'], ['needs_improvement', '🛠️', '개선이 필요해요']];

export function ReportFeedback({ jobId }: { jobId: string }) {
  const anchor = useRef<HTMLDivElement>(null);
  const popup = useRef<HTMLElement>(null);
  const previousFocus = useRef<HTMLElement | null>(null);
  const [open, setOpen] = useState(false);
  const [answered, setAnswered] = useState(false);
  const submitted = useRef(false);
  const storageKey = `edge-portfolio-feedback:${jobId}`;
  useEffect(() => {
    if (finished.has(jobId)) return;
    try { if (localStorage.getItem(storageKey)) return; } catch { /* The in-memory guard still works without storage. */ }
    let shown = false;
    function check() {
      if (shown || document.visibilityState !== 'visible' || !anchor.current || document.querySelector('dialog[open]')) return;
      if (anchor.current.getBoundingClientRect().top > innerHeight - 64) return;
      shown = true; setOpen(true); trackFeedback('shown');
    }
    const timer = window.setTimeout(check, 0);
    window.addEventListener('scroll', check, { passive: true });
    window.addEventListener('resize', check);
    document.addEventListener('visibilitychange', check);
    return () => { clearTimeout(timer); window.removeEventListener('scroll', check); window.removeEventListener('resize', check); document.removeEventListener('visibilitychange', check); };
  }, [jobId, storageKey]);
  function remember() {
    finished.add(jobId);
    try { localStorage.setItem(storageKey, 'done'); } catch { /* Feedback must not depend on browser storage. */ }
  }
  function close() {
    if (!submitted.current) trackFeedback('dismissed');
    if (popup.current?.contains(document.activeElement) && previousFocus.current?.isConnected) previousFocus.current.focus({ preventScroll: true });
    remember(); setOpen(false);
  }
  useEffect(() => {
    if (!open) return;
    previousFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    popup.current?.focus({ preventScroll: true });
    function escape(event: KeyboardEvent) { if (event.key === 'Escape' && !document.querySelector('dialog[open]')) close(); }
    document.addEventListener('keydown', escape);
    return () => document.removeEventListener('keydown', escape);
  }, [open]);
  function choose(rating: FeedbackRating) {
    if (submitted.current) return;
    submitted.current = true; remember(); trackFeedback('submitted', rating); setAnswered(true);
    popup.current?.focus({ preventScroll: true });
  }
  return <div ref={anchor} className="feedback-anchor">
    {open && <aside ref={popup} tabIndex={-1} className="report-feedback" role="dialog" aria-labelledby="feedback-title" aria-modal="false" data-analytics-section="feedback">
      <button className="feedback-close" aria-label="평가 팝업 닫기" onClick={close}>×</button>
      <h3 id="feedback-title">이번 분석은 어떠셨나요?</h3>
      {answered ? <p role="status">의견 감사합니다!</p> : <div className="feedback-choices">{choices.map(([rating, emoji, label]) => <button key={rating} onClick={() => choose(rating)}><span aria-hidden="true">{emoji}</span>{label}</button>)}</div>}
    </aside>}
  </div>;
}
