import { ensureVisitorSession } from './api';

export function startPresence() {
  let stopped = false;
  let busy = false;
  async function ping() {
    if (stopped || busy || document.visibilityState !== 'visible') return;
    busy = true;
    try {
      await ensureVisitorSession();
      if (stopped || document.visibilityState !== 'visible') return;
      const response = await fetch('/api/portfolio/presence', { method: 'POST', signal: AbortSignal.timeout(10000) });
      if (!response.ok) throw new Error('Presence recording unavailable');
    } catch {
      console.warn('방문 활동 기록을 전송하지 못했습니다. 다음 주기에 재시도합니다.');
    } finally { busy = false; }
  }
  void ping();
  const timer = setInterval(ping, 30000);
  document.addEventListener('visibilitychange', ping);
  return () => { stopped = true; clearInterval(timer); document.removeEventListener('visibilitychange', ping); };
}
