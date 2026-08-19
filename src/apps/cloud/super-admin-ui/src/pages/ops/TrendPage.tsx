/* 산출·품질 추이 (ALPHA-738).
 *
 * 답하는 질문: **주요 데이터셋의 산출량과 품질이 평소 또는 운영 기준에서 벗어났는가.**
 *
 * 범위는 파이프라인 실행 결과 · 데이터셋별 산출량과 완전성 · 장중 수집의 일별 상태 · 분석 결과
 * 생성까지다. Cloud 게시·테넌트 발번·소비자 전달은 전달 화면 소관이라 지표로 두지 않는다.
 *
 * 지키는 선:
 *   · 지표마다 **판정 규칙이 다르다**(trendMetrics 의 comparisonType) — 완전성 95% 미만과
 *     산출량 ±25% 를 같은 자로 재지 않는다.
 *   · 단위·규모가 다른 지표를 한 y 축에 겹치지 않는다 — 지표마다 독립 그래프.
 *   · 중앙값·정상 범위·편차·판정이 **전부 같은 계열**에서 나온다.
 *   · 이 화면은 이상을 지목하기만 하고 원인은 말하지 않는다.
 *
 * 뉴스 단계별 감소(퍼널)는 데이터 화면 소관이라 여기서 반복하지 않는다.
 */
import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { StatusBadge } from 'ui-kit';
import { AxisHeader, ConsoleGate, Info, useConsoleFactsQuery, useFocusRow } from './shared';
import { InfoPopover } from '../_shared/InfoPopover';
import { GROUP_LABEL, evaluateMetric, formatValue } from './trendMetrics';
/* 판단은 JSX 밖에 둔다 — 여기 두면 `node --test` 가 import 을 못 해 변이가 안 잡힌다 */
import { asOfLabel } from './trendAsOf';
import type { Metric, MetricGroup, Verdict } from './trendMetrics';
import { buildMetrics } from './trendCatalog';
import { useMinuteStatus } from '../../domains/sources/hooks';
import { useEntityResolutionTrend } from '../../domains/console/hooks';
import { extent, points } from './trendSeries';
import '../../styles/ops.css';

type Filter = 'abnormal' | MetricGroup;

const FILTERS: { key: Filter; label: string }[] = [
  { key: 'abnormal', label: '이상 지표' },
  { key: 'batch', label: GROUP_LABEL.batch },
  { key: 'intraday', label: GROUP_LABEL.intraday },
  { key: 'news', label: GROUP_LABEL.news },
  { key: 'analysis', label: GROUP_LABEL.analysis },
];

const SCOPE_TIP = [
  '이 화면의 범위 — 파이프라인 실행 결과 · 데이터셋 산출량과 완전성 · 장중 수집의 일별 상태 ·',
  '분석 결과 생성.',
  '',
  'Cloud 게시 · 테넌트 발번 · 소비자 전달 · 테넌트별 상태는 전달 화면 소관이라 지표로 두지 않는다.',
  '',
  '판정 규칙은 지표마다 다르다:',
  '  산출량 — 직전 10영업일 중앙값 대비 ±25%',
  '  완전성·비율 — 계약된 최소 비율 미만',
  '  기준일 지연 — 허용 거래일 초과',
  '  워터마크 — 허용 지연 시간 초과',
  '  무증거·실패 수 — 임계 초과',
  '  계측 없음 — 근거가 없으면 정상으로도 실패로도 세지 않는다',
  '',
  '중앙값 · 정상 범위 · 편차 · 판정은 모두 같은 계열에서 계산된다.',
].join('\n');

/** 지표 하나의 작은 그래프 — 자기 y 범위를 갖는다(공통 축 금지) */
function Spark({ metric, verdict, asOf }: { metric: Metric; verdict: Verdict; asOf: string | null }) {
  const W = 240;
  const H = 44;
  const values = metric.series.map((p) => p.value);
  if (values.length === 0) {
    return (
      <div className="tr-spark tr-spark-empty t-xs" style={{ color: 'var(--fg-4)' }}>
        계열 없음
      </div>
    );
  }
  const range = extent(values, verdict.band ?? []);
  const [lo, hi] = range;
  const y = (v: number) => (1 - (v - lo) / (hi - lo)) * H;
  const p = points(values, range);
  const path = p.map((q, i) => `${i === 0 ? 'M' : 'L'}${q.x * W} ${q.y * H}`).join(' ');
  const last = p[p.length - 1];
  const abnormal = verdict.kind === 'abnormal';

  return (
    <svg
      className="tr-spark"
      viewBox={`0 0 ${W} ${H}`}
      preserveAspectRatio="none"
      role="img"
      aria-label={`${metric.label} ${values.length}개 관측 추이 — ${asOf ?? '마지막 값'} ${formatValue(
        metric,
        verdict.actual,
      )}, 기준 ${formatValue(metric, verdict.expected)}, ${verdict.label}`}
    >
      {/* 정상 범위 음영 — 지표마다 정의가 다르다(분포 밴드 · 임계 위/아래) */}
      {verdict.band && (
        <rect
          x="0"
          y={y(verdict.band[1])}
          width={W}
          height={Math.max(1, y(verdict.band[0]) - y(verdict.band[1]))}
          className="tr-band"
        />
      )}
      {/* 기준선 — 중앙값 또는 운영 임계 */}
      {verdict.expected !== null && (
        <line x1="0" x2={W} y1={y(verdict.expected)} y2={y(verdict.expected)} className="tr-base" />
      )}
      <path d={path} className="tr-line" />
      <circle
        cx={last.x * W}
        cy={last.y * H}
        r={abnormal ? 4.5 : 3}
        className={
          abnormal ? (verdict.tone === 'warn' ? 'tr-today tr-warn' : 'tr-today tr-out') : 'tr-today'
        }
      />
    </svg>
  );
}

