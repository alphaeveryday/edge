import { StatusBadge } from 'ui-kit';
import type { BadgeTone } from 'ui-kit';
import type {
  DataStatus,
  ExecutionStatus,
  LaunchStatus,
  OrchestrationStatus,
  TaskOutcome,
  TaskStatus,
} from '../domains/sources';
import { useSourceReport } from '../domains/sources/hooks';
import { LoadError } from './_shared/LoadError';

/* 원장 어휘를 그대로 라벨링한다 — 화면에서 새 상태 이름을 만들지 않는다(ALPHA-181 이 새 상태
 * 테이블을 안 만든 것과 같은 이유: 어휘가 하나 늘 때마다 대조할 곳이 하나 늘어난다). */
const OUTCOME: Record<TaskOutcome, { label: string; tone: BadgeTone }> = {
  FULFILLED: { label: '완료', tone: 'active' },
  FAILED: { label: '실패', tone: 'blocked' },
  MISSED: { label: '미실행', tone: 'blocked' },
  BLOCKED: { label: '선행 미충족', tone: 'warn' },
  PENDING: { label: '대기', tone: 'neutral' },
};

const ORCHESTRATION: Record<OrchestrationStatus, { label: string; tone: BadgeTone }> = {
  SUCCEEDED: { label: '성공', tone: 'active' },
  RUNNING: { label: '실행 중', tone: 'env' },
  FAILED: { label: '실패', tone: 'blocked' },
  TIMED_OUT: { label: '시간 초과', tone: 'blocked' },
  ABORTED: { label: '중단', tone: 'warn' },
  UNKNOWN: { label: '확인 불가', tone: 'neutral' },
};

const LAUNCH: Record<LaunchStatus, { label: string; tone: BadgeTone }> = {
  LAUNCHED: { label: '기동됨', tone: 'active' },
  PLANNING: { label: '계획 중', tone: 'neutral' },
  LAUNCH_FAILED: { label: '기동 실패', tone: 'blocked' },
  LAUNCH_CONFLICT: { label: '기동 충돌', tone: 'warn' },
  LAUNCH_UNKNOWN: { label: '기동 확인 불가', tone: 'warn' },
};

/* 데이터 축은 실행 축과 별개다. 정상(VALID·VALID_EMPTY)과 근거 부족(UNKNOWN)은 굳이 표시하지
 * 않는다 — dev 실측상 UNKNOWN 이 대다수라 열이 소음이 된다. 다만 **결손은 반드시 드러낸다**:
 * 실행이 성공했는데 산출이 불완전한 경우가 이 화면이 놓치면 안 되는 바로 그 상태다. */
const DATA_DEFECT: Partial<Record<DataStatus, string>> = {
  INCOMPLETE: '데이터 불완전',
  INVALID: '데이터 오류',
};

/* 정상으로 보고 넘길 값. 이 목록에 없고 DATA_DEFECT 에도 없는 값은 **원문 그대로 띄운다** —
 * 원장이 새 결함 어휘를 추가해 먼저 배포되면, 숨기는 쪽은 새 결함이 정상으로 보이는 방향이다. */
const DATA_BENIGN: DataStatus[] = ['VALID', 'VALID_EMPTY', 'UNKNOWN'];

function dataDefect(status: DataStatus | null) {
  if (status === null || DATA_BENIGN.includes(status)) return undefined;
  return DATA_DEFECT[status] ?? status;
}

/* outcome 이 PENDING 일 때 **시도 축**이 말해주는 것. PENDING 은 "아직 판정 못 함"이지 "대기"가
 * 아니다 — 원장은 attempt 종료와 outcome 갱신을 별개 _safe 호출로 쓰므로, 앞만 커밋되면
 * `PENDING + FAILED` 가 정상적으로 존재한다(Reconciler 가 고치기 전까지, dev 는 그마저 DISABLED).
 * RUNNING 만 예외로 두면 **확정된 실패가 '대기'로** 보인다 — 관대해지는 쪽이라 위험하다. */
