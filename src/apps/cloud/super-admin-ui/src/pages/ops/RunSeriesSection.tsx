/* 데이터셋별 실행 추이 (ALPHA-1284).
 *
 * 답하는 질문: **이 데이터셋의 실행이 지난 실행들과 달라졌는가.** 판정은 붙이지 않는다 —
 * 임계는 추이를 본 뒤에 정한다. 계열을 만드는 판단은 `domains/sources/runSeries.ts` 에 있다
 * (여기 두면 `node --test` 가 import 을 못 한다).
 *
 * 지키는 선:
 *   · 값이 없는 실행은 선을 끊고 바닥에 빈 점으로 둔다 — 0 으로 그리지 않는다.
 *   · 부분 성공(종료 코드 2)은 색과 **높이**가 정상과 다르다(색만으로 말하지 않는다).
 */
import { ALL_DATASETS } from '../../domains/sources/datasetCatalog';
import { useSourceGrid } from '../../domains/sources/hooks';
import { EXIT_LABEL, buildRunSeries, formatDuration } from '../../domains/sources/runSeries';
import type { DatasetRunSeries, ExitState } from '../../domains/sources/runSeries';
import { extent } from './trendSeries';
import { Info } from './shared';

const GRID_DATASETS = ALL_DATASETS.filter((d) => d.inOpsGrid);

const TIP = [
  '점 하나가 실행 하나다. 하루에 여러 번 도는 데이터셋(뉴스·장중 수급)을 하루로 합치지 않는다.',
  '',
  '행 수 — 대표 작업(적재 단계의 첫 작업)이 원장에 적은 건수.',
  '소요 시간 — 그 실행에서 이 데이터셋의 작업들이 쓴 시간의 합. 작업 하나라도 시각이 없으면 값을 내지 않는다.',
  '종료 상태 — 작업들의 종료 코드 중 가장 나쁜 것. 0 은 정상, 2 는 부분 성공, 그 밖은 실패.',
  '대상 수 대비 받은 수 — 원장에 대상 수가 적히는 작업만 낸다. 없으면 분모 없음이다.',
  '',
  '값이 없는 실행은 0 으로 채우지 않고 바닥의 빈 점으로 둔다. 정상·이상 판정은 하지 않는다.',
].join('\n');

const W = 160;
const H = 28;

/** 빈 값에서 선을 끊는 작은 그래프. `dashed` 는 받은 수 옆에 겹쳐 그리는 대상 수다 */
function RunSpark({ label, runs, values, dashed }: {
  label: string;
  runs: string[];
  values: (number | null)[];
  dashed?: (number | null)[];
}) {
  const known = [...values, ...(dashed ?? [])].filter((v): v is number => v !== null);
  if (known.length === 0) return null;
  const [lo, hi] = extent(known);
  const x = (i: number) => (values.length === 1 ? W : (i / (values.length - 1)) * W);
  const y = (v: number) => (1 - (v - lo) / (hi - lo)) * H;
  const line = (series: (number | null)[]) =>
    series
      .map((v, i) => (v === null ? '' : `${i === 0 || series[i - 1] === null ? 'M' : 'L'}${x(i)} ${y(v)}`))
      .join(' ');
  return (
    <svg className="rs-spark" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img"
      aria-label={`${label} — 실행 ${values.length}회 중 값 있는 실행 ${values.filter((v) => v !== null).length}회`}>
      {dashed && <path d={line(dashed)} className="rs-line rs-line-dashed" />}
      <path d={line(values)} className="rs-line" />
      {values.map((v, i) => (
        <path key={runs[i]} d={`M${x(i)} ${v === null ? H : y(v)}h0`} className={v === null ? 'rs-dot rs-dot-empty' : 'rs-dot'}>
          <title>{`${runs[i]} · ${v === null ? '기록 없음' : v.toLocaleString('ko-KR')}`}</title>
        </path>
      ))}
    </svg>
  );
}

const count = (states: ExitState[], state: ExitState) => states.filter((s) => s === state).length;