function MetricCard({ metric, today }: { metric: Metric; today: string }) {
  const v = evaluateMetric(metric);
  const mockPoints = metric.series.filter((p) => p.isMock).length;
  const asOf = asOfLabel(metric, today);
  return (
    <li className="tr-card" id={'metric-' + metric.id}>
      <div className="tr-card-head">
        <span className="t-label">{metric.label}</span>
        <StatusBadge tone={v.tone}>{v.label}</StatusBadge>
        <span className="tr-card-info">
          <InfoPopover label={metric.label} title={`${metric.label} · 집계와 판정`} text={metric.help} />
        </span>
      </div>

      <Spark metric={metric} verdict={v} asOf={asOf} />

      <div className="tr-figures t-xs">
        <span>
          {/* 날짜가 없으면(계열 자체가 없음) **붙이지 않는다** — 옆의 `계측 없음` 배지가 답한다 */}
          {asOf && `${asOf} `}
          <b>{formatValue(metric, v.actual)}</b>
        </span>
        {v.expected !== null && (
          <span style={{ color: 'var(--fg-3)' }}>
            {metric.comparisonType === 'medianDelta' ? '중앙값' : '기준'} {formatValue(metric, v.expected)}
          </span>
        )}
        {v.deltaPct !== null && (
          <span style={v.kind === 'abnormal' ? { color: 'var(--down)', fontWeight: 600 } : { color: 'var(--fg-2)' }}>
            {v.deltaPct > 0 ? '+' : ''}
            {v.deltaPct}%
          </span>
        )}
      </div>

      <div className="tr-meta t-xs">
        <span style={{ color: 'var(--fg-3)' }}>{v.basis}</span>
      </div>

      <div className="tr-foot t-xs">
        {/* 실측과 목을 시각적으로 가른다 — 계열 전체가 목인지, 오늘만 실측인지까지 */}
        {mockPoints === 0 ? (
          <span className="chip">{metric.source}</span>
        ) : mockPoints === metric.series.length ? (
          /* 계열 전체가 실측이 아닐 때 **무엇이 아닌지**는 출처가 정한다 — 지어낸 값(MOCK)과
             한때 관측한 스냅샷을 한 칩에 그리면 그 둘을 못 가른다. */
          <span className="chip chip-warn">
            {metric.source === 'SNAPSHOT' ? `${asOf ?? ''} 스냅샷 계열`.trim() : 'MOCK 계열'}
          </span>
        ) : (
          <span className="chip">{asOf} 실측 · 과거 MOCK</span>
        )}
        <Link to={metric.drill.href} className="tr-drill">
          {metric.drill.label} →
        </Link>
      </div>
    </li>
  );
}

