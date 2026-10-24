import { Button, Notice } from './design-system/components';
import { useState } from 'react';
import { request } from './api';
import { preview } from './preview';

export function NewsletterConfirmation({ token }: { token: string }) {
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState('');
  const [ready, setReady] = useState(false);
  async function confirm() {
    if (busy) return;
    setBusy(true); setError('');
    try {
      const result = await request<{ delivery_ready: boolean }>('/newsletter/confirm', { method: 'POST', body: JSON.stringify({ token }) });
      setReady(result.delivery_ready);
      setDone(true);
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  return <main className="app"><article className="ans newsletter-confirmation">
    <small>ETF Orca · 보유 종목 데일리</small>
    <h1>{done ? '이메일 확인을 마쳤어요!' : '매일 내 ETF의 핵심 소식을 받아보세요'}</h1>
    {done ? <><p role="status">{ready ? '보유 종목의 핵심 이슈와 가격 변동 이유를 매일 쉽게 정리해 보내드릴게요.' : '구독 신청이 확인됐어요. 발송 준비가 끝나면 보유 종목 소식을 보내드릴게요.'}</p><p className="muted">이전에 수신을 거부했다면 이전 뉴스레터 하단의 ‘수신 거부’ 링크에서 수신 설정을 다시 허용해 주세요.</p></> : <><p>아래 버튼을 누르면 이메일 확인을 마치고 구독을 시작해요.</p><Button variant="primary" className="primary" disabled={busy} onClick={confirm}>{busy ? '확인 중…' : '이메일 확인하고 구독하기'}</Button></>}
    {error && <Notice tone="error">{error}</Notice>}
    <a href={preview ? '?preview=input' : '/portfolio/'}>포트폴리오로 돌아가기</a>
  </article></main>;
}
