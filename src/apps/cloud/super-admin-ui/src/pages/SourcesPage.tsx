import { Fragment } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { PageSkeleton, StatusBadge } from 'ui-kit';
import type { BadgeTone } from 'ui-kit';
import { ApiError } from '../api/client';
import type {
  Attempt,
  DataStatus,
  ExecutionStatus,
  LaunchStatus,
  OrchestrationStatus,
  ReconciliationIssue,
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

/**
 * 상세 줄용 짧은 시각. **날짜를 버리지 않는다** — 같은 런의 기록이라도 사후 복구는 며칠 뒤
 * `now()` 로 찍히므로, 시:분만 쓰면 `23:50 → 09:00` 처럼 순서와 경과가 거꾸로 읽힌다.
 */
function clock(iso: string | null) {
  if (iso === null) return '—';
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? '—'
    : d.toLocaleString('ko-KR', {
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        hour12: false,
      });
}

/* 정상 계측(WRAPPER)과 사후 복구를 가른다 — 뭉개면 "원장이 스스로 메운 행"이 실제로 관측된
 * 실행처럼 보인다. 관대해지는 방향이라 화면에서 반드시 구분한다. */
const BACKFILL = 'RECONCILER_BACKFILL';

/**
 * 이 시도가 "그냥 잘 된 것"인가. 아니면 상세 줄에 펼쳐 보여줄 사유가 있다는 뜻이다.
 * exit_code 는 **null(모름)을 성공으로 치지 않는다** — 0 일 때만 성공이다.
 */
function isClean(a: Attempt) {
  return (
    a.executionStatus === 'SUCCEEDED' &&
    a.exitCode === 0 &&
    a.failureReason === null &&
    a.recordSource !== BACKFILL
  );
}

/**
 * 상세 줄을 펼칠 가치가 있는 작업인가.
 *
 * 25행 전부에 시각을 늘어놓으면 예외가 묻힌다 — `expected_at`·`deadline_at` 은 Planner 가 **모든**
 * 작업에 채우므로 "값이 있으면 보여준다"는 규칙은 그대로 전 행 노출이 된다. 그래서 사유가 있는
 * 행(재시도·미실행 판정·제외/귀결 사유·깨끗하지 않은 시도)만 펼치고, 그 행에서는 시각을 함께 낸다.
 */
function hasDetail(task: TaskStatus) {
  return (
    task.attempts.length > 1 ||
    task.attempts.some((a) => !isClean(a)) ||
    task.missedAt !== null ||
    task.skipReason !== null ||
    task.outcomeReason !== null
  );
}

/* ECS Task ARN 은 길어서 전부 쓰면 줄이 묻힌다. 운영자가 로그를 찾을 때 쓰는 건 마지막 조각
 * (task id)이라 그것만 낸다 — 이걸 아예 안 보여주면 실패한 시도에서 로그로 넘어갈 손잡이가 없다. */
function taskId(arn: string | null) {
  if (arn === null) return null;
  const last = arn.split('/').pop();
  return last === undefined || last === '' ? null : last;
}

function AttemptLine({ attempt, index }: { attempt: Attempt; index: number }) {
  const span = `${clock(attempt.startedAt)} → ${clock(attempt.finishedAt)}`;
  const id = taskId(attempt.ecsTaskArn);
  return (
    <span style={{ display: 'block' }}>
      {`#${attempt.attemptNumber ?? index + 1} ${attempt.executionStatus} · ${span}`}
      {/* exit_code 는 null 이면 "모름"이다 — 0(성공)으로 메우지 않는다 */}
      {attempt.exitCode !== null && ` · exit ${attempt.exitCode}`}
      {attempt.recordSource === BACKFILL && ' · 사후 복구 기록'}
      {attempt.failureReason && ` · ${attempt.failureReason}`}
      {id && ` · task ${id}`}
    </span>
  );
}

function TaskDetailRow({ task }: { task: TaskStatus }) {
  const facts = [
    task.expectedAt && `예정 ${clock(task.expectedAt)}`,
    task.deadlineAt && `기한 ${clock(task.deadlineAt)}`,
    // 비래치라 나중에 FULFILLED 로 가도 남는다 — "늦게라도 됐다"를 outcome 이 못 말해준다.
    task.missedAt && `미실행 판정 ${clock(task.missedAt)}`,
    task.fulfilledAt && `완료 ${clock(task.fulfilledAt)}`,
    task.skipReason && `제외 사유 ${task.skipReason}`,
    // 시도 행이 아예 없는 실패(FAILED_TO_START)의 유일한 설명이다.
    task.outcomeReason && `귀결 사유 ${task.outcomeReason}`,
  ].filter(Boolean);

  return (
    <tr>
      <td />
      <td colSpan={6} className="t-xs" style={{ color: 'var(--fg-3)', paddingTop: 0 }}>
        {facts.length > 0 && <span style={{ display: 'block' }}>{facts.join(' · ')}</span>}
        {task.attempts.map((a, i) => (
          <AttemptLine key={a.ecsTaskArn ?? i} attempt={a} index={i} />
        ))}
      </td>
    </tr>
  );
}

function TaskRow({ task, runKey }: { task: TaskStatus; runKey: string | undefined }) {
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
          {/* 시도가 2회 이상이면 상세 줄을 안 읽어도 재시도가 있었다는 것이 보여야 한다 */}
          {task.attempts.length > 1 && (
            <StatusBadge tone="neutral">{`시도 ${task.attempts.length}회`}</StatusBadge>
          )}
        </span>
        {task.completeness && (
          <span
            className="t-xs num"
            style={{ display: 'block', color: 'var(--fg-3)', marginTop: 4 }}
          >
            {`ETF 대조 · 기대 ${count(task.completeness.expected)} · 수집 ${count(task.completeness.received)} · 누락 ${count(task.completeness.missing)}`}
            {/* 누락이 있으면 근거 목록(어떤 ETF·어떤 분석)으로 내려간다 — 집계만 있고 목록
             * 없는 숫자를 만들지 않는다(ALPHA-692). 결손 영향 화면이 그 목록의 소유자다. */}
            {(task.completeness.missing ?? 0) > 0 && runKey && (
              <>
                {' · '}
                <Link to={`/impact/holdings?runKey=${encodeURIComponent(runKey)}`}>
                  누락 영향 보기
                </Link>
              </>
            )}
          </span>
        )}
      </td>
      <td className="num">{finishedAt(task.lastFinishedAt)}</td>
      <td className="col-num num">{count(task.recordsOut)}</td>
      <td className="col-num num">{count(task.failedRecords)}</td>
    </tr>
  );
}

