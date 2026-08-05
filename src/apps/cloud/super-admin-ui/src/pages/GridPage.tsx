/* 실행 이력 — 데이터셋별 일 단위 실행 요약 (ALPHA-594 → ALPHA-738).
 *
 * 답하는 질문: "최근 며칠 동안 각 데이터셋의 예정된 실행·수집이 정상으로 귀결됐는가?"
 *
 * 역할 분리:
 *   실행 이력(여기) — 여러 **날짜**를 비교하는 일 단위 요약. 박스 하나 = 데이터셋 × 날짜.
 *   장중 세션(/minute) — 특정 날짜의 분·시간 단위 상태. 여기서 복제하지 않는다.
 *   실행 원장 상세(/sources) — 개별 실행의 시도·이슈·산출물.
 *
 * 이전 구조와 다른 점: 행이 **작업(task_key)** 이 아니라 **운영 그룹 › 데이터셋**이고,
 * 열이 **슬롯(런)** 이 아니라 **날짜**다. 같은 날 런이 여럿이어도 박스 하나로 접힌다.
 *
 * 데이터셋 축은 화면 쪽 카탈로그다 — 격자 API 가 dataset 을 주지 않는다(datasetCatalog 참고).
 * 상태·기대 실행 수는 원장 값에서만 센다(dailyRollup 참고) — 주기로 숫자를 지어내지 않는다.
 */
