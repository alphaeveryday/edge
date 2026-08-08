import { Fragment, useState } from 'react';
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
  SourceReport,
  TaskOutcome,
  TaskStatus,
} from '../domains/sources';
import { useMinuteStatus, useSourceReport } from '../domains/sources/hooks';
import { datasetKind, gapRuns, liveness, segments } from '../domains/sources/minuteView';
import { holdingsFlow } from '../domains/sources/holdingsFlow';
import { MOCK_MINUTE, MOCK_REPORT, mockReportForRun } from '../mock/preview';
import { useConsoleEvaluation } from './ops/shared';
import { incidentHref, incidentOfVid } from './ops/investigation';
import { EmptyRealNotice, MockChip, MockPreview } from './_shared/MockPreview';
import { InfoPopover } from './_shared/InfoPopover';
import { LoadError } from './_shared/LoadError';
import '../styles/ops.css';

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
            {/* 누락 상세 링크는 여기 걸지 않는다 — 결손 상세가 계산하는 값은 이 작업 하나의
             * 결과가 아니라 `기대 − 기준일 적재`(데이터셋의 최종 완전성)다. 그래서 아래
             * 데이터셋 흐름 블록에 조건부로 건다(HoldingsDatasetFlow). */}
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
const RECORDS_TIP =
  '산출 = 작업이 원장에 기록한 records_out 이다.\n' +
  '"—" 는 0 이 아니라 **계측 값이 기록되지 않았다**는 뜻이다 — 0건 처리와 구분한다(ALPHA-182 NULL 계약).';
const FAILED_TIP =
  '유실 = 작업이 원장에 기록한 failed_records 다. 스텝이 스스로 판정한 값이고 잡마다 단위가 다르다.\n' +
  '"—" 는 0 이 아니라 계측 값이 기록되지 않았다는 뜻이다.';

/* ══ ETF 구성종목 데이터셋 흐름 ══
 *
 * 수집 → 정제 → 적재 → **최종 완전성**까지를 한 문맥으로 놓고, 결손이 있을 때만 상세로 보낸다.
 * 결손 상세는 특정 상태의 전용 페이지가 아니라 **이 데이터셋 계약에 특화된 보고서**라서
 * 조건부 진입이다 — 상태마다 새 페이지를 만들지 않는다.
 */
const HOLDINGS_TIP = [
  '최종 완전성 = 기대(Planner snapshot) − 기준 거래일 현재 적재.',
  '수집 작업 하나의 결과가 아니라 데이터셋의 최종 상태다.',
  '',
  '판정에 쓰는 신호는 원장 값뿐이다 — 수집 완전성 · 적재 데이터 판정 · 적재 유실.',
  '어느 단계에서 탈락했는지는 근거가 없어 단정하지 않는다(그 분해는 S3 로그 소관).',
  '',
  '적재가 안 끝났으면 차집합이 전부 누락으로 보이므로 확정 결손으로 그리지 않는다.',
  '기대 목록이 없으면 "결손 없음"이 아니라 계산 불가다.',
].join('\n');

