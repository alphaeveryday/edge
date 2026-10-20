import { useId, useState, type FormEvent } from 'react';
import { Notice, Button, TextInput } from './design-system/components';
import { request } from './api';
import { trackAction } from './analytics';
import newsletterExample from './newsletter-example.png';

export function NewsletterSignup({ jobId, showPreview = false, compact = false, onSubscribed }: { jobId?: string; showPreview?: boolean; compact?: boolean; onSubscribed?: () => void }) {
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
      const result = await request<{ status: string; delivery_ready: boolean }>(jobId ? `/analyses/${jobId}/newsletter` : '/workbook/newsletter', {
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
  return <div className="feedback-newsletter newsletter-signup" data-compact={compact || undefined}>
    {!compact && <><h3>뉴스를 찾는 시간은 줄이고,<br />내 ETF의 중요한 변화만 읽으세요.</h3>
    <p>뉴스·공시를 찾고, 가격이 움직인 이유를 정리하는 일은 저희가 할게요. 내 ETF의 핵심만 읽으세요.</p></>}
    {showPreview && <>
      <figure className="newsletter-example"><a href={newsletterExample} target="_blank" rel="noreferrer" aria-label="예시 이메일 크게 보기"><img src={newsletterExample} width="680" height="1100" alt="ETF Orca 예시 이메일: 포트폴리오 하루 수익률, 보유 ETF 핵심 이슈" /></a><figcaption>{compact ? '받게 될 메일 미리보기 ↗' : '이렇게 받아보세요 · 예시 메일 · 눌러서 크게 보기'}</figcaption></figure>

    </>}
    {subscription ? <p role="status">{subscription}</p> : <form onSubmit={subscribe}>
      <label className="sr-only" htmlFor={inputId}>뉴스레터 이메일</label>
      <TextInput id={inputId} type="email" autoComplete="email" placeholder="이메일 주소" maxLength={254} required value={email} onChange={e => setEmail(e.target.value)} />
      <Button variant={compact ? 'primary' : 'secondary'} className="newsletter-submit" type="submit" disabled={sending}>{sending ? '신청 중…' : compact ? '이메일로 받아보기' : '매일 핵심 소식 받기'}</Button>
      <small className="newsletter-consent">신청하면 이메일·보유 종목 이용과 뉴스레터 수신에 동의합니다.</small>
      {error && <Notice tone="error">{error}</Notice>}
    </form>}
    <small>{compact ? '확인 메일로 구독을 시작해요. 언제든 해지할 수 있어요.' : '이메일 확인 후 구독이 시작돼요. 언제든 메일에서 수신 거부할 수 있어요.'}</small>
    {showPreview && !compact && <blockquote className="newsletter-quote"><p>“경기는 전광판에 눈을 고정한 사람이 아니라, 경기장에 집중하는 선수가 이깁니다.”</p><cite><a href="https://www.berkshirehathaway.com/2013ar/2013ar.pdf#page=20" target="_blank" rel="noreferrer">워런 버핏 · 2013년 주주서한, 번역</a></cite></blockquote>}
  </div>;
}
