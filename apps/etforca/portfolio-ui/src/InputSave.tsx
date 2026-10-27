import { useEffect, useRef, useState } from 'react';
import { request } from './api';
import { Button, Notice } from './design-system/components';
import { useConfirmation } from './design-system/confirmation';
import { SeedMoney } from './SeedMoney';
import { canApplySeed, needsSeed, withSeed, type Saved, type Workbook } from './workbook';
import { trackResult } from './analytics';
import { preview } from './preview';

export function InputSave({ jobId, screen, active = true }: { jobId: string; screen: 'result' | 'failed'; active?: boolean }) {
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const visible = useRef(active);
  visible.current = active;
  const [error, setError] = useState('');
  const [seed, setSeed] = useState<Workbook | null>(null);
  const locked = useRef(false);
  const mounted = useRef(true);
  const button = useRef<HTMLButtonElement>(null);
  const shown = useRef(false);
  const { ask, confirmation, restoreFocus } = useConfirmation();
  const dashboard = preview ? '?preview=dashboard&tab=overview' : '?view=dashboard&tab=overview';
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    if (!active || !button.current || shown.current) return;
    const observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting) && !shown.current) {
        shown.current = true;
        trackResult('input_save_shown', { analysis_id: jobId, screen });
        observer.disconnect();
      }
    });
    observer.observe(button.current);
    return () => observer.disconnect();
  }, [active, jobId, screen]);
  async function save(prepared?: Workbook) {
    if (locked.current || saved) return;
    const returnFocus = document.activeElement as HTMLElement;
    locked.current = true; setBusy(true); setError('');
    trackResult('input_save_clicked', { analysis_id: jobId, screen });
    try {
      const workbook = prepared ?? await request<Workbook>(`/analyses/${jobId}/workbook`);
      if (!mounted.current || !visible.current) return;
      if (!workbook.assets.length) throw new Error('저장할 보유 종목이 없어요. 입력한 보유가 있는 분석에서 저장해 주세요.');
      if (needsSeed(workbook)) {
        if (!canApplySeed(workbook)) throw new Error('보유 비중과 금액을 확인해 주세요. 입력 고치기에서 수정할 수 있어요.');
        setSeed(workbook); return;
      }
      const current = await request<Saved>('/workbook');
      if (!mounted.current || !visible.current) return;
      if (current.workbook && !await ask(`‘${current.workbook.title}’의 보유 ${current.workbook.assets.length}개와 배당·월 기록을 이번 입력 보유로 교체할까요?`, '입력한 보유로 교체', returnFocus)) return;
      if (!mounted.current || !visible.current) return;
      const next = await request<Saved>('/workbook', { method: 'POST', body: JSON.stringify({ version: current.version, workbook }) });
      if (!next.workbook || !Number.isSafeInteger(next.version) || next.version <= current.version) throw new Error('저장 응답을 확인하지 못했어요. 저장된 포트폴리오를 확인해 주세요.');
      trackResult('input_save_succeeded', { analysis_id: jobId, screen });
      // Reload the dashboard even when saving from its embedded analysis, so cached holdings cannot win.
      if (mounted.current) {
        setSaved(true); setSeed(null);
        if (visible.current) location.assign(dashboard);
      }
    } catch (e) {
      trackResult('input_save_failed', { analysis_id: jobId, screen });
      if (mounted.current) setError((e as Error).message);
    } finally {
      locked.current = false;
      if (mounted.current) { setBusy(false); if (visible.current) restoreFocus(); }
    }
  }
  return <section className="dashboard-card" aria-label="입력한 포트폴리오 보관" data-analytics-section="input_save">
    <h2>입력한 보유 종목을 이어서 관리해요</h2>
    <p>추천안이 아닌 입력한 보유를 저장해요. 금액 기준으로 자산과 예상 배당을 확인하고, 수량·평균매수가는 나중에 보완할 수 있어요.</p>
    {error && <><Notice tone="error">{error}</Notice><p>입력은 그대로 남아 있어요. 저장된 내용을 확인한 뒤 다시 시도해 주세요. <a href={dashboard}>저장된 포트폴리오 확인</a></p></>}
    {saved && <Notice tone="success">저장했어요. <a href={dashboard}>저장한 포트폴리오 보기</a></Notice>}
    <Button ref={button} variant="primary" loading={busy} disabled={!!seed || saved} onClick={() => void save()}>입력한 포트폴리오 저장</Button>
    {seed && <><SeedMoney busy={busy} onSubmit={amount => {
      try { void save(withSeed(seed, amount)); } catch (e) { setError((e as Error).message); }
    }} /><Button disabled={busy} onClick={() => { setSeed(null); requestAnimationFrame(() => button.current?.focus()); }}>금액 입력 취소</Button></>}
    {confirmation}
  </section>;
}
