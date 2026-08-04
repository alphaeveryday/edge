/* 규칙 엔진 콘솔 홈 (ALPHA-738) — 레퍼런스 edge-console-v4 의 React 포트.
 *
 * 카드는 손으로 고르지 않는다: 규칙 위반 → 인과 병합 → 심각도·연쇄 크기순 정렬의 렌더링이다.
 * 규칙이 안 걸리면 카드도 없다. 상단 판정 문장도 evaluate() 출력에서 생성한다(하드코딩 0).
 *
 * 사실은 현재 동봉 스냅샷(facts-snapshot.json, 목 포함 — MOCK 배지)이다.
 * API 배선 시 이 파일에서 스냅샷 import 만 fetch 로 바뀐다 — 규칙·화면은 그대로.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { evaluate } from '../rules/evaluate';
import { RULES } from '../rules/rules';
import type { Facts, Incident, RunbookEntry, Violation } from '../rules/types';
import factsJson from '../rules/facts-snapshot.json';
import '../styles/console.css';

/* ── 스냅샷 부가 축(규칙 밖 표시용) 타입 ── */
interface FunnelStep {
  stage: string;
  value: number;
  unit: string;
  note?: string;
}
interface DeliveryFacts {
  coverage_0803: { published_without_new_delivery: number; new_delivery_now_nonpublished: number };
  integrity_0803: { delivery_rows: number };
}
type ConsoleFacts = Facts & { news_funnel: FunnelStep[]; delivery: DeliveryFacts };

const F = factsJson as unknown as ConsoleFacts;
const EV = evaluate(F);
const V = EV.violations;
const INCIDENTS = EV.incidents;
const P0 = INCIDENTS.filter((i) => i.sev === 'P0');

const fmt = (n: unknown): string =>
  n == null ? '—' : typeof n === 'number' ? n.toLocaleString('ko-KR') : String(n);
const esc = (s: unknown): string =>
  String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c] as string);
const kst = (iso?: string | null): string =>
  iso
    ? new Intl.DateTimeFormat('ko-KR', {
        timeZone: 'Asia/Seoul',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
      }).format(new Date(iso))
    : '—';

const LEGEND_TIP =
  '<b>부재 4구분</b> <code>0</code> 실측 0 · <code>—</code> 집계 없음 · <b>관측 불가</b> 접근 못 함 · <b>계측 없음</b> 안 남김<br>' +
  '<b>출처</b> DB 원장 · S3 로그 · AWS 제어면 · CODE 설정 · SEED 로컬 시드 · <span class=mk>MOCK 목데이터</span><br>' +
  '<b>시간 축</b> 실행 시각과 데이터 시각(거래일)은 어긋날 수 있다';
const MOCK_TIP =
  '<b class=mk>목데이터가 섞여 있다</b><hr>계측이 없어 규칙이 돌 수 없던 항목을 목값으로 채웠다. 목에 의존하는 규칙은 카드·표에 <span class=mk>MOCK</span>으로 표시된다.<hr>' +
  '<b>목 항목</b> ETF별 분석 원장 · 재시도 정책 상한 · 완전성 분모 확장(가격·수급) · 데이터셋 actual_as_of · 큐→구독 서비스 매핑 · task별 런북 · 백필 런 1건';

/** 위반 상세 툴팁 — 규칙·대상·왜·근거·연쇄·조치(런북 없으면 "런북 미등록")·MOCK/SEED */
function vTip(v: Violation, I?: Incident): string {
  let h = `<b>${esc(v.ruleName)}</b> <code>${v.rule}</code> · ${v.layer} 층 · ${v.sev}<hr>`;
  h += `<b>대상</b> <code>${esc(v.target)}</code><br><b>왜</b> ${esc(v.why)}<br>`;
  if (v.list) h += `<b>목록</b> ${v.list.map(esc).join(' · ')}<br>`;
  if (v.lastok) h += `<b>마지막 정상</b> ${kst(v.lastok)} · 귀결률 ${v.okrate}<br>`;
  h += `<b>근거</b> ${esc(v.evidence)}`;
  if (I && I.members.length) {
    h += `<hr><b>이 사건에 묶인 위반 ${I.members.length}건</b> <span style="color:var(--dim)">— 인과로 연결돼 카드 하나로 세웠다</span><br>`;
    h += I.members
      .map((m) => `<code>${m.v.rule}</code> ${esc(m.v.title)} <span style="color:var(--dim)">← ${esc(m.why)}</span>`)
      .join('<br>');
  }
  const rb: RunbookEntry | undefined = F.runbook[`${v.rule}.${v.target}`] ?? F.runbook[v.rule];
  h +=
    '<hr><b>조치</b> ' +
    (rb
      ? `<code>${esc(rb.cmd)}</code>` + (rb.note ? ` — ${esc(rb.note)}` : '')
      : '<span style="color:var(--dim)">런북 미등록</span>');
  if (v.mock) h += `<hr><span class=mk>MOCK</span> 이 규칙은 목데이터에 의존한다 — 실제 계측: ${esc(v.dep ?? '—')}`;
  if (v.seed) h += '<hr><span style="color:var(--warn)">SEED</span> 로컬 시드 유래';
  return h;
}

