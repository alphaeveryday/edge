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
import { Fragment, useEffect, useMemo, useState } from 'react';
import { Link, Navigate, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { StatusBadge } from 'ui-kit';
import type { BadgeTone } from 'ui-kit';
import { LANE_UNKNOWN, retryCap } from '../../rules/rules';
import type { RunFact, TaskFact } from '../../rules/types';
import { MockChip } from '../_shared/MockPreview';
import {
  Absent,
  AxisHeader,
  ConsoleGate,
  Info,
  fmt,
  kst,
  useConsoleEvaluation,
  useConsoleFactsQuery,
} from './shared';
import {
  ABSENCE_LABEL,
  ABSENCE_MEANING,
  awsFailureEvidence,
  awsObservation,
  isAwsTerminalFailure,
  ledgerObservation,
  reconcile,
} from './runObservation';
import type { Observation } from './runObservation';
import { incidentHref, incidentOfVid, ledgerHref, runHref } from './investigation';
import '../../styles/ops.css';

const KIND_LABEL: Record<string, string> = { scheduled: '정규', manual: '수동', backfill: '백필' };
const LANE_LABEL: Record<string, string> = { 'etf-daily': '시장 EOD', news: '뉴스' };


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

/**
 * 런 하나의 작업 — **원장이 준 것만**.
 *
 * 검수용 목(`MOCK_RUN_TASKS`)으로 메우던 자리다. 사실 축이 앱 번들 안 스냅샷이던 동안은 표가
 * 늘 비어 UI 를 평가할 수 없었지만, 이제 이 런은 **실 원장에서 왔다** — 그 런에 계획 행이
 * 없다는 것 자체가 사실이고(`no_run_row`·계획 결손), 거기에 목 작업을 채우면 원장이 하지
 * 않은 실행을 화면이 단정한다. 빈 경우의 문장은 아래 `RunTasks` 가 이미 갖고 있다.
 */
function tasksOfRun(tasks: TaskFact[], runId: string): TaskFact[] {
  return tasks.filter((t) => t.run_id === runId);
}

/** 슬롯 키에서 예정 시각만 — "etf-daily:2026-08-03T15:40" → "15:40" */
const slotTime = (runId: string) => runId.match(/T(\d{2}:\d{2})/)?.[1] ?? null;

/**
 * 관측 하나를 배지로 — 값이 있으면 한국어 라벨(원본 enum 은 title), 없으면 부재 사유다.
 * **다른 축의 값을 여기 끌어오지 않는다**(예전에는 원장 열에 AWS 값을 복사했다).
 */
function ObservationCell({ obs, at }: { obs: Observation; at: string }) {
  return (
    <>
      {obs.status ? (
        <StatusBadge tone={obs.status.tone}>
          <span title={`원본 enum: ${obs.status.raw}`}>{obs.status.label}</span>
        </StatusBadge>
      ) : (
        <span className="t-xs" style={{ color: 'var(--fg-4)' }} title={ABSENCE_MEANING[obs.absence!]}>
          {ABSENCE_LABEL[obs.absence!]}
        </span>
      )}
      {/* 관측 시각은 보조 정보 — 상태 옆에 작게 둔다 */}
      <span className="t-xs ops-obs-at" style={{ color: 'var(--fg-3)' }}>
        {obs.at ? `${at} ${kst(obs.at)}` : ''}
      </span>
    </>
  );
}

const AXIS_TIP = [
  'DB 원장 투영',
  '  파이프라인 운영 원장에 마지막으로 기록된 실행 상태.',
  '',
  'AWS 실행 상태',
  '  Step Functions 제어면에서 조회한 실행 상태.',
  '',
  '두 값은 별도 관측이다 — 다르면 어느 쪽으로도 덮어쓰지 않고 대조 열에 불일치로 남긴다.',
  '',
  '대조',
  '  두 관측값의 일치 여부. 불일치는 투영 지연이나 원장 기록 누락일 수 있으며,',
  '  이 화면에서 원인을 추정하지 않는다.',
  '',
  '판정 마감',
  '  계획된 실행의 완료 판정 기한이다. **AWS 종료 시각과 다르다.**',
  '',
  '부재는 한 가지가 아니다.',
  `  런 행 없음 — ${ABSENCE_MEANING.runRowMissing}`,
  `  원장 상태 미기록 — ${ABSENCE_MEANING.ledgerStatusMissing}`,
  `  AWS 관측 없음 — ${ABSENCE_MEANING.awsNotObserved}`,
].join('\n');

/**
 * 최근 런 **목록**. 런을 고르면 그 런의 상세 페이지로 간다.
 *
 * 예전에는 이 화면이 목록 + 상세를 한 장에 들고 있었다. 세 가지가 걸렸다:
 *   · 선택이 `runs[0]` 로 폴백해서 목록만 보러 와도 첫 런의 상세가 강제로 딸려 왔다.
 *   · 선택이 `replace: true` 라 뒤로가기가 목록이 아니라 화면 밖으로 나갔다.
 *   · 진입의 다수(격자·현재 실행·구성종목 결손·사건)가 이미 특정 런 직행이라 목록이 소음이었다.
 *     ⚠️ 이 셋째 근거는 지금 사건 하나만 남았다 — 나머지 셋은 임의 날짜를 다루는 실 API
 *     화면이라 링크를 끊었다(`RUN_DETAIL_UNAVAILABLE`). 분리 자체는 앞의 두 근거로 여전히 선다.
 *
 * 옛 주소 `/ops/runs?run_id=X` 는 여기서 상세로 넘긴다 — 사건이 남긴 주소가 끊기면 안 된다.
 */
export function RunAxisPage() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const q = useConsoleFactsQuery();

  const legacyRunId = params.get('run_id');
  if (legacyRunId) {
    const rest = new URLSearchParams(params);
    rest.delete('run_id');
    return <Navigate to={runHref(legacyRunId, Object.fromEntries(rest))} replace />;
  }
  if (!q.ready) return <ConsoleGate q={q} />;
  const runs = q.facts.runs;

  return (
    <div className="flex flex-col gap-4">
      <AxisHeader q={q} question="오늘 어떤 런이 돌았고, 그 런의 작업은 귀결됐는가?" />
      {runs.length === 0 ? (
        <div className="card card-pad">
          <p className="t-sm m-0" style={{ fontWeight: 600 }}>이 조회일에 런이 없습니다</p>
          <p className="t-xs m-0" style={{ color: 'var(--fg-3)', marginTop: 4 }}>
            {/* ⚠️ 세 가지를 인용하면 안 된다.
                ① R01 — `runs` 안의 계획 슬롯 행(`planned && no_run_row`)만 위반으로 만들어 이
                   배열이 비면 **반드시 0건**이다.
                ② "어떤 규칙도" — 런 축이 비어도 `outputs`·`boundary` 를 읽는 R13·R14 는 돈다.
                ③ "런 축 규칙은 전부 판정했다" — `canRun` 이 **없는** 규칙(R01·R02·R04…)만
                   빈 배열을 순회해 `evaluated: true, violations: 0` 이 된다. `canRun` 을 가진
                   R03(제어면·원장 대조)은 `f.runs.some(...)` 이 거짓이라 **`못 돎`** 이다.
                   한쪽으로 뭉뚱그리면 "AWS 대조를 못 했다"는 사실이 초록 아래로 숨는다. */}
            원장에 이 날짜의 실행 행도, 미기동 계획 슬롯도 없습니다 — <b>계획 결손조차 기록되지
            않았다</b>는 뜻입니다. 런 축 규칙 대부분은 이 빈 목록을 보고{' '}
            <b>조건에 걸린 것 없음</b>으로 판정하지만(못 본 것이 아닙니다), 관측할 런이 있어야
            도는 규칙(제어면·원장 대조)은 <b>판정 자체를 못 합니다</b> — 어느 쪽인지는{' '}
            <Link to="/ops/incidents">문제·사건</Link>의 규칙 목록이 줄마다 말합니다. 조회일은
            원장이 아는 가장 최근 날이라 거래일이라는 보장이 없고, 다른 날을 보려면 그 날짜로
            조회해야 합니다.
          </p>
        </div>
      ) : (
        <RunList runs={runs} onSelect={(id) => navigate(runHref(id))} />
      )}
    </div>
  );
}