export function TrendPage() {
  const [filter, setFilter] = useState<Filter>('abnormal');
  const q = useConsoleFactsQuery();
  const minute = useMinuteStatus(q.ready ? q.facts.meta.today : undefined, q.ready);
  const entityResolution = useEntityResolutionTrend(q.ready ? q.facts.meta.today : undefined, q.ready);
  useFocusRow(q.ready);

  /* 필터를 바꿔도 다시 평가하지 않는다(상태만 바뀐다). **`useState` 초기화가 아니라
   * `useMemo`** 인 이유: 지표가 이제 응답에서 만들어져 1분마다 갱신되는데, 초기화 함수는
   * 한 번만 돌아 화면이 첫 응답에 영원히 고정된다. */
  const evaluated = useMemo(
    () => {
      if (!q.ready) return [];
      const metrics = buildMetrics(q.facts, minute.data, entityResolution.data);
      return metrics
        .filter((m) => entityResolution.data !== undefined || m.id !== 'n.entity_resolution_rate')
        .map((m) => ({ m, v: evaluateMetric(m) }));
    },
    [q, minute.data, entityResolution.data],
  );
  if (!q.ready) return <ConsoleGate q={q} />;
  const counts: Record<Filter, number> = {
    abnormal: evaluated.filter((x) => x.v.kind === 'abnormal').length,
    batch: evaluated.filter((x) => x.m.group === 'batch').length,
    intraday: evaluated.filter((x) => x.m.group === 'intraday').length,
    news: evaluated.filter((x) => x.m.group === 'news').length,
    analysis: evaluated.filter((x) => x.m.group === 'analysis').length,
  };
  const shown =
    filter === 'abnormal'
      ? evaluated.filter((x) => x.v.kind === 'abnormal')
      : evaluated.filter((x) => x.m.group === filter);
  const uninstrumented = evaluated.filter((x) => x.v.kind === 'uninstrumented').length;
  const entityResolutionPending = entityResolution.isPending && entityResolution.data === undefined;
  const entityResolutionFailed = entityResolution.isError && entityResolution.data === undefined;

  return (
    <div className="flex flex-col gap-4">
      <AxisHeader q={q} question="주요 데이터셋의 산출량과 품질이 평소 또는 운영 기준에서 벗어났는가?" />

      {minute.isError && (
        <p className="t-xs m-0" style={{ color: 'var(--warn)' }}>
          분봉 상태 재조회에 실패했습니다 — {minute.data ? '직전 실측을 유지합니다.' : '분봉 지표는 MOCK 계열입니다.'}
        </p>
      )}

      {entityResolutionPending && (
        <p className="t-xs m-0" style={{ color: 'var(--fg-3)' }}>
          엔티티 해소율 실측을 불러오는 중입니다 — 판정은 응답 뒤에 표시합니다.
        </p>
      )}

      {entityResolution.isError && (
        <p className="t-xs m-0" style={{ color: 'var(--warn)' }}>
          엔티티 해소율 재조회에 실패했습니다 —{' '}
          {entityResolution.data ? '직전 실측을 유지합니다.' : '해당 지표를 판정하지 않습니다.'}
        </p>
      )}

      <div className="card">
        <div className="card-head">
          <span className="t-label">산출 축</span>
          <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
            사건의 산출 ID와 같은 앵커입니다
          </span>
        </div>
        <table className="table">
          <thead><tr><th>산출</th><th className="num">오늘</th><th className="num">기준</th><th>단위</th></tr></thead>
          <tbody>
            {q.facts.outputs.map((output) => (
              <tr key={output.id} id={`out-${output.id}`}>
                <td>{output.label} <span className="mono t-xs">{output.id}</span></td>
                <td className="num">{output.today.toLocaleString('ko-KR')}</td>
                <td className="num">{output.base?.toLocaleString('ko-KR') ?? '—'}</td>
                <td>{output.unit}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="card">
        <div className="card-head">
          <span className="t-label">산출·품질 추이</span>
          <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
            지표마다 판정 규칙이 다릅니다 · 이상을 지목할 뿐 원인은 말하지 않습니다
            <Info tip={SCOPE_TIP} label="화면 범위와 판정 규칙" />
          </span>
        </div>

        <div className="card-pad" style={{ paddingBottom: 0 }}>
          <div className="tr-filters" role="group" aria-label="지표 분류 필터">
            {FILTERS.map((f) => (
              <button
                key={f.key}
                type="button"
                className="tr-filterbtn"
                aria-pressed={filter === f.key}
                onClick={() => setFilter(f.key)}
              >
                {f.label} <span className="tr-count">{counts[f.key]}</span>
              </button>
            ))}
          </div>
        </div>

        <div className="card-pad">
          {shown.length === 0 ? (
            <p className="t-sm m-0" style={{ color: 'var(--fg-3)' }}>
              {filter === 'abnormal'
                ? entityResolutionPending
                  ? '엔티티 해소율 조회 중이라 전체 이상 여부가 아직 확정되지 않았습니다.'
                  : entityResolutionFailed
                    ? '엔티티 해소율 조회 실패로 전체 이상 여부를 확인할 수 없습니다.'
                    : '현재 기준을 벗어난 지표가 없습니다.'
                : '이 분류에 지표가 없습니다.'}
              {filter === 'abnormal' && uninstrumented > 0 && (
                <>
                  {' '}
                  계측이 없어 판정하지 못한 지표 {uninstrumented}개는 각 분류에서 확인하세요.
                </>
              )}
            </p>
          ) : (
            /* 지표마다 독립된 그래프 — 단위·규모가 달라 한 축에 겹치지 않는다 */
            <ul className="tr-grid">
              {shown.map(({ m }) => (
                <MetricCard key={m.id} metric={m} today={q.facts.meta.today} />
              ))}
            </ul>
          )}

          <p className="t-xs m-0" style={{ color: 'var(--fg-3)', marginTop: 12 }}>
            조회일 {q.facts.meta.today} 기준(원장이 아는 가장 최근 날 — 거래일 달력이 아닙니다) ·
            DB_LEDGER 계열은 카드에 표시된 날짜의 실측입니다. 일별 API가 없는 지표의 과거 점만
            검수용 목이며, 응답 밖 값은 스냅샷 칩으로 구분합니다. 뉴스의 단계별 감소는{' '}
            <Link to="/ops/datasets?focus=news-funnel">데이터 화면의 뉴스 처리 퍼널</Link>이 답합니다 —
            여기서 반복하지 않습니다.
          </p>
        </div>
      </div>
    </div>
  );
}
