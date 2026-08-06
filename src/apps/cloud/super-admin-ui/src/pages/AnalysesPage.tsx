/* 가격 변동 분석 목록 — 종목 중심 (ALPHA-738).
 *
 * 장중에 같은 종목의 분석이 여러 번 생성돼도 이력으로 남는다. 그래서 기본 보기는
 * **종목별 최신**(종목당 한 행)이고, 전량은 `분석 이력` 보기가 답한다 — 시간순으로 평평하게
 * 깔면 같은 종목이 반복돼 목록이 읽히지 않는다.
 *
 * 지키는 선:
 *   · 최신은 **변동 기준 시각**으로 정한다(완료 시각이 아니다). 최신 시도가 실패해도 이전
 *     유효 설명을 지우지 않는다 — 판정은 domains/analyses/symbols 소관이다.
 *   · **실데이터와 목데이터를 한 목록에 섞지 않는다.** 실데이터가 0건일 때만 목 미리보기를
 *     띄우고, 그 행의 링크는 `?preview=mock` 을 달아 상세도 같은 목을 읽게 한다(죽은 링크 금지).
 *   · 게시·발번 등 전달 축은 이 화면의 범위 밖이라 열로 두지 않는다.
 */
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Delta, Icon, PageSkeleton, StatusBadge } from 'ui-kit';
import type { Analysis, AnalysisMarket, AnalysisStatus } from '../domains/analyses';
import { ANALYSIS_STATUS_LABEL, ANALYSIS_STATUS_TONE } from '../domains/analyses';
import { useAnalyses } from '../domains/analyses/hooks';
import { groupBySymbol } from '../domains/analyses/symbols';
import type { SymbolGroup } from '../domains/analyses/symbols';
import { MOCK_ANALYSES } from '../mock/preview';
import { EmptyRealNotice, MockChip, MockPreview } from './_shared/MockPreview';
import { LoadError } from './_shared/LoadError';

type View = 'symbol' | 'history';

/** 목 미리보기에서는 상세도 같은 목을 읽어야 한다 — 주소로 운반한다(새로고침해도 남는다) */
const withPreview = (href: string, mock: boolean) => (mock ? `${href}?preview=mock` : href);

function AnalysesBody({ items, mock = false }: { items: Analysis[]; mock?: boolean }) {
  const navigate = useNavigate();
  const [view, setView] = useState<View>('symbol');
  const [q, setQ] = useState('');
  const [fStatus, setFStatus] = useState<AnalysisStatus | 'ALL'>('ALL');
  const [fMarket, setFMarket] = useState<AnalysisMarket | 'ALL'>('ALL');

  const keyword = q.trim().toLowerCase();
  const match = (a: Analysis) =>
    (fMarket === 'ALL' || a.market === fMarket) &&
    (!keyword || `${a.name}${a.code}`.toLowerCase().includes(keyword));

  const groups = groupBySymbol(items.filter(match)).filter(
    (g) => fStatus === 'ALL' || g.latestAttempt.status === fStatus,
  );
  const history = items
    .filter(match)
    .filter((a) => fStatus === 'ALL' || a.status === fStatus)
    .sort((a, b) => (a.basisTimeAbs < b.basisTimeAbs ? 1 : a.basisTimeAbs > b.basisTimeAbs ? -1 : 0));

  const segBtn = (value: AnalysisMarket | 'ALL', label: string) => (
    <button className={fMarket === value ? 'active' : ''} onClick={() => setFMarket(value)}>
      {label}
    </button>
  );

  return (
    <div className="flex max-w-[1200px] flex-col gap-4">
      {/* 보기 전환 — 같은 데이터의 두 축이다(다른 목록이 아니다) */}
      <div className="seg" role="group" aria-label="보기 전환">
        <button className={view === 'symbol' ? 'active' : ''} onClick={() => setView('symbol')}>
          종목별 최신
        </button>
        <button className={view === 'history' ? 'active' : ''} onClick={() => setView('history')}>
          분석 이력
        </button>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <label className="field field-search">
          <Icon name="search" className="ic" />
          <input placeholder="종목명 · 종목코드 검색" value={q} onChange={(e) => setQ(e.target.value)} />
        </label>
        <select
          className="select"
          value={fStatus}
          onChange={(e) => setFStatus(e.target.value as AnalysisStatus | 'ALL')}
        >
          <option value="ALL">전체 상태</option>
          {(Object.keys(ANALYSIS_STATUS_LABEL) as AnalysisStatus[]).map((s) => (
            <option key={s} value={s}>
              {ANALYSIS_STATUS_LABEL[s]}
            </option>
          ))}
        </select>
        <div className="seg">
          {segBtn('ALL', '전체 시장')}
          {segBtn('KRX', 'KRX')}
          {segBtn('NASDAQ', 'NASDAQ')}
        </div>
        <div className="flex-1" />
        {mock && <MockChip />}
        <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
          {view === 'symbol' ? `종목 ${groups.length}` : `분석 ${history.length}건`}
        </span>
      </div>

      {view === 'symbol' ? (
        <SymbolTable
          groups={groups}
          mock={mock}
          onOpen={(g) => navigate(withPreview(`/analyses/symbol/${g.market}/${g.code}`, mock))}
        />
      ) : (
        <HistoryTable
          items={history}
          mock={mock}
          onOpen={(a) => navigate(withPreview(`/analyses/${a.id}`, mock))}
        />
      )}
    </div>
  );
}