import { Fragment, useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { PageSkeleton, StatusBadge } from 'ui-kit';
import type { BadgeTone } from 'ui-kit';
import type { SourceGrid } from '../domains/sources';
import { useSourceGrid } from '../domains/sources/hooks';
import { CATALOG_SOURCE, DATASET_GROUPS } from '../domains/sources/datasetCatalog';
import type { DatasetEntry } from '../domains/sources/datasetCatalog';
import { datesOf, groupState, rollup } from '../domains/sources/dailyRollup';
import type { DayRollup, DayState } from '../domains/sources/dailyRollup';
import { MOCK_GRID } from '../mock/preview';
import { EmptyRealNotice, MockChip, MockPreview } from './_shared/MockPreview';
import { InfoPopover } from './_shared/InfoPopover';
import { LoadError } from './_shared/LoadError';
import '../styles/grid.css';

/* 상태 → 박스 모양. 색 하나에만 기대지 않도록 테두리·사선·빈 칸을 함께 쓴다.
 * 값은 기존 격자 인코딩을 물려받는다(초록 성공 · 주황 주의 · 빨강 장애 · 파란 테두리 실행 중 · 사선 스킵). */
const STATE_CLASS: Record<DayState, string> = {
  정상: 'gd-s-ok',
  주의: 'gd-s-warn',
  장애: 'gd-s-bad',
  '실행 중': 'gd-s-run',
  '계획 스킵': 'gd-s-skip',
  '계획 없음': 'gd-s-none',
};
const STATE_ORDER: DayState[] = ['정상', '주의', '장애', '실행 중', '계획 스킵', '계획 없음'];
const STATE_TONE: Record<DayState, BadgeTone> = {
  정상: 'active',
  주의: 'warn',
  장애: 'blocked',
  '실행 중': 'env',
  '계획 스킵': 'gated',
  '계획 없음': 'neutral',
};

const OUTCOME_LABEL: Record<string, string> = {
  FULFILLED: '성공',
  FAILED: '실패',
  BLOCKED: '선행 미충족',
  MISSED: '무증거',
  PENDING: '대기',
};

const STATUS_TIP = [
  '박스 하나 = 데이터셋 × 날짜. 그 날짜에 예정된 실행·수집을 전부 접은 결과다.',
  '',
  '정상 — 기한이 지난 기대 실행이 모두 정상 귀결됐다',
  '주의 — 불완전·무효·유실 등 확인이 필요하다',
  '장애 — 실패 또는 기한이 지난 무증거가 있다',
  '실행 중 — 아직 끝나지 않은 것이 남았다. 기한 전 대기를 실패로 보지 않는다',
  '계획 스킵 — 계획 단계에서 빠졌다(비거래일 등). 안 한 게 아니라 할 일이 아니었다',
  '계획 없음 — 그 날짜에 이 데이터셋의 계획 행 자체가 없다',
  '',
  '빈 데이터와 무증거는 합치지 않는다.',
  '  빈 데이터(VALID_EMPTY) — 돌았고 그 날 데이터가 없었다는 증거가 남았다. 정상이다.',
  '  무증거(MISSED) — 기한이 지났는데 실행·결과 증거가 없다. 장애다.',
  '',
  '기대 실행 수는 주기에서 지어내지 않고 원장의 계획 행(plan_status=DUE)을 센다 —',
  '그래서 주기가 다른 데이터셋에 같은 기대치가 적용되지 않는다.',
  '',
  '그룹 행은 하위 데이터셋 상태의 결정적 집계다(장애 > 주의 > 실행 중 > 정상 > 계획 스킵 > 계획 없음).',
].join('\n');

/** 박스 — 격자와 범례가 이 컴포넌트 하나를 공유한다(두 곳에 모양을 복제하면 범례가 거짓말한다) */
function StateBox({ state }: { state: DayState }) {
  return <span className={`gd-cell ${STATE_CLASS[state]}`} />;
}

function GridLegend() {
  return (
    <div className="gd-legend">
      {STATE_ORDER.map((s) => (
        <span key={s} className="gd-legend-item">
          <StateBox state={s} />
          {s}
        </span>
      ))}
      <InfoPopover label="상태 기준" title="상태 기준" text={STATUS_TIP} />
    </div>
  );
}

const mmdd = (d: string) => d.slice(5);

type LaneFilter = 'all' | 'market' | 'news';
const LANE_FILTERS: { key: LaneFilter; label: string }[] = [
  { key: 'all', label: '전체' },
  { key: 'market', label: '시장' },
  { key: 'news', label: '뉴스' },
];

export function GridPage() {
  const { data: grid, isPending, isError, error } = useSourceGrid();

  if (isError) return <LoadError error={error} />;
  if (isPending) return <PageSkeleton rows={6} />;

  /* 격자가 비면 상태 인코딩을 전혀 볼 수 없다 — 사실을 먼저 밝히고 검수용 목을 붙인다 */
  if (grid.slots.length === 0) {
    return (
      <div className="flex flex-col gap-4">
        <EmptyRealNotice>최근 {grid.days}일 안에 기록된 파이프라인 실행이 없습니다.</EmptyRealNotice>
        <MockPreview>
          <GridBody grid={MOCK_GRID} mock />
        </MockPreview>
      </div>
    );
  }

  return <GridBody grid={grid} />;
}

interface Selection {
  dataset: DatasetEntry;
  date: string;
  rollup?: DayRollup;
}

function GridBody({ grid, mock = false }: { grid: SourceGrid; mock?: boolean }) {
  const [laneFilter, setLaneFilter] = useState<LaneFilter>('all');
  /* 기본은 접힘 — 전부 펼치면 화면이 다시 길어진다 */
  const [openGroups, setOpenGroups] = useState<Record<string, boolean>>({});
  const [selected, setSelected] = useState<Selection | null>(null);

  const slots = useMemo(
    () =>
      grid.slots.filter((slot) => {
        if (laneFilter === 'all') return true;
        return laneFilter === 'news'
          ? slot.runKey.startsWith('news:')
          : !slot.runKey.startsWith('news:');
      }),
    [grid.slots, laneFilter],
  );

  const dates = useMemo(() => datesOf(slots), [slots]);
  const rolled = useMemo(() => rollup(slots), [slots]);
  const at = (datasetId: string, date: string) => rolled.get(`${datasetId}|${date}`);

  /* 이 창에서 셀이 하나도 없는 데이터셋은 감춘다 — 레인 필터로 통째로 빈 행은 소음이다.
   * 다른 원장 소관(장중)은 필터와 무관하게 남겨 "이 격자에 없다"는 사실을 말한다. */
  const visibleGroups = DATASET_GROUPS.map((g) => ({
    ...g,
    datasets: g.datasets.filter(
      (d) => !d.inOpsGrid || dates.some((date) => at(d.id, date) !== undefined),
    ),
  })).filter((g) => g.datasets.length > 0);

  const stateOfDataset = (d: DatasetEntry, date: string): DayState =>
    at(d.id, date)?.state ?? '계획 없음';

  return (
    <div className="flex flex-col gap-4">
      <div className="card">
        <div className="card-head">
          <span className="t-label">파이프라인 실행 이력 {mock && <MockChip />}</span>
          <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
            최근 {grid.days}일 · 데이터셋별 일 단위
          </span>
          <span style={{ display: 'inline-flex', gap: 4 }}>
            {LANE_FILTERS.map((f) => (
              <button
                key={f.key}
                type="button"
                className="t-xs"
                aria-pressed={laneFilter === f.key}
                onClick={() => {
                  setLaneFilter(f.key);
                  setSelected(null);
                }}
                style={{
                  padding: '2px 8px',
                  borderRadius: 4,
                  border: '1px solid var(--border-strong)',
                  cursor: 'pointer',
                  background: laneFilter === f.key ? 'var(--fg-2)' : 'transparent',
                  color: laneFilter === f.key ? '#fff' : 'var(--fg-2)',
                }}
              >
                {f.label}
              </button>
            ))}
          </span>
        </div>

        <div className="card-pad" style={{ paddingTop: 0 }}>
          <GridLegend />
          <div className="gd-hint">
            <span>
              <b>그룹 행</b> 선택 → 데이터셋 펼치기
            </span>
            <span>
              <b>박스</b> 선택 → 그 데이터셋·날짜의 일별 요약과 실행 목록
            </span>
            <span style={{ marginLeft: 'auto' }}>
              분 단위 상태는 <Link to="/minute">장중 세션</Link>이 답합니다
            </span>
          </div>
        </div>

        {dates.length === 0 ? (
          <div className="card-pad" style={{ paddingTop: 0 }}>
            <p className="t-xs m-0" style={{ color: 'var(--fg-3)' }}>
              {laneFilter === 'all'
                ? `최근 ${grid.days}일 안에 기록된 파이프라인 실행이 없습니다.`
                : `최근 ${grid.days}일 안에 이 레인의 실행이 없습니다 (필터: ${
                    LANE_FILTERS.find((f) => f.key === laneFilter)?.label
                  }).`}
            </p>
          </div>
        ) : (
          <div className="gd-scroll">
            <table className="gd-table">
              <thead>
                <tr>
                  <th className="gd-rowhead" />
                  {dates.map((d) => (
                    <th key={d} className="gd-datehead" scope="col">
                      {mmdd(d)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {visibleGroups.map((g) => {
                  const open = !!openGroups[g.group];
                  return (
                    <Fragment key={g.group}>
                      <tr>
                        <th className="gd-rowhead" scope="row">
                          <button
                            type="button"
                            className="gd-grouptoggle"
                            aria-expanded={open}
                            onClick={() => setOpenGroups((s) => ({ ...s, [g.group]: !s[g.group] }))}
                          >
                            <span aria-hidden="true">{open ? '▾' : '▸'}</span> {g.group}
                          </button>
                        </th>
                        {dates.map((date) => {
                          const st = groupState(g.datasets.map((d) => stateOfDataset(d, date)));
                          return (
                            <td key={date} className="gd-box">
                              {/* 그룹 박스는 요약이라 선택 대상이 아니다 — 조치는 데이터셋 단위다 */}
                              <span
                                className="gd-static"
                                title={`${g.group} · ${date} · ${st}\n하위 데이터셋 ${g.datasets.length}개의 집계`}
                                aria-label={`${g.group} ${date} ${st}`}
                              >
                                <StateBox state={st} />
                              </span>
                            </td>
                          );
                        })}
                      </tr>
                      {open &&
                        g.datasets.map((d) => (
                          <tr key={`${g.group}|${d.id}`}>
                            <th className="gd-rowhead gd-rowhead-sub" scope="row">
                              {d.label}
                              {!d.inOpsGrid && d.elsewhere && (
                                <Link to={d.elsewhere.href} className="t-xs" style={{ marginLeft: 6 }}>
                                  {d.elsewhere.label} →
                                </Link>
                              )}
                            </th>
                            {dates.map((date) => {
                              const r = at(d.id, date);
                              const st = r?.state ?? '계획 없음';
                              const sel = selected?.dataset.id === d.id && selected?.date === date;
                              const tip = boxTip(d, date, r);
                              return (
                                <td key={date} className="gd-box">
                                  {mock || !d.inOpsGrid ? (
                                    /* 목 미리보기와 다른 원장 소관은 선택 대상이 아니다 */
                                    <span className="gd-static" title={tip}>
                                      <StateBox state={st} />
                                    </span>
                                  ) : (
                                    <button
                                      type="button"
                                      className={'gd-cellbtn' + (sel ? ' gd-selected' : '')}
                                      aria-pressed={sel}
                                      title={tip}
                                      aria-label={`${d.label} ${date} ${st} — 일별 요약 보기`}
                                      onClick={() =>
                                        setSelected(sel ? null : { dataset: d, date, rollup: r })
                                      }
                                    >
                                      <StateBox state={st} />
                                    </button>
                                  )}
                                </td>
                              );
                            })}
                          </tr>
                        ))}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        <div className="card-pad" style={{ paddingTop: 0 }}>
          <p className="t-xs m-0" style={{ color: 'var(--fg-3)' }}>
            행 축(운영 그룹 · 데이터셋 · 수집 주기)은 <b>{CATALOG_SOURCE}</b>입니다 — 격자 응답이 데이터셋을
            주지 않아 화면이 작업을 데이터셋으로 묶습니다. 상태와 기대 실행 수는 원장 값에서만 셉니다.
          </p>
        </div>
      </div>

      {selected && <DayDetail sel={selected} mock={mock} onClose={() => setSelected(null)} />}
    </div>
  );
}

function boxTip(d: DatasetEntry, date: string, r?: DayRollup): string {
  if (!d.inOpsGrid) {
    return [
      `${d.label} · ${date}`,
      `이 격자의 원장(ops_expected_task)에 없습니다 — ${
        d.cadence.kind === 'intradayWindows' ? d.cadence.ledger : '다른 원장'
      } 소관`,
      d.elsewhere ? `${d.elsewhere.label} 화면에서 확인` : '',
    ]
      .filter(Boolean)
      .join('\n');
  }
  if (!r) return `${d.label} · ${date} · 계획 없음\n이 날짜에 계획 행이 없습니다`;
  const c = r.counts;
  return [
    `${d.label} · ${date} · ${r.state}`,
    `주기 ${d.cadence.label}`,
    `기대 실행 ${r.expected}`,
    `성공 ${c.fulfilled}`,
    c.emptyEvidence ? `빈 데이터 증거 ${c.emptyEvidence}` : '',
    c.failed ? `실패 ${c.failed}` : '',
    c.incomplete ? `불완전 ${c.incomplete}` : '',
    c.invalid ? `무효 ${c.invalid}` : '',
    c.noEvidence ? `무증거 ${c.noEvidence}` : '',
    c.pending ? `판정 대기 ${c.pending}` : '',
    c.running ? `실행 중 ${c.running}` : '',
    c.skipped ? `계획 스킵 ${c.skipped}` : '',
  ]
    .filter(Boolean)
    .join('\n');
}

/** 박스 선택 → 일별 요약과 실행 목록. 개별 실행은 기존 실행 원장 상세로 내려간다. */
function DayDetail({ sel, mock, onClose }: { sel: Selection; mock: boolean; onClose: () => void }) {
  const navigate = useNavigate();
  const { dataset: d, date, rollup: r } = sel;
  const c = r?.counts;
  const open = (runKey: string, taskKey: string) =>
    navigate(
      `/sources?${mock ? 'preview=mock&' : ''}runKey=${encodeURIComponent(
        runKey,
      )}&task=${encodeURIComponent(taskKey)}`,
    );

  const figures: [string, number][] = c
    ? [
        ['성공', c.fulfilled],
        ['빈 데이터 증거', c.emptyEvidence],
        ['실패', c.failed],
        ['불완전', c.incomplete],
        ['무효', c.invalid],
        ['무증거', c.noEvidence],
        ['판정 대기', c.pending],
        ['실행 중', c.running],
        ['계획 스킵', c.skipped],
      ]
    : [];

  return (
    <div className="card" id="gd-detail">
      <div className="card-head">
        <span className="t-label">
          {d.label} · {date}
        </span>
        <StatusBadge tone={STATE_TONE[r?.state ?? '계획 없음']}>{r?.state ?? '계획 없음'}</StatusBadge>
        <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
          주기 {d.cadence.label} · 기대 실행 {r?.expected ?? 0}
        </span>
        <button type="button" className="btn btn-sm" style={{ marginLeft: 'auto' }} onClick={onClose}>
          닫기
        </button>
      </div>
      <div className="card-pad">
        {!r ? (
          <p className="t-sm m-0" style={{ color: 'var(--fg-3)' }}>
            이 날짜에 이 데이터셋의 계획 행이 없습니다 — 실행 계획 자체가 없었다는 사실입니다.
          </p>
        ) : (
          <>
            <div className="gd-figures">
              {figures.map(([label, n]) => (
                <span key={label} className={'gd-figure' + (n === 0 ? ' gd-figure-zero' : '')}>
                  {label} <b>{n}</b>
                </span>
              ))}
            </div>
            <p className="t-xs m-0" style={{ color: 'var(--fg-3)', marginTop: 8 }}>
              빈 데이터 증거는 <b>돌았고 데이터가 없었다</b>는 사실이고, 무증거는{' '}
              <b>기한이 지났는데 증거가 없다</b>는 사실입니다 — 합쳐 세지 않습니다.
            </p>

            <div style={{ overflowX: 'auto' }}>
              <table className="table" style={{ marginTop: 12 }}>
                <thead>
                  <tr>
                    <th>실행(런)</th>
                    <th>작업</th>
                    <th>귀결</th>
                    <th>데이터</th>
                    <th className="num">산출</th>
                    <th className="num">유실</th>
                    <th>사유</th>
                    <th>상세</th>
                  </tr>
                </thead>
                <tbody>
                  {r.runs.map((run) => (
                    <tr key={`${run.runKey}|${run.taskKey}`}>
                      <td className="mono">{run.runKey}</td>
                      <td className="mono">{run.taskKey}</td>
                      <td>
                        {run.planStatus === 'SKIPPED'
                          ? '계획 스킵'
                          : run.running
                            ? '실행 중'
                            : (OUTCOME_LABEL[run.outcome ?? ''] ?? run.outcome ?? '판정 없음')}
                      </td>
                      <td className="col-muted">{run.dataStatus ?? '—'}</td>
                      <td className="num">{run.recordsOut ?? '—'}</td>
                      <td className="num">{run.failedRecords ?? '—'}</td>
                      <td className="col-muted t-xs">{run.reason ?? '—'}</td>
                      <td>
                        <button
                          type="button"
                          className="gd-linkbtn"
                          onClick={() => open(run.runKey, run.taskKey)}
                        >
                          실행 상세 →
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
