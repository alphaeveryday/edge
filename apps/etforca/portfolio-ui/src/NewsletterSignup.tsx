import { useId, useState, type FormEvent } from 'react';
import { Notice, Button, TextInput } from './design-system/components';
import { request } from './api';
import { trackAction } from './analytics';
import newsletterExample from './newsletter-example.png';

// 결과 화면의 신청(분석별 주소·긴 안내·인용)은 8차 결과 틀에서 빠졌다. 대시보드와 전시 화면의 짧은 신청만 남는다.
export function NewsletterSignup({ showPreview = false, onSubscribed }: { showPreview?: boolean; onSubscribed?: () => void }) {
  const inputId = useId();
  const [email, setEmail] = useState('');
  const [sending, setSending] = useState(false);
  const [subscription, setSubscription] = useState('');
  const [error, setError] = useState('');
  async function subscribe(event: FormEvent) {
    event.preventDefault();
    if (sending) return;
    setSending(true); setError('');
    try {
      const result = await request<{ status: string; delivery_ready: boolean }>('/workbook/newsletter', {
        method: 'POST', body: JSON.stringify({ email: email.trim(), consent: true }),
      });
      if (result.status === 'active' || result.status === 'confirmed') {
        setSubscription((result.status === 'active' ? '이미 구독 중이에요. ' : '이메일 확인을 마쳤어요. ') + (result.delivery_ready
          ? '매일 보유 종목 소식을 보내드릴게요.' : '발송 준비가 끝나면 소식을 보내드릴게요.'));
      } else {
        setSubscription(result.delivery_ready ? '신청했어요! 확인 메일의 링크를 눌러 구독을 시작해 주세요.'
          : '신청했어요! 발송 준비가 끝나면 이메일 확인 링크를 보내드릴게요.');
      }
      trackAction('newsletter_requested'); setEmail(''); onSubscribed?.();
    } catch (e) { setError((e as Error).message); } finally { setSending(false); }
  }
  return <div className="feedback-newsletter newsletter-signup" data-compact>
    {showPreview && <>
      <figure className="newsletter-example"><a href={newsletterExample} target="_blank" rel="noreferrer" aria-label="예시 이메일 크게 보기"><img src={newsletterExample} width="680" height="1100" alt="ETF Orca 예시 이메일: 포트폴리오 하루 수익률, 보유 ETF 핵심 이슈" /></a><figcaption>받게 될 메일 미리보기 ↗</figcaption></figure>

    </>}
    {subscription ? <p role="status">{subscription}</p> : <form onSubmit={subscribe}>
      <label className="sr-only" htmlFor={inputId}>뉴스레터 이메일</label>
      <TextInput id={inputId} type="email" autoComplete="email" placeholder="이메일 주소" maxLength={254} required value={email} onChange={e => setEmail(e.target.value)} />
      <Button variant="primary" className="newsletter-submit" type="submit" disabled={sending}>{sending ? '신청 중…' : '이메일로 받아보기'}</Button>
      <small className="newsletter-consent">신청하면 이메일·보유 종목 이용과 뉴스레터 수신에 동의합니다.</small>
      {error && <Notice tone="error">{error}</Notice>}
    </form>}
    <small>확인 메일로 구독을 시작해요. 언제든 해지할 수 있어요.</small>
  </div>;
}
