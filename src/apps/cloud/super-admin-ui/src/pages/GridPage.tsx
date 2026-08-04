/* 실행 격자 — 슬롯(런) × 작업, 최근 30일 (ALPHA-594).
 *
 * "언제부터 무엇이 깨졌나"를 한 화면에서 답한다. 셀 의미론(4축 접기 — 로컬 프로토타입에서
 * 실데이터로 검증한 규칙 그대로):
 *   기본색   = task_outcome (FULFILLED 초록 / FAILED 빨강 / BLOCKED 주황 / MISSED 흰+빨강 테두리
 *              / PENDING 회색)
 *   파란 테두리 = 귀결 전(PENDING)에 도는 시도 있음 (outcome 은 끝날 때 써서 실행 중엔
 *              PENDING — 이 축이 없으면 "돌고 있다"와 "시작 전"이 같은 회색이 된다)
 *   사선     = plan_status SKIPPED (비거래일 등 — 안 한 게 아니라 할 일이 아니었다)
 *   모서리 점 = failed_records>0 또는 data_status INCOMPLETE·INVALID ("실행 성공 ≠ 데이터 유효")
 *   빈칸 ·   = 그 슬롯의 카탈로그에 없던 작업 (뉴스 6작업이 07-28부터 시장 런에 없는 것이 실례)
 *   우하 초록 점 = data_status VALID — 완전성 대조까지 통과한 성공 (ALPHA-611/630 배선 후 ETF
 *              3작업만 도달 가능. 나머지 작업의 UNKNOWN 은 설계값이라 무표시 — "성공"과
 *              "증거 있는 성공"을 격자에서 가르는 것이 이 뱃지의 이유다, ALPHA-650)
 *
 * 셀을 누르면 그 슬롯의 드릴다운(/sources?runKey=)으로 간다 — 격자는 이상 지점을 찾는 화면이고,
 * 원인(시도 이력·이슈)은 드릴다운이 답한다(ALPHA-574).
 */
import { Fragment, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { PageSkeleton } from 'ui-kit';
import type { GridCell, GridSlot, SourceGrid } from '../domains/sources';
import { useSourceGrid } from '../domains/sources/hooks';
import { MOCK_GRID } from '../mock/preview';
import { EmptyRealNotice, MockChip, MockPreview } from './_shared/MockPreview';
import { LoadError } from './_shared/LoadError';

/* 원장 어휘를 그대로 색에 대응시킨다 — 화면에서 새 상태 이름을 만들지 않는다(SourcesPage 와
 * 같은 이유). 목록에 없는 새 어휘는 PENDING 회색으로 떨어진다 — 모르는 값을 초록·빨강 어느
 * 쪽으로도 단정하지 않는 중립값이다. */
const OUTCOME_BG: Record<string, string> = {
  FULFILLED: '#22c55e',
  FAILED: '#ef4444',
  BLOCKED: '#f59e0b',
  MISSED: '#fff',
  PENDING: '#e5e7eb',
};

const STAGE_ORDER: Record<string, number> = { raw: 0, normalize: 1, feature: 2 };
const STAGE_LABEL: Record<string, string> = { raw: '수집', normalize: '정제', feature: '적재·피처' };

/* 헤더 색은 SourcesPage 의 상태 분류(blocked·warn 톤)를 따른다 — 같은 원장 어휘를 두 화면이
 * 다르게 칠하면 운영자가 화면마다 다른 심각도로 읽는다. FAILED 만 강조하면 TIMED_OUT·ABORTED·
 * LAUNCH_CONFLICT 슬롯이 정상색이라, 빈 열의 · 표시와 겹쳐 "카탈로그 변화"로 오독된다. */
function slotHeaderColor(slot: GridSlot) {
  if (
    slot.orchestrationStatus === 'FAILED' ||
    slot.orchestrationStatus === 'TIMED_OUT' ||
    slot.launchStatus === 'LAUNCH_FAILED'
  ) {
    return 'var(--down, #b91c1c)';
  }
  if (
    slot.orchestrationStatus === 'ABORTED' ||
    slot.launchStatus === 'LAUNCH_CONFLICT' ||
    slot.launchStatus === 'LAUNCH_UNKNOWN'
  ) {
    return '#b45309';
  }
  return 'var(--fg-2, #374151)';
}

/** "etf-daily:2026-07-27T15:40" → "07-27\n15:40". 시각 없는 구형 키(날짜만)는 날짜만 낸다.
 * 뉴스 레인(ALPHA-591)은 "뉴스" 접두를 붙인다 — 시장 15:40 과 뉴스 15:30 이 이웃 열이라
 * 시각만으로는 어느 레인의 슬롯인지 안 갈린다. */
function slotLabel(runKey: string) {
  const m = runKey.match(/(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}:\d{2}))?/);
  if (!m) return runKey;
  const lane = runKey.startsWith('news:') ? '뉴스 ' : '';
  return m[4] ? `${lane}${m[2]}-${m[3]}\n${m[4]}` : `${lane}${m[2]}-${m[3]}`;
}