type Tab = 'home' | 'run' | 'chain' | 'dataset' | 'trend' | 'delivery';
const TABS: { id: Tab; n: string }[] = [
  { id: 'home', n: '오늘' },
  { id: 'run', n: '실행' },
  { id: 'chain', n: '산출 체인' },
  { id: 'dataset', n: '데이터셋' },
  { id: 'trend', n: '추이' },
  { id: 'delivery', n: '전달' },
];

export function ConsolePage() {
  const [tab, setTab] = useState<Tab>('home');
  const [theme, setTheme] = useState<'dark' | 'light'>('dark');
  const [anchor, setAnchor] = useState<string | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const tipRef = useRef<HTMLDivElement>(null);

  const drill = (t: string, a: string) => {
    setTab(t as Tab);
    setAnchor(a);
  };

  /* L3 드릴다운 — 탭 전환 후 해당 행으로 스크롤 + 하이라이트 */
  useEffect(() => {
    if (!anchor) return;
    const n = document.getElementById(anchor);
    if (n) {
      n.scrollIntoView({ behavior: 'smooth', block: 'center' });
      n.classList.add('flash');
      const t = setTimeout(() => n.classList.remove('flash'), 1700);
      return () => clearTimeout(t);
    }
  }, [tab, anchor]);

  /* L2 툴팁 — data-tip 보유 요소 호버 시 고정 툴팁. 내용은 신뢰 로컬 사실에서 esc() 로 조립 */
  useEffect(() => {
    const tipEl = tipRef.current;
    if (!tipEl) return;
    const show = (html: string, x: number, y: number) => {
      tipEl.innerHTML = html;
      tipEl.style.display = 'block';
      const r = tipEl.getBoundingClientRect();
      tipEl.style.left = `${Math.max(8, Math.min(x + 14, window.innerWidth - r.width - 10))}px`;
      tipEl.style.top = `${y + 18 + r.height > window.innerHeight ? Math.max(8, y - r.height - 12) : y + 18}px`;
    };
    const hide = () => {
      tipEl.style.display = 'none';
    };
    const over = (e: PointerEvent) => {
      const t = (e.target as Element).closest?.('[data-tip]');
      if (t instanceof HTMLElement && t.dataset.tip && rootRef.current?.contains(t))
        show(t.dataset.tip, e.clientX, e.clientY);
    };
    const move = (e: PointerEvent) => {
      if (tipEl.style.display !== 'block') return;
      const t = (e.target as Element).closest?.('[data-tip]');
      if (!(t instanceof HTMLElement) || !t.dataset.tip) {
        hide();
        return;
      }
      show(t.dataset.tip, e.clientX, e.clientY);
    };
    const out = (e: PointerEvent) => {
      const rel = e.relatedTarget as Element | null;
      if (!rel || !rel.closest?.('[data-tip]')) hide();
    };
    document.addEventListener('pointerover', over);
    document.addEventListener('pointermove', move);
    document.addEventListener('pointerout', out);
    addEventListener('scroll', hide, true);
    return () => {
      document.removeEventListener('pointerover', over);
      document.removeEventListener('pointermove', move);
      document.removeEventListener('pointerout', out);
      removeEventListener('scroll', hide, true);
    };
  }, []);

  const view = useMemo(() => {
    switch (tab) {
      case 'home':
        return <Home drill={drill} />;
      case 'run':
        return <RunView />;
      case 'chain':
        return <ChainView />;
      case 'dataset':
        return <DatasetView />;
      case 'trend':
        return <TrendView />;
      case 'delivery':
        return <DeliveryView />;
    }
  }, [tab]);

  return (
    <div className="crx" data-t={theme} ref={rootRef}>
      <div className="chead">
        <span className="i" data-tip={LEGEND_TIP}>i</span>
        <span className="chip c-mock">목데이터 포함</span>
        <span className="i m" data-tip={MOCK_TIP}>i</span>
        <span className="stamp">
          DB {kst(F.meta.db)} · AWS/S3 {kst(F.meta.aws)}
        </span>
        <button className="tbtn" onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}>
          {theme === 'dark' ? '라이트' : '다크'}
        </button>
      </div>
      <nav className="ctabs">
        {TABS.map((t) => (
          <button
            key={t.id}
            className={tab === t.id ? 'on' : ''}
            onClick={() => {
              setTab(t.id);
              setAnchor(null);
              window.scrollTo({ top: 0 });
            }}
          >
            {t.n}
            {t.id === 'home' && P0.length > 0 && <span className="cnt">{P0.length}</span>}
          </button>
        ))}
      </nav>
      {view}
      <div className="crx-tip" ref={tipRef} />
    </div>
  );
}