/** 런 하나의 상세 — 목록의 선택 상태가 아니라 자기 주소를 갖는 페이지다 */
export function RunDetailPage() {
  const { runId = '' } = useParams();
  const [params] = useSearchParams();

  /* 사건 카드의 드릴다운이 실은 작업을 지목한다(?focus=task-…) — 그 지목은 상세 안에서
   * 해당 작업을 펼치는 데 쓴다. 런 자체는 주소(:runId)가 정하므로 여기서 다시 고르지 않는다. */
  const focus = params.get('focus');

  /* 사건에서 왔으면 그 사건을 함께 띄운다 — 조사 문맥이 없으면 이 화면이 왜 이 런을 열었는지
   * 알 수 없고, 돌아갈 곳도 사라진다. */
  const fromIncident = params.get('fromIncident');
  const ev = useConsoleEvaluation();
  if (!ev.ready) return <ConsoleGate q={ev} />;
  const runs = ev.facts.runs;
  const incident = fromIncident
    ? /* 흡수된 위반의 vid 로 와도 찾는다 — 뿌리만 보면 breadcrumb 이 죽은 텍스트가 된다 */
      (incidentOfVid(ev.incidents, fromIncident)?.incident ?? null)
    : null;

  /**
   * ⚠️ **지목한 런을 못 찾으면 다른 런으로 조용히 갈아타지 않는다.**
   * 예전에는 첫 런을 골라 주소까지 고쳐 썼는데, 그러면 사건이 준 run_id 가 오타이거나 사라졌을
   * 때 **무관한 최근 실행이 그 사건의 실행처럼** 보인다(관대해지는 쪽 오류). 못 찾았다는 사실을
   * 그대로 낸다.
   */
  const selected = runs.find((r) => r.id === runId);

  return (
    <div className="flex flex-col gap-4">
      <AxisHeader q={ev} question="이 실행의 작업은 귀결됐는가?" />

      <nav className="t-xs ops-crumb" aria-label="조사 경로">
        {incident ? (
          <>
            <Link to="/ops/incidents">문제·사건</Link>
            <span aria-hidden="true">›</span>
            <Link to={incidentHref(incident.root)}>{incident.root.title}</Link>
          </>
        ) : (
          <Link to="/ops/runs">최근 런</Link>
        )}
        <span aria-hidden="true">›</span>
        <span style={{ color: 'var(--fg-1)' }}>실행 {runId}</span>
      </nav>

      {selected ? (
        <RunTasks tasks={ev.facts.tasks} run={selected} focus={focus} />
      ) : (
        /* 지목한 run_id 를 못 찾았다 — 다른 런을 대신 열지 않는다.
         *
         * ⚠️ "그 실행이 없었다"고 말하지 않는다. 이 응답의 창은 **하루**(조회일)라, 다른 날의
         * 런이나 조회 창 밖의 런은 여기 없는 것이 정상이다. 북마크·이전에 공유된 딥링크는
         * 그 창 밖에서도 여기 도달하므로, 부재의 사유를 목적지가 말한다. */
        <div className="card card-pad">
          <p className="t-sm m-0" style={{ fontWeight: 600 }}>이 조회 결과에 그 실행이 없습니다</p>
          <p className="t-xs m-0" style={{ color: 'var(--fg-3)', marginTop: 4 }}>
            지금 열려 있는 조회일 <b>{ev.facts.meta.today}</b> 의 런 {runs.length}건 안에{' '}
            <code>{runId}</code> 가 없습니다 — 그 실행이 없었다는 뜻이 아니라 <b>이 창 밖</b>
            이라는 뜻입니다(다른 날짜의 런일 수 있습니다). 다른 최근 실행을 대신 선택하지
            않습니다 — 무관한 런이 이 사건의 실행처럼 보이기 때문입니다.{' '}
            {incident ? (
              <Link to={incidentHref(incident.root)}>사건으로 돌아가기</Link>
            ) : (
              <Link to="/ops/runs">최근 런 전체 보기</Link>
            )}
          </p>
        </div>
      )}
    </div>
  );
}