/** 서버가 "그런 런 없음"으로 낸 404 인가(AdminErrorStatus.RUN_NOT_FOUND). 라우팅 404 와 가른다. */
function isRunNotFound(error: ApiError) {
  return (
    typeof error.body === 'object' &&
    error.body !== null &&
    (error.body as { code?: unknown }).code === 'ADMN4041'
  );
}

const ISSUE_SCOPE_LABEL: Record<string, string> = { run: '런', task: '작업', slot: '슬롯' };

/* 원장이 이미 판정해 저장해 둔 불일치다. 화면에 없으면 운영자에게는 없는 사실이라, 지금까지
 * 콘솔은 이 표를 한 번도 그리지 않았다(dev 의 거짓 LEDGER_GAP 17건이 그렇게 묻혀 있었다).
 * 여기서 이슈를 새로 판정하거나 심각도를 매기지 않는다 — 다섯 번째 어휘를 만들지 않는다. */
function IssuesCard({ issues }: { issues: ReconciliationIssue[] }) {
  return (
    <div className="card">
      <div className="card-head">
        <span className="t-label">대조 이슈</span>
        <span className="t-xs num" style={{ color: 'var(--fg-3)' }}>
          {`열림 ${issues.filter((i) => i.status === 'OPEN').length} / 전체 ${issues.length}`}
        </span>
      </div>
      <table className="table">
        <thead>
          <tr>
            <th>유형</th>
            <th>범위</th>
            <th>대상</th>
            <th>상태</th>
            <th className="col-num">발생</th>
            {/* 최초 관측이 없으면 발생 횟수만으로는 "언제부터 이러는지"를 알 수 없다 */}
            <th>최초 관측</th>
            <th>최근 관측</th>
          </tr>
        </thead>
        <tbody>
          {issues.map((issue) => (
            <tr key={`${issue.issueType}-${issue.scope}-${issue.taskKey}-${issue.firstSeenAt}`}>
              <td className="font-semibold">{issue.issueType}</td>
              <td className="col-muted">
                {issue.scope === null ? '—' : (ISSUE_SCOPE_LABEL[issue.scope] ?? issue.scope)}
              </td>
              <td className="col-muted">{issue.taskKey ?? '—'}</td>
              <td>
                <span style={{ display: 'inline-flex', gap: 6, flexWrap: 'wrap' }}>
                  <StatusBadge tone={issue.status === 'OPEN' ? 'blocked' : 'neutral'}>
                    {issue.status === 'OPEN' ? '열림' : '해결됨'}
                  </StatusBadge>
                  {/* 자동 복구인지 운영자 조치인지 — 해결 사유가 없으면 둘이 같아 보인다 */}
                  {issue.resolutionReason && (
                    <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
                      {issue.resolutionReason}
                    </span>
                  )}
                </span>
              </td>
              <td className="col-num num">{issue.occurrenceCount.toLocaleString('ko-KR')}</td>
              <td className="num">{finishedAt(issue.firstSeenAt)}</td>
              <td className="num">{finishedAt(issue.lastSeenAt)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function SourcesPage() {
  /* 런 주소지정 — 파라미터가 없으면 지금까지처럼 최신 런이다. 새 라우트를 만들지 않는 이유:
   * 이 페이지가 이미 "실행 1건 상세"라, 드릴다운에 필요한 건 새 화면이 아니라 주소뿐이다. */
  const [searchParams] = useSearchParams();
  /* 공백만 있는 값은 지목이 아니다 — `?runKey=` 를 그대로 보내면 "지정한 실행"이라 표시해 놓고
   * 실제로는 서버가 404 를 내거나(엄한 쪽) 최신 런을 준다(관대한 쪽). 둘 다 화면 문구와 어긋난다. */
  const runKey = searchParams.get('runKey')?.trim() || undefined;
  /* 격자 셀이 지목한 작업 — 있으면 그 작업 하나만 보여준다(셀을 누른 목적이 그 작업의 상세다).
   * 원장에 없는 taskKey 면 전체 목록으로 폴백한다 — 지목 실패를 에러로 만들면 격자 쪽
   * 오타가 화면 전체를 죽인다. */
  const focusTask = searchParams.get('task')?.trim() || undefined;
  const { data: report, isPending, isError, error } = useSourceReport(runKey);

  if (isError) {
    /* 없는 런과 고장 난 서버는 다른 사실이다 — 404 를 일반 에러로 뭉개면 운영자가 오타를
     * 장애로 읽는다(서버가 빈 리포트 대신 404 를 내는 이유와 같은 이유).
     * 반대로 **모든 404 를 "없는 런"이라 부르면 반대 방향으로 틀린다** — 프록시 오설정·배포
     * 불일치로 엔드포인트 자체가 404 면 실제 장애가 운영자 오타로 숨는다. 그래서 런을 지목한
     * 요청이면서 서버가 그 코드(ADMN4041)를 낸 경우로 한정한다. */
    if (runKey && error instanceof ApiError && error.status === 404 && isRunNotFound(error)) {
      return (
        <div className="card card-pad t-xs" style={{ color: 'var(--fg-3)' }}>
          {`지정한 실행(${runKey})을 찾을 수 없습니다. 런 키를 확인해 주세요.`}
        </div>
      );
    }
    return <LoadError error={error} />;
  }
  if (isPending) return <PageSkeleton rows={6} />;

  const run = report.run;
  const focusedExists = focusTask !== undefined && report.tasks.some((t) => t.taskKey === focusTask);
  const visibleTasks = focusedExists
    ? report.tasks.filter((t) => t.taskKey === focusTask)
    : report.tasks;
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
          <span className="t-label">
            데이터 소스 수집 상태
            {/* 지목해서 보는 중이면 그 사실을 밝힌다 — 옛 런을 최신 상태로 오독하면 안 된다 */}
            {runKey && <span className="t-xs">{' · 지정한 실행'}</span>}
          </span>
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
            {focusedExists && (
              <p className="t-xs m-0" style={{ color: 'var(--fg-3)' }}>
                {'격자에서 지목한 작업만 표시 중 · '}
                <Link to={`/sources?runKey=${encodeURIComponent(runKey ?? '')}`}>
                  이 실행의 전체 작업 보기
                </Link>
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
                {visibleTasks.map((t) => (
                  <Fragment key={t.taskKey}>
                    <TaskRow task={t} runKey={run?.runKey} />
                    {/* 지목된 작업은 사유가 없어도 펼친다 — 셀을 누른 목적이 그 작업의
                        상세(시각·시도)이기 때문이다 */}
                    {(hasDetail(t) || focusedExists) && <TaskDetailRow task={t} />}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </>
        )}
      </div>
      {report.issues.length > 0 && <IssuesCard issues={report.issues} />}
      <p className="t-xs m-0" style={{ color: 'var(--fg-3)' }}>
        산출·유실이 “—”인 작업은 건수 신호를 남기지 않은 것입니다 — 0건 처리와 다릅니다.
      </p>
    </div>
  );
}