function HoldingsDatasetFlow({ report, runKey }: { report: SourceReport; runKey?: string }) {
  const flow = holdingsFlow(report.tasks);
  if (flow.state === 'absent') return null;
  const c = flow.completeness;

  return (
    <div className="card">
      <div className="card-head">
        <span className="t-label">ETF 구성종목 — 데이터셋 흐름</span>
        <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
          수집 → 정제 → 적재 → 최종 완전성
          <InfoPopover label="최종 완전성" title="ETF 구성종목 최종 완전성" text={HOLDINGS_TIP} />
        </span>
      </div>
      <div className="card-pad">
        <div className="ops-facts">
          {flow.steps.map((s) => (
            <div key={s.label} className="t-xs ops-fact">
              <span style={{ color: 'var(--fg-3)' }}>{s.label}</span>
              <span>
                {s.task.planStatus === 'SKIPPED'
                  ? '계획 제외'
                  : (OUTCOME[s.task.outcome ?? 'PENDING']?.label ?? s.task.outcome ?? '판정 없음')}
                {dataDefect(s.task.dataStatus) && ` · ${dataDefect(s.task.dataStatus)}`}
              </span>
            </div>
          ))}
          <div className="t-xs ops-fact">
            <span style={{ color: 'var(--fg-3)' }}>최종 상태</span>
            <span>
              {c && c.expected !== null
                ? `현재 적재 ${count(c.received)}/${count(c.expected)}${
                    (c.missing ?? 0) > 0 ? ` · 누락 ${c.missing}종` : ''
                  }`
                : '완전성 계산 불가 — 기대 목록 없음'}
            </span>
          </div>
        </div>

        <p className="t-xs m-0" style={{ color: 'var(--fg-3)', marginTop: 8 }}>
          {flow.basis}
        </p>

        {/* 결손이 있을 때만 상세로 보낸다. 판정 대기·계산 불가는 정상 링크처럼 보이지 않게
         * 비활성 문구로 남기고, 결손 없음이면 액션 자체를 만들지 않는다. */}
        {flow.state === 'missing' && runKey && (
          <p className="t-xs m-0" style={{ marginTop: 8 }}>
            <Link
              to={`/impact/holdings?runKey=${encodeURIComponent(runKey)}`}
              className="btn btn-sm"
            >
              {(c?.missing ?? 0) > 0 ? `누락 ETF ${c!.missing}종 보기 →` : '구성종목 결손 상세 →'}
            </Link>
          </p>
        )}
        {flow.state === 'pending' && (
          <p className="t-xs m-0" style={{ color: 'var(--fg-3)', marginTop: 8 }}>
            <b>판정 대기</b> — 적재가 귀결된 뒤에 누락 상세를 엽니다.
          </p>
        )}
        {flow.state === 'unknown' && (
          <p className="t-xs m-0" style={{ color: 'var(--fg-3)', marginTop: 8 }}>
            <b>완전성 계산 불가</b> — 기대 목록(expectation snapshot)이 없어 누락을 셀 수 없습니다.
          </p>
        )}
      </div>
    </div>
  );
}

const ISSUE_TIP = [
  '대조 이슈 = Reconciler 가 저장한 **예정과 실제의 불일치**다. 원천은 ops_reconciliation_issue 이고,',
  '화면이 지금 계산한 값이 아니라 원장에 이미 적혀 있는 판정이다.',
  '',
  '범위(scope)가 셋이라 섞으면 안 된다.',
  '  task — 그 작업 하나의 불일치',
  '  run  — 런 전체의 불일치',
  '  slot — 예정 슬롯의 불일치',
  '',
  '작업을 지목해 들어오면 **그 작업의 task 이슈 + 런·슬롯 이슈**만 본다 —',
  '다른 작업의 task 이슈를 같은 표에 섞으면 선택한 작업의 문제로 오독된다.',
].join('\n');

/**
 * 대조 이슈 — 작업을 지목해 들어왔으면 그 문맥의 이슈만 보여준다.
 * 런 전체 이슈는 지우지 않고 **따로 구분**한다(선택 작업의 문제로 읽히면 안 된다).
 */
