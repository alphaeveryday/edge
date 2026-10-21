import { useEffect, useRef, useState } from 'react';
import { AppDownloadSheet } from './AppDownloadSheet';
import { DashboardNewsletter } from './DashboardNewsletter';
import { preview } from './preview';

const dismissedUntilKey = preview ? 'edge-preview-app-dismissed-until' : 'edge-app-dismissed-until';

export function DashboardPromotions({ ready, blocked, newsletterEligible }: { ready: boolean; blocked: boolean; newsletterEligible: boolean }) {
  const counted = useRef(false);
  const [returning, setReturning] = useState<boolean | null>(null);
  const [shown, setShown] = useState(false);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (!ready || counted.current) return;
    counted.current = true; // StrictMode and tab changes must not count as another dashboard visit.
    try {
      const key = preview ? 'edge-preview-dashboard-visited' : 'edge-dashboard-visited';
      const visited = localStorage.getItem(key) === 'seen';
      localStorage.setItem(key, 'seen');
      setReturning(visited);
    } catch {
      setReturning(false); // Without a visit history, do not assume this is a return visit.
    }
  }, [ready]);
  useEffect(() => {
    if (!returning || blocked || shown) return;
    let timer: ReturnType<typeof setTimeout>;
    function show() {
      try {
        if (Number(localStorage.getItem(dismissedUntilKey)) > Date.now()) return;
      } catch { return; } // Do not show a promotion when its dismissal history cannot be read.
      if (document.hidden || document.querySelector('dialog[open], [role="dialog"]')) {
        timer = setTimeout(show, 1000);
        return;
      }
      setShown(true);
      setOpen(true);
    }
    timer = setTimeout(show, 3000);
    return () => clearTimeout(timer);
  }, [returning, blocked, shown]);
  function dismiss() {
    try { localStorage.setItem(dismissedUntilKey, String(Date.now() + 24 * 60 * 60 * 1000)); }
    catch { /* Closing still works when browser storage is unavailable. */ }
    setOpen(false);
  }
  if (returning === null) return null;
  return returning
    ? <AppDownloadSheet open={open} onClose={dismiss} />
    : newsletterEligible ? <DashboardNewsletter blocked={blocked} /> : null;
}
