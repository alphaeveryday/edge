import { useEffect, useRef, useState } from 'react';
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
  const [auth, setAuth] = useState<'checking' | 'login' | 'ready'>('checking');
  const [overview, setOverview] = useState<Overview | null>(null);
  const [records, setRecords] = useState<Records | null>(null);
  const [kind, setKind] = useState<Kind>('analyses');
  const [days, setDays] = useState(7);
  const [state, setState] = useState('');
  const [page, setPage] = useState(1);
  const [error, setError] = useState('');
  const [loginError, setLoginError] = useState('');
  const [busy, setBusy] = useState(false);
  const [detail, setDetail] = useState<DetailData | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);
  const authGeneration = useRef(0);
  function clearPrivate() { authGeneration.current++; setAuth('login'); setOverview(null); setRecords(null); setDetail(null); setSelected(null); setError(''); }

  useEffect(() => {
    let stopped = false;
    api<Overview>('/overview').then(value => { if (!stopped) { setOverview(value); setAuth('ready'); } }).catch(e => { if (!stopped) { setAuth('login'); if (!(e instanceof ApiError && e.status === 401)) setLoginError(e.message); } });
    return () => { stopped = true; };
  }, []);
  useEffect(() => {
    if (auth !== 'ready') return;
    const generation = authGeneration.current;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    let pending = false;
    let unauthorized = false;
    async function read<T>(path: string) {
      try { return await api<T>(path); }
      catch (e) {
        // Every response must revoke private UI, even after another request failed first.
        if (generation === authGeneration.current && e instanceof ApiError && e.status === 401) {
          unauthorized = true; clearPrivate(); setLoginError('로그인이 만료되었습니다. 다시 로그인해 주세요.');
        }
        throw e;
      }
    }
    async function poll() {
      clearTimeout(timer);
      if (stopped || generation !== authGeneration.current || document.visibilityState !== 'visible' || pending) return;
      pending = true;
      try {
        const [summary, list, expanded] = await Promise.all([
          read<Overview>('/overview'), read<Records>(`/records?kind=${kind}&days=${days}&state=${kind === 'analyses' ? state : ''}&page=${page}`),
          selected ? read<DetailData>(`/records/${kind}/${selected}`).catch(e => {
            if (e instanceof ApiError && e.status === 404) return null;
            throw e;
          }) : Promise.resolve(null),
        ]);
        if (!stopped && !unauthorized && generation === authGeneration.current) { setOverview(summary); setRecords(list); setDetail(expanded); if (selected && !expanded) setSelected(null); setError(''); }
      } catch (e) {
        if (!stopped && !unauthorized && generation === authGeneration.current) setError((e as Error).message);
      } finally { pending = false; if (!stopped && !unauthorized && generation === authGeneration.current) timer = setTimeout(poll, 10000); }
    }
    void poll();
    document.addEventListener('visibilitychange', poll);
    return () => { stopped = true; clearTimeout(timer); document.removeEventListener('visibilitychange', poll); };
  }, [auth, kind, days, state, page, selected, refresh]);

  async function login(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault(); authGeneration.current++; setBusy(true); setLoginError('');
    const form = event.currentTarget;
    const fields = new FormData(form);
    try { await api('/login', { method: 'POST', body: JSON.stringify({ username: fields.get('username'), password: fields.get('password') }) }); form.reset(); setAuth('ready'); }
    catch (e) { setLoginError((e as Error).message); }
    finally { setBusy(false); }
  }
  async function logout() {
    setBusy(true);
    try { await api('/logout', { method: 'POST' }); clearPrivate(); }
    catch { setError('로그아웃에 실패했습니다. 연결을 확인한 뒤 다시 시도해 주세요.'); }
    finally { setBusy(false); }
  }
  const tab = tabs.find(t => t.id === kind)!;
  function resetSelection() { setSelected(null); setDetail(null); setRecords(null); setPage(1); }
  if (auth !== 'ready') return <main className="login-page"><div className="login-brand"><span className="orca-mark">O</span><span>ORCA <small>OPERATIONS</small></span></div><form className="login-card" onSubmit={login}><span className="eyebrow">관리자 전용</span><h1>운영 현황을<br />한눈에.</h1><p>방문부터 분석 결과까지 확인하세요.</p>{auth === 'checking' ? <p role="status">로그인 상태를 확인하고 있어요.</p> : <><label>아이디<input name="username" autoComplete="username" defaultValue="admin" required /></label><label>비밀번호<input name="password" type="password" autoComplete="current-password" required maxLength={256} /></label>{loginError && <p role="alert" className="error">{loginError}</p>}<button className="primary" disabled={busy}>{busy ? '확인 중…' : '관리자 로그인'}</button></>}<footer>일반 방문자 계정과 별도로 관리됩니다.</footer></form></main>;
  return <div className="dashboard"><aside className="sidebar"><a className="brand" href="/portfolio/admin.html"><span className="orca-mark">O</span>ORCA<small>운영 대시보드</small></a><nav aria-label="관리 메뉴">{tabs.map(t => <button key={t.id} aria-current={kind === t.id ? 'page' : undefined} onClick={() => { resetSelection(); setKind(t.id); }}>{t.label}<span>↗</span></button>)}</nav><div className="sidebar-bottom"><span>관리자</span><button onClick={logout} disabled={busy}>로그아웃</button></div></aside>
    <main className="workspace"><header className="topbar"><div><span className="eyebrow">PORTFOLIO OPERATIONS</span><h1>지금, ORCA</h1></div><div className="sync"><span className={error ? 'dot error-dot' : 'dot'} />{error ? '갱신 지연' : '10초마다 자동 갱신'}<small>{overview ? date(overview.as_of) + ' 기준 · KST' : '불러오는 중'}</small><button className="quiet" onClick={() => setRefresh(n => n + 1)}>새로고침</button></div></header>
      {error && <p className="error banner" role="alert">{error} 표시된 값은 마지막으로 성공한 조회 결과입니다.</p>}
      <section className="stats" aria-label="운영 지표">{[
        ['현재 활성 방문자', overview?.active_visitors, '최근 2분 내 활동', 'accent'],
        ['오늘 방문자', overview?.today_visitors, '브라우저 쿠키 기준', ''],
        ['오늘 분석 요청', overview ? Object.values(overview.today_jobs).reduce((a, b) => a + b, 0) : undefined, '추가 채팅 제외', ''],
        ['대기 / 분석 중', overview ? `${overview.queued} / ${overview.running}` : undefined, '현재 전체 요청', ''],
      ].map(([label, value, hint, style]) => <article className={'stat ' + style} key={String(label)}><span>{label}</span><strong>{value ?? '—'}</strong><small>{hint}</small></article>)}</section>
      <section className="activity"><div><span className="eyebrow">오늘 처리 현황</span><div className="outcomes"><span>완료 <b>{overview?.today_jobs.succeeded ?? '—'}</b></span><span>실패 <b className="red">{overview?.today_jobs.failed ?? '—'}</b></span></div><p className="muted">방문 계측은 배포 이후부터 수집합니다.<br />다른 기기는 별도 방문자로 집계됩니다.</p></div><div className="trend"><header><b>최근 7일</b><span><i /> 방문자 <i className="blue" /> 분석 요청</span></header><div className="bars">{overview?.daily.map(day => { const max = Math.max(1, ...overview.daily.flatMap(d => [d.visitors, d.analyses])); return <div className="bar-day" key={day.day}><div className="bar-pair"><span title={`방문자 ${day.visitors}`} style={{ height: `${day.visitors / max * 70}px` }} /><span title={`분석 요청 ${day.analyses}`} style={{ height: `${day.analyses / max * 70}px` }} /></div><small>{day.day.slice(5).replace('-', '.')}</small><span className="sr-only">방문자 {day.visitors}, 분석 요청 {day.analyses}</span></div>; })}</div></div></section>
      <div className={'content-grid' + (selected ? ' expanded' : '')}><section className="records"><header className="records-head"><div><h2>{tab.label} <span>{records?.total ?? '—'}</span></h2><p className="muted">{tab.description}</p></div><div className="filters"><label className="sr-only" htmlFor="days">조회 기간</label><select id="days" value={days} onChange={e => { resetSelection(); setDays(Number(e.target.value)); }}><option value={1}>오늘</option><option value={7}>최근 7일</option><option value={30}>최근 30일</option><option value={90}>최근 90일</option></select>{kind === 'analyses' && <><label className="sr-only" htmlFor="state">분석 상태</label><select id="state" value={state} onChange={e => { resetSelection(); setState(e.target.value); }}><option value="">모든 상태</option>{Object.entries(states).map(([key, label]) => <option value={key} key={key}>{label}</option>)}</select></>}</div></header>
      {!records ? <p className="empty" role="status">기록을 불러오고 있어요.</p> : !records.items.length ? <div className="empty"><b>이 기간에는 기록이 없습니다.</b><p>다른 기간을 선택하거나 새 요청을 기다려 주세요.</p></div> : <div className="table-wrap"><table><thead><tr><th>방문자</th><th>{kind === 'reports' ? '이메일' : '상태'}</th><th>{kind === 'drafts' ? '저장 시각' : '요청 시각'}</th><th><span className="sr-only">상세</span></th></tr></thead><tbody>{records.items.map(row => <tr className={selected === row.id ? 'selected' : ''} key={row.id}><td><b className="visitor">{row.visitor}</b><small>{row.id.slice(0, 8)}</small></td><td>{kind === 'reports' ? <span className="email">{row.email}</span> : <span className={'badge ' + (row.state || 'draft')}>{row.state ? states[row.state] : '입력 저장'}</span>}{row.state && <small>{progress(row, overview?.as_of)}</small>}{row.failure && <small className="red">{row.failure}</small>}</td><td className="date">{date(row.at)}</td><td><button className="open-detail" onClick={() => { setDetail(null); setSelected(row.id); }} aria-label={`${row.visitor} 상세 보기`}>보기 ↗</button></td></tr>)}</tbody></table></div>}
      <footer className="pagination"><span>{records ? `${records.total.toLocaleString()}건 · ${page}페이지` : ''}</span><div><button disabled={page === 1} onClick={() => { setSelected(null); setDetail(null); setRecords(null); setPage(n => n - 1); }}>이전</button><button disabled={!records || page * records.page_size >= records.total} onClick={() => { setSelected(null); setDetail(null); setRecords(null); setPage(n => n + 1); }}>다음</button></div></footer></section>
      {selected && (detail ? <Detail data={detail} onClose={() => { setSelected(null); setDetail(null); }} /> : <section className="detail"><button className="quiet" onClick={() => setSelected(null)}>상세 닫기</button><p role="status">상세 내용을 불러오고 있어요.</p></section>)}</div>
    </main></div>;
}
createRoot(document.getElementById('root')!).render(<App />);
