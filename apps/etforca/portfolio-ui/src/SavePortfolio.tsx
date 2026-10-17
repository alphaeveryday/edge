import { useConfirmation } from './design-system/confirmation';
import { Button } from './design-system/components';
import { useEffect, useRef, useState } from 'react';
import { request } from './api';
import { preview } from './preview';
import dashboardPreview from './dashboard-preview.png';
import type { Saved, Workbook } from './workbook';

export function SavePortfolio({ jobId }: { jobId: string }) {
  const saveButton = useRef<HTMLButtonElement>(null);
  const { ask, confirmation, restoreFocus } = useConfirmation();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => { if (error && !busy) saveButton.current?.focus(); }, [error, busy]);
  async function save() {
    if (busy) return;
    setBusy(true); setError('');
    try {
      const [saved, workbook] = await Promise.all([request<Saved>('/workbook'), request<Workbook>(`/analyses/${jobId}/workbook`)]);
      if (saved.version > 0 && !await ask('저장된 포트폴리오를 바꿀까요? 기존 배당·수익 기록도 함께 바뀌어요.', '바꾸고 저장하기', saveButton.current)) return;
      await request('/workbook', { method: 'POST', body: JSON.stringify({ version: saved.version, workbook }) });
      location.href = preview ? '?preview=dashboard' : '?view=dashboard';
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); restoreFocus(); }
  }
  return <div className="save-portfolio"><h3>내 포트폴리오로 저장</h3><p>보유 종목과 배당, 수익을 이어서 관리하세요.</p><figure className="save-preview"><img src={dashboardPreview} alt="대시보드 예시: 총자산, 평가손익, 자산 구성과 보유 종목" width="1200" height="960" loading="lazy" /><figcaption>대시보드 예시</figcaption></figure><Button ref={saveButton} variant="primary" className="primary" loading={busy} onClick={() => void save()}>{busy ? '저장 중…' : '저장하고 대시보드 보기'}</Button>{error && <p role="alert">{error}</p>}{confirmation}</div>;
}
