/* 실행 격자 — 슬롯(런) × 작업, 최근 30일 (ALPHA-594).
 *
 * "언제부터 무엇이 깨졌나"를 한 화면에서 답한다. 셀 의미론(4축 접기 — 로컬 프로토타입에서
 * 실데이터로 검증한 규칙 그대로):
 *   기본색   = task_outcome (FULFILLED 초록 / FAILED 빨강 / BLOCKED 주황 / MISSED 흰+빨강 테두리
 *              / PENDING 회색)
 *   사선     = plan_status SKIPPED (비거래일 등 — 안 한 게 아니라 할 일이 아니었다)
 *   모서리 점 = failed_records>0 또는 data_status INCOMPLETE·INVALID ("실행 성공 ≠ 데이터 유효")
 *   빈칸 ·   = 그 슬롯의 카탈로그에 없던 작업 (뉴스 6작업이 07-28부터 시장 런에 없는 것이 실례)
 * VALID 뱃지는 그리지 않는다 — completeness 배선이 없어 도달 불가다(ALPHA-182 에서 폐기).
 *
 * 셀을 누르면 그 슬롯의 드릴다운(/sources?runKey=)으로 간다 — 격자는 이상 지점을 찾는 화면이고,
 * 원인(시도 이력·이슈)은 드릴다운이 답한다(ALPHA-574).
 */
import { Fragment } from 'react';
import { useNavigate } from 'react-router-dom';
import type { GridCell } from '../domains/sources';
import { useSourceGrid } from '../domains/sources/hooks';
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

/** "etf-daily:2026-07-27T15:40" → "07-27\n15:40". 시각 없는 구형 키(날짜만)는 날짜만 낸다. */
function slotLabel(runKey: string) {
  const m = runKey.match(/(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}:\d{2}))?/);
  if (!m) return runKey;
  return m[4] ? `${m[2]}-${m[3]}\n${m[4]}` : `${m[2]}-${m[3]}`;
}

function cellTip(cell: GridCell, runKey: string) {
  return [
    `${cell.taskKey} @ ${runKey}`,
    `귀결 ${cell.outcome ?? '—'} · 계획 ${cell.planStatus} · 데이터 ${cell.dataStatus ?? '—'}`,
    /* null 은 "모름"이지 0 이 아니다 — '—' 로 낸다(ALPHA-182) */
    `산출 ${cell.recordsOut ?? '—'} · 유실 ${cell.failedRecords ?? '—'}`,
    cell.skipReason ?? cell.outcomeReason ?? '',
  ]
    .filter(Boolean)
    .join('\n');
}

export function GridPage() {
  const navigate = useNavigate();
  const { data: grid, isPending, isError } = useSourceGrid();

  if (isError) return <LoadError />;
  if (isPending) return null;

  const slots = grid.slots;

  /* 행 = 창 안 어느 슬롯에든 등장한 작업의 합집합. 한 슬롯만 보면 카탈로그에서 빠진 작업
   * (뉴스 레인 분리 등)이 행째로 사라져 "언제부터 없어졌나"를 못 본다. */
  const taskStage = new Map<string, string>();
  for (const slot of slots) {
    for (const cell of slot.tasks) {
      if (!taskStage.has(cell.taskKey)) taskStage.set(cell.taskKey, cell.stage);
    }
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

  const openDrilldown = (runKey: string) =>
    navigate(`/sources?runKey=${encodeURIComponent(runKey)}`);

  return (
    <div className="flex flex-col gap-4">
      <div className="card">
        <div className="card-head">
          <span className="t-label">실행 격자</span>
          <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
            최근 {grid.days}일 · 색=귀결 · 사선=계획 스킵 · 모서리 점=데이터 결손 · ·=카탈로그에
            없음 · 셀을 누르면 그 실행의 드릴다운
          </span>
        </div>

        {slots.length === 0 ? (
          /* 창 안에 런이 없다 — 볼 게 없는 것과 고장 난 것은 다르다(에러 화면을 띄우지 않는다). */
          <p className="t-xs m-0" style={{ color: 'var(--fg-3)' }}>
            최근 {grid.days}일 안에 기록된 파이프라인 실행이 없습니다.
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
                        cursor: 'pointer',
                        /* 런 실패와 기동 실패 둘 다 빨강 — 기동 실패는 orchestration 이 영영
                         * null 이라 그 축만 보면 "아예 못 뜬 슬롯"이 무색으로 남는다. */
                        color:
                          slot.orchestrationStatus === 'FAILED' ||
                          slot.launchStatus === 'LAUNCH_FAILED'
                            ? 'var(--down, #b91c1c)'
                            : 'var(--fg-2, #374151)',
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
                          return (
                            <td
                              key={slot.runKey}
                              title={cellTip(cell, slot.runKey)}
                              onClick={() => openDrilldown(slot.runKey)}
                              style={{ padding: 2, cursor: 'pointer' }}
                            >
                              <div
                                style={{
                                  width: 18,
                                  height: 18,
                                  borderRadius: 3,
                                  background: bg,
                                  border:
                                    cell.outcome === 'MISSED'
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