function cellTip(cell: GridCell, runKey: string) {
  return [
    `${cell.taskKey} @ ${runKey}`,
    `귀결 ${cell.outcome ?? '—'} · 계획 ${cell.planStatus} · 데이터 ${cell.dataStatus ?? '—'}`,
    /* null 은 "모름"이지 0 이 아니다 — '—' 로 낸다(ALPHA-182) */
    `산출 ${cell.recordsOut ?? '—'} · 유실 ${cell.failedRecords ?? '—'}`,
    cell.running ? '실행 중 (시도 진행)' : '',
    cell.skipReason ?? cell.outcomeReason ?? '',
  ]
    .filter(Boolean)
    .join('\n');
}

/* 레인 판별은 run_key 접두가 정본이다(slotLabel 과 같은 근거) — 시장 15:40 과 뉴스 15:30 이
 * 섞이면 열이 소음이 된다는 운영 피드백(ALPHA-594 잔여 → 692). */
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

  /* 격자가 비면 셀 의미론(색·테두리·점)을 전혀 볼 수 없다 — 사실을 먼저 밝히고 검수용 목을 붙인다 */
  if (grid.slots.length === 0) {
    return (
      <div className="flex flex-col gap-4">
        <EmptyRealNotice>
          최근 {grid.days}일 안에 기록된 파이프라인 실행이 없습니다.
        </EmptyRealNotice>
        <MockPreview>
          <GridBody grid={MOCK_GRID} mock />
        </MockPreview>
      </div>
    );
  }

  return <GridBody grid={grid} />;
}