/* ══ 마스터 — 최근 런 ══ */
function RunList({ runs, onSelect }: { runs: RunFact[]; onSelect: (id: string) => void }) {
  return (
    <div className="card">
      <div className="card-head">
        <span className="t-label">최근 런</span>
        <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
          런을 고르면 그 런의 상세로 갑니다 · DB 원장 투영과 AWS 실행 상태는 별도 관측이라
          어긋나면 대조 열에 남깁니다
          <Info tip={AXIS_TIP} label="관측 축·대조·판정 마감" />
        </span>
      </div>
      <table className="table">
        <thead>
          <tr>
            <th>런</th>
            <th>파이프라인</th>
            <th>실행 방식</th>
            <th>거래일</th>
            <th>예정</th>
            <th>DB 원장 투영</th>
            <th>AWS 실행 상태</th>
            <th>대조</th>
            <th>판정 마감</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((r) => {
            const led = ledgerObservation(r);
            const aws = awsObservation(r);
            const rec = reconcile(r);
            return (
              <tr
                key={r.id}
                id={'run-' + r.id}
                /* 행 전체가 상세로 가는 이동이다 — 선택 상태가 아니라서 표시할 것이 없다 */
                className="ops-run-row"
                onClick={() => onSelect(r.id)}
              >
                <td>
                  {/* 진짜 버튼이라 Tab 으로 닿고 Enter·Space 가 그대로 동작한다 */}
                  <button
                    type="button"
                    className="mono ops-run-btn"
                    onClick={(e) => {
                      e.stopPropagation();
                      onSelect(r.id);
                    }}
                  >
                    {r.id}
                  </button>
                  {r.mock && <MockChip />}
                </td>
                <td className="col-muted">
                  {/* 레인 부재는 계측 없음이 아니라 **슬롯 키를 못 읽었다**는 뜻이다 —
                      표기는 규칙 층과 한 벌(`LANE_UNKNOWN`)이라 문장·셀이 갈리지 않는다. */}
                  {r.lane ? (LANE_LABEL[r.lane] ?? r.lane) : LANE_UNKNOWN}
                </td>
                {/* `kind` 는 계측이 없어 실 응답에서 빠진다 — 부재를 '정규'로 그리면 없는
                    사실을 단정한다. 부재 4구분의 '계측 없음' 칸으로 낸다. */}
                <td>
                  {r.kind ? (
                    <span className="chip">{KIND_LABEL[r.kind] ?? r.kind}</span>
                  ) : (
                    <Absent kind="uninstrumented" />
                  )}
                </td>
                {/* null 을 그냥 그리면 **빈 칸**이 된다 — 부재가 값 없음이 아니라 "안 물었다"로 읽힌다 */}
                <td className="col-muted">
                  {r.trading_date ?? <Absent kind="none" />}
                </td>
                <td className="col-muted">{slotTime(r.id) ?? <Absent kind="none" />}</td>
                <td className="ops-obs">
                  <ObservationCell obs={led} at="갱신" />
                </td>
                <td className="ops-obs">
                  <ObservationCell obs={aws} at="종료" />
                </td>
                <td>
                  {/* 색만으로 갈리지 않게 텍스트 배지로 낸다 */}
                  <StatusBadge tone={rec.tone}>
                    <span title={rec.basis}>{rec.label}</span>
                  </StatusBadge>
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
          <code>run_id</code> 로 지목합니다. 실행 방식(kind)은 원장이 기록하지 않는 축이라 이 열은{' '}
          <b>계측 없음</b> 으로 설 수 있습니다 — 값이 없는 것이지 정규 런이라는 뜻이 아닙니다.
          {/* ⚠️ 이 문장은 같은 열의 셀이 그리는 부재 어휘와 **같아야** 한다. 예전에는 셀이
            * '계측 없음'을 그리는데 캡션은 "목값 · MOCK"이라 말해 한 열에 두 어휘가 섰다.
            * 순수 문구라 컴파일러도 테스트도 안 잡는다 — 셀을 고치면 여기도 같이 본다.
            * "스냅샷"을 한 화면에서 두 뜻으로 쓰지 않는다 — 상세 분기는 같은 단어를
            * "앱에 동봉된 정적 파일"로 쓴다. 여기서 말하려던 건 관측 시점이다. */}
          목값에 기댄 행에는 <b>MOCK</b> 이 따로 붙습니다 — 나머지 값은 운영 원장·AWS 제어면을 한
          시점에 떠 온 관측값입니다.
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
  '유실 = 작업이 원장에 기록한 ops.failed_records 다. 스텝이 스스로 판정한 값이며 skipped_* 를 더한 값이 아니고, 잡마다 단위가 다르다.\n' +
  '"—" 는 0 이 아니라 **계측 값이 기록되지 않았다**는 뜻이다.';
const RECORDS_TIP =
  '산출 = 작업이 원장에 기록한 records_out 이다.\n' +
  '"—" 는 0 이 아니라 **계측 값이 기록되지 않았다**는 뜻이다 — 0건 처리와 구분한다(ALPHA-182 의 NULL 계약).';
const PLAN_TIP =
  '이 목록은 ops_expected_task(계획 스냅샷) 행이다 — 계획에 있는 작업만 나오고, plan_status 가 계획 축, ' +
  'task_outcome 이 실제 축이다. 두 축을 대조해 계획 제외·미기동을 가른다.\n' +
  '계획 행 자체가 없는 런은 무엇이 예정이었는지 알 수 없어 "계획 정보 없음"이라고 쓴다 — 0 개가 아니다.';

/* ══ 제어면·원장 대조 ══
 *
 * 두 관측이 다르거나 한쪽이 없을 때만 세운다. **원인을 만들지 않는다** — 두 값과 각각의
 * 관측 시각을 나란히 놓고, 아래 작업 목록이 DB 원장 기준이라는 사실만 덧붙인다.
 */
function ObservationGap({ run }: { run: RunFact }) {
  const rec = reconcile(run);
  if (rec.kind === 'match' || rec.kind === 'noObservation') return null;

  const led = ledgerObservation(run);
  const aws = awsObservation(run);
  const title =
    rec.kind === 'mismatch'
      ? '제어면·원장 불일치'
      : rec.kind === 'ledgerStatusMissing'
        ? '원장 상태 미기록'
        : rec.kind === 'awsNotObserved'
          ? 'AWS 관측 없음'
          : '런 미생성';

  return (
    <div className="card-pad" style={{ paddingBottom: 0 }}>
      <div className="ops-gapcard">
        <div className="ops-gapcard-head">
          <StatusBadge tone={rec.tone}>{title}</StatusBadge>
          <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
            {rec.basis}
          </span>
        </div>
        <div className="ops-facts" style={{ marginTop: 8 }}>
          <div className="t-xs ops-fact">
            <span style={{ color: 'var(--fg-3)' }}>AWS 실행</span>
            <span>
              {aws.status ? (
                <>
                  {aws.status.label} <span className="mono">({aws.status.raw})</span>
                  {aws.at ? ` · ${kst(aws.at)} 종료` : ''}
                </>
              ) : (
                ABSENCE_LABEL[aws.absence!]
              )}
            </span>
          </div>
          <div className="t-xs ops-fact">
            <span style={{ color: 'var(--fg-3)' }}>DB 원장 투영</span>
            <span>
              {led.status ? (
                <>
                  {led.status.label} <span className="mono">({led.status.raw})</span>
                  {led.at ? ` · ${kst(led.at)} 마지막 갱신` : ''}
                </>
              ) : (
                ABSENCE_LABEL[led.absence!]
              )}
            </span>
          </div>
        </div>
        <p className="t-xs m-0" style={{ color: 'var(--fg-2)', marginTop: 8 }}>
          아래 작업 목록은 <b>DB 원장 기준의 마지막 관측값</b>입니다. AWS 최종 상태가 작업 원장에
          아직 반영되지 않았을 수 있습니다 — 화면이 작업을 AWS 상태에 맞춰 바꾸지 않습니다.
        </p>
      </div>
    </div>
  );
}

/* ══ AWS 실패 근거 ══
 *
 * 제어면이 terminal failure 로 끝났을 때만 연다. **응답에 있는 축만 값을 갖고** 나머지는
 * 계측 없음이다 — 하단 작업 중 하나를 실패 원인으로 지목하지 않는다(작업과 실패 state 를
 * 잇는 key 가 어느 응답에도 없다).
 */
function AwsFailureEvidence({ run }: { run: RunFact }) {
  if (!isAwsTerminalFailure(run)) return null;
  const rows = awsFailureEvidence(run);
  return (
    <div className="card-pad" style={{ paddingBottom: 0 }}>
      <div className="ops-gapcard">
        <div className="ops-gapcard-head">
          <span className="t-label">AWS 실패 근거</span>
          <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
            제어면이 준 값만 — 없는 축은 계측 없음입니다
          </span>
        </div>
        <div className="ops-facts" style={{ marginTop: 8 }}>
          {rows.map((r) => (
            <div key={r.label} className="t-xs ops-fact">
              <span style={{ color: 'var(--fg-3)' }}>{r.label}</span>
              <span>
                {r.value === null ? (
                  <Absent kind="uninstrumented" />
                ) : r.label === '종료' ? (
                  kst(r.value)
                ) : (
                  <span className="mono">{r.value}</span>
                )}
              </span>
            </div>
          ))}
        </div>
        <p className="t-xs m-0" style={{ color: 'var(--fg-3)', marginTop: 8 }}>
          실패 state·error·cause·execution ARN 은 이 응답에 없습니다. 그래서 아래 작업 중 하나를
          AWS 실패 원인으로 연결하지 않습니다 — 둘을 잇는 key 가 계측되면 그때 지목합니다.
        </p>
      </div>
    </div>
  );
}

/* ══ 단계 흐름 — 어디서 막혔는가 ══
 *
 * 원장의 stage 순서(수집 → 정제 → 적재)는 **실제 의존 관계**다: 정제는 수집 산출을 읽고,
 * 적재는 정제 산출을 읽는다. 그래서 이 순서로만 그린다 — 같은 stage 안의 작업들은 서로
 * 병렬이라 순서를 만들지 않고 한 칸으로 접는다.
 *
 * 산출량은 여기 넣지 않는다. 데이터셋마다 레코드 단위가 달라 비율로 비교하면 거짓 대비가
 * 생긴다 — 이 그림의 목적은 **단계 상태와 실패 위치**뿐이다.
 */
const FLOW_ORDER: TaskState[] = [
  '실패',
  '타임아웃',
  '미기동',
  '선행 미충족',
  '부분 결손',
  '실행 중',
  '대기',
  '판정 없음',
  '계획 제외',
  '성공',
];

function stageState(states: TaskState[]): TaskState {
  for (const s of FLOW_ORDER) if (states.includes(s)) return s;
  return '판정 없음';
}

function StageFlow({ groups }: { groups: [string, TaskFact[]][] }) {
  /* 단계가 하나뿐이면 흐름이 아니다 — 화살표를 그리지 않는다 */
  if (groups.length < 2) return null;
  const steps = groups.map(([name, items]) => ({
    name,
    count: items.length,
    state: stageState(items.map(taskState)),
  }));
  /* 앞 단계가 막힌 뒤의 단계는 "그 결과"라는 사실만 표시한다 — 원인을 새로 만들지 않는다 */
  const blockedFrom = steps.findIndex((x) => x.state === '실패' || x.state === '타임아웃' || x.state === '미기동');

  return (
    <div className="card-pad" style={{ paddingBottom: 0 }}>
      <div className="ops-flow" role="img" aria-label={steps.map((x) => `${x.name} ${x.state}`).join(', ')}>
        {steps.map((x, i) => (
          <Fragment key={x.name}>
            {i > 0 && (
              <span className="ops-flow-arrow" aria-hidden="true">
                →
              </span>
            )}
            <span className={'ops-flow-step' + (blockedFrom >= 0 && i > blockedFrom ? ' ops-flow-after' : '')}>
              <span className="t-label">{x.name}</span>
              <StatusBadge tone={STATE_TONE[x.state]}>{x.state}</StatusBadge>
              <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
                작업 {x.count}
              </span>
            </span>
          </Fragment>
        ))}
        <Info
          tip={
            '원장 stage 순서(수집 → 정제 → 적재)는 실제 의존 관계다 — 정제는 수집 산출을, 적재는 정제 산출을 읽는다.\n' +
            '같은 단계 안의 작업들은 서로 병렬이라 순서를 만들지 않고 한 칸으로 접는다.\n\n' +
            '단계 상태는 그 단계 작업 중 가장 나쁜 것이다(실패 > 타임아웃 > 미기동 > 선행 미충족 > 부분 결손 > 실행 중 > 대기 > 계획 제외 > 성공).\n' +
            '막힌 단계 뒤는 흐리게 두되 "그래서 못 했다"고 인과를 단정하지는 않는다 — 원장이 준 것은 각 단계의 귀결뿐이다.\n\n' +
            '산출량은 여기 넣지 않는다: 데이터셋마다 레코드 단위가 달라 비율로 비교하면 거짓 대비가 된다.'
          }
          label="단계 흐름"
        />
      </div>
    </div>
  );
}

function RunTasks({
  tasks: all,
  run,
  focus,
}: {
  tasks: TaskFact[];
  run: RunFact;
  focus: string | null;
}) {
  const tasks = tasksOfRun(all, run.id);
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
  const led = ledgerObservation(run);
  const aws = awsObservation(run);
  const rec = reconcile(run);

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
        <span className="t-label">작업 목록 · DB 원장 기준 — {tasks.length}개</span>
        <span className="t-xs" style={{ color: 'var(--fg-3)' }}>
          이 런(run_id)에 속한 작업만 표시합니다 — 다른 런의 작업을 합치지 않습니다
          <Info tip={PLAN_TIP} label="계획 축" />
        </span>
      </div>

      {/* 선택한 런의 식별 정보 — 아래 숫자가 "어느 런의 것인가"에 답한다 */}
      <div className="card-pad" style={{ paddingBottom: 0 }}>
        <p className="t-sm m-0">
          <b>{run.lane ? (LANE_LABEL[run.lane] ?? run.lane) : LANE_UNKNOWN}</b> ·{' '}
          {run.trading_date ?? <Absent kind="none" />} ·{' '}
          {slotTime(run.id) ?? '예정 시각 없음'} ·{' '}
          {run.kind ? KIND_LABEL[run.kind] ?? run.kind : <Absent kind="uninstrumented" />}{' '}
          {/* 두 축을 각각 낸다 — 하나로 합치지 않는다 */}
          <ObservationCell obs={led} at="갱신" /> <ObservationCell obs={aws} at="종료" />{' '}
          <StatusBadge tone={rec.tone}>
            <span title={rec.basis}>{rec.label}</span>
          </StatusBadge>
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
      </div>

      <ObservationGap run={run} />
      <AwsFailureEvidence run={run} />

      {tasks.length > 0 && <StageFlow groups={groups} />}

      {tasks.length === 0 ? (
        <div className="card-pad">
          <p className="t-sm m-0">이 런에 기록된 작업이 없습니다.</p>
          <p className="t-xs m-0" style={{ color: 'var(--fg-3)', marginTop: 4 }}>
            {/* 예정 개수를 모르면 지어내지 않는다 — "예정 N개"를 쓰려면 계획 행이 있어야 한다 */}
            계획 정보 없음 · 실행 기록 0개 —{' '}
            {run.no_run_row
              ? '이 슬롯은 런 행 자체가 생성되지 않아(계획 슬롯 미기동) 무엇이 예정이었는지 원장이 답하지 못합니다.'
              : '이 런의 계획(ops_expected_task) 행이 원장에 없어 예정 작업 수를 알 수 없습니다.'}{' '}
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
                <th className="num">
                  산출 행 <Info tip={RECORDS_TIP} label="산출 행" />
                </th>
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
  /* 조사 문맥을 원장까지 이어 붙인다 — 사건에서 시작했으면 원장 화면의 breadcrumb 가
   * 그 사건으로 돌아갈 수 있어야 한다 */
  const [params] = useSearchParams();
  const incidentId = params.get('fromIncident');
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
        <Link to={ledgerHref({ incident: incidentId ?? undefined, runKey: run.id, task: t.task_key, dataset: t.dataset ?? undefined })!}>
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