/** 종목별 최신 — 종목당 한 행. 최신 유효 설명과 최신 시도를 함께 말한다 */
function SymbolTable({
  groups,
  mock,
  onOpen,
}: {
  groups: SymbolGroup[];
  mock: boolean;
  onOpen: (g: SymbolGroup) => void;
}) {
  return (
    <div className="card overflow-x-auto">
      <table className="table">
        <thead>
          <tr>
            <th>종목</th>
            <th>시장</th>
            <th className="col-num">최신 변동</th>
            <th>최신 설명 기준</th>
            <th className="col-num">오늘 분석</th>
            <th>최신 유효 결과</th>
            <th>최근 생성</th>
          </tr>
        </thead>
        <tbody>
          {groups.map((g) => (
            <tr key={g.key} className="cursor-pointer" onClick={() => onOpen(g)}>
              <td>
                <div className="flex flex-col gap-px">
                  <span className="font-semibold">
                    {g.name} {mock && <MockChip />}
                  </span>
                  <span className="mono t-xs" style={{ color: 'var(--fg-3)' }}>
                    {g.code}
                  </span>
                </div>
              </td>
              <td>
                <span className="tag">{g.market}</span>
              </td>
              <td className="col-num">
                {g.latestValid ? (
                  <Delta direction={g.latestValid.direction} pct={g.latestValid.changePct} />
                ) : (
                  <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
                    —
                  </span>
                )}
              </td>
              <td className="col-muted num whitespace-nowrap">{g.latestValid?.basisTime ?? '—'}</td>
              <td className="col-num">{g.todayCount}건</td>
              <td>
                {g.latestValid ? (
                  <StatusBadge tone="active">있음</StatusBadge>
                ) : (
                  <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
                    없음
                  </span>
                )}
              </td>
              <td>
                {/* 최신 시도가 실패·진행 중이어도 위의 유효 설명은 그대로 남는다 */}
                <StatusBadge tone={ANALYSIS_STATUS_TONE[g.latestAttempt.status]}>
                  {g.latestAttempt.basisTime} {ANALYSIS_STATUS_LABEL[g.latestAttempt.status]}
                </StatusBadge>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {groups.length === 0 && (
        <div className="p-10 text-center" style={{ color: 'var(--fg-3)', fontSize: 13 }}>
          조건에 맞는 종목이 없습니다.
        </div>
      )}
    </div>
  );
}

/** 분석 이력 — 실행·결과 전량. 기준 시각 최신순 */
function HistoryTable({
  items,
  mock,
  onOpen,
}: {
  items: Analysis[];
  mock: boolean;
  onOpen: (a: Analysis) => void;
}) {
  return (
    <div className="card overflow-x-auto">
      <table className="table">
        <thead>
          <tr>
            <th>종목</th>
            <th>변동 기준 시각</th>
            <th className="col-num">등락률</th>
            <th>실행 상태</th>
            <th>결과</th>
            <th>완료 시각</th>
          </tr>
        </thead>
        <tbody>
          {items.map((a) => (
            <tr key={a.id} className="cursor-pointer" onClick={() => onOpen(a)}>
              <td>
                <div className="flex flex-col gap-px">
                  <span className="font-semibold">
                    {a.name} {mock && <MockChip />}
                  </span>
                  <span className="mono t-xs" style={{ color: 'var(--fg-3)' }}>
                    {a.code} · {a.market}
                  </span>
                </div>
              </td>
              <td className="col-muted num whitespace-nowrap">{a.basisTime}</td>
              <td className="col-num">
                <Delta direction={a.direction} pct={a.changePct} />
              </td>
              <td>
                <StatusBadge tone={ANALYSIS_STATUS_TONE[a.status]}>
                  {ANALYSIS_STATUS_LABEL[a.status]}
                </StatusBadge>
              </td>
              <td className="t-xs" style={{ color: 'var(--fg-3)' }}>
                {a.result.trim() ? '있음' : '없음'}
              </td>
              <td className="col-muted num whitespace-nowrap">{a.doneTime}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {items.length === 0 && (
        <div className="p-10 text-center" style={{ color: 'var(--fg-3)', fontSize: 13 }}>
          조건에 맞는 분석 건이 없습니다.
        </div>
      )}
    </div>
  );
}

export function AnalysesPage() {
  const analysesQuery = useAnalyses();

  if (analysesQuery.isError) return <LoadError error={analysesQuery.error} />;
  if (analysesQuery.isPending) return <PageSkeleton rows={6} />;

  /* 실데이터가 0건이면 목록·보기 전환의 의미를 볼 수 없다 — 사실을 먼저 밝히고 목을 따로 붙인다.
   * 두 벌을 한 표에 섞지 않는다. */
  if (analysesQuery.data.length === 0) {
    return (
      <div className="flex flex-col gap-4">
        <EmptyRealNotice>
          원장(explanation_result)에 기록된 가격 변동 분석이 아직 없습니다.
        </EmptyRealNotice>
        <MockPreview>
          <AnalysesBody items={MOCK_ANALYSES} mock />
        </MockPreview>
      </div>
    );
  }

  return <AnalysesBody items={analysesQuery.data} />;
}