/* ══ 홈 ══ */
function Home({ drill }: { drill: (t: string, a: string) => void }) {
  const N = Math.max(P0.length, 3);
  const rest = INCIDENTS.slice(N);
  const pub = F.chain.stages.find((s) => s.id === 'c.pub');
  /* 장중 갈래가 체인에 못 들어갔는가 — 첫 비(非)blind 단계 기준 (판정 문장 재료) */
  const firstStage = F.chain.stages.find((s) => !s.blind);
  const intradayFeed = F.chain.feeds[1];
  const intradayStalled = intradayFeed && intradayFeed.v > 0 && firstStage?.intraday === 0;
  const genTip =
    '<b>화면 생성 순서</b><hr>1. 규칙 ' + RULES.length + '개를 사실 위에서 각각 평가 → 위반 목록<br>' +
    '2. 인과 간선으로 위반을 사건으로 병합 (같은 런의 재시도 소진은 런 실패의 결과, 장중 체인 손실은 소비자 부재의 결과)<br>' +
    '3. 사건을 <b>심각도 → 연쇄 크기 → 영향 수치</b>순 정렬<br>4. 상위를 카드로, 나머지를 접힌 목록으로<hr>' +
    '고정된 카드는 하나도 없다. 규칙이 안 걸리면 카드도 안 나온다.';

  return (
    <div>
      <div className="verdict">
        규칙 <b>{RULES.length}</b>개가 위반 <em>{V.length}</em>건을 잡았고, 인과로 묶어 사건{' '}
        <em>{INCIDENTS.length}</em>건입니다 — 그중 P0 <em>{P0.length}</em>건.
        {pub != null && ` 오늘 게시 ${fmt(pub.batch)}종`}
        {intradayStalled && `, 장중 트리거 ${fmt(intradayFeed.v)}건은 체인 진입 전 정지`}.
      </div>
      <div className="vsub">
        카드는 손으로 고른 게 아니라 <b>규칙 위반 → 인과 병합 → 심각도·연쇄 크기순</b>으로 생성됩니다. 내일 뉴스가
        아니라 공시가 깨지면 카드도 그쪽으로 바뀝니다.
        <span className="i" data-tip={genTip}>i</span>
      </div>

      <div className="probs">
        {INCIDENTS.slice(0, N).map((I) => (
          <ProbCard key={I.root.vid} I={I} drill={drill} />
        ))}
      </div>
      {rest.length > 0 && (
        <details className="norm">
          <summary>
            나머지 사건 <b style={{ color: 'var(--warn)' }}>{rest.length}건</b>
            {' — '}P1 {rest.filter((x) => x.sev === 'P1').length} · P2 {rest.filter((x) => x.sev === 'P2').length}
          </summary>
          <div className="nl">
            {rest.map((I) => (
              <div key={I.root.vid} data-tip={vTip(I.root, I)} onClick={() => drill(I.root.drill[0], I.root.drill[1])}>
                <b>{I.root.title}</b>
                {I.sev} · {I.root.rule} · {I.root.kls} · {fmt(I.root.metric)} {I.root.unit}
                {I.members.length > 0 && <span style={{ color: 'var(--dim)' }}> +{I.members.length}</span>}
              </div>
            ))}
          </div>
        </details>
      )}

      <ChainCard />

      <div className="card">
        <h2>
          규칙 실행 결과 <span className="r">규칙은 코드에, 사실은 데이터에 — 카드는 이 결과의 렌더링일 뿐</span>
        </h2>
        <div className="rulebar">
          {EV.rules.map((R) => {
            const rule = RULES.find((x) => x.id === R.id)!;
            const tip =
              `<b>${esc(R.name)}</b> <code>${R.id}</code> · ${R.layer} 층 · 기본 ${rule.base}<hr>` +
              `<b>조건</b> ${esc(rule.desc)}<br><b>분류</b> ${esc(rule.kls)}<br>` +
              `<b>결과</b> ` +
              (!R.evaluated
                ? '<span style="color:var(--warn)">평가 불가 — 필요한 사실 축이 없다(계측 없음 ≠ 위반 0)</span>'
                : R.violations
                  ? `위반 ${R.violations}건`
                  : '<span style="color:var(--ok)">위반 없음 — 이 규칙은 오늘 조용하다</span>') +
              (rule.dep ? `<hr><span class=mk>계측 의존</span> ${esc(rule.dep)} — 현재 목데이터로 대체` : '');
            return (
              <span
                key={R.id}
                className={(R.violations ? 'hit ' : '') + (R.depends_on_mock ? 'mockdep' : '')}
                data-tip={tip}
              >
                {R.id} {R.name}
                {R.violations > 0 && ` ·${R.violations}`}
              </span>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function ProbCard({ I, drill }: { I: Incident; drill: (t: string, a: string) => void }) {
  const v = I.root;
  return (
    <div
      className={'prob ' + (I.sev === 'P1' ? 'p1' : I.sev === 'P2' ? 'p2' : '')}
      onClick={() => drill(v.drill[0], v.drill[1])}
    >
      <div className="top">
        <span className="sev">{I.sev}</span>
        <span className="rid">{v.rule}</span>
        <span className={'i' + (v.mock ? ' m' : '')} data-tip={vTip(v, I)}>i</span>
        <span className="kls">{v.kls}</span>
        {I.members.length > 0 && <span className="chn">연쇄 +{I.members.length}</span>}
      </div>
      <h3>{v.title}</h3>
      <div className={'m' + (typeof v.metric !== 'number' ? ' txt' : '')}>{fmt(v.metric)}</div>
      <div className="u">{v.unit}</div>
      <div className="go">상세 →</div>
    </div>
  );
}

/* ── 산출 체인 (단일 체인, 두 피드) ── */
function ChainCard() {
  const S = F.chain.stages;
  const chainTip =
    '<b>체인은 하나다</b><hr>배치 트리거와 장중 트리거는 별개 흐름이 아니라 <b>같은 체인에 들어오는 두 갈래 입력</b>이다 — <code>etf_contribution_observation</code>이 두 트리거 FK를 모두 갖는다.<hr>파란 = 배치 · 주황 = 장중';
  return (
    <div className="card" id="chain-card">
      <h2>
        설명 생산 체인 <span className="i" data-tip={chainTip}>i</span>
        <span className="r">파랑 배치 · 주황 장중</span>
      </h2>
      <div className="chainrow">
        <div className="feedcol">
          {F.chain.feeds.map((f, i) => (
            <div
              key={f.id}
              className={'feed ' + (i ? 'fi' : 'fb')}
              data-tip={`<b>${esc(f.label)}</b><hr><b>단위</b> ${esc(f.unit)}<br><b>출처</b> <code>${esc(f.src)}</code>${f.note ? `<br>${esc(f.note)}` : ''}`}
            >
              <div className="n">{fmt(f.v)}</div>
              <div className="l">{f.label}</div>
            </div>
          ))}
        </div>
        {S.map((s, i) => {
          const prevB = i === 0 ? F.chain.feeds[0]?.v : S[i - 1].batch;
          const prevI = i === 0 ? F.chain.feeds[1]?.v : S[i - 1].intraday;
          const lb = prevB != null && s.batch != null && s.batch < prevB ? prevB - s.batch : 0;
          const li = prevI != null && s.intraday != null && s.intraday < prevI ? prevI - s.intraday : 0;
          return (
            <span key={s.id} style={{ display: 'contents' }}>
              <div
                className={'stg' + (s.blind ? ' blind' : '')}
                id={'chain-' + s.id}
                data-tip={
                  `<b>${esc(s.label)}</b><hr><b>출처</b> <code>${esc(s.src)}</code>` +
                  (s.note ? `<br>${esc(s.note)}` : '') +
                  (s.blind ? '<br><b>관측 불가</b> 클라우드에 소비 확인 채널이 없다' : '')
                }
              >
                <div className={'n' + (s.batch === 0 && s.intraday === 0 ? ' z' : '')}>
                  {s.blind ? (
                    '?'
                  ) : (
                    <>
                      {fmt(s.batch)}
                      <span className="s"> / {fmt(s.intraday)}</span>
                    </>
                  )}
                </div>
                <div className="l">{s.label}</div>
              </div>
              {i < S.length - 1 && (
                <div className={'gp' + (lb || li ? '' : ' ok')}>
                  {lb || li ? (
                    <>
                      {lb > 0 && `−${lb}`}
                      {lb > 0 && li > 0 && <br />}
                      {li > 0 && <span style={{ color: 'var(--warn)' }}>−{li}</span>}
                    </>
                  ) : (
                    '·'
                  )}
                </div>
              )}
            </span>
          );
        })}
      </div>
      <div className="mini">
        각 칸은 <b>배치 / 장중</b>. 장중은 관측 단계에서 전량이 사라진다 — 체인에 못 들어간 것이지 체인에서 실패한 게
        아니다.
      </div>
    </div>
  );
}

/* ══ 실행 축 ══ */
function RunView() {
  return (
    <div>
      <div className="card">
        <h2>
          런 <span className="r">정규 / 수동 / 백필</span>
        </h2>
        <table>
          <thead>
            <tr>
              <th>런</th><th>종류</th><th>거래일</th><th>원장</th><th>AWS</th><th>마감</th>
            </tr>
          </thead>
          <tbody>
            {F.runs.map((r) => {
              const kc = { scheduled: 'c-dim', manual: 'c-warn', backfill: 'c-mock' }[r.kind];
              const lc = r.no_run_row
                ? 'c-bad'
                : ({ SUCCEEDED: 'c-ok', FAILED: 'c-bad', TIMED_OUT: 'c-bad', RUNNING: 'c-warn' } as Record<string, string>)[
                    r.ledger_status ?? ''
                  ] ?? 'c-dim';
              return (
                <tr key={r.id} id={'run-' + r.id}>
                  <td className="mono">
                    {r.id} {r.mock && <span className="chip c-mock">MOCK</span>}
                  </td>
                  <td><span className={'chip ' + kc}>{r.kind}</span></td>
                  <td>{r.trading_date}</td>
                  <td><span className={'chip ' + lc}>{r.no_run_row ? '행 없음' : r.ledger_status ?? '—'}</span></td>
                  <td>
                    {r.aws_status ? (
                      <span className={'chip ' + (r.aws_status === 'SUCCEEDED' ? 'c-ok' : 'c-bad')}>{r.aws_status}</span>
                    ) : (
                      '—'
                    )}
                  </td>
                  <td style={{ color: 'var(--dim)' }}>{r.deadline ? kst(r.deadline) : '—'}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        <div className="mini">
          백필은 흐름이 아니라 <b>실행 방식</b>이다 — 같은 체인을 과거 날짜로 다시 돌린다. 산출 축의 숫자에는 "어느 런이
          만든 것인가"가 붙어야 한다.
        </div>
      </div>

      <div className="card">
        <h2>
          오늘 작업 {F.tasks.length}건 <span className="r">숫자에 마우스를 올리면 단위</span>
        </h2>
        <table>
          <thead>
            <tr>
              <th>작업</th><th>스테이지</th><th>결과</th><th className="num">out</th><th className="num">failed</th>
              <th>data</th><th>완전성</th><th className="num">시도</th>
            </tr>
          </thead>
          <tbody>
            {F.tasks.map((x) => {
              const oc =
                ({ FULFILLED: 'c-ok', FAILED: 'c-bad', PENDING: 'c-dim', BLOCKED: 'c-warn' } as Record<string, string>)[
                  x.task_outcome
                ] ?? 'c-dim';
              const dc =
                ({ VALID: 'c-ok', INCOMPLETE: 'c-warn', UNKNOWN: 'c-dim' } as Record<string, string>)[
                  x.data_status ?? ''
                ] ?? 'c-dim';
              return (
                <tr key={x.task_key} id={'task-' + x.task_key}>
                  <td className="mono">{x.task_key}</td>
                  <td style={{ color: 'var(--dim)' }}>{x.stage}</td>
                  <td><span className={'chip ' + oc}>{x.task_outcome}</span></td>
                  <td className="num">{fmt(x.records_out)}</td>
                  <td className="num" style={x.failed_records ? { color: 'var(--warn)' } : undefined}>
                    {x.failed_records ? fmt(x.failed_records) : x.failed_records === 0 ? '0' : '—'}
                  </td>
                  <td><span className={'chip ' + dc}>{x.data_status}</span></td>
                  <td>
                    {x.completeness_expected != null ? (
                      <>
                        {x.completeness_received}/{x.completeness_expected}
                        {x.cmpl_mock && <span className="chip c-mock"> M</span>}
                      </>
                    ) : (
                      <span style={{ color: 'var(--dim)' }}>분모 없음</span>
                    )}
                  </td>
                  <td className="num" style={{ color: 'var(--dim)' }}>
                    {x.attempts}
                    {x.max_retries != null && `/${x.max_retries}`}
                    {x.retry_mock && <span className="chip c-mock"> M</span>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* ══ 체인 상세 ══ */
function ChainView() {
  const rows = F.etf_ledger?.rows ?? [];
  const failN = rows.filter((r) => r.outcome === 'FAILED').length;
  const pubN = rows.filter((r) => r.published).length;
  return (
    <div>
      <ChainCard />
      <div className="card" id="etf-ledger">
        <h2>
          ETF별 분석 귀결 <span className="chip c-mock">MOCK</span>
          <span
            className="i m"
            data-tip={`<b class=mk>목데이터</b><hr>${esc(F.etf_ledger?.why ?? '')}<hr>이것이 있으면 "미생성 ${failN}종이 어느 ETF인지"에 답할 수 있고, R15 규칙이 대상을 식별한다.`}
          >i</span>
          <span className="r">
            {rows.length}종 · 게시 {pubN} / 실패 {failN} / 트리거 없음 {rows.filter((r) => !r.triggered).length}
          </span>
        </h2>
        <div className="grid33">
          {rows.map((r) => (
            <div
              key={r.etf}
              className={'etf ' + (r.outcome === 'FAILED' ? 'f' : r.published ? 'p' : '')}
              data-tip={
                `<b>${esc(r.name)}</b> <code>${esc(r.etf)}</code><hr>` +
                `<b>트리거</b> ${r.triggered ? '발화' : '미발화(정상변동)'}<br><b>귀결</b> ${r.outcome}` +
                (r.error ? `<br><b>오류</b> <code>${esc(r.error)}</code>` : '') +
                `<br><b>게시</b> ${r.published ? 'PUBLISHED' : '—'} · <b>전달</b> ${r.delivered ? 'NEW' : '—'}`
              }
            >
              <div className="nm">{r.name}</div>
              <div className="st">{r.outcome === 'FAILED' ? '실패' : r.published ? '게시' : '트리거 없음'}</div>
            </div>
          ))}
        </div>
      </div>

      <div className="card">
        <h2>큐 · 구독 서비스</h2>
        <table>
          <thead>
            <tr>
              <th>큐</th><th>용도</th><th className="num">대기</th><th className="num">in-flight</th>
              <th className="num">DLQ</th><th>구독 서비스</th>
            </tr>
          </thead>
          <tbody>
            {(F.queues ?? []).map((q) => (
              <tr key={q.name} id={'q-' + q.name}>
                <td className="mono">{q.name}</td>
                <td style={{ color: 'var(--dim)' }}>{q.purpose}</td>
                <td className="num" style={q.visible ? { color: 'var(--bad)', fontWeight: 600 } : undefined}>
                  {q.visible}
                </td>
                <td className="num">{q.in_flight}</td>
                <td className="num">{q.dlq}</td>
                <td>
                  {(q.subscribers ?? []).length ? (
                    q.subscribers!.map((s) => (
                      <span key={s} className="chip c-ok">{s} </span>
                    ))
                  ) : (
                    <span className="chip c-bad">없음</span>
                  )}{' '}
                  {q.sub_mock && <span className="chip c-mock">M</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="mini">
          큐→서비스 매핑이 <span className="chip c-mock">MOCK</span>인 이유: 어디에도 선언돼 있지 않아 "소비자 미배포"를
          사람이 판단해야 했다. 이 매핑이 생기면 R11이 자동으로 돈다.
        </div>
      </div>
    </div>
  );
}

/* ══ 데이터셋 축 ══ */
function DatasetView() {
  const byDataset = new Map<string, typeof F.tasks>();
  for (const t of F.tasks) {
    const d = t.dataset ?? '—';
    if (!byDataset.has(d)) byDataset.set(d, []);
    byDataset.get(d)!.push(t);
  }
  return (
    <div>
      <div className="card">
        <h2>
          데이터셋 신선도 <span className="r">멘토: "왜 모든 데이터가 하루 주기야?"</span>
        </h2>
        <table>
          <thead>
            <tr>
              <th>데이터셋</th><th>expected_as_of</th><th>actual_as_of</th><th>수집 시각</th><th>상태</th><th>다음 실행</th>
            </tr>
          </thead>
          <tbody>
            {F.datasets.map((d) => {
              let st = 'FRESH';
              let cls = 'c-ok';
              if (d.unverifiable) {
                st = '판정 불가';
                cls = 'c-dim';
              } else if (!d.contract) {
                st = '계약 없음';
                cls = 'c-dim';
              } else if (d.window_contract) {
                st = '창 계약';
                cls = 'c-warn';
              } else if (d.actual_as_of != null && d.expected_as_of != null && d.actual_as_of < d.expected_as_of) {
                st = 'STALE';
                cls = 'c-bad';
              }
              return (
                <tr key={d.id} id={'ds-' + d.id}>
                  <td className="mono">
                    {d.id} {d.mock && <span className="chip c-mock">M</span>}
                  </td>
                  <td>{d.expected_as_of ?? '—'}</td>
                  <td>{d.actual_as_of ?? <span style={{ color: 'var(--dim)' }}>근거 없음</span>}</td>
                  <td style={{ color: 'var(--dim)' }}>{kst(d.collected_at)}</td>
                  <td><span className={'chip ' + cls}>{st}</span></td>
                  <td style={{ color: 'var(--dim)' }}>{d.next_run ?? '—'}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        <div className="mini">
          실제로는 계약 등록이 <b>1건</b>(KRX holdings)뿐이고 그마저 actual 근거가 없어 영구 UNKNOWN이다 — 위 표의
          actual_as_of는 <span className="chip c-mock">MOCK</span>. 이 축이 배선돼야 R08(STALE)이 실제로 돈다.
        </div>
      </div>

      <div className="card">
        <h2>수집 → 정제 → 적재 (데이터셋별 배치 흐름)</h2>
        <table>
          <thead>
            <tr>
              <th>데이터셋</th><th>작업</th><th>결과</th>
            </tr>
          </thead>
          <tbody>
            {[...byDataset.entries()].map(([d, xs]) => (
              <tr key={d}>
                <td className="mono">{d}</td>
                <td style={{ fontSize: 11.5, color: 'var(--dim)' }}>{xs.map((x) => x.task_key).join(' → ')}</td>
                <td>
                  {xs.every((x) => x.task_outcome === 'FULFILLED') ? (
                    <span className="chip c-ok">전건 귀결</span>
                  ) : (
                    <span className="chip c-bad">미귀결 포함</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="mini">
          <b>여기가 진짜 배치다</b> — 데이터셋별 수집·정제·적재. 앞 화면의 체인(트리거→게시)은 배치가 아니라{' '}
          <b>설명 생산 흐름</b>이고, 이 데이터셋들이 그 재료다.
        </div>
      </div>
    </div>
  );
}

/* ══ 추이 ══ */
function TrendView() {
  return (
    <div>
      <div className="card">
        <h2>
          산출 델타 <span className="r">직전 10영업일 중앙값 대비 · R13</span>
        </h2>
        <table>
          <thead>
            <tr>
              <th>지표</th><th className="num">오늘</th><th className="num">평소</th><th className="num">편차</th><th>판정</th>
            </tr>
          </thead>
          <tbody>
            {F.outputs.map((o) => {
              const p = o.base ? Math.round(((o.today - o.base) / o.base) * 100) : null;
              const hit = p !== null && Math.abs(p) >= 25;
              return (
                <tr key={o.id} id={'out-' + o.id}>
                  <td>{o.label}</td>
                  <td className="num">{fmt(o.today)}</td>
                  <td className="num" style={{ color: 'var(--dim)' }}>{fmt(o.base)}</td>
                  <td className="num" style={hit ? { color: 'var(--bad)', fontWeight: 650 } : { color: 'var(--dim)' }}>
                    {p === null ? '—' : `${p > 0 ? '+' : ''}${p}%`}
                  </td>
                  <td>{hit ? <span className="chip c-bad">분포 밖</span> : <span className="chip c-dim">정상 범위</span>}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="card">
        <h2>뉴스 계보 — 단계별</h2>
        <div className="chainrow">
          {F.news_funnel.map((x, i) => (
            <span key={x.stage} style={{ display: 'contents' }}>
              <div className="stg" data-tip={`<b>단위</b> ${esc(x.unit)}` + (x.note ? `<br>${esc(x.note)}` : '')}>
                <div className="n">{fmt(x.value)}</div>
                <div className="l">{x.stage.replace(/\(.*\)/, '')}</div>
              </div>
              {i < F.news_funnel.length - 1 && (
                <div className={'gp' + (x.value - F.news_funnel[i + 1].value > 0 ? '' : ' ok')}>
                  {x.value - F.news_funnel[i + 1].value > 0 ? `−${fmt(x.value - F.news_funnel[i + 1].value)}` : '·'}
                </div>
              )}
            </span>
          ))}
        </div>
        <div className="mini">
          <b style={{ color: 'var(--warn)' }}>실질 탈락은 유니버스 매칭(in_universe)</b> — 앞의 감소는 창 겹침 dedup과 축
          차이다.
        </div>
      </div>
    </div>
  );
}

/* ══ 전달 ══ */
function DeliveryView() {
  const c1 = F.delivery.coverage_0803;
  const cells: [string | number, string][] = [
    [F.delivery.integrity_0803.delivery_rows, '전달 행'],
    [c1.published_without_new_delivery, '게시·미발번'],
    ['관측 불가', '소비자 수신'],
    ['0 (+시드 1)', '운영 테넌트'],
  ];
  return (
    <div>
      <div className="big">
        {cells.map(([n, l]) => (
          <div key={l}>
            <div className="n" style={typeof n === 'string' ? { fontSize: 16 } : undefined}>{fmt(n)}</div>
            <div className="l">{l}</div>
          </div>
        ))}
      </div>
      <div className="card" id="b-dlv">
        <h2>경계 정합 · R14</h2>
        <table>
          <thead>
            <tr>
              <th>검사</th><th className="num">건</th><th>해석</th>
            </tr>
          </thead>
          <tbody>
            <tr id="b-pub">
              <td>게시됐는데 미발번</td>
              <td className="num">{c1.published_without_new_delivery}</td>
              <td style={{ color: 'var(--dim)' }}>같은 트랜잭션이라 구조상 0</td>
            </tr>
            <tr>
              <td>전달됐는데 현재 비게시</td>
              <td className="num" style={{ color: 'var(--warn)' }}>{c1.new_delivery_now_nonpublished}</td>
              <td style={{ color: 'var(--dim)' }}>{F.boundary.seed_note}</td>
            </tr>
            <tr>
              <td>소비 커서 행</td>
              <td className="num">{F.boundary.sync_cursor_rows}</td>
              <td style={{ color: 'var(--dim)' }}>
                writer가 없어 <b>기록하지 않음</b> — "pull 안 함"이 아니다
              </td>
            </tr>
          </tbody>
        </table>
        <div className="mini">
          전달 이후(온프렘 심사 → 게시 → 소비자 노출)는 관측 경계 밖 — "전달 완료"가 곧 "읽혔다"가 아니다.
        </div>
      </div>
    </div>
  );
}