export function RunSeriesRow({ series }: { series: DatasetRunSeries }) {
  const { dataset, points } = series;
  const runs = points.map((p) => p.runKey);
  const last = points[points.length - 1];
  if (!last) {
    return (
      <tr>
        <td>{dataset.label}</td>
        <td colSpan={4} style={{ color: 'var(--fg-3)' }}>조회 기간에 실행이 없습니다</td>
      </tr>
    );
  }
  const exits = points.map((p) => p.exit);
  const hasTarget = points.some((p) => p.expected !== null);
  return (
    <tr>
      <td>
        {dataset.label}
        <div className="t-xs mono" style={{ color: 'var(--fg-3)' }}>{series.representative ?? '대표 작업 없음'}</div>
      </td>
      <td>
        <RunSpark label={`${dataset.label} 행 수`} runs={runs} values={points.map((p) => p.rows)} />
        <span className="rs-latest">최근 {last.rows === null ? '기록 없음' : `${last.rows.toLocaleString('ko-KR')}행`}</span>
      </td>
      <td>
        <RunSpark label={`${dataset.label} 소요 시간`} runs={runs} values={points.map((p) => p.durationSec)} />
        <span className="rs-latest">최근 {formatDuration(last.durationSec)}</span>
      </td>
      <td>
        <div className="rs-exits" role="img"
          aria-label={`${dataset.label} 종료 상태 — ${(['ok', 'partial', 'failed', 'none'] as const)
            .map((s) => `${EXIT_LABEL[s]} ${count(exits, s)}회`)
            .join(', ')}`}>
          {points.map((p) => (
            <span key={p.runKey} className={`rs-exit rs-exit-${p.exit}`} title={`${p.runKey} · ${EXIT_LABEL[p.exit]}`} />
          ))}
        </div>
        <span className="rs-latest">
          최근 {EXIT_LABEL[last.exit]}
          <span style={{ color: 'var(--fg-3)' }}>
            {' '}· 부분 성공 {count(exits, 'partial')} · 실패 {count(exits, 'failed')} · 기록 없음 {count(exits, 'none')}
          </span>
        </span>
      </td>
      <td>
        {hasTarget ? (
          <>
            <RunSpark label={`${dataset.label} 대상 수 대비 받은 수`} runs={runs}
              values={points.map((p) => p.received)} dashed={points.map((p) => p.expected)} />
            <span className="rs-latest">
              최근{' '}
              {last.expected === null
                ? '기록 없음'
                : `${last.received?.toLocaleString('ko-KR') ?? '기록 없음'} / ${last.expected.toLocaleString('ko-KR')}`}
            </span>
          </>
        ) : (
          <span className="rs-latest" style={{ color: 'var(--fg-3)' }}>분모 없음</span>
        )}
      </td>
    </tr>
  );
}

export function RunSeriesSection() {
  const grid = useSourceGrid();
  return (
    <div className="card">
      <div className="card-head">
        <span className="t-label">데이터셋별 실행 추이</span>
        <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
          {grid.data ? `최근 ${grid.data.days}일 · ` : ''}점 하나가 실행 하나입니다 · 판정은 하지 않습니다
          <Info tip={TIP} label="실행 추이 읽는 법" />
        </span>
      </div>
      {grid.data ? (
        <table className="table rs-table">
          <thead>
            <tr>
              <th>데이터셋</th><th>행 수</th><th>소요 시간</th><th>종료 상태</th><th>대상 수 대비 받은 수</th>
            </tr>
          </thead>
          <tbody>
            {buildRunSeries(grid.data.slots, GRID_DATASETS).map((s) => (
              <RunSeriesRow key={s.dataset.id} series={s} />
            ))}
          </tbody>
        </table>
      ) : (
        <p className="card-pad t-sm m-0" style={{ color: grid.isError ? 'var(--warn)' : 'var(--fg-3)' }}>
          {grid.isError ? '실행 이력 조회에 실패했습니다 — 실행 추이를 그리지 않습니다.' : '실행 이력을 불러오는 중입니다.'}
        </p>
      )}
    </div>
  );
}
