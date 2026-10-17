import { useState } from 'react';
import { request } from './api';
import { preview } from './preview';
import type { Saved, Workbook } from './workbook';

export function SavePortfolio({ jobId }: { jobId: string }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function save() {
    if (busy) return;
    setBusy(true); setError('');
    try {
      const [saved, workbook] = await Promise.all([request<Saved>('/workbook'), request<Workbook>(`/analyses/${jobId}/workbook`)]);
      if (saved.version > 0 && !confirm('저장된 포트폴리오를 이번 분석의 보유 종목으로 바꿀까요? 기존 배당·수익 기록도 함께 바뀝니다.')) return;
      await request('/workbook', { method: 'POST', body: JSON.stringify({ version: saved.version, workbook }) });
      location.href = preview ? '?preview=dashboard' : '?view=dashboard';
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  return <div className="save-portfolio"><h3>이 포트폴리오, 저장해 둘까요?</h3><p>보유 자산·배당·수익을 대시보드에서 한눈에 보세요.</p><div className="save-preview" aria-hidden="true"><span>내 자산</span><div><i /><i /><i /><i /></div><small>자산 · 배당 · 수익 기록</small></div><button className="primary" disabled={busy} onClick={() => void save()}>{busy ? '저장 중…' : '저장하고 대시보드 보기'}</button>{error && <p role="alert">{error}</p>}</div>;
}
