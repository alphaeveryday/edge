import { Button } from './design-system/components';
import { useEffect, useRef, useState } from 'react';
import { request } from './api';
import { preview } from './preview';
import dashboardPreview from './dashboard-preview.png';
import { canApplySeed, withSeed, type Saved, type Workbook } from './workbook';
import { SeedMoney } from './SeedMoney';
import { trackAction } from './analytics';

export function SavePortfolio({ jobId }: { jobId: string }) {
  const saveButton = useRef<HTMLButtonElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [pending, setPending] = useState<Workbook | null>(null);
  useEffect(() => { if (error && !busy && !pending) saveButton.current?.focus(); }, [error, busy, pending]);
  async function save(seed?: number) {
    if (busy) return;
    setBusy(true); setError('');
    try {
      const [saved, source] = pending ? [await request<Saved>('/workbook'), pending] : await Promise.all([request<Saved>('/workbook'), request<Workbook>(`/analyses/${jobId}/workbook`)]);
      if (canApplySeed(source) && seed === undefined) { setPending(source); return; }
      const workbook = seed === undefined ? source : withSeed(source, seed);
      await request('/workbook', { method: 'POST', body: JSON.stringify({ version: saved.version, workbook }) });
      trackAction('portfolio_saved');
      location.href = (preview ? '?preview=dashboard' : '?view=dashboard') + '&setup=holdings';
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  return <div className="save-portfolio"><h3>내 포트폴리오를 저장해 두세요</h3><p>입력한 보유 종목은 그대로 관리하고, 이번 추천 결과도 대시보드에서 언제든 다시 볼 수 있어요.</p><figure className="save-preview"><img src={dashboardPreview} alt="대시보드 예시: 총자산, 평가손익, 자산 구성과 보유 종목" width="1200" height="960" loading="lazy" /><figcaption>대시보드 예시</figcaption></figure>{!pending && <Button ref={saveButton} variant="primary" className="primary" loading={busy} onClick={() => void save()}>{busy ? '저장 중…' : '입력한 내 포트폴리오 저장하기'}</Button>}{pending && <SeedMoney busy={busy} onSubmit={amount => void save(amount)} />}{error && <p role="alert">{error}</p>}</div>;
}
