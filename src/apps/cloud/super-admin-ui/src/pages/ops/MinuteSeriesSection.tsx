/* 1분 세션의 일별 창 수 (ALPHA-1289).
 *
 * 1분 레인은 실행 원장(ops_*)이 아니라 세션 원장에 적혀 실행 추이 표에 없다. 여기서 날짜별로
 * 기대 창 수 대비 유효 창 수와 결손 창 수를 보여 준다. 판정은 붙이지 않는다 — 날짜별 판정은
 * 실행 이력 화면이 서버의 state·basis 로 이미 낸다.
 *
 * 계열을 만드는 판단은 `domains/sources/dailySeries.ts` 에 있고, 그래프·표 스타일은 실행 추이
 * 구역의 것(`RunSpark`, `rs-*`)을 그대로 쓴다.
 */
import { ALL_DATASETS } from '../../domains/sources/datasetCatalog';
import { minuteDailySeries } from '../../domains/sources/dailySeries';
import type { MinuteDailySeries } from '../../domains/sources/dailySeries';
import { useMinuteDailyStatus } from '../../domains/sources/hooks';
import { RunSpark } from './RunSeriesSection';
import { Info } from './shared';

const MINUTE_DATASETS = ALL_DATASETS.filter((d) => !d.inOpsGrid && d.sessionDataset);

/** 실행 추이 구역과 같은 기간을 본다. 서버 상한은 31일이다 */
const DAYS = 30;
/* 30일 범위 집계를 1분마다 다시 부르지 않는다 — 지난 날의 창 수는 분 단위로 움직이지 않고,
 * 이 범위 조회의 원장 부하는 재 보지 않았다. 화면을 다시 열면 새로 읽는다. */
const NO_POLLING = false;

const TIP = [
  '점 하나가 하루다. 그날 그 데이터셋 세션들의 창 수를 합친 값이다.',
  '',
  '기대 창 수 대비 유효 창 수 — 실선이 유효 창(VALID + VALID_EMPTY), 점선이 기대 창 수.',
  '빈 데이터(VALID_EMPTY)는 실행 증거가 있는 창이라 유효에 센다.',
  '결손 창 수 — INCOMPLETE + MISSING + INVALID.',
  '',
  '세션이 없던 날은 0 으로 채우지 않고 바닥의 빈 점으로 둔다. 결손이 없던 날의 0 은 0 이다.',
  '정상·이상 판정은 하지 않는다.',
].join('\n');

export function MinuteSeriesRow({ series }: { series: MinuteDailySeries }) {
  const { dataset, points } = series;
  const dates = points.map((p) => p.date);
  const last = points[points.length - 1];
  if (!last) {
    return (
      <tr>
        <td>{dataset.label}</td>
        <td colSpan={2} style={{ color: 'var(--fg-3)' }}>조회 기간에 날짜가 없습니다</td>
      </tr>
    );
  }
  return (
    <tr>
      <td>
        {dataset.label}
        <div className="t-xs mono" style={{ color: 'var(--fg-3)' }}>{dataset.sessionDataset}</div>
      </td>
      <td>
        <RunSpark label={`${dataset.label} 기대 창 수 대비 유효 창 수`} runs={dates} unit="날짜"
          values={points.map((p) => p.valid)} dashed={points.map((p) => p.expected)} />
        <span className="rs-latest">
          최근{' '}
          {last.expected === null
            ? '기록 없음'
            : `${last.valid!.toLocaleString('ko-KR')} / ${last.expected.toLocaleString('ko-KR')}창`}
        </span>
      </td>
      <td>
        <RunSpark label={`${dataset.label} 결손 창 수`} runs={dates} unit="날짜" values={points.map((p) => p.defects)} />
        <span className="rs-latest">
          최근 {last.defects === null ? '기록 없음' : `${last.defects.toLocaleString('ko-KR')}창`}
        </span>
      </td>
    </tr>
  );
}

export function MinuteSeriesSection() {
  const daily = useMinuteDailyStatus(DAYS, NO_POLLING);
  return (
    <div className="card">
      <div className="card-head">
        <span className="t-label">1분 세션 일별 창 수</span>
        <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
          {daily.data ? `${daily.data.from} ~ ${daily.data.to} · ` : ''}점 하나가 하루입니다 · 판정은 하지 않습니다
          <Info tip={TIP} label="1분 세션 창 수 읽는 법" />
        </span>
      </div>
      {daily.data && daily.isError && (
        <p className="card-pad t-xs m-0" style={{ color: 'var(--warn)', paddingBottom: 0 }}>
          1분 세션 일별 상태 재조회에 실패했습니다 — 직전 조회 결과를 유지합니다.
        </p>
      )}
      {daily.data ? (
        <table className="table rs-table">
          <thead>
            <tr><th>데이터셋</th><th>기대 창 수 대비 유효 창 수</th><th>결손 창 수</th></tr>
          </thead>
          <tbody>
            {minuteDailySeries(daily.data.dates, MINUTE_DATASETS).map((s) => (
              <MinuteSeriesRow key={s.dataset.id} series={s} />
            ))}
          </tbody>
        </table>
      ) : (
        <p className="card-pad t-sm m-0" style={{ color: daily.isError ? 'var(--warn)' : 'var(--fg-3)' }}>
          {daily.isError
            ? '1분 세션 일별 상태 조회에 실패했습니다 — 창 수를 그리지 않습니다.'
            : '1분 세션 일별 상태를 불러오는 중입니다.'}
        </p>
      )}
    </div>
  );
}