const PENDING_BY_ATTEMPT: Record<ExecutionStatus, { label: string; tone: BadgeTone }> = {
  RUNNING: { label: '실행 중', tone: 'env' },
  FAILED: { label: '시도 실패', tone: 'blocked' },
  TIMED_OUT: { label: '시도 시간초과', tone: 'blocked' },
  // 시도는 끝났는데 판정이 안 쓰였다 — 원장 기록이 빠진 것이지 정상이 아니다.
  SUCCEEDED: { label: '판정 누락', tone: 'warn' },
};

const STAGE_LABEL: Record<string, string> = {
  raw: '수집',
  normalize: '정제',
  feature: '적재',
};

/** 건수 표시 — null 은 "모름"이라 0 으로 쓰지 않는다(ALPHA-182 의 NULL 계약을 화면까지 보존). */
function count(value: number | null) {
  return value === null ? '—' : value.toLocaleString('ko-KR');
}

function finishedAt(iso: string | null) {
  if (iso === null) return '—';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString('ko-KR', { hour12: false });
}

function TaskRow({ task }: { task: TaskStatus }) {
  /* SKIPPED 는 outcome 이 없다 — "완료"로 칠하면 휴장이라 안 한 것과 해서 된 것이 같은 초록이 된다. */
  /* 원장 어휘가 늘어나면(소유는 data-pipeline) 여기 맵에 없는 값이 내려온다. undefined 를
   * 그대로 역참조하면 대시보드가 통째로 흰 화면이 된다 — 모르는 상태는 원문 그대로 보여주고
   * 화면은 살려둔다. 운영 화면이 죽는 것이 모르는 라벨보다 나쁘다. */
  const badge =
    task.planStatus === 'SKIPPED'
      ? { label: '계획 제외', tone: 'gated' as BadgeTone }
      : task.outcome === null
        ? { label: '판정 없음', tone: 'neutral' as BadgeTone }
        : /* outcome 은 작업이 **끝나야** 쓰인다. 그동안 PENDING 인 채로 시도 축만 움직이므로,
             PENDING 이면 시도가 말해주는 것을 그대로 낸다. 시도가 아예 없을 때만 "대기"다. */
          task.outcome === 'PENDING' && task.executionStatus !== null
          ? (PENDING_BY_ATTEMPT[task.executionStatus] ?? {
              label: task.executionStatus,
              tone: 'neutral' as BadgeTone,
            })
          : (OUTCOME[task.outcome] ?? { label: task.outcome, tone: 'neutral' as BadgeTone });

  /* 재시도가 시작돼도 원장은 이전 outcome 을 되돌리지 않는다(`record_attempt_start` 는 attempt 만
     만든다). 그래서 "이전 시도는 실패했고 지금 다시 돌고 있다"는 **두 사실**이 동시에 참이다.
     RUNNING 으로 outcome 을 덮어쓰면 실패했다는 사실이 지워지고, 안 보여주면 재시도 중인 걸
     모른다 — 축을 합치지 않는다는 이 화면의 원칙대로 둘 다 낸다(Codex #297). */
  const retrying =
    task.executionStatus === 'RUNNING' && task.outcome !== null && task.outcome !== 'PENDING';

  const defect = dataDefect(task.dataStatus);

  return (
    <tr>
      <td className="col-muted">{STAGE_LABEL[task.stage] ?? task.stage}</td>
      <td className="font-semibold">{task.dataset ?? '—'}</td>
      <td className="col-muted">{task.taskKey}</td>
      <td>
        <span style={{ display: 'inline-flex', gap: 6, flexWrap: 'wrap' }}>
          <StatusBadge tone={badge.tone}>{badge.label}</StatusBadge>
          {retrying && <StatusBadge tone="env">재시도 중</StatusBadge>}
          {/* 실행 성공 옆의 데이터 결손 — 이걸 빼면 불완전한 산출이 온전히 초록으로 보인다 */}
          {defect && <StatusBadge tone="warn">{defect}</StatusBadge>}
        </span>
      </td>
      <td className="num">{finishedAt(task.lastFinishedAt)}</td>
      <td className="col-num num">{count(task.recordsOut)}</td>
      <td className="col-num num">{count(task.failedRecords)}</td>
    </tr>
  );
}

