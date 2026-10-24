import { useEffect, useState, type ReactNode } from 'react';
import { Button } from './design-system/components';
import { request, ensureVisitorSession, type Job } from './api';
import { trackResult } from './analytics';
import { AppDownloadSheet } from './AppDownloadSheet';
import { Brand, Glyph, Quota } from './flow';
import { dashboardHref, verdictOf } from './Start';
import icon from '../../app-ui/assets/icon.png';

// 막힘 화면(S3·L2·L3·F3·W3 — F1 실패는 결과 쪽 FailedView). 횟수가 실제로 줄지 않는 경우에만 그렇게 적는다:
// 실패한 분석과 서버가 받지 않은 요청(대기열 가득·하루 한도)은 24시간 3번 계산에 들어가지 않는다.
const Kept = () => <p className="fl-badge" data-tone="success"><Glyph name="check" size={14} />횟수는 줄지 않았어요</p>;
function Screen({ title, children, actions, head }: { title: ReactNode; children?: ReactNode; actions: ReactNode; head?: ReactNode }) {
  return <div className="fl" data-surface="plain"><Brand>{head}</Brand><div className="fl-state"><h1>{title}</h1>{children}</div><div className="fl-cta">{actions}</div></div>;
}
// 결정(2026-10-23): '열리면 앱으로 알림'은 그대로 둔다 — 누르면 앱 받기 시트가 열린다.
function AppAlert() {
  const [open, setOpen] = useState(false);
  return <><Button className="fl-app" onClick={() => { setOpen(true); trackResult('app_alert_clicked', { screen: 'limit' }); }}><span className="fl-app__icon"><img src={icon} alt="" width="32" height="32" /><i aria-hidden="true">1</i></span><b>열리면 앱으로 알림 받기</b><span className="fl-app__free">무료<Glyph name="right" size={16} /></span></Button>
    <AppDownloadSheet open={open} onClose={() => setOpen(false)} /></>;
}

export function LimitScreen({ code, busy, onRetry, onEdit, onOpen }: { code: string; busy: boolean; onRetry: () => void; onEdit: () => void; onOpen: (job: Job) => void }) {
  const [latest, setLatest] = useState<Job | null>(null);
  useEffect(() => {
    if (code !== 'REQUEST_LIMIT') return;
    let stopped = false;
    void ensureVisitorSession().then(() => request<Job | null>('/analyses/latest')).then(job => { if (!stopped && job?.state === 'succeeded' && job.result) setLatest(job); }).catch(() => { /* 지난 분석 없이도 안내는 보인다. */ });
    return () => { stopped = true; };
  }, [code]);
  const edit = <Button className="fl-tertiary" onClick={onEdit}>입력 고치기</Button>;
  if (code === 'QUEUE_FULL') return <Screen title={<>지금 분석 요청이<br />많아요</>} actions={<><Button variant="primary" className="fl-go" loading={busy} onClick={onRetry}>다시 시도</Button>{edit}</>}>
    <p>잠시 뒤에 다시 시도해 주세요. 입력한 내용은 그대로 있어요.</p><Kept /></Screen>;
  if (code === 'DAILY_LIMIT') return <Screen title={<>오늘 분석할 수 있는 양을<br />다 썼어요</>} actions={<><AppAlert />{edit}</>}>
    <p>24시간 안에 다시 열려요.<br />내 횟수는 줄지 않았어요.</p><Quota /></Screen>;
  const last = latest && verdictOf(latest), when = latest?.created_at ? new Date(latest.created_at) : null;
  const score = latest?.result?.current_evaluation?.overall;
  return <Screen head={<a className="fl-textlink" href={dashboardHref}>내 포트폴리오 <Glyph name="right" size={16} /></a>} title={<>무료 3번을<br />다 썼어요</>} actions={<><AppAlert />{edit}</>}>
    <p>분석은 24시간에 3번까지 무료예요.</p>
    <section className="fl-card" data-tone="soft" data-align="start"><span className="fl-sublabel">다음 분석은</span><strong className="fl-next">첫 분석 24시간 뒤부터</strong><small>24시간 안에 받은 분석이 3번이에요</small></section>
    {latest && last && <section className="fl-card" data-tone="soft" data-align="start"><h2 className="fl-card__title">최근 받은 분석</h2>
      <Button className="fl-row" onClick={() => onOpen(latest)}><span className="fl-icontile" data-tone="warning"><Glyph name="star" /></span><span><b>{last.headline}</b>
        <small>{[when?.toLocaleString('ko-KR', { month: 'long', day: 'numeric', hour: 'numeric', minute: '2-digit' }), `종목 ${(latest.input?.holdings ?? []).filter(h => h.kind !== 'cash').length}개`, score != null && `${score}점`].filter(Boolean).join(' · ')}</small></span><Glyph name="right" /></Button></section>}
  </Screen>;
}

// F3: 외부 자료 서버가 막혔을 때는 바로 다시 눌러도 같은 이유로 멈춘다 — 1분 기다린 뒤 다시 시도하게 한다. 실패한 분석은 3번 계산에서 빠진다.
export function RateScreen({ job, busy, onRetry, onEdit }: { job: Job; busy: boolean; onRetry: () => void; onEdit: () => void }) {
  const [until] = useState(() => Date.now() + 60000), [now, setNow] = useState(() => Date.now());
  useEffect(() => { const timer = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(timer); }, []);
  const wait = Math.max(0, Math.ceil((until - now) / 1000));
  return <Screen title={<>자료 서버가<br />잠시 응답하지 않아요</>}
    actions={<>{wait > 0 && <p className="fl-cta__hint">시간이 지나면 버튼을 누를 수 있어요</p>}
      {job.input && <Button variant="primary" className="fl-go" loading={busy} disabled={wait > 0} onClick={onRetry}>{wait > 0 ? <><Glyph name="clock" size={18} />{wait}초 뒤에 다시 시도</> : '같은 입력으로 다시 분석'}</Button>}
      <Button className="fl-tertiary" disabled={busy} onClick={onEdit}>입력 고치기</Button></>}>
    <p>1~2분 뒤에 다시 시도해 주세요.</p><Kept /></Screen>;
}

export function LostScreen({ onNew }: { onNew: () => void }) {
  return <Screen title={<>이 브라우저에서는<br />지난 분석을 찾을 수 없어요</>} actions={<><Quota /><Button variant="primary" className="fl-go" onClick={onNew}>새로 분석하기</Button></>}>
    <p>분석을 시작한 브라우저에서 결과 링크로 열어 주세요.</p></Screen>;
}