function IssuesCard({ issues, focusTask }: { issues: ReconciliationIssue[]; focusTask?: string }) {
  const scoped = focusTask
    ? issues.filter((i) => i.scope !== 'task' || i.taskKey === focusTask)
    : issues;
  const hidden = issues.length - scoped.length;
  return (
    <div className="card">
      <div className="card-head">
        <span className="t-label">
          대조 이슈{focusTask && ' — 이 작업 문맥'}
        </span>
        <span className="t-xs num" style={{ color: 'var(--fg-3)' }}>
          {`열림 ${scoped.filter((i) => i.status === 'OPEN').length} / 전체 ${scoped.length}`}
        </span>
        <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
          예정과 실제의 불일치 · ops_reconciliation_issue
          <InfoPopover label="대조 이슈" title="대조 이슈" text={ISSUE_TIP} />
          {hidden > 0 && ` · 다른 작업의 task 이슈 ${hidden}건은 여기 섞지 않습니다`}
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

/** 실시간 수집 데이터셋 — 어휘 정본은 data_pipeline/minute/states.py */
const REALTIME_DATASETS = new Set(['price_minute', 'news_minute']);

/**
 * 조사 문맥 breadcrumb — **query 문자열을 그대로 찍지 않는다.** 사건은 평가 결과에서, 런·작업은
 * 원장 응답에서 실제로 찾아 표시하고, 못 찾으면 "원장에서 확인되지 않음"이라고 쓴다.
 */
function LedgerCrumb({
  incidentId,
  runKey,
  task,
  dataset,
  date,
  report,
}: {
  incidentId?: string;
  runKey?: string;
  task?: string;
  dataset?: string;
  date?: string;
  report?: SourceReport;
}) {
  const { incidents } = useConsoleEvaluation();
  /* 흡수된 위반의 vid 로 와도 그 사건을 찾는다 — 뿌리만 보면 문맥이 조용히 사라진다 */
  const incident = incidentId ? (incidentOfVid(incidents, incidentId)?.incident ?? undefined) : undefined;
  const runFound = report?.run?.runKey === runKey;
  const taskFound = task !== undefined && (report?.tasks.some((t) => t.taskKey === task) ?? false);
  const crumbs: React.ReactNode[] = [];

  if (incidentId) {
    crumbs.push(<Link key="list" to="/ops/incidents">문제·사건</Link>);
    crumbs.push(
      incident ? (
        <Link key="inc" to={incidentHref(incident.root)}>
          {incident.root.title}
        </Link>
      ) : (
        <span key="inc" title="이 식별자의 사건이 지금 평가 결과에 없다">
          사건 {incidentId} <b>(확인되지 않음)</b>
        </span>
      ),
    );
  }
  if (runKey) {
    crumbs.push(
      <span key="run" className="mono">
        {runKey}
        {!runFound && <b> (원장에서 확인되지 않음)</b>}
      </span>,
    );
  }
  if (task) {
    crumbs.push(
      <span key="task" className="mono">
        {task}
        {!taskFound && <b> (이 런에 없음)</b>}
      </span>,
    );
  }
  if (!runKey && dataset) {
    crumbs.push(<span key="ds" className="mono">{dataset}{date ? ` · ${date}` : ''}</span>);
  }
  crumbs.push(<span key="here" style={{ color: 'var(--fg-1)' }}>원장 근거</span>);

  return (
    <nav className="t-xs ops-crumb" aria-label="조사 경로">
      {crumbs.map((c, i) => (
        <Fragment key={i}>
          {i > 0 && <span aria-hidden="true">›</span>}
          {c}
        </Fragment>
      ))}
    </nav>
  );
}

/** 문맥 표 — 이 화면이 무엇으로 범위를 좁혔는지. 값이 있는 축만 낸다 */
function LedgerScope({ rows }: { rows: [string, string][] }) {
  return (
    <div className="card card-pad">
      <span className="t-label">원장 근거 · 조사 문맥</span>
      <div className="ops-facts" style={{ marginTop: 8 }}>
        {rows.map(([k, v]) => (
          <div key={k} className="t-xs ops-fact">
            <span style={{ color: 'var(--fg-3)' }}>{k}</span>
            <span className="mono" style={{ color: 'var(--fg-1)' }}>{v}</span>
          </div>
        ))}
      </div>
      <p className="t-xs m-0" style={{ color: 'var(--fg-3)', marginTop: 8 }}>
        위 문맥에 해당하는 원시 사실만 봅니다 — 이 화면은 원인을 문장으로 만들지 않고, 상위 화면의
        판정이 무엇을 근거로 했는지 증명합니다.
      </p>
    </div>
  );
}

/** 문맥 없이 열었을 때 — 전체 원장을 덤프하지 않는다 */
function LedgerNoContext() {
  const [key, setKey] = useState('');
  return (
    <div className="flex flex-col gap-4">
      <div className="card card-pad">
        <p className="t-sm m-0" style={{ fontWeight: 600 }}>조사 문맥이 없습니다</p>
        <p className="t-xs m-0" style={{ color: 'var(--fg-3)', marginTop: 4 }}>
          원장 근거는 <b>무엇을 증명하려는가</b>가 정해졌을 때 여는 화면입니다. 문맥 없이 전체 원장을
          펼치면 어느 판정의 근거인지 알 수 없어, 최신 실행을 임의로 골라 보여주지 않습니다.
        </p>
        <p className="t-xs m-0" style={{ color: 'var(--fg-3)', marginTop: 6 }}>
          <Link to="/ops/incidents">문제·사건</Link> · <Link to="/ops/runs">실행</Link> ·{' '}
          <Link to="/minute">현재 실행</Link> · <Link to="/grid">실행 이력</Link> 에서 조사하다가 원장
          근거를 열어 주세요.
        </p>
      </div>
      <div className="card card-pad">
        <span className="t-label">런 키로 직접 열기</span>
        <form
          style={{ display: 'flex', gap: 8, marginTop: 8, flexWrap: 'wrap' }}
          onSubmit={(e) => {
            e.preventDefault();
            const v = key.trim();
            if (v) window.location.assign(`/sources?runKey=${encodeURIComponent(v)}`);
          }}
        >
          <input
            className="t-xs mono"
            aria-label="런 키"
            placeholder="etf-daily:2026-08-03T15:40"
            value={key}
            onChange={(e) => setKey(e.target.value)}
            style={{ border: '1px solid var(--border-strong)', borderRadius: 4, padding: '4px 8px', minWidth: 280 }}
          />
          <button type="submit" className="btn btn-sm" disabled={!key.trim()}>
            원장 근거 열기
          </button>
        </form>
      </div>
    </div>
  );
}

/**
 * 실시간 세션의 원장 근거 — ops 원장이 아니라 `minute_ingestion_*` 이 답하는 문맥이다.
 * 같은 화면·같은 문맥 규약을 쓰되 조회하는 원장이 다르다는 사실을 그대로 밝힌다.
 *
 * `sourceGroup` 이 문맥에 있으면 그걸로 좁힌다. 세션 identity 는 `(dataset, sourceGroup, date)`
 * 라 데이터셋만으로 고르면 벤더가 다른 세션 행(sessionId·phase·lease)이 아무 경고 없이 선다 —
 * 사건은 벤더로 갈렸는데 근거는 남의 것을 보여주는 셈이다.
 *
 * ⚠️ **목 폴백 판정은 데이터셋 축으로만 한다.** 벤더까지 넣어 `real` 을 재면, 실 응답에
 * 그 벤더가 없을 때 목으로 떨어져 **목 세션의 sessionId·phase·lease** 가 서거나 "세션이
 * 계획되지 않았다는 사실입니다"라는 거짓 단언이 난다. 좁힘은 view 를 고른 **뒤에** 한다.
 */
function RealtimeLedger({
  dataset,
  date,
  sourceGroup,
}: {
  dataset: string;
  date?: string;
  sourceGroup?: string;
}) {
  const { data, isPending, isError, error } = useMinuteStatus(date, true);
  if (isError) return <LoadError error={error} />;
  if (isPending) return <PageSkeleton rows={4} />;

  const real = data.sessions.some((s) => s.dataset === dataset);
  const view = real ? data : MOCK_MINUTE;
  const ofDataset = view.sessions.filter((s) => s.dataset === dataset);
  /* 벤더가 문맥에 없는데 후보가 둘 이상이면 **고르지 않는다.** 예전엔 조용히 `[0]` 을 집어,
   * 손으로 친 주소나 벤더를 빠뜨린 링크가 남의 세션 행(sessionId·phase·lease)을 근거처럼
   * 세웠다. 생산자마다 주석을 다는 것보다 소비자 한 곳에서 막는 게 짧다. */
  const ambiguous = !sourceGroup && ofDataset.length > 1;
  const session = sourceGroup
    ? ofDataset.find((s) => s.sourceGroup === sourceGroup)
    : ambiguous
      ? undefined
      : ofDataset[0];
  const kindLabel = datasetKind(dataset) === 'news' ? 'poll' : '창';

  if (!session) {
    return (
      <div className="card card-pad">
        <p className="t-sm m-0">
          {ambiguous ? (
            <>어느 <span className="mono">{dataset}</span> 세션인지 문맥이 없습니다.</>
          ) : (
            <>이 날짜에 <span className="mono">{dataset}</span> 세션 행이 없습니다.</>
          )}
        </p>
        {ambiguous && (
          <p className="t-xs m-0" style={{ color: 'var(--fg-3)', marginTop: 4 }}>
            이 날짜에 세션이 {ofDataset.length}개입니다 — 아무거나 골라 근거로 세우지 않습니다.
            벤더를 고르세요:{' '}
            {ofDataset.map((s, i) => (
              <span key={s.sourceGroup}>
                {i > 0 && ' · '}
                <Link
                  to={`/sources?dataset=${encodeURIComponent(dataset)}${date ? `&date=${encodeURIComponent(date)}` : ''}&sourceGroup=${encodeURIComponent(s.sourceGroup)}`}
                  className="mono"
                >
                  {s.sourceGroup}
                </Link>
              </span>
            ))}
          </p>
        )}
        {/* 부재 문장은 **고를 수 없는 게 아니라 없는** 경우에만 쓴다(위 갈래와 배타다) */}
        {!ambiguous && (
          <p className="t-xs m-0" style={{ color: 'var(--fg-3)', marginTop: 4 }}>
            {/* ⚠️ **부재는 `real` 로만 가른다.** 목 폴백일 때 `ofDataset` 은 목의 행이라, 그걸로
                 "이 데이터셋의 세션은 있다"를 말하면 실 응답엔 없는 세션의 존재를 단언하게 된다.
                 `ofDataset.length > 0` 를 같이 묻지 않는 이유: `real` 이면 `view === data` 라
                 그 필터가 반드시 하나 이상을 낸다 — 둘을 묶으면 죽은 항이 조건처럼 보인다. */}
            {real
              ? /* 데이터셋 행은 있는데 그 벤더가 없다 — "세션이 없다"와 다른 사실이다.
                   벤더를 안 밝히고 부재라 말하면 있는 세션을 없다고 단언하게 된다. */
                `이 데이터셋의 세션은 있지만 source_group=${sourceGroup} 인 행이 없습니다 — 다른 벤더의 세션으로 대체하지 않습니다.`
              : /* 어느 응답의 부재인지 밝힌다 — 이 화면의 나머지 카드는 지금 목 미리보기를 그리고
                   있어서, 밝히지 않으면 목이 말한 부재로 읽힌다. */
                '실 응답에 이 데이터셋의 세션 행이 없습니다 — 계획되지 않았다는 뜻입니다(비거래일 · 미가동 · 레인 미편입). 다른 날짜나 다른 데이터셋의 세션으로 대체하지 않습니다.'}
          </p>
        )}
      </div>
    );
  }

  const live = liveness(session);
  const runs = gapRuns(session.gaps);
  return (
    <div className="flex flex-col gap-4">
      <div className="card">
        <div className="card-head">
          <span className="t-label">세션 행 (minute_ingestion_session) {!real && <MockChip />}</span>
          <StatusBadge tone={live.tone}>{live.label}</StatusBadge>
          <span className="t-xs mono" style={{ color: 'var(--fg-3)' }}>{session.sessionId}</span>
        </div>
        <div className="card-pad">
          <div className="ops-facts">
            {(
              [
                ['dataset / source_group', `${session.dataset} / ${session.sourceGroup}`],
                ['phase', session.phase],
                ['universe_version', session.universeVersion],
                ['expected_window_count', String(session.expectedWindowCount)],
                ['processed_through', session.processedThrough ?? '—'],
                ['contiguous_complete_through', session.contiguousCompleteThrough ?? '—'],
                ['heartbeat_at', session.heartbeatAt ?? '—'],
                ['lease_expires_at', session.leaseExpiresAt ?? '—'],
                ['lease_expired (서버 판정)', String(session.leaseExpired)],
              ] as [string, string][]
            ).map(([k, v]) => (
              <div key={k} className="t-xs ops-fact">
                <span style={{ color: 'var(--fg-3)' }}>{k}</span>
                <span className="mono" style={{ color: 'var(--fg-1)' }}>{v}</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="card">
        <div className="card-head">
          <span className="t-label">{kindLabel} 상태 집계 (minute_ingestion_window)</span>
          <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
            서버가 센 값 그대로입니다 — 화면이 다시 계산하지 않습니다
          </span>
        </div>
        <table className="table">
          <thead>
            <tr>
              <th>구분</th>
              <th className="num">{kindLabel}</th>
              <th>뜻</th>
            </tr>
          </thead>
          <tbody>
            {segments(session).map((seg) => (
              <tr key={seg.key}>
                <td>{seg.label}</td>
                <td className="num">{seg.count}</td>
                <td className="col-muted t-xs">{seg.meaning}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="card">
        <div className="card-head">
          <span className="t-label">결손 · 무증거 {kindLabel} 행 {session.gaps.length}개</span>
          <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
            정상 {kindLabel}의 분별 행은 이 응답에 포함되지 않습니다
          </span>
        </div>
        {runs.length === 0 ? (
          <div className="card-pad">
            <p className="t-xs m-0" style={{ color: 'var(--fg-3)' }}>
              결손·무증거 행이 없습니다.
            </p>
          </div>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>window_start ~ window_end (KST)</th>
                <th className="num">{kindLabel}</th>
                <th>data_status</th>
                <th>무증거 파생</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => (
                <tr key={`${r.from}-${r.dataStatus}-${r.noEvidence}`}>
                  <td className="mono t-xs">{r.from} ~ {r.to}</td>
                  <td className="num">{r.count}</td>
                  <td className="mono">{r.dataStatus}</td>
                  <td>
                    {r.noEvidence ? (
                      <StatusBadge tone="blocked">true (기한 경과 · 결과 없음)</StatusBadge>
                    ) : (
                      <span className="col-muted">false</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <div className="card-pad" style={{ paddingTop: 0 }}>
          <p className="t-xs m-0" style={{ color: 'var(--fg-3)' }}>
            무증거는 <b>서버(DB 시계) 판정</b>입니다 — 기한(window_end)이 지난 DUE, 또는 유효한 lease가
            없는 CLAIMED. 실행 로그(CloudWatch)는 별개 축이며 이 응답에 조회 경로가 없습니다.
          </p>
        </div>
      </div>

      <div className="card">
        <div className="card-head">
          <span className="t-label">job 집계</span>
          <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
            {datasetKind(dataset) === 'news'
              ? '뉴스 추출 job 은 세션 연결 컬럼이 없어 날짜 축 집계입니다'
              : 'price_window_job — 세션 축'}
          </span>
        </div>
        <div className="card-pad">
          <p className="t-xs mono m-0">
            {JSON.stringify(
              datasetKind(dataset) === 'news' ? view.newsJobs : session.priceJobs,
            )}
          </p>
        </div>
      </div>
    </div>
  );
}

export function SourcesPage() {
  /* 원장 근거는 **문맥으로 범위를 좁혀** 여는 화면이다. 새 라우트를 만들지 않는 이유는 예전과
   * 같다 — 이 페이지가 이미 "실행 1건의 원장"이라 필요한 건 새 화면이 아니라 주소다. */
  const [searchParams] = useSearchParams();
  /* 공백만 있는 값은 지목이 아니다 — `?runKey=` 를 그대로 보내면 "지정한 실행"이라 표시해 놓고
   * 실제로는 서버가 404 를 내거나(엄한 쪽) 최신 런을 준다(관대한 쪽). 둘 다 화면 문구와 어긋난다. */
  const runKey = searchParams.get('runKey')?.trim() || undefined;
  /* 목 격자에서 온 주소는 같은 픽스처를 읽는다. 라이브 원장에 목 runKey 를 보내 404 를 만드는
   * 것은 미리보기 흐름이 아니며, preview 표식은 새로고침해도 남아야 하므로 URL 로 운반한다. */
  const mockPreview = searchParams.get('preview') === 'mock';
  /* 격자 셀이 지목한 작업 — 있으면 그 작업 하나만 보여준다(셀을 누른 목적이 그 작업의 상세다).
   * 원장에 없는 taskKey 면 전체 목록으로 폴백한다 — 지목 실패를 에러로 만들면 격자 쪽
   * 오타가 화면 전체를 죽인다. */
  const focusTask = searchParams.get('task')?.trim() || undefined;
  const incidentId = searchParams.get('incident')?.trim() || undefined;
  const dataset = searchParams.get('dataset')?.trim() || undefined;
  const date = searchParams.get('date')?.trim() || undefined;
  const sourceGroup = searchParams.get('sourceGroup')?.trim() || undefined;

  const realtime = dataset !== undefined && REALTIME_DATASETS.has(dataset) && runKey === undefined;
  /* 문맥이 하나도 없으면 조회 자체를 하지 않는다 — 전체 덤프를 만들지 않기 위해서다 */
  const hasContext = Boolean(runKey || dataset || incidentId);
  const { data: report, isPending, isError, error } = useSourceReport(
    runKey,
    !mockPreview && hasContext && !realtime,
  );

  if (!hasContext) return <LedgerNoContext />;

  const scope: [string, string][] = [
    ...(incidentId ? ([['사건', incidentId]] as [string, string][]) : []),
    ...(runKey ? ([['실행(run_key)', runKey]] as [string, string][]) : []),
    ...(focusTask ? ([['작업(task_key)', focusTask]] as [string, string][]) : []),
    ...(dataset ? ([['데이터셋', dataset]] as [string, string][]) : []),
    ...(date ? ([['세션 날짜', date]] as [string, string][]) : []),
    ...(sourceGroup ? ([['벤더(source_group)', sourceGroup]] as [string, string][]) : []),
  ];

  /* 실시간 문맥 — ops 원장이 아니라 minute 원장이 답한다 */
  if (realtime) {
    return (
      <div className="flex flex-col gap-4">
        <LedgerCrumb incidentId={incidentId} dataset={dataset} date={date} />
        <LedgerScope rows={[...scope, ['원장', 'minute_ingestion_session · minute_ingestion_window']]} />
        <RealtimeLedger dataset={dataset!} date={date} sourceGroup={sourceGroup} />
      </div>
    );
  }

  /* 런 문맥이 없는 데이터셋 사건 — 원장을 런·작업으로 좁힐 식별자가 없다. 최신 런으로 대신하지 않는다 */
  if (!runKey) {
    return (
      <div className="flex flex-col gap-4">
        <LedgerCrumb incidentId={incidentId} dataset={dataset} date={date} />
        <LedgerScope rows={scope} />
        <div className="card card-pad">
          <p className="t-sm m-0">이 문맥으로는 원장을 실행·작업까지 좁힐 수 없습니다.</p>
          <p className="t-xs m-0" style={{ color: 'var(--fg-3)', marginTop: 4 }}>
            데이터셋 축만 주어졌고 런 키가 없습니다 — 최신 실행을 임의로 골라 이 사건의 근거처럼
            보여주지 않습니다. 데이터셋 계약·신선도의 근거는{' '}
            <Link to={`/ops/datasets?focus=ds-${encodeURIComponent(dataset ?? '')}`}>데이터 화면</Link>이,
            실행 축은 <Link to="/ops/runs">실행</Link>이 답합니다.
          </p>
        </div>
      </div>
    );
  }

  if (mockPreview) {
    const previewReport = mockReportForRun(runKey);
    if (previewReport === null) {
      return (
        <div className="card card-pad t-xs" style={{ color: 'var(--fg-3)' }}>
          지정한 목 실행을 찾을 수 없습니다. <Link to="/grid">실행 이력으로 돌아가기</Link>
        </div>
      );
    }
    return (
      <div className="flex flex-col gap-4">
        <LedgerCrumb incidentId={incidentId} runKey={runKey} task={focusTask} report={previewReport} />
        <LedgerScope rows={scope} />
        <MockPreview>
          <SourcesBody report={previewReport} runKey={runKey} focusTask={focusTask} mock />
        </MockPreview>
      </div>
    );
  }

  if (isError) {
    /* 없는 런과 고장 난 서버는 다른 사실이다 — 404 를 일반 에러로 뭉개면 운영자가 오타를
     * 장애로 읽는다(서버가 빈 리포트 대신 404 를 내는 이유와 같은 이유).
     * 반대로 **모든 404 를 "없는 런"이라 부르면 반대 방향으로 틀린다** — 프록시 오설정·배포
     * 불일치로 엔드포인트 자체가 404 면 실제 장애가 운영자 오타로 숨는다. 그래서 런을 지목한
     * 요청이면서 서버가 그 코드(ADMN4041)를 낸 경우로 한정한다. */
    if (error instanceof ApiError && error.status === 404 && isRunNotFound(error)) {
      return (
        <div className="flex flex-col gap-4">
          <LedgerCrumb incidentId={incidentId} runKey={runKey} task={focusTask} />
          <div className="card card-pad t-xs" style={{ color: 'var(--fg-3)' }}>
            {`지정한 실행(${runKey})을 찾을 수 없습니다. 런 키를 확인해 주세요 — 다른 실행으로 대체하지 않습니다.`}
          </div>
        </div>
      );
    }
    return <LoadError error={error} />;
  }
  if (isPending) return <PageSkeleton rows={6} />;

  /* 런이 없으면 표의 열(상태·산출·유실·완전성·시도)이 무엇을 말하는지 볼 수 없다 —
   * 사실을 먼저 밝히고 검수용 목을 분리해 붙인다 */
  if (report.run === null) {
    return (
      <div className="flex flex-col gap-4">
        <LedgerCrumb incidentId={incidentId} runKey={runKey} task={focusTask} report={report} />
        <EmptyRealNotice>아직 기록된 파이프라인 실행이 없습니다.</EmptyRealNotice>
        <MockPreview>
          <SourcesBody report={MOCK_REPORT} mock />
        </MockPreview>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <LedgerCrumb incidentId={incidentId} runKey={runKey} task={focusTask} report={report} />
      <LedgerScope rows={scope} />
      <SourcesBody report={report} runKey={runKey} focusTask={focusTask} />
    </div>
  );
}

function SourcesBody({
  report,
  runKey,
  focusTask,
  mock = false,
}: {
  report: SourceReport;
  runKey?: string;
  focusTask?: string;
  mock?: boolean;
}) {
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
            {/* 런을 지목해 내려온 경우 이 화면은 격자의 한 단계 아래다 — 계층을 문구로 밝힌다.
             * 지목 없이 들어오면(네비 진입) 최신 런의 수집 상태 화면 그대로다. */}
            {runKey ? '실행 이력 › 실행 원장 상세' : '데이터 소스 수집 상태'} {mock && <MockChip />}
          </span>
          {run && (
            <span className="t-xs num" style={{ color: 'var(--fg-3)' }}>
              {run.runKey}
              {run.tradingDate && ` · 거래일 ${run.tradingDate}`}
            </span>
          )}
          {runKey && (
            <Link to="/grid" className="t-xs" style={{ marginLeft: 'auto' }}>
              ← 실행 이력으로 돌아가기
            </Link>
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
                <Link
                  to={`/sources?${mock ? 'preview=mock&' : ''}runKey=${encodeURIComponent(runKey ?? '')}`}
                >
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
                  <th className="col-num">
                    산출 <InfoPopover label="산출" text={RECORDS_TIP} />
                  </th>
                  <th className="col-num">
                    유실 <InfoPopover label="유실" text={FAILED_TIP} />
                  </th>
                </tr>
              </thead>
              <tbody>
                {visibleTasks.map((t) => (
                  <Fragment key={t.taskKey}>
                    <TaskRow task={t} />
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
      <HoldingsDatasetFlow report={report} runKey={runKey} />

      {report.issues.length > 0 && <IssuesCard issues={report.issues} focusTask={focusTask} />}
      <p className="t-xs m-0" style={{ color: 'var(--fg-3)' }}>
        산출·유실이 “—”인 작업은 건수 신호를 남기지 않은 것입니다 — 0건 처리와 다릅니다.
      </p>
      <p className="t-xs m-0" style={{ color: 'var(--fg-3)' }}>
        이 화면은 <b>실행 원장 상세</b>입니다 — 시도(attempt) 전량과 대조 이슈가 여기 있습니다.
        여러 실행을 나란히 놓고 “언제부터 깨졌는지” 찾는 것은 <Link to="/grid">실행 이력</Link> 소관입니다.
      </p>
    </div>
  );
}
