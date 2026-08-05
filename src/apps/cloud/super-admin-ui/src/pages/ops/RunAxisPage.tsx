/* 실행 축 — 최근 런 → 선택한 런의 작업 (ALPHA-738, 마스터–상세).
 *
 * 이 화면의 유일한 계약: **아래 작업 목록은 위에서 고른 런 하나에 속한다.** 런이 다르면
 * 작업도 다르다 — 서로 다른 런의 작업을 한 목록에 합치면 "언제 것인지 모르는 표"가 된다.
 *
 * 런–작업 연결 키는 `run_id` 하나다. 거래일이나 파이프라인명으로 잇지 않는다 — 같은 거래일에
 * 정규·수동·백필·재실행이 함께 존재하므로 날짜로 이으면 남의 런 작업이 섞인다.
 *
 * 실행 방식은 정규 / 수동 / 백필 셋이다. 백필은 흐름이 아니라 실행 방식이고, 같은 체인을
 * 과거 날짜로 다시 돌린다 — 산출 축 숫자에 "어느 런이 만든 것인가"가 붙어야 하는 이유다.
 */
import { useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { StatusBadge } from 'ui-kit';
import type { BadgeTone } from 'ui-kit';
import { retryCap } from '../../rules/rules';
import type { RunFact, TaskFact } from '../../rules/types';
import { MOCK_RUN_TASKS } from '../../mock/preview';
import { MockChip } from '../_shared/MockPreview';
import { Absent, AxisHeader, F, Info, fmt, kst } from './shared';
import '../../styles/ops.css';

const KIND_LABEL: Record<string, string> = { scheduled: '정규', manual: '수동', backfill: '백필' };
const LANE_LABEL: Record<string, string> = { 'etf-daily': '시장 EOD', news: '뉴스' };

const LEDGER_TONE: Record<string, BadgeTone> = {
  SUCCEEDED: 'active',
  FAILED: 'blocked',
  TIMED_OUT: 'blocked',
  ABORTED: 'blocked',
  RUNNING: 'warn',
};

/* 작업 stage → 화면 그룹. 원장 어휘를 그대로 쓰고, 모르는 stage 는 원문으로 남긴다 —
 * 새 stage 가 생겼을 때 조용히 사라지는 것보다 낯선 이름으로 보이는 편이 낫다. */
const STAGE_GROUP: Record<string, string> = {
  raw: '수집',
  normalize: '정제',
  feature: '적재',
  analysis: '분석',
  publish: '게시·전달',
  delivery: '게시·전달',
};
const STAGE_ORDER = ['수집', '정제', '적재', '분석', '게시·전달'];

/* ── 작업 상태 판정 ──
 * plan 축(plan_status)과 outcome 축(task_outcome)은 다른 축이다. 데이터가 없다고 상태를
 * 추정하지 않는다 — 아래는 전부 원장 필드의 직접 대응이고, 유일한 파생은 PENDING 을
 * 시도 유무로 가르는 것(시도가 있으면 실행 중, 없으면 대기)이다. */
type TaskState =
  | '성공'
  | '부분 결손'
  | '실행 중'
  | '대기'
  | '실패'
  | '타임아웃'
  | '미기동'
  | '선행 미충족'
  | '계획 제외'
  | '판정 없음';

const STATE_TONE: Record<TaskState, BadgeTone> = {
  성공: 'active',
  '부분 결손': 'warn',
  '실행 중': 'env',
  대기: 'neutral',
  실패: 'blocked',
  타임아웃: 'blocked',
  미기동: 'blocked',
  '선행 미충족': 'warn',
  '계획 제외': 'gated',
  '판정 없음': 'neutral',
};

const TIMEOUT_REASON = /TIMED_OUT|TIMEOUT/i;

function taskState(t: TaskFact): TaskState {
  if (t.plan_status === 'SKIPPED') return '계획 제외';
  switch (t.task_outcome) {
    case 'FULFILLED':
      /* 실행 성공과 데이터 유효는 다른 축이다 — 불완전한 산출이 온전히 초록으로 보이면 안 된다 */
      return t.data_status === 'INCOMPLETE' || t.data_status === 'INVALID' ? '부분 결손' : '성공';
    case 'FAILED':
      return TIMEOUT_REASON.test(String(t.outcome_reason ?? '')) ? '타임아웃' : '실패';
    case 'MISSED':
      return '미기동';
    case 'BLOCKED':
      return '선행 미충족';
    case 'PENDING':
      return (t.attempts ?? 0) > 0 ? '실행 중' : '대기';
    default:
      return '판정 없음';
  }
}

/** 런 하나의 작업 — 원장 기록이 있으면 그것이 이긴다. 없으면 검수용 목이고, 그 사실을 밝힌다. */
function tasksOfRun(runId: string): { tasks: TaskFact[]; mock: boolean } {
  const ledger = F.tasks.filter((t) => t.run_id === runId);
  if (ledger.length > 0) return { tasks: ledger, mock: false };
  return { tasks: MOCK_RUN_TASKS[runId] ?? [], mock: (MOCK_RUN_TASKS[runId]?.length ?? 0) > 0 };
}

/** 슬롯 키에서 예정 시각만 — "etf-daily:2026-08-03T15:40" → "15:40" */
const slotTime = (runId: string) => runId.match(/T(\d{2}:\d{2})/)?.[1] ?? null;

function runStatus(r: RunFact): { label: string; tone: BadgeTone } {
  if (r.no_run_row) return { label: '행 없음', tone: 'blocked' };
  if (r.ledger_status) return { label: r.ledger_status, tone: LEDGER_TONE[r.ledger_status] ?? 'neutral' };
  if (r.aws_status) return { label: `원장 없음 · AWS ${r.aws_status}`, tone: 'warn' };
  return { label: '원장 없음', tone: 'neutral' };
}

export function RunAxisPage() {
  const [params, setParams] = useSearchParams();
  const runs = F.runs;

  /* 사건 카드의 기존 드릴다운(?focus=run-… / task-…)을 계속 받는다 — 지목된 작업이 속한
   * 런을 골라야 그 작업이 화면에 남는다. 안 그러면 첫 런이 선택돼 지목이 사라진다. */
  const focus = params.get('focus');
  const focusedRunId = useMemo(() => {
    if (!focus) return undefined;
    if (focus.startsWith('run-')) return focus.slice(4);
    if (focus.startsWith('task-')) {
      const key = focus.slice(5);
      return F.tasks.find((t) => t.task_key === key)?.run_id;
    }
    return undefined;
  }, [focus]);

  const requested = params.get('run_id');
  const requestedValid = requested != null && runs.some((r) => r.id === requested);
  const selectedId =
    (requestedValid ? requested : undefined) ??
    (focusedRunId && runs.some((r) => r.id === focusedRunId) ? focusedRunId : undefined) ??
    runs[0]?.id;

  /* URL 에 있는 run_id 가 목록에 없으면(오타·필터로 사라짐) 첫 런으로 떨어뜨리고 주소를 정리한다 */
  useEffect(() => {
    if (requested != null && !requestedValid && selectedId) {
      const next = new URLSearchParams(params);
      next.set('run_id', selectedId);
      setParams(next, { replace: true });
    }
  }, [requested, requestedValid, selectedId, params, setParams]);

  const selectRun = (id: string) => {
    const next = new URLSearchParams(params);
    next.set('run_id', id);
    /* 지목(focus)은 직전 드릴다운의 것이라 런을 바꾸면 버린다 */
    next.delete('focus');
    /* 페이지 이동이 아니라 이 화면의 선택 상태다 — 히스토리를 쌓지 않는다 */
    setParams(next, { replace: true });
  };

  const selected = runs.find((r) => r.id === selectedId);

  return (
    <div className="flex flex-col gap-4">
      <AxisHeader question="오늘 어떤 런이 돌았고, 그 런의 작업은 귀결됐는가?" />

      <RunList runs={runs} selectedId={selectedId} onSelect={selectRun} />

      {selected ? (
        <RunTasks run={selected} focus={focus} />
      ) : (
        <div className="card card-pad">
          <p className="t-sm m-0" style={{ fontWeight: 600 }}>
            실데이터 0건
          </p>
          <p className="t-xs m-0" style={{ color: 'var(--fg-3)', marginTop: 4 }}>
            원장에 기록된 런이 없습니다. 선택된 런이 없으므로 작업 목록도 표시하지 않습니다.
          </p>
        </div>
      )}
    </div>
  );
}

/* ══ 마스터 — 최근 런 ══ */
function RunList({
  runs,
  selectedId,
  onSelect,
}: {
  runs: RunFact[];
  selectedId?: string;
  onSelect: (id: string) => void;
}) {
  return (
    <div className="card">
      <div className="card-head">
        <span className="t-label">최근 런</span>
        <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
          런을 고르면 아래 작업 목록이 그 런의 것으로 바뀝니다 · 원장과 AWS 제어면이 어긋나면 어느 쪽으로도
          덮지 않고 둘 다 보여줍니다
        </span>
      </div>
      <table className="table">
        <thead>
          <tr>
            <th style={{ width: 34 }}>
              <span className="sr-only">선택</span>
            </th>
            <th>런</th>
            <th>파이프라인</th>
            <th>실행 방식</th>
            <th>거래일</th>
            <th>예정</th>
            <th>원장</th>
            <th>AWS 제어면</th>
            <th>마감</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((r) => {
            const on = r.id === selectedId;
            const st = runStatus(r);
            return (
              <tr
                key={r.id}
                id={'run-' + r.id}
                className={on ? 'ops-run-selected' : undefined}
                onClick={() => onSelect(r.id)}
                style={{ cursor: 'pointer' }}
              >
                {/* 색에만 기대지 않는다 — 표식(▶)과 aria-pressed 로도 선택을 말한다 */}
                <td style={{ color: 'var(--accent)', fontWeight: 700 }}>{on ? '▶' : ''}</td>
                <td>
                  {/* 진짜 버튼이라 Tab 으로 닿고 Enter·Space 가 그대로 동작한다 */}
                  <button
                    type="button"
                    className="mono ops-run-btn"
                    aria-pressed={on}
                    onClick={(e) => {
                      e.stopPropagation();
                      onSelect(r.id);
                    }}
                  >
                    {r.id}
                  </button>
                  {r.mock && <MockChip />}
                  {on && (
                    <span className="chip chip-accent" style={{ marginLeft: 6 }}>
                      선택됨
                    </span>
                  )}
                </td>
                <td className="col-muted">{LANE_LABEL[r.lane] ?? r.lane}</td>
                <td>
                  <span className="chip">{KIND_LABEL[r.kind] ?? r.kind}</span>
                </td>
                <td className="col-muted">{r.trading_date}</td>
                <td className="col-muted">{slotTime(r.id) ?? <Absent kind="none" />}</td>
                <td>
                  <StatusBadge tone={st.tone}>{st.label}</StatusBadge>
                </td>
                <td>
                  {r.aws_status ? (
                    <StatusBadge tone={r.aws_status === 'SUCCEEDED' ? 'active' : 'blocked'}>
                      {r.aws_status}
                    </StatusBadge>
                  ) : (
                    <Absent kind="none" />
                  )}
                </td>
                <td className="col-muted">{r.deadline ? kst(r.deadline) : <Absent kind="none" />}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <div className="card-pad" style={{ paddingTop: 0 }}>
        <p className="t-xs m-0" style={{ color: 'var(--fg-3)' }}>
          같은 거래일에 정규·수동·백필·재실행이 함께 있을 수 있어 런은 날짜가 아니라{' '}
          <code>run_id</code> 로 지목합니다. 실행 방식(kind)은 아직 원장에 기록되지 않아 이 열은 목값입니다.
        </p>
      </div>
    </div>
  );
}

/* ══ 상세 — 선택한 런의 작업 ══ */
const COMPLETENESS_TIP =
  '완전성 = received / expected(엔티티 기준). 분모가 없는 작업은 위반이 아니라 평가 대상이 아니다 — ' +
  '분모를 |유니버스| × 거래일 곱으로 잡으면 휴장일마다 거짓 INCOMPLETE 가 난다.';
const ATTEMPT_TIP =
  '시도 / 정책 상한. 상한은 CatalogEntry 에 아직 없어 목값이다(SFN Retry 블록 0개) — ' +
  '분모 없이 2/3 처럼 쓰면 안 되고, 계측이 붙기 전엔 "시도 N회"까지만 정직하다.';
const FAILED_TIP =
  'ops.failed_records — 스텝이 스스로 판정한 유실값이며 skipped_* 를 더한 값이 아니다. 잡마다 단위가 다르다.';
const PLAN_TIP =
  '이 목록은 ops_expected_task(계획 스냅샷) 행이다 — 계획에 있는 작업만 나오고, plan_status 가 계획 축, ' +
  'task_outcome 이 실제 축이다. 두 축을 대조해 계획 제외·미기동을 가른다.\n' +
  '계획 행 자체가 없는 런은 무엇이 예정이었는지 알 수 없어 "계획 정보 없음"이라고 쓴다 — 0 개가 아니다.';

function RunTasks({ run, focus }: { run: RunFact; focus: string | null }) {
  const { tasks, mock } = tasksOfRun(run.id);
  const [open, setOpen] = useState<string | null>(null);

  /* 런이 바뀌면 펼침도 닫는다 — 상세는 (run_id, task_key) 조합으로만 의미가 있다 */
  useEffect(() => setOpen(null), [run.id]);

  /* 드릴다운 지목 — 이 런의 작업일 때만 스크롤·강조한다 */
  useEffect(() => {
    if (!focus) return;
    const el = document.getElementById(focus);
    if (!el) return;
    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    el.classList.add('ops-flash');
    const t = setTimeout(() => el.classList.remove('ops-flash'), 1800);
    return () => clearTimeout(t);
  }, [focus, run.id]);

  const states = tasks.map(taskState);
  const n = (s: TaskState) => states.filter((x) => x === s).length;
  const st = runStatus(run);

  const groups = useMemo(() => {
    const by = new Map<string, TaskFact[]>();
    for (const t of tasks) {
      const g = STAGE_GROUP[t.stage] ?? t.stage;
      if (!by.has(g)) by.set(g, []);
      by.get(g)!.push(t);
    }
    /* 이 런에 없는 단계는 빈 영역으로 만들지 않고 아예 숨긴다 */
    return [...by.entries()].sort(
      (a, b) =>
        (STAGE_ORDER.indexOf(a[0]) + 1 || 99) - (STAGE_ORDER.indexOf(b[0]) + 1 || 99) ||
        a[0].localeCompare(b[0]),
    );
  }, [tasks]);

  return (
    <div className="card">
      <div className="card-head">
        <span className="t-label">
          선택한 런의 작업 {tasks.length}개 {mock && <MockChip />}
        </span>
        <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
          이 런(run_id)에 속한 작업만 표시합니다 — 다른 런의 작업을 합치지 않습니다
          <Info tip={PLAN_TIP} label="계획 축" />
        </span>
      </div>

      {/* 선택한 런의 식별 정보 — 아래 숫자가 "어느 런의 것인가"에 답한다 */}
      <div className="card-pad" style={{ paddingBottom: 0 }}>
        <p className="t-sm m-0">
          <b>{LANE_LABEL[run.lane] ?? run.lane}</b> · {run.trading_date} ·{' '}
          {slotTime(run.id) ?? '예정 시각 없음'} · {KIND_LABEL[run.kind] ?? run.kind}{' '}
          <StatusBadge tone={st.tone}>{st.label}</StatusBadge>
        </p>
        <p className="t-xs mono m-0" style={{ color: 'var(--fg-3)', marginTop: 2 }}>
          run_id: {run.id}
        </p>
        {tasks.length > 0 && (
          <p className="t-sm m-0" style={{ marginTop: 6 }}>
            전체 <b>{tasks.length}</b>
            {(
              [
                ['성공', n('성공')],
                ['부분 결손', n('부분 결손')],
                ['실행 중', n('실행 중')],
                ['대기', n('대기')],
                ['실패', n('실패')],
                ['타임아웃', n('타임아웃')],
                ['미기동', n('미기동')],
                ['선행 미충족', n('선행 미충족')],
                ['계획 제외', n('계획 제외')],
                ['판정 없음', n('판정 없음')],
              ] as [TaskState, number][]
            )
              .filter(([, c]) => c > 0)
              .map(([label, c]) => (
                <span key={label}>
                  {' · '}
                  {label} <b>{c}</b>
                </span>
              ))}
          </p>
        )}
        {mock && (
          <p className="t-xs m-0" style={{ color: 'var(--fg-3)', marginTop: 6 }}>
            이 런의 작업은 스냅샷에 기록이 없어 <b>화면 검수용 목데이터</b>로 채웠습니다 — 실제 운영
            데이터가 아닙니다. 원장 기록이 있는 런은 목으로 덮지 않습니다.
          </p>
        )}
      </div>

      {tasks.length === 0 ? (
        <div className="card-pad">
          <p className="t-sm m-0">이 런에 기록된 작업이 없습니다.</p>
          <p className="t-xs m-0" style={{ color: 'var(--fg-3)', marginTop: 4 }}>
            {/* 예정 개수를 모르면 지어내지 않는다 — "예정 N개"를 쓰려면 계획 행이 있어야 한다 */}
            계획 정보 없음 · 실행 기록 0개 —{' '}
            {run.no_run_row
              ? '이 슬롯은 런 행 자체가 생성되지 않아(계획 슬롯 미기동) 무엇이 예정이었는지 원장이 답하지 못합니다.'
              : '이 런의 계획(ops_expected_task) 행이 스냅샷에 없어 예정 작업 수를 알 수 없습니다.'}{' '}
            다른 런의 작업으로 대체하지 않습니다.
          </p>
        </div>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table className="table">
            <thead>
              <tr>
                <th>작업</th>
                <th>상태</th>
                <th className="num">산출 행</th>
                <th className="num">
                  유실 <Info tip={FAILED_TIP} label="유실" />
                </th>
                <th>데이터 판정</th>
                <th>
                  완전성 <Info tip={COMPLETENESS_TIP} label="완전성" />
                </th>
                <th className="num">
                  시도 <Info tip={ATTEMPT_TIP} label="시도·상한" />
                </th>
              </tr>
            </thead>
            {groups.map(([group, items]) => (
              <tbody key={group}>
                <tr>
                  <td colSpan={7} className="t-label" style={{ paddingTop: 14 }}>
                    {group} {items.length}개
                  </td>
                </tr>
                {items.map((t) => {
                  const rowKey = `${run.id}|${t.task_key}`;
                  const state = taskState(t);
                  return (
                    <TaskRows
                      key={rowKey}
                      run={run}
                      task={t}
                      state={state}
                      open={open === rowKey}
                      onToggle={() => setOpen(open === rowKey ? null : rowKey)}
                    />
                  );
                })}
              </tbody>
            ))}
          </table>
        </div>
      )}
    </div>
  );
}

const DATA_TONE: Record<string, BadgeTone> = {
  VALID: 'active',
  VALID_EMPTY: 'active',
  INCOMPLETE: 'warn',
  INVALID: 'blocked',
  UNKNOWN: 'neutral',
};

function TaskRows({
  run,
  task: t,
  state,
  open,
  onToggle,
}: {
  run: RunFact;
  task: TaskFact;
  state: TaskState;
  open: boolean;
  onToggle: () => void;
}) {
  const detailId = `task-detail-${run.id}-${t.task_key}`;
  return (
    <>
      <tr id={'task-' + t.task_key} onClick={onToggle} style={{ cursor: 'pointer' }}>
        <td>
          <button type="button" className="mono ops-run-btn" aria-expanded={open} aria-controls={detailId}
            onClick={(e) => { e.stopPropagation(); onToggle(); }}>
            {t.task_key}
          </button>
        </td>
        <td>
          <StatusBadge tone={STATE_TONE[state]}>{state}</StatusBadge>
        </td>
        <td className="num">{t.records_out != null ? fmt(t.records_out) : <Absent kind="none" />}</td>
        <td className="num" style={t.failed_records ? { color: 'var(--warn)', fontWeight: 600 } : undefined}>
          {t.failed_records != null ? fmt(t.failed_records) : <Absent kind="none" />}
        </td>
        <td>
          {t.data_status ? (
            <StatusBadge tone={DATA_TONE[t.data_status] ?? 'neutral'}>{t.data_status}</StatusBadge>
          ) : (
            <Absent kind="none" />
          )}
        </td>
        <td>
          {t.completeness_expected != null ? (
            <>
              <span className="num">
                {t.completeness_received}/{t.completeness_expected}
              </span>
              {t.cmpl_mock && <MockChip />}
            </>
          ) : (
            <span className="t-xs" style={{ color: 'var(--fg-4)' }}>
              분모 없음
            </span>
          )}
        </td>
        {/* 정책 상한이 없으면 분모를 지어내지 않는다 — "시도 N회"까지만 정직하다 */}
        <td className="num col-muted">
          {retryCap(t) != null ? (
            <>
              <span className="num">
                {t.attempts}/{retryCap(t)}
              </span>
              {t.retry_mock && <MockChip />}
            </>
          ) : (
            <>
              {t.attempts != null ? `${t.attempts}회` : <Absent kind="none" />}
              <span className="chip" style={{ marginLeft: 6 }}>
                상한 미선언
              </span>
            </>
          )}
        </td>
      </tr>
      {open && (
        <tr id={detailId}>
          <td colSpan={7} style={{ background: 'var(--bg-sunken)' }}>
            <TaskDetail run={run} task={t} state={state} />
          </td>
        </tr>
      )}
    </>
  );
}

/** 작업 상세 — (run_id, task_key, 마지막 시도)로 식별한다. 같은 작업명의 다른 런 기록과 섞이지 않는다. */
function TaskDetail({ run, task: t, state }: { run: RunFact; task: TaskFact; state: TaskState }) {
  const facts: [string, React.ReactNode][] = [
    ['run_id', <span className="mono">{run.id}</span>],
    ['작업', <span className="mono">{t.task_key}</span>],
    ['stage', `${t.stage} (${STAGE_GROUP[t.stage] ?? t.stage})`],
    ['귀결', `${state}${t.task_outcome ? ` · 원장 ${t.task_outcome}` : ''} · 계획 ${t.plan_status ?? '—'}`],
    ['데이터셋', t.dataset ?? <Absent kind="none" />],
    ['시작', t.started_at ? kst(t.started_at) : <Absent kind="uninstrumented" />],
    ['종료', t.finished_at ? kst(t.finished_at) : t.fulfilled_at ? `${kst(String(t.fulfilled_at))} (완료 시각)` : <Absent kind="uninstrumented" />],
    ['시도', t.attempts != null ? `${t.attempts}회 (마지막 #${Math.max(t.attempts, 1)})` : <Absent kind="none" />],
    ['exit code', t.exit_code != null ? String(t.exit_code) : <Absent kind="uninstrumented" />],
    ['귀결 사유', (t.outcome_reason as string) ?? (t.skip_reason as string) ?? <Absent kind="none" />],
    ['산출 행', t.records_out != null ? fmt(t.records_out) : <Absent kind="none" />],
    ['유실', t.failed_records != null ? fmt(t.failed_records) : <Absent kind="none" />],
    [
      '완전성',
      t.completeness_expected != null
        ? `${t.completeness_received}/${t.completeness_expected}`
        : '분모 없음 (평가 대상 아님)',
    ],
  ];
  /* 런이 terminal 인데 작업 귀결이 안 쓰였다 — 상태를 지어내지 않고 사실만 덧붙인다 */
  const runTerminal = ['FAILED', 'TIMED_OUT', 'ABORTED'].includes(run.ledger_status ?? '');
  return (
    <div style={{ padding: 'var(--sp-4) 0' }}>
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(230px, 1fr))',
          gap: 'var(--sp-3) var(--sp-6)',
        }}
      >
        {facts.map(([k, v]) => (
          <div key={k} className="t-xs">
            <span style={{ color: 'var(--fg-3)' }}>{k}</span>{' '}
            <span style={{ color: 'var(--fg-1)' }}>{v}</span>
          </div>
        ))}
      </div>
      {runTerminal && (state === '실행 중' || state === '대기') && (
        <p className="t-xs m-0" style={{ color: 'var(--warn)', marginTop: 8 }}>
          런은 {run.ledger_status} 로 끝났는데 이 작업의 귀결이 원장에 쓰이지 않았습니다 — 상태를 추정하지 않고
          원장 값 그대로 둡니다.
        </p>
      )}
      {/* 시도 축이 실제로 빠져 있을 때만 말한다 — 값이 있는 행에서 띄우면 거짓 안내가 된다 */}
      {(t.started_at == null || t.finished_at == null || t.exit_code == null) && (
        <p className="t-xs m-0" style={{ color: 'var(--fg-3)', marginTop: 8 }}>
          시작·종료·exit code 는 시도 축(ops_task_attempt) 값입니다. 이 스냅샷은 그 축을 담지 않아{' '}
          <b>계측 없음</b>으로 나옵니다 — 0 이나 성공이 아닙니다.
        </p>
      )}
      {/* 한 단계 아래 = 원장 근거. 이 화면은 스냅샷 요약이고 시도 전량·대조 이슈는 라이브 원장에 있다.
       * 같은 run_key 네임스페이스라 그대로 넘기고, 원장에 없으면 그쪽이 정직하게 404 를 말한다. */}
      <p className="t-xs m-0" style={{ marginTop: 8 }}>
        <Link to={`/sources?runKey=${encodeURIComponent(run.id)}&task=${encodeURIComponent(t.task_key)}`}>
          원장 근거 보기 →
        </Link>{' '}
        <span style={{ color: 'var(--fg-3)' }}>
          시도(attempt) 전량·대조 이슈는 실행 원장 상세가 답합니다 (라이브 원장 조회 — 이 스냅샷 런이
          원장에 없으면 그 화면이 그렇게 밝힙니다)
        </span>
      </p>
    </div>
  );
}
