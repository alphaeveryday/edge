import { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { api, ApiError, progress, type Kind, type Overview, type Records, type Detail as DetailData, type State } from './api';
import { Detail } from './Detail';
import './styles.css';

const tabs: { id: Kind; label: string; description: string }[] = [
  { id: 'analyses', label: '분석 요청', description: '사용자가 제출한 구성과 분석 결과를 확인하세요.' },
  { id: 'drafts', label: '저장된 입력', description: '분석 전 단계에서 저장한 입력도 확인할 수 있어요.' },
  { id: 'reports', label: '리포트 신청', description: '이메일 신청 내역입니다. 자동 발송은 연결되어 있지 않아요.' },
];
const states: Record<State, string> = { queued: '대기', running: '분석 중', succeeded: '완료', failed: '실패' };
const date = (value: string) => new Date(value).toLocaleString('ko-KR', { timeZone: 'Asia/Seoul', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });

function App() {
  const [connection, setConnection] = useState<'checking' | 'disconnected' | 'ready'>('checking');
  const [overview, setOverview] = useState<Overview | null>(null);
  const [records, setRecords] = useState<Records | null>(null);
  const [kind, setKind] = useState<Kind>('analyses');
  const [days, setDays] = useState(7);
  const [scope, setScope] = useState('all');
  const [state, setState] = useState('');
  const [page, setPage] = useState(1);
  const [detail, setDetail] = useState<DetailData | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);
  function disconnect() { setConnection('disconnected'); setOverview(null); setRecords(null); setDetail(null); setSelected(null); }

  useEffect(() => {
    if (connection === 'disconnected') return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    let pending = false;
    let failed = false;
    async function poll() {
      clearTimeout(timer);
      if (stopped || pending || document.visibilityState !== 'visible') return;
      pending = true;
      try {
        const [summary, list, expanded] = await Promise.all([
          api<Overview>(`/overview?scope=${scope}`), api<Records>(`/records?kind=${kind}&days=${days}&state=${kind === 'analyses' ? state : ''}&page=${page}&scope=${scope}`),
          selected ? api<DetailData>(`/records/${kind}/${selected}`).catch(e => {
            if (e instanceof ApiError && e.status === 404) return null;
            throw e;
          }) : Promise.resolve(null),
        ]);
        if (!stopped) { setOverview(summary); setRecords(list); setDetail(expanded); setConnection('ready'); if (selected && !expanded) setSelected(null); }
      } catch {
        failed = true;
        if (!stopped) disconnect();
      } finally { pending = false; if (!stopped && !failed) timer = setTimeout(poll, 10000); }
    }
    void poll();
    document.addEventListener('visibilitychange', poll);
    return () => { stopped = true; clearTimeout(timer); document.removeEventListener('visibilitychange', poll); };
  }, [connection, kind, days, state, page, scope, selected, refresh]);

  const tab = tabs.find(t => t.id === kind)!;
  function resetSelection() { setSelected(null); setDetail(null); setRecords(null); setPage(1); }
  if (connection !== 'ready') return <main className="login-page"><div className="login-card"><span className="eyebrow">SSM 전용 관리자</span><h1>{connection === 'checking' ? '연결 확인 중' : '연결이 끊겼어요'}</h1><p role="status">{connection === 'checking' ? '관리자 서버에 연결하고 있어요.' : '관리자 접속 스크립트를 실행한 뒤 다시 연결해 주세요.'}</p>{connection === 'disconnected' && <button className="primary" onClick={() => setConnection('checking')}>다시 연결</button>}</div></main>;
  return <div className="dashboard"><aside className="sidebar"><a className="brand" href="/portfolio/admin.html"><span className="orca-mark">O</span>ORCA<small>운영 대시보드</small></a><nav aria-label="관리 메뉴">{tabs.map(t => <button key={t.id} aria-current={kind === t.id ? 'page' : undefined} onClick={() => { resetSelection(); setKind(t.id); }}>{t.label}<span>↗</span></button>)}</nav><div className="sidebar-bottom"><span>SSM 전용 연결</span><small>접속 종료: 터널 창에서 Ctrl+C</small></div></aside>
    <main className="workspace"><header className="topbar"><div><span className="eyebrow">PORTFOLIO OPERATIONS</span><h1>지금, ORCA</h1></div><div className="sync"><span className="dot" />10초마다 자동 갱신<small>{overview ? date(overview.as_of) + ' 기준 · KST' : '불러오는 중'}</small><button className="quiet" onClick={() => setRefresh(n => n + 1)}>새로고침</button></div></header>
      <div className="filters"><label htmlFor="scope">요청 구분</label><select id="scope" value={scope} onChange={e => { resetSelection(); setOverview(null); setScope(e.target.value); }}><option value="all">전체</option><option value="customers">고객 요청 · 테스트 제외</option><option value="tests">테스트만</option></select><small>분석·리포트 목록과 분석 건수에 적용합니다. 방문자·이메일 지표와 저장된 입력은 전체입니다.</small></div>
      <section className="stats" aria-label="운영 지표">{[
        ['현재 활성 방문자', overview?.active_visitors, '최근 2분 내 활동', 'accent'],
        ['오늘 방문자', overview?.today_visitors, '브라우저 쿠키 기준', ''],
        ['오늘 분석 요청', overview ? Object.values(overview.today_jobs).reduce((a, b) => a + b, 0) : undefined, '추가 채팅 제외', ''],
        ['대기 / 분석 중', overview ? `${overview.queued} / ${overview.running}` : undefined, '현재 전체 요청', ''],
        ['오늘 고유 이메일', overview?.today_unique_emails, '오늘 신청 레코드 · KST', ''],
        ['누적 고유 이메일', overview?.total_unique_emails, '공백 제거·소문자 기준, 사람 수 아님', ''],
        ['리포트 신청 레코드', overview?.total_report_requests, '누적 저장 건수 · 반복 클릭 수 아님', ''],
      ].map(([label, value, hint, style]) => <article className={'stat ' + style} key={String(label)}><span>{label}</span><strong>{value ?? '—'}</strong><small>{hint}</small></article>)}</section>
      <section className="activity"><div><span className="eyebrow">오늘 처리 현황</span><div className="outcomes"><span>완료 <b>{overview?.today_jobs.succeeded ?? '—'}</b></span><span>실패 <b className="red">{overview?.today_jobs.failed ?? '—'}</b></span></div><p className="muted">방문 계측은 배포 이후부터 수집합니다.<br />다른 기기는 별도 방문자로 집계됩니다.</p></div><div className="trend"><header><b>최근 7일</b><span><i /> 방문자 <i className="blue" /> 분석 요청</span></header><div className="bars">{overview?.daily.map(day => { const max = Math.max(1, ...overview.daily.flatMap(d => [d.visitors, d.analyses])); return <div className="bar-day" key={day.day}><div className="bar-pair"><span title={`방문자 ${day.visitors}`} style={{ height: `${day.visitors / max * 70}px` }} /><span title={`분석 요청 ${day.analyses}`} style={{ height: `${day.analyses / max * 70}px` }} /></div><small>{day.day.slice(5).replace('-', '.')}</small><span className="sr-only">방문자 {day.visitors}, 분석 요청 {day.analyses}</span></div>; })}</div></div></section>
      <div className={'content-grid' + (selected ? ' expanded' : '')}><section className="records"><header className="records-head"><div><h2>{tab.label} <span>{records?.total ?? '—'}</span></h2><p className="muted">{tab.description}</p></div><div className="filters"><label className="sr-only" htmlFor="days">조회 기간</label><select id="days" value={days} onChange={e => { resetSelection(); setDays(Number(e.target.value)); }}><option value={1}>오늘</option><option value={7}>최근 7일</option><option value={30}>최근 30일</option><option value={90}>최근 90일</option></select>{kind === 'analyses' && <><label className="sr-only" htmlFor="state">분석 상태</label><select id="state" value={state} onChange={e => { resetSelection(); setState(e.target.value); }}><option value="">모든 상태</option>{Object.entries(states).map(([key, label]) => <option value={key} key={key}>{label}</option>)}</select></>}</div></header>
      {!records ? <p className="empty" role="status">기록을 불러오고 있어요.</p> : !records.items.length ? <div className="empty"><b>이 기간에는 기록이 없습니다.</b><p>다른 기간을 선택하거나 새 요청을 기다려 주세요.</p></div> : <div className="table-wrap"><table><thead><tr><th>방문자</th><th>{kind === 'reports' ? '이메일' : '상태'}</th><th>{kind === 'drafts' ? '저장 시각' : '요청 시각'}</th><th><span className="sr-only">상세</span></th></tr></thead><tbody>{records.items.map(row => <tr className={selected === row.id ? 'selected' : ''} key={row.id}><td><b className="visitor">{row.visitor}</b><small>{row.id.slice(0, 8)}</small>{row.test_label && <span className="badge test" title={row.test_label}>테스트</span>}</td><td>{kind === 'reports' ? <span className="email">{row.email}</span> : <span className={'badge ' + (row.state || 'draft')}>{row.state ? states[row.state] : '입력 저장'}</span>}{row.state && <small>{progress(row, overview?.as_of)}</small>}{row.failure && <small className="red">{row.failure}</small>}</td><td className="date">{date(row.at)}</td><td><button className="open-detail" onClick={() => { setDetail(null); setSelected(row.id); }} aria-label={`${row.visitor} 상세 보기`}>보기 ↗</button></td></tr>)}</tbody></table></div>}
      <footer className="pagination"><span>{records ? `${records.total.toLocaleString()}건 · ${page}페이지` : ''}</span><div><button disabled={page === 1} onClick={() => { setSelected(null); setDetail(null); setRecords(null); setPage(n => n - 1); }}>이전</button><button disabled={!records || page * records.page_size >= records.total} onClick={() => { setSelected(null); setDetail(null); setRecords(null); setPage(n => n + 1); }}>다음</button></div></footer></section>
      {selected && (detail ? <Detail key={kind + ':' + detail.id} data={detail} onClose={() => { setSelected(null); setDetail(null); }} /> : <section className="detail"><button className="quiet" onClick={() => setSelected(null)}>상세 닫기</button><p role="status">상세 내용을 불러오고 있어요.</p></section>)}</div>
    </main></div>;
}
createRoot(document.getElementById('root')!).render(<App />);
