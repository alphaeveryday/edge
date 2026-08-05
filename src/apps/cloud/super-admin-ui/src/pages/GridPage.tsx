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
import { InfoPopover } from './_shared/InfoPopover';
import { LoadError } from './_shared/LoadError';
import '../styles/grid.css';

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

/** 귀결 라벨 — 원장 어휘의 표시명. 배경색과 짝이라 범례와 셀이 같은 출처를 본다. */
const OUTCOME_LABEL: Record<string, string> = {
  FULFILLED: '성공',
  FAILED: '실패',
  BLOCKED: '선행 미충족',
  MISSED: '미기동',
  PENDING: '대기',
};

/**
 * 셀 한 칸의 시각 표현 — 격자와 범례가 **이 컴포넌트 하나**를 공유한다.
 * 두 곳에 모양을 복제하면 범례가 조용히 거짓말을 하게 된다.
 *
 * 인코딩(기존 그대로): 배경=task_outcome · 파란 테두리=귀결 전 PENDING 인데 도는 시도 있음 ·
 * 사선=plan_status SKIPPED · 우상 주황 점=failed_records>0 또는 data_status INCOMPLETE/INVALID ·
 * 우하 초록 점=data_status VALID(VALID_EMPTY 는 제외).
 */
function CellVisual({
  outcome,
  skipped = false,
  running = false,
  defect = false,
  verified = false,
}: {
  outcome?: string | null;
  skipped?: boolean;
  running?: boolean;
  defect?: boolean;
  verified?: boolean;
}) {
  const cls = [
    'gd-cell',
    skipped ? 'gd-skipped' : '',
    running ? 'gd-running' : '',
    !running && outcome === 'MISSED' ? 'gd-missed' : '',
  ]
    .filter(Boolean)
    .join(' ');
  return (
    <span
      className={cls}
      /* 사선(SKIPPED)일 때는 배경을 덮지 않는다 — 계획 축이 귀결 축을 가린다 */
      style={skipped ? undefined : { background: OUTCOME_BG[outcome ?? ''] ?? OUTCOME_BG.PENDING }}
    >
      {defect && <span className="gd-dot gd-dot-defect" />}
      {verified && <span className="gd-dot gd-dot-valid" />}
    </span>
  );
}

const STATUS_TIP = [
  '배경색 = ops_expected_task.task_outcome (성공 · 실패 · 선행 미충족 · 미기동 · 대기).',
  '  목록에 없는 새 어휘는 대기 회색으로 떨어진다 — 모르는 상태를 성공·실패로 단정하지 않는다.',
  '',
  '파란 테두리 = 귀결은 아직 PENDING 인데 도는 시도(ops_task_attempt RUNNING)가 있다.',
  '  배경이 아니라 테두리인 이유: 귀결 축과 시도 축은 다른 축이라 덮으면 한쪽이 사라진다.',
  '',
  '사선 = plan_status SKIPPED (계획 제외). 안 한 게 아니라 할 일이 아니었다.',
  '  "작업 정의 없음"(·)과 다른 사실이다 — 그쪽은 그 슬롯의 작업 정의에 애초에 없었다는 뜻이고,',
  '  이쪽은 정의에는 있는데 계획 단계에서 빠졌다는 뜻이다.',
  '',
  '우상 주황 점 = failed_records > 0 또는 data_status 가 INCOMPLETE·INVALID.',
  '  실행 성공과 데이터 유효는 다른 축이라, 이 점이 없으면 불완전한 산출이 온전히 초록으로 보인다.',
  '',
  '우하 초록 점 = data_status VALID — 기대와 대조까지 통과한 성공.',
  '  VALID_EMPTY 에는 붙이지 않는다. 그건 "검증할 산출이 없었다"이지 "기대와 대조해 맞았다"가 아니다.',
  '',
  '산출·유실이 "—" 인 칸은 건수 신호를 남기지 않은 것이다 — 0건 처리와 다르다.',
].join('\n');

/** 범례 — 실제 셀과 같은 CellVisual 을 쓴다. 항상 보이는 핵심 인코딩만 담는다. */
function GridLegend() {
  return (
    <div className="gd-legend">
      <span className="gd-legend-label">귀결</span>
      {(['FULFILLED', 'FAILED', 'BLOCKED', 'MISSED', 'PENDING'] as const).map((o) => (
        <span key={o} className="gd-legend-item">
          <CellVisual outcome={o} />
          {OUTCOME_LABEL[o]}
        </span>
      ))}
      <span className="gd-legend-label">표식</span>
      <span className="gd-legend-item">
        <CellVisual outcome="PENDING" running />
        실행 중
      </span>
      <span className="gd-legend-item">
        <CellVisual skipped />
        계획 제외
      </span>
      <span className="gd-legend-item">
        <CellVisual outcome="FULFILLED" defect />
        데이터 결손
      </span>
      <span className="gd-legend-item">
        <CellVisual outcome="FULFILLED" verified />
        완전성 검증
      </span>
      <span className="gd-legend-item">
        <span className="gd-none" aria-hidden="true">·</span>
        작업 정의 없음
      </span>
      <InfoPopover label="상태 기준" title="상태 기준" text={STATUS_TIP} />
    </div>
  );
}

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