function GridBody({ grid, mock = false }: { grid: SourceGrid; mock?: boolean }) {
  const navigate = useNavigate();
  const [laneFilter, setLaneFilter] = useState<LaneFilter>('all');

  const slots = grid.slots.filter((slot) => {
    if (laneFilter === 'all') return true;
    return laneFilter === 'news'
      ? slot.runKey.startsWith('news:')
      : !slot.runKey.startsWith('news:');
  });

  /* 행 = 창 안 어느 슬롯에든 등장한 작업의 합집합. 한 슬롯만 보면 카탈로그에서 빠진 작업
   * (뉴스 레인 분리 등)이 행째로 사라져 "언제부터 없어졌나"를 못 본다.
   * stage 는 마지막(최신) 슬롯의 값이 이긴다 — 카탈로그가 작업의 stage 를 옮기면 첫 등장에
   * 고정할 경우 새 셀이 옛 stage 행에 계속 붙는다(현재 카탈로그가 행 축의 정본이다). */
  const taskStage = new Map<string, string>();
  for (const slot of slots) {
    for (const cell of slot.tasks) taskStage.set(cell.taskKey, cell.stage);
  }
  const stages = [...new Set([...taskStage.values()])].sort(
    (a, b) => (STAGE_ORDER[a] ?? 9) - (STAGE_ORDER[b] ?? 9),
  );
  const tasksOf = (stage: string) =>
    [...taskStage.entries()]
      .filter(([, s]) => s === stage)
      .map(([key]) => key)
      .sort();

  const cellByKey = new Map<string, GridCell>();
  for (const slot of slots) {
    for (const cell of slot.tasks) cellByKey.set(`${slot.runKey}|${cell.taskKey}`, cell);
  }

  /* 셀 클릭은 그 작업을 지목해 드릴다운의 해당 행으로 바로 떨어진다 — 런 전체만 열면
   * 방금 누른 작업을 목록에서 다시 찾아야 한다. 헤더 클릭은 런 전체. */
  const openDrilldown = (runKey: string, taskKey?: string) =>
    mock
      ? undefined
      : navigate(
      `/sources?runKey=${encodeURIComponent(runKey)}${
        taskKey ? `&task=${encodeURIComponent(taskKey)}` : ''
      }`,
    );

  return (
    <div className="flex flex-col gap-4">
      <div className="card">
        <div className="card-head">
          <span className="t-label">파이프라인 실행 이력 {mock && <MockChip />}</span>
          <span style={{ display: 'inline-flex', gap: 4 }}>
            {LANE_FILTERS.map((f) => (
              <button
                key={f.key}
                type="button"
                className="t-xs"
                onClick={() => setLaneFilter(f.key)}
                style={{
                  padding: '2px 8px',
                  borderRadius: 4,
                  border: '1px solid var(--border, #d1d5db)',
                  cursor: 'pointer',
                  background: laneFilter === f.key ? 'var(--fg-2, #374151)' : 'transparent',
                  color: laneFilter === f.key ? '#fff' : 'var(--fg-2, #374151)',
                }}
              >
                {f.label}
              </button>
            ))}
          </span>
          <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
            최근 {grid.days}일 · 색=귀결 · 파란 테두리=실행 중 · 사선=계획 스킵 · 우상 주황
            점=데이터 결손 · 우하 초록 점=완전성 검증(VALID) · ·=카탈로그에 없음 · 셀을 누르면
            그 실행의 드릴다운
          </span>
        </div>

        {slots.length === 0 ? (
          /* 창 안에 런이 없다 — 볼 게 없는 것과 고장 난 것은 다르다(에러 화면을 띄우지 않는다).
           * 필터로 비었을 땐 그 사실을 밝힌다 — "필터 때문"과 "원장이 빔"은 다른 사실이다. */
          <p className="t-xs m-0" style={{ color: 'var(--fg-3)' }}>
            {laneFilter === 'all'
              ? `최근 ${grid.days}일 안에 기록된 파이프라인 실행이 없습니다.`
              : `최근 ${grid.days}일 안에 이 레인의 실행이 없습니다 (필터: ${
                  LANE_FILTERS.find((f) => f.key === laneFilter)?.label
                }).`}
          </p>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ borderCollapse: 'collapse', fontSize: 11 }}>
              <thead>
                <tr>
                  <th
                    style={{ position: 'sticky', left: 0, background: 'var(--bg-card, #fff)' }}
                  />
                  {slots.map((slot) => (
                    <th
                      key={slot.runKey}
                      title={`${slot.runKey}\n기동 ${slot.launchStatus ?? '—'} · 실행 전체 ${slot.orchestrationStatus ?? '—'}`}
                      onClick={() => openDrilldown(slot.runKey)}
                      style={{
                        padding: '2px 3px',
                        whiteSpace: 'pre',
                        fontWeight: 500,
                        cursor: mock ? 'default' : 'pointer',
                        /* 기동 축도 함께 본다 — 기동 실패는 orchestration 이 영영 null 이라
                         * 그 축만 보면 "아예 못 뜬 슬롯"이 무색으로 남는다. */
                        color: slotHeaderColor(slot),
                      }}
                    >
                      {slotLabel(slot.runKey)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {stages.map((stage) => (
                  <Fragment key={stage}>
                    <tr>
                      <td
                        colSpan={slots.length + 1}
                        style={{ paddingTop: 10, fontWeight: 700, color: 'var(--fg-2, #374151)' }}
                      >
                        {STAGE_LABEL[stage] ?? stage}
                      </td>
                    </tr>
                    {tasksOf(stage).map((taskKey) => (
                      <tr key={taskKey}>
                        <td
                          style={{
                            position: 'sticky',
                            left: 0,
                            background: 'var(--bg-card, #fff)',
                            padding: '2px 8px 2px 4px',
                            whiteSpace: 'nowrap',
                          }}
                        >
                          {taskKey}
                        </td>
                        {slots.map((slot) => {
                          const cell = cellByKey.get(`${slot.runKey}|${taskKey}`);
                          if (!cell) {
                            /* 그 슬롯의 카탈로그에 없던 작업 — 실패(빈 셀 방치)와 구분해 그린다 */
                            return (
                              <td
                                key={slot.runKey}
                                style={{ textAlign: 'center', color: 'var(--fg-3, #d1d5db)' }}
                              >
                                ·
                              </td>
                            );
                          }
                          const skipped = cell.planStatus === 'SKIPPED';
                          const bg = skipped
                            ? 'repeating-linear-gradient(45deg,#e5e7eb 0 3px,#fff 3px 6px)'
                            : (OUTCOME_BG[cell.outcome ?? ''] ?? OUTCOME_BG.PENDING);
                          const defect =
                            (cell.failedRecords ?? 0) > 0 ||
                            cell.dataStatus === 'INCOMPLETE' ||
                            cell.dataStatus === 'INVALID';
                          /* VALID_EMPTY 는 표시하지 않는다 — "검증할 산출이 없었다"이지
                           * "기대와 대조해 맞았다"가 아니다. 뱃지는 후자만 주장한다. */
                          const verified = cell.dataStatus === 'VALID';
                          return (
                            <td
                              key={slot.runKey}
                              title={cellTip(cell, slot.runKey)}
                              onClick={() => openDrilldown(slot.runKey, cell.taskKey)}
                              style={{ padding: 2, cursor: mock ? 'default' : 'pointer' }}
                            >
                              <div
                                style={{
                                  width: 18,
                                  height: 18,
                                  borderRadius: 3,
                                  background: bg,
                                  /* 실행 중은 배경(귀결 축)을 덮지 않고 테두리로 겹쳐 그린다 */
                                  border: cell.running
                                    ? '2px solid #3b82f6'
                                    : cell.outcome === 'MISSED'
                                      ? '2px solid #ef4444'
                                      : '1px solid #d1d5db',
                                  position: 'relative',
                                }}
                              >
                                {defect && (
                                  <span
                                    style={{
                                      position: 'absolute',
                                      top: -3,
                                      right: -3,
                                      width: 8,
                                      height: 8,
                                      borderRadius: 4,
                                      background: '#f59e0b',
                                      border: '1px solid #fff',
                                    }}
                                  />
                                )}
                                {verified && (
                                  /* 결손 점(우상 주황)과 자리를 가른다 — 셀 배경 초록(FULFILLED)
                                   * 위에서도 읽히도록 진초록 + 흰 테두리 */
                                  <span
                                    style={{
                                      position: 'absolute',
                                      bottom: -3,
                                      right: -3,
                                      width: 8,
                                      height: 8,
                                      borderRadius: 4,
                                      background: '#15803d',
                                      border: '1px solid #fff',
                                    }}
                                  />
                                )}
                              </div>
                            </td>
                          );
                        })}
                      </tr>
                    ))}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