export function SourcesPage() {
  const { data: report, isPending, isError, dataUpdatedAt } = useSourceReport();

  if (isPending) return null;
  /* 30초 폴링(hooks.ts)이 붙은 뒤로 isError 는 **두 가지**를 뜻한다 — 첫 조회부터 실패(보여줄 게
     없다)와 갱신만 실패(마지막으로 받은 상태는 여전히 유효하다). 둘을 같이 다루면 일시적인
     네트워크 한 번에 멀쩡한 운영 화면이 통째로 사라진다. 데이터가 없을 때만 에러 화면을 띄우고,
     있으면 마지막 상태를 유지하되 **언제 것인지 밝힌다**(Codex #297). */
  if (!report) return <LoadError />;

  const run = report.run;
  const orchestration = run?.orchestrationStatus
    ? (ORCHESTRATION[run.orchestrationStatus] ?? {
        label: run.orchestrationStatus,
        tone: 'neutral' as BadgeTone,
      })
    : null;
  /* 기동 실패는 orchestration 이 영영 null 이라, 기동 축을 따로 안 보여주면 "아예 못 떴다"가
   * 화면에서 "표시할 상태 없음"으로 사라진다 — 원장이 답하려는 바로 그 질문이다. */
  const launch = run?.launchStatus
    ? (LAUNCH[run.launchStatus] ?? { label: run.launchStatus, tone: 'neutral' as BadgeTone })
    : null;

  return (
    <div className="flex max-w-[1100px] flex-col gap-4">
      <div className="card">
        <div className="card-head">
          <span className="t-label">데이터 소스 수집 상태</span>
          {run && (
            <span className="t-xs num" style={{ color: 'var(--fg-3)' }}>
              {run.runKey}
              {run.tradingDate && ` · 거래일 ${run.tradingDate}`}
            </span>
          )}
        </div>

        {run === null ? (
          /* 원장에 런이 없다 — 볼 게 없는 것과 고장 난 것은 다르다(에러 화면을 띄우지 않는다). */
          <p className="t-xs m-0" style={{ color: 'var(--fg-3)' }}>
            아직 기록된 파이프라인 실행이 없습니다.
          </p>
        ) : (
          <>
            {(launch || orchestration) && (
              <p
                className="t-xs m-0"
                style={{ color: 'var(--fg-3)', display: 'flex', gap: 12, flexWrap: 'wrap' }}
              >
                {/* 런 귀결은 작업별 성패와 다른 축이다 — 런이 실패여도 작업 대부분은 성공일 수
                    있어, 목록만 보면 "대체로 초록"인 상태가 실제로는 실패한 런이다. */}
                {launch && (
                  <span>
                    기동 <StatusBadge tone={launch.tone}>{launch.label}</StatusBadge>
                  </span>
                )}
                {orchestration && (
                  <span>
                    실행 전체{' '}
                    <StatusBadge tone={orchestration.tone}>{orchestration.label}</StatusBadge>
                  </span>
                )}
              </p>
            )}
            <table className="table">
              <thead>
                <tr>
                  <th>단계</th>
                  <th>데이터셋</th>
                  <th>작업</th>
                  <th>상태</th>
                  <th>마지막 실행</th>
                  <th className="col-num">산출</th>
                  <th className="col-num">유실</th>
                </tr>
              </thead>
              <tbody>
                {report.tasks.map((t) => (
                  <TaskRow key={t.taskKey} task={t} />
                ))}
              </tbody>
            </table>
          </>
        )}
      </div>
      {isError && (
        <p className="t-xs m-0" style={{ color: 'var(--fg-3)' }}>
          <StatusBadge tone="warn">갱신 실패</StatusBadge>{' '}
          마지막으로 받은 상태입니다({new Date(dataUpdatedAt).toLocaleString('ko-KR', { hour12: false })}
          {') '}— 지금 원장과 다를 수 있습니다.
        </p>
      )}
      <p className="t-xs m-0" style={{ color: 'var(--fg-3)' }}>
        산출·유실이 “—”인 작업은 건수 신호를 남기지 않은 것입니다 — 0건 처리와 다릅니다.
      </p>
    </div>
  );
}
