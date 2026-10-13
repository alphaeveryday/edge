/* v2 분석의 일별 추이 (ALPHA-1290).
 *
 * 전망과 가격변동 분석 각각의 날짜별 완료·실패·끝나지 않은 건수다. 값은 새 분석 엔진의 표
 * (`outlook_analyses`·`movement_analyses`)에서 온다. 판정은 붙이지 않는다.
 *
 * 계열을 만드는 판단은 `domains/sources/dailySeries.ts` 에 있고, 그래프·표 스타일은 실행 추이
 * 구역의 것(`RunSpark`, `rs-*`)을 그대로 쓴다.
 */
import type { UseQueryResult } from '@tanstack/react-query';
import type { AnalysisTrendDto, AnalysisTrendKind } from '../../domains/console/types';
import { analysisDailySeries } from '../../domains/sources/dailySeries';
import type { AnalysisDailySeries } from '../../domains/sources/dailySeries';
import { RunSpark } from './RunSeriesSection';
import { Info } from './shared';

const KIND_LABEL: Record<AnalysisTrendKind, string> = { outlook: '전망', movement: '가격변동 분석' };
const KIND_NOTE: Record<AnalysisTrendKind, string> = {
  outlook: '주말·휴일에도 매일 돕니다',
  movement: '장이 열린 날에만 생깁니다',
};

const TIP = [
  '점 하나가 하루다(분석 시각의 KST 날짜). 값은 새 분석 엔진의 결과 표에서 온다.',
  '',
  '완료 — 분석이 끝까지 돈 건수. 게시 여부와는 다르다(가격변동 분석은 완료돼도 게시되지 않을 수 있다).',
  '실패 — 실패로 끝난 건수.',
  '끝나지 않은 건수 — 상태가 진행 중인 채 남은 행. 아직 도는 중인지 끝나지 못한 것인지 이 표만으로는',
  '가를 수 없어 실패로 세지 않는다.',
  '',
  '분석 행이 없던 날은 0 으로 채우지 않고 바닥의 빈 점으로 둔다. 정상·이상 판정은 하지 않는다.',
].join('\n');

const COLUMNS = [
  ['completed', '완료 건수'],
  ['failed', '실패 건수'],
  ['running', '끝나지 않은 건수'],
] as const;

export function AnalysisSeriesRow({ series }: { series: AnalysisDailySeries }) {
  const { kind, points } = series;
  const dates = points.map((p) => p.date);
  const last = points[points.length - 1];
  return (
    <tr>
      <td>
        {KIND_LABEL[kind]}
        <div className="t-xs" style={{ color: 'var(--fg-3)' }}>{KIND_NOTE[kind]}</div>
      </td>
      {COLUMNS.map(([key, label]) => (
        <td key={key}>
          <RunSpark label={`${KIND_LABEL[kind]} ${label}`} runs={dates} unit="날짜" values={points.map((p) => p[key])} />
          <span className="rs-latest">
            최근 {last[key] === null ? '기록 없음' : `${last[key]!.toLocaleString('ko-KR')}건`}
          </span>
        </td>
      ))}
    </tr>
  );
}

export function AnalysisSeriesSection({ trend }: { trend: UseQueryResult<AnalysisTrendDto> }) {
  return (
    <div className="card">
      <div className="card-head">
        <span className="t-label">분석 일별 추이</span>
        <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
          {trend.data ? `${trend.data.from} ~ ${trend.data.to} · ` : ''}점 하나가 하루입니다 · 판정은 하지 않습니다
          <Info tip={TIP} label="분석 일별 추이 읽는 법" />
        </span>
      </div>
      {trend.data && trend.isError && (
        <p className="card-pad t-xs m-0" style={{ color: 'var(--warn)', paddingBottom: 0 }}>
          분석 추이 재조회에 실패했습니다 — 직전 조회 결과를 유지합니다.
        </p>
      )}
      {trend.data ? (
        <table className="table rs-table">
          <thead>
            <tr><th>분석</th>{COLUMNS.map(([key, label]) => <th key={key}>{label}</th>)}</tr>
          </thead>
          <tbody>
            {analysisDailySeries(trend.data).map((s) => (
              <AnalysisSeriesRow key={s.kind} series={s} />
            ))}
          </tbody>
        </table>
      ) : (
        <p className="card-pad t-sm m-0" style={{ color: trend.isError ? 'var(--warn)' : 'var(--fg-3)' }}>
          {trend.isError
            ? '분석 추이 조회에 실패했습니다 — 분석 결과 카드는 계측 없음으로 두고 이 구역은 그리지 않습니다.'
            : '분석 추이를 불러오는 중입니다.'}
        </p>
      )}
    </div>
  );
}