/** 스크린리더용 — 런·작업·귀결·데이터 상태와 "어디로 가는가"까지 담는다 */
function cellLabel(cell: GridCell, runKey: string, skipped: boolean) {
  const outcome = skipped
    ? '계획 제외'
    : (OUTCOME_LABEL[cell.outcome ?? ''] ?? cell.outcome ?? '판정 없음');
  return (
    `실행 ${runKey} · 작업 ${cell.taskKey} · 귀결 ${outcome} · 데이터 ${cell.dataStatus ?? '기록 없음'}` +
    (cell.running ? ' · 실행 중' : '') +
    ` — 이 실행의 ${cell.taskKey} 상세 보기`
  );
}

function headTip(slot: GridSlot) {
  return [
    slot.runKey,
    `기동 ${slot.launchStatus ?? '—'} · 실행 전체 ${slot.orchestrationStatus ?? '—'}`,
    '이 실행 전체 보기',
  ].join('\n');
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
    navigate(
      `/sources?${mock ? 'preview=mock&' : ''}runKey=${encodeURIComponent(runKey)}${
        taskKey ? `&task=${encodeURIComponent(taskKey)}` : ''
      }`,
    );

  return (
    <div className="flex flex-col gap-4">
      <div className="card">
        <div className="card-head">
          <span className="t-label">
            파이프라인 실행 이력 {mock && <MockChip />}
          </span>
          <span className="t-xs" style={{ color: 'var(--fg-3)' }}>최근 {grid.days}일</span>
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
        </div>

        {/* 상태 범례 — 실제 셀과 같은 모양. 조작 안내는 아래에서 따로 말한다 */}
        <div className="card-pad" style={{ paddingTop: 0 }}>
          <GridLegend />
          <div className="gd-hint">
            <span>
              <b>런 헤더</b> 선택 → 이 실행 전체 보기
            </span>
            <span>
              <b>작업 셀</b> 선택 → 그 실행의 해당 작업 상세
            </span>
            <span style={{ marginLeft: 'auto' }}>
              이동 대상은 <b>실행 원장 상세</b>입니다 — 시도 이력·대조 이슈가 거기 있습니다
            </span>
          </div>
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
                    <th key={slot.runKey} style={{ padding: 0 }}>
                      {/* th 의 onClick 대신 진짜 버튼 — Tab·Enter·Space 가 그대로 동작한다 */}
                      <button
                        type="button"
                        className="gd-headbtn"
                        title={headTip(slot)}
                        aria-label={`실행 ${slot.runKey} · 기동 ${slot.launchStatus ?? '기록 없음'} · 실행 전체 ${slot.orchestrationStatus ?? '기록 없음'} — 이 실행 전체 보기`}
                        onClick={() => openDrilldown(slot.runKey)}
                        /* 기동 축도 함께 본다 — 기동 실패는 orchestration 이 영영 null 이라
                         * 그 축만 보면 "아예 못 뜬 슬롯"이 무색으로 남는다. */
                        style={{ color: slotHeaderColor(slot) }}
                      >
                        {slotLabel(slot.runKey)}
                      </button>
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
                            /* 그 슬롯의 작업 정의에 없던 작업 — 실패(빈 셀 방치)와도, 계획
                             * 제외(SKIPPED)와도 다른 사실이다. 클릭 대상으로 보이지 않게 둔다. */
                            return (
                              <td key={slot.runKey} style={{ padding: 2 }}>
                                <span
                                  className="gd-none"
                                  title={`${taskKey} @ ${slot.runKey}\n해당 실행 슬롯의 작업 정의에 포함되지 않음`}
                                >
                                  ·
                                </span>
                              </td>
                            );
                          }
                          const skipped = cell.planStatus === 'SKIPPED';
                          const defect =
                            (cell.failedRecords ?? 0) > 0 ||
                            cell.dataStatus === 'INCOMPLETE' ||
                            cell.dataStatus === 'INVALID';
                          /* VALID_EMPTY 는 표시하지 않는다 — "검증할 산출이 없었다"이지
                           * "기대와 대조해 맞았다"가 아니다. 뱃지는 후자만 주장한다. */
                          const verified = cell.dataStatus === 'VALID';
                          const visual = (
                            <CellVisual
                              outcome={cell.outcome}
                              skipped={skipped}
                              running={cell.running}
                              defect={defect}
                              verified={verified}
                            />
                          );
                          return (
                            <td key={slot.runKey} style={{ padding: 2 }}>
                              {/* td 의 onClick 대신 진짜 버튼 — 18×18 밀도는 버튼 자체가 유지한다 */}
                              <button
                                type="button"
                                className="gd-cellbtn"
                                title={cellTip(cell, slot.runKey)}
                                aria-label={cellLabel(cell, slot.runKey, skipped)}
                                onClick={() => openDrilldown(slot.runKey, cell.taskKey)}
                              >
                                {visual}
                              </button>
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
