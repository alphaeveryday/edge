import { useEffect, useRef, useState } from 'react';
import { Delta, Toaster, toast, formatDelta } from 'ui-kit';
import edgeLogoUrl from 'ui-kit/assets/edge-logo-white.svg';
import { QUEUE, REPORTS } from './data/reports.ts';
import { DEMO_BASE, MIN, direction, relative, type FeedItem } from './report.ts';
import { ReportDetail } from './components/ReportDetail';
import { SectorLogo } from './components/SectorLogo';

/** 시연 중 새 리포트가 도착하는 간격 */
const ARRIVAL_MS = 25_000;
const LOAD = Date.now();
/** 데모 시계 — DEMO_BASE 에서 시작해 실제 경과 시간만큼 흐른다. */
const clock = () => DEMO_BASE + (Date.now() - LOAD);

function initialItems(): FeedItem[] {
  const now = clock();
  // 최근 세 건은 안 읽음. 첫 건은 열자마자 선택되므로 읽음으로 시작한다.
  return REPORTS.map(({ ago, ...r }, i) => ({ ...r, id: 'r' + i, recv: now - ago * MIN, read: i === 0 || i > 2, fresh: false }));
}

const NAV = [
  {
    section: '리포트',
    items: [
      { view: 'all', label: '실시간 리포트', icon: <path d="M22 12h-4l-3 9L9 3l-3 9H2" /> },
      {
        view: 'archive',
        label: '지난 리포트',
        icon: (
          <>
            <path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z" />
            <path d="M14 2v4a2 2 0 0 0 2 2h4" />
            <path d="M16 13H8" />
            <path d="M16 17H8" />
          </>
        ),
      },
    ],
  },
  {
    section: '안내',
    items: [
      {
        view: 'guide',
        label: '고객 안내 기준',
        icon: (
          <path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z" />
        ),
      },
    ],
  },
] as const;

// 데모는 오늘 실시간 리포트 한 화면만 제공한다 — 나머지 메뉴는 안내 토스트로 답한다.
const NAV_NOTICE: Record<string, string> = {
  archive: '데모에서는 오늘 리포트만 제공합니다.',
  guide: '고객 안내 기준은 시범 운영 때 함께 정리합니다.',
};

export function App() {
  const [items, setItems] = useState<FeedItem[]>(initialItems);
  const [selected, setSelected] = useState(items[0].id);
  const [now, setNow] = useState(clock);
  const queued = useRef(0);
  const detailRef = useRef<HTMLElement>(null);

  useEffect(() => {
    const arrive = setInterval(() => {
      if (queued.current >= QUEUE.length) return;
      const i = queued.current++;
      const r: FeedItem = { ...QUEUE[i], id: 'q' + i, recv: clock(), fresh: true, read: false };
      setItems((prev) => [r, ...prev]);
      setNow(clock());
      toast(`새 리포트 · ${r.name} ${formatDelta(direction(r.pct), Math.abs(r.pct))}`);
    }, ARRIVAL_MS);
    const tick = setInterval(() => setNow(clock()), 15_000);
    return () => {
      clearInterval(arrive);
      clearInterval(tick);
    };
  }, []);

  // 도착 강조(.fresh)는 애니메이션 한 번이면 끝난다 — 이후 다시 그려질 때 반복되지 않게 걷는다.
  useEffect(() => {
    if (!items.some((r) => r.fresh)) return;
    const t = setTimeout(() => setItems((prev) => prev.map((r) => (r.fresh ? { ...r, fresh: false } : r))), 2500);
    return () => clearTimeout(t);
  }, [items]);

  const select = (id: string) => {
    setSelected(id);
    setItems((prev) => prev.map((r) => (r.id === id && !r.read ? { ...r, read: true } : r)));
    detailRef.current?.scrollTo({ top: 0 });
  };

  const unread = items.filter((r) => !r.read).length;
  const latest = Math.max(...items.map((r) => r.recv));
  const report = items.find((r) => r.id === selected);

  return (
    <div className="shell">
      <aside>
        <div className="brand">
          <span className="mark">QB</span>
          <div className="brand-name">쿼터백자산운용</div>
        </div>
        <nav className="side-nav">
          {NAV.map((s) => (
            <div key={s.section}>
              <div className="nav-section">{s.section}</div>
              {s.items.map((n) => (
                <button
                  key={n.view}
                  type="button"
                  className={`nav-item${n.view === 'all' ? ' active' : ''}`}
                  onClick={() => n.view in NAV_NOTICE && toast(NAV_NOTICE[n.view])}
                >
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                    {n.icon}
                  </svg>
                  <span className="nav-label">{n.label}</span>
                  {n.view === 'all' && unread > 0 && <span className="nav-count num">{unread}</span>}
                </button>
              ))}
            </div>
          ))}
        </nav>
        <div className="powered">
          Powered by <img src={edgeLogoUrl} alt="EDGE" />
        </div>
      </aside>
      <div className="main">
        <header>
          <div className="page-title">실시간 리포트</div>
          <div style={{ flex: 1 }} />
          <div className="pill">
            <span className="dot" style={{ background: '#2f7d5b' }} />
            <span>수신 정상</span>
            <span style={{ color: 'var(--fg-4)' }}>· 최근 수신 {relative(now, latest)}</span>
          </div>
          <div className="user">
            <div className="avatar">Q</div>
            <span className="user-name">데모 QFA</span>
            <span className="chip">읽기 전용</span>
          </div>
        </header>
        <div className="demo-bar">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
            <circle cx="12" cy="12" r="10" />
            <path d="M12 16v-4M12 8h.01" />
          </svg>
          <span>
            <b>EDGE 제안용 데모</b> — 쿼터백자산운용 테넌트를 가정한 화면입니다. 리포트의 수치·문구는 시연용 가상
            데이터입니다.
          </span>
        </div>
        <div className="content">
          <section className="feed">
            <div className="feed-list">
              {items.map((r) => (
                <button
                  key={r.id}
                  type="button"
                  className={`row${r.id === selected ? ' sel' : ''}${r.fresh ? ' fresh' : ''}`}
                  onClick={() => select(r.id)}
                >
                  <SectorLogo theme={r.theme} size={32} />
                  <span className="nm">{r.name}</span>
                  <Delta direction={direction(r.pct)} pct={Math.abs(r.pct)} style={{ fontSize: 13 }} />
                </button>
              ))}
            </div>
          </section>
          <section className="detail" ref={detailRef}>
            {report && <ReportDetail key={report.id} report={report} />}
          </section>
        </div>
      </div>
      <Toaster />
    </div>
  );
}
