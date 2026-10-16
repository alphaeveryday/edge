import { useState } from 'react';
import { request } from './api';

export function NewsletterConfirmation({ token }: { token: string }) {
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState('');
  async function confirm() {
    if (busy) return;
    setBusy(true); setError('');
    try {
      await request('/newsletter/confirm', { method: 'POST', body: JSON.stringify({ token }) });
      setDone(true);
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  return <main className="app"><article className="ans newsletter-confirmation">
    <small>ETF Orca · 보유 종목 데일리</small>
    <h1>{done ? '구독이 시작됐어요!' : '매일 내 ETF의 핵심 소식을 받아보세요'}</h1>
    {done ? <p role="status">보유 종목의 핵심 이슈와 가격 변동 이유를 매일 쉽게 정리해 보내드릴게요.</p> : <><p>아래 버튼을 누르면 이메일 확인을 마치고 구독을 시작해요.</p><button className="primary" disabled={busy} onClick={confirm}>{busy ? '확인 중…' : '이메일 확인하고 구독하기'}</button></>}
    {error && <p role="alert">{error}</p>}
    <a href="/portfolio/">포트폴리오로 돌아가기</a>
  </article></main>;
}
