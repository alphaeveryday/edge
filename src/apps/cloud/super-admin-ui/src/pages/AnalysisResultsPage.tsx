/* 분석 결과 목록 — 새 분석 엔진(v2)의 전망·가격변동 분석 행 (ALPHA-1333).
 *
 * 필터는 주소에 둔다 — 흐름 화면 ETF 표가 `?etf=…&from=…` 로 그 ETF 의 그날 목록을 바로 연다.
 * 상태어는 ETF 표와 같은 말을 쓴다(`STATE_LABEL`). 상태·게시·회수는 서버가 정한 값을 그대로 보여 준다.
 */
import { useNavigate, useSearchParams } from 'react-router-dom';
import { PageSkeleton, StatusBadge, type BadgeTone } from 'ui-kit';
import type { AnalysisKind, AnalysisResultRow, AnalysisResultStatus } from '../domains/analysisResults';
import { useAnalysisResults } from '../domains/analysisResults/hooks';
import { STATE_LABEL } from './ops/etfAnalysisView';
import { kst } from './ops/shared';
import { LoadError } from './_shared/LoadError';

export const KIND_LABEL: Record<AnalysisKind, string> = { outlook: '전망', movement: '가격변동 분석' };
export const STATUS_TONE: Record<AnalysisResultStatus, BadgeTone> = {
  completed: 'active',
  failed: 'blocked',
  running: 'neutral',
};
const STATUSES: AnalysisResultStatus[] = ['completed', 'failed', 'running'];
const PAGE_SIZE = 50;

/** KST 오늘(YYYY-MM-DD) — 서버의 기본 기간과 같은 날이다 */
const kstToday = () => new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Seoul' }).format(new Date());

/** 게시 칸 — 테넌트가 받는 게시본인지(서버 판정)와 회수 여부를 따로 보여 준다 */
export function publicationText(r: { published: boolean; publishedAt: string | null; withdrawn: boolean | null }) {
  if (r.withdrawn) return `회수됨 (게시 ${kst(r.publishedAt)})`;
  if (r.published) return `게시 ${kst(r.publishedAt)}`;
  return '게시 안 됨';
}

export function AnalysisResultsPage() {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const kind: AnalysisKind = params.get('kind') === 'movement' ? 'movement' : 'outlook';
  const today = kstToday();
  const from = params.get('from') || today;
  const to = params.get('to') || from;
  const etf = params.get('etf') ?? '';
  const statusParam = params.get('status');
  const status = STATUSES.find((s) => s === statusParam);
  const allSources = params.get('all') === '1';
  const page = Math.max(0, Number(params.get('page')) || 0);

  const { data, isPending, isError, error } = useAnalysisResults({
    kind,
    from,
    to,
    etf: etf || undefined,
    status,
    allSources,
    page,
    size: PAGE_SIZE,
  });

  /** 필터가 바뀌면 첫 페이지로 돌아간다 */
  const set = (key: string, value: string | null) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    if (key !== 'page') next.delete('page');
    setParams(next, { replace: true });
  };

  const pages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;

  return (
    <div className="flex max-w-[1200px] flex-col gap-4">
      <div className="seg" role="group" aria-label="분석 종류">
        {(['outlook', 'movement'] as const).map((k) => (
          <button key={k} className={kind === k ? 'active' : ''} onClick={() => set('kind', k)}>
            {KIND_LABEL[k]}
          </button>
        ))}
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <input
          type="date"
          className="select"
          aria-label="시작일"
          value={from}
          max={to}
          onChange={(e) => set('from', e.target.value)}
        />
        <span className="t-xs">~</span>
        <input
          type="date"
          className="select"
          aria-label="종료일"
          value={to}
          min={from}
          onChange={(e) => set('to', e.target.value)}
        />
        <label className="field field-search">
          <input
            placeholder="ETF 코드"
            defaultValue={etf}
            key={etf}
            onKeyDown={(e) => {
              if (e.key === 'Enter') set('etf', e.currentTarget.value.trim() || null);
            }}
            onBlur={(e) => {
              if (e.currentTarget.value.trim() !== etf) set('etf', e.currentTarget.value.trim() || null);
            }}
          />
        </label>
        <select className="select" value={status ?? ''} onChange={(e) => set('status', e.target.value || null)}>
          <option value="">전체 상태</option>
          {STATUSES.map((s) => (
            <option key={s} value={s}>
              {STATE_LABEL[s]}
            </option>
          ))}
        </select>
        <label className="t-xs flex items-center gap-1">
          <input type="checkbox" checked={allSources} onChange={(e) => set('all', e.target.checked ? '1' : null)} />
          합성 입력 포함
        </label>
        <div className="flex-1" />
        {data && (
          <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
            {data.total.toLocaleString('ko-KR')}건
          </span>
        )}
      </div>

      {isPending ? (
        <PageSkeleton rows={6} />
      ) : isError ? (
        <LoadError error={error} />
      ) : (
        <div className="card overflow-x-auto">
          <table className="table">
            <thead>
              <tr>
                <th>분석 시각</th>
                <th>ETF</th>
                <th>상태</th>
                <th>게시</th>
                <th>요약</th>
              </tr>
            </thead>
            <tbody>
              {data.rows.map((r) => (
                <ResultRow
                  key={r.analysisId}
                  row={r}
                  onOpen={() => navigate(`/analysis-results/${kind}/${encodeURIComponent(r.analysisId)}`)}
                />
              ))}
            </tbody>
          </table>
          {data.rows.length === 0 && (
            <div className="p-10 text-center" style={{ color: 'var(--fg-3)', fontSize: 13 }}>
              조건에 맞는 {KIND_LABEL[kind]}이 없습니다.
            </div>
          )}
        </div>
      )}

      {data && data.total > PAGE_SIZE && (
        <div className="flex items-center justify-end gap-2 t-xs">
          <button className="btn" disabled={page === 0} onClick={() => set('page', String(page - 1))}>
            이전
          </button>
          <span>
            {page + 1} / {pages}
          </span>
          <button className="btn" disabled={page + 1 >= pages} onClick={() => set('page', String(page + 1))}>
            다음
          </button>
        </div>
      )}
    </div>
  );
}

function ResultRow({ row: r, onOpen }: { row: AnalysisResultRow; onOpen: () => void }) {
  return (
    <tr className="cursor-pointer" onClick={onOpen}>
      <td className="col-muted num whitespace-nowrap">{kst(r.analysisAt)}</td>
      <td>
        <div className="flex flex-col gap-px">
          <span className="font-semibold">{r.etfName}</span>
          <span className="mono t-xs" style={{ color: 'var(--fg-3)' }}>
            {r.etfCode}
            {r.dataSource !== 'database' && ` · ${r.dataSource}`}
          </span>
        </div>
      </td>
      <td>
        <StatusBadge tone={STATUS_TONE[r.status]}>{STATE_LABEL[r.status]}</StatusBadge>
      </td>
      <td className="t-xs whitespace-nowrap">{publicationText(r)}</td>
      <td className="t-xs" style={r.status === 'failed' ? { color: 'var(--down)' } : undefined}>
        {r.status === 'failed' ? r.errorHead ?? '—' : r.title ?? '—'}
      </td>
    </tr>
  );
}
