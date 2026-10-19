import { useEffect, useRef, useState } from 'react';
import { Button } from './design-system/components';
import { NewsletterSignup } from './NewsletterSignup';
import { request } from './api';

const promptKey = 'edge-dashboard-newsletter-prompt';
export function DashboardNewsletter({ blocked }: { blocked: boolean }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const previousFocus = useRef<HTMLElement | null>(null);
  const [ready, setReady] = useState(false);
  const [eligible, setEligible] = useState(false);
  const [shown, setShown] = useState(false);
  useEffect(() => {
    let active = true;
    try { if (localStorage.getItem(promptKey)) return; } catch { /* The in-memory guard also prevents repeated prompts. */ }
    const timer = setTimeout(() => setReady(true), 10000);
    void request<{ subscribed: boolean }>('/workbook/newsletter').then(status => { if (active) setEligible(!status.subscribed); }).catch(() => { /* An unavailable subscription status must not prompt an existing subscriber. */ });
    return () => { active = false; clearTimeout(timer); };
  }, []);
  useEffect(() => {
    if (!ready || !eligible || blocked || shown) return;
    let timer: ReturnType<typeof setTimeout>;
    function open() {
      if (document.hidden || document.querySelector('dialog[open], [role="dialog"]')) { timer = setTimeout(open, 1000); return; }
      previousFocus.current = document.activeElement as HTMLElement;
      dialog.current?.showModal(); setShown(true);
    }
    open();
    return () => clearTimeout(timer);
  }, [ready, eligible, blocked, shown]);
  function remember() { try { localStorage.setItem(promptKey, 'seen'); } catch { /* Dismissal still works without storage. */ } }
  function close() { remember(); dialog.current?.close(); previousFocus.current?.isConnected && previousFocus.current.focus({ preventScroll: true }); }
  return <dialog ref={dialog} className="followup-dialog dashboard-newsletter" aria-labelledby="dashboard-newsletter-title" onCancel={remember} onClose={remember}>
    <Button className="dialog-close" aria-label="이메일 안내 닫기" onClick={close}>×</Button>
    <h2 id="dashboard-newsletter-title">내 ETF 핵심 소식 이메일로 받기</h2>
    <p>매일 오후 3시 30분, 내 보유 종목의 중요한 변화만 짧게 정리해 드려요.</p>
    <NewsletterSignup onSubscribed={remember} />
    <Button onClick={close}>나중에 할게요</Button>
  </dialog>;
}
