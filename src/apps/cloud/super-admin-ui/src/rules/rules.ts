/* 규칙 R01~R19 + 인과 간선 7개 — 명세 edge-console-rules.md §2·§3 의 정식 구현 (ALPHA-738).
 *
 * R17~R19 는 명세 이후에 붙인 실시간(1분) 레인 규칙이다 — 그 절 아래 주석에 임계값 근거를 적었다.
 *
 * 규칙은 선언 데이터(id/layer/name/desc/kls/base/dep/source) + 조건 함수(run)다.
 * 화면 하단 "규칙 실행 결과" 바는 이 배열에서 자동 생성된다 — 여기 없는 규칙은 화면에 없다.
 *
 * 시각 비교는 ctx.now 를 쓴다(벽시계 직접 참조 금지) — 스냅샷 평가가 재현 가능해야 한다.
 */
import type { Edge, Facts, MinuteSessionFact, OutputFact, Rule, RunFact, TaskFact } from './types.ts';

/** 재시도 정책 상한 — 없으면 null.
 *
 * 원장은 정책 미선언을 `0` 으로 적는다(SFN Retry 블록이 0개라 상한이라는 개념 자체가 없다).
 * 이 `0` 을 상한 0회로 읽으면 두 가지가 동시에 틀린다 — 화면이 `1/0` 이라는 없는 분모를 그리고,
 * R16 이 "평가됨"이라 주장한다. 정책 없음과 상한 0회는 다르므로 여기서 한 번만 정규화한다. */
/**
 * 런이 실패로 끝났다고 말하는 종료 상태 — **원장과 AWS 제어면이 같은 어휘를 쓴다.**
 *
 * 두 벌로 두면 한쪽만 늘어난다: 실제로 AWS 쪽 목록에 `ABORTED` 가 빠져 있어, 원장이 비고 SFN 이
 * `ABORTED` 를 낸 런(운영자가 멈췄는데 투영이 안 된 상태)이 통째로 안 잡혔다. 원장 쪽은 같은
 * 값을 이미 실패로 세고 있었으므로 비대칭이 근거를 가진 것도 아니었다.
 */
const TERMINAL_FAILURE = ['FAILED', 'TIMED_OUT', 'ABORTED'];

export function retryCap(t: TaskFact): number | null {
  return t.max_retries != null && t.max_retries > 0 ? t.max_retries : null;
}

const kst = (iso?: string | null): string =>
  iso
    ? new Intl.DateTimeFormat('ko-KR', {
        timeZone: 'Asia/Seoul',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
      }).format(new Date(iso))
    : '—';

/**
 * 런 종류 표기 — **부재는 '정규'가 아니다.**
 *
 * `kind` 는 계측이 없어 실 응답에서 통째로 빠진다(`RunFact.kind`). 부재를 정규로 접으면 수동 런의
 * 원장 공백이 '정규 런 미귀결'로 서고, 운영자는 안 봐도 되는 것을 본다. 넷째 값을 만든다.
 */
const runKind = (r: RunFact): string =>
  r.kind === 'manual'
    ? '수동 런'
    : r.kind === 'backfill'
      ? '백필 런'
      : r.kind === 'scheduled'
        ? '정규 런'
        : '런 종류 미기록';

/**
 * R10 이 비교할 수 있는 점의 수 — 그 레인의 피드 + 관측 가능한 단계 값.
 *
 * `run()` 과 같은 축을 세야 한다: blind 는 0이 아니라 **빠짐**이라 점이 아니고, `null` 도 점이
 * 아니다. 두 점이 없으면 "인접 감소"라는 물음 자체가 성립하지 않는다 — 위반 0건이 아니다.
 */
/**
 * R13 이 기준으로 쓸 수 있는 산출인가 — **`canRun` 과 `run()` 이 같은 술어를 봐야 한다.**
 *
 * 두 곳에 따로 쓰면 반드시 갈린다(실제로 두 번 갈렸다: `!= null` vs truthy, 그 다음 `isFinite`
 * vs `!== 0`). 갈리면 `canRun` 만 통과하고 `run()` 이 걸러 **평가됨 · 위반 0**("분포 안")이 선다.
 * `0` 은 기준이 아니다(나눗셈이 성립하지 않는다), `NaN` 도 아니다(비교가 언제나 거짓이다).
 */
const hasBase = (o: OutputFact): boolean => Number.isFinite(o.base) && o.base !== 0;

/**
 * R13 이 **실제로 판정할 수 있는** 산출인가 — 기준과 오늘 값이 둘 다 수여야 한다.
 *
 * `canRun` 이 기준만 보면, 기준은 있는데 오늘 값이 수가 아닌 산출뿐인 응답이 `평가됨 · 위반 0`
 * ("전부 분포 안")으로 선다 — 하나도 판정 못 했는데. R19 가 "원장을 하나도 못 읽으면 못 돎"인 것과
 * 같은 형태다. `canRun`·`run()`·`note` 세 곳이 이 한 술어를 공유한다.
 */
const judgeable = (o: OutputFact): boolean => hasBase(o) && Number.isFinite(o.today);

const chainPoints = (f: Facts, src: 'batch' | 'intraday'): number => {
  /* ⚠️ **`canRun` 은 `evaluated` 와 무관하게 전 규칙에 대해 무조건 불린다** — 여기서 죽으면
   * 그 규칙만이 아니라 **평가 전체**가 사라진다(19규칙의 사건이 통째로). 축이 옵셔널이 되는 날
   * (계약 문서의 남은 항목) 그게 실제로 일어나므로 지금 널 안전하게 둔다. */
  const feeds = f.chain?.feeds ?? [];
  const stages = f.chain?.stages ?? [];
  return [
    (src === 'batch' ? feeds[0] : feeds[1])?.v,
    ...stages.filter((s) => !s.blind).map((s) => s[src]),
  ].filter((v) => Number.isFinite(v)).length;
  /* `!= null` 이 아니라 `Number.isFinite` 다 — `NaN` 은 `!= null` 을 통과하지만 `<` 비교가
   * 언제나 거짓이라 "점은 있는데 손실은 없다"(평가됨 · 위반 0)로 인증된다. 검증 안 된 JSON 에서
   * 오는 값이라 타입 선언이 못 막는다. */
};

/** 세션을 사람이 읽는 이름 — 화면(MinutePage)이 쓰는 표기와 같다 */
const sessionLabel = (s: MinuteSessionFact) => `${s.dataset} / ${s.sourceGroup}`;

/**
 * 합성 대상 축 — **조각이 하나라도 비면 합성하지 않는다**(빈 문자열을 낸다).
 *
 * 엔진의 빈 축 가드는 합성 **후** 문자열만 본다(`targetId === ''`). 그래서 조각이 빈 응답은
 * `price_minute/`·`batch:` 처럼 **정상처럼 보이는** 사건 키를 통과시킨다 — 위반이 하나면 충돌도
 * 안 나 그대로 나가고, 내일 또 같은 모양이 오면 어제 공유한 링크가 오늘 사건을 연다(충돌
 * 검사로는 못 잡는 시간 축 충돌). 여기서 빈 문자열로 접으면 그 가드가 **그 규칙만**
 * `못 돎(identity)` 으로 세운다 — 새 가드를 만들지 않고 이미 있는 판정 경로 하나를 쓴다.
 *
 * ⚠️ **합성하는 자리는 전부 이 함수를 통과해야 한다.** 자리마다 인라인으로 쓰면 가드가 여럿이
 * 되고 나중에 한쪽만 고쳐진다 — 실제로 처음 고칠 때 세션 축만 막고 체인 축(`${src}:${s.id}`)을
 * 놓쳤다(리뷰가 잡았다). 구분자가 자리마다 다른 것은 **런북 키가 그 모양에 매여 있어서**다
 * (`R10.batch:c.run`) — 통일하면 등록된 조치를 못 찾는다.
 */
const compose = (sep: string, ...parts: string[]): string =>
  /* `Boolean` 만으로는 모자란다 — 응답은 런타임 검증을 안 거치고 오므로 조각이 `[]`(truthy 인데
   * join 하면 빈 문자열)·객체(`[object Object]`)로 올 수 있다. 타입 선언은 그걸 못 막는다.
   * 문자열이고 비어 있지 않은 것만 조각으로 친다. */
  parts.every((p) => typeof p === 'string' && p !== '') ? parts.join(sep) : '';

/**
 * 세션의 **대상 축** — 무엇이 고장났나. `dataset/sourceGroup` 이고 **날짜가 없다**.
 *
 * 데이터셋만으로는 안 갈린다(같은 `news_minute` 를 벤더별로 따로 돌린다). 날짜를 여기 넣으면
 * 런북 키 `${rule}.${targetId}` 가 매일 달라져 어떤 조치도 등록할 수 없다 — 시점은 `scope` 다.
 */
const sessionTarget = (s: MinuteSessionFact) => compose('/', s.dataset, s.sourceGroup);

export const RULES: Rule[] = [
  {
    id: 'R01',
    layer: '런',
    name: '계획 슬롯 미기동',
    kls: '미기동',
    base: 'P0',
    desc: '계획된 슬롯에 런 행이 없다 — 프로세스가 아예 안 떴다',
    dep: null,
    source: 'DB_LEDGER',
    run: (f) =>
      f.runs
        .filter((r) => r.planned && r.no_run_row)
        .map((r) => ({
          target: r.id,
          title: '런이 생성되지 않음',
          metric: 1,
          unit: '슬롯',
          why: `${r.lane} ${r.trading_date} 슬롯에 ops_pipeline_run 행이 없다`,
          evidence: 'ops_reconciliation_issue PLANNER_MISSING',
          drill: ['run', 'run-' + r.id] as [string, string],
        })),
  },

  {
    id: 'R02',
    layer: '런',
    name: '마감 초과 미귀결',
    kls: '미마감',
    base: 'P1',
    desc: 'hard_deadline을 넘겼는데 orchestration_status가 비어 있다',
    dep: null,
    source: 'DB_LEDGER',
    run: (f, ctx) =>
      f.runs
        .filter((r) => r.deadline && !r.ledger_status && new Date(r.deadline) < ctx.now)
        .map((r) => ({
          target: r.id,
          title: '마감 초과 · 원장 미귀결',
          /* 세는 값이 없다 — 이 위반은 양이 아니라 판정 하나다 */
          metric: null,
          state: '미귀결',
          why: `${runKind(r)} · 마감 ${kst(r.deadline)} 경과, 원장 상태 없음 (AWS는 ${r.aws_status || '—'})`,
          evidence: 'ops_pipeline_run.orchestration_status IS NULL',
          drill: ['run', 'run-' + r.id] as [string, string],
        })),
  },

  {
    id: 'R03',
    layer: '런',
    name: '제어면·원장 불일치',
    kls: '투영 지연',
    base: 'P1',
    desc: 'AWS 최종 상태와 DB 투영 원장이 다르다 — 어느 쪽도 정본으로 덮지 않는다',
    dep: 'SFN DescribeExecution 조회 배선(runs[].aws_status)',
    source: 'AWS_CONTROL+DB_LEDGER',
    /* AWS 상태를 가진 런이 하나도 없으면 비교할 표면이 한쪽뿐이다 — "두 표면이 같다"가 아니라
     * **한 표면을 못 봤다**. 실 응답은 SFN 조회가 붙기 전까지 이 축이 통째로 없어, 없으면
     * R03 이 매일 '평가됨 · 위반 0'("제어면과 원장이 일치한다")이라는 거짓을 주장한다.
     *
     * `run()` 과 **같은 truthy 축**을 쓴다(`!= null` 이 아니다) — 어댑터가 미관측을 `''` 로 옮기면
     * `!= null` 은 그걸 관측으로 세고, `run()` 의 필터는 버려 다시 '평가됨 · 위반 0' 이 된다.
     * ⚠️ **부분 관측은 이 축이 답하지 않는다**: 40런 중 1건만 관측돼도 참이다. 그건 규칙 단위
     * 불리언으로 표현할 수 없는 축이라 `meta.awsUnobservedRuns`(계약 문서, 미배선) 소관이다.
     * `every` 로 바꾸면 SFN 실행이 애초에 없는 런 하나가 전체를 `못 돎` 으로 만든다 — 볼 수 있는
     * 불일치를 통째로 버리는 쪽이라 더 나쁘다. */
    canRun: (f) => f.runs.some((r) => !!r.aws_status),
    /* 한 런만 관측돼도 `canRun` 은 참이다(그게 규칙 단위 불리언의 한계다) — 나머지 런은 비교조차
     * 안 했는데 "조건에 걸린 것 없음"에 묻힌다. 몇 런을 실제로 봤는지 여기서 밝힌다.
     * 부분 실패의 **크기**는 `meta.awsUnobservedRuns`(미배선) 소관이고, 이 note 는 그때까지
     * 침묵을 없애는 최소값이다. R10·R19 의 부분 관측 표기와 같은 형태다. */
    note: (f) => {
      const seen = f.runs.filter((r) => !!r.aws_status).length;
      return seen === f.runs.length
        ? null
        : `AWS 상태를 관측한 런 ${seen}/${f.runs.length} — 나머지는 비교하지 않았다(위반 0건이 "두 표면이 일치한다"는 뜻이 아니다)`;
    },
    run: (f) =>
      f.runs
        .filter((r) => r.aws_status && r.ledger_status && r.aws_status !== r.ledger_status)
        .map((r) => ({
          target: r.id,
          title: '두 표면이 다름',
          metric: null,
          state: `${r.aws_status} ≠ ${r.ledger_status}`,
          why: `AWS ${r.aws_status} ${kst(r.aws_stop)} vs 원장 ${r.ledger_status} ${kst(r.ledger_updated)}`,
          evidence: 'stepfunctions 최종 상태 vs ops_pipeline_run',
          drill: ['run', 'run-' + r.id] as [string, string],
        })),
  },

  {
    id: 'R04',
    layer: '런',
    name: '런 실패',
    kls: '고장',
    base: 'P0',
    desc: '런이 FAILED·TIMED_OUT·ABORTED로 끝났다',
    dep: null,
    source: 'DB_LEDGER',
    run: (f) =>
      f.runs
        .filter(
          (r) =>
            TERMINAL_FAILURE.includes(r.ledger_status ?? '') ||
            /* 원장이 비고 AWS만 실패면 — **수동·백필로 확인된 런만 뺀다**. 수동·백필의 원장
             * 공백은 실패 단정 근거가 아니라는 것이 명세 §2 R04 의 경계인데, `kind === 'scheduled'`
             * 를 **요구**하면 계측이 없는 실 응답에서 그 분기가 영구 사문화된다 — 원장 공백 +
             * AWS 실패가 통째로 안 잡히는 P0 거짓 음성이다. 배제는 아는 것으로만 한다:
             * 모름은 배제 근거가 아니고, 대신 `why` 가 종류를 못 읽었다고 밝힌다. */
            (!r.ledger_status &&
              TERMINAL_FAILURE.includes(r.aws_status ?? '') &&
              r.kind !== 'manual' &&
              r.kind !== 'backfill'),
        )
        .map((r) => ({
          target: r.id,
          title: '런 ' + (r.ledger_status || r.aws_status),
          metric: null,
          state: (r.ledger_status || r.aws_status) as string,
          why: `${r.lane} ${r.id.split('T')[1] || ''} 슬롯 · ${runKind(r)}`,
          evidence: 'ops_pipeline_run.orchestration_status',
          drill: ['run', 'run-' + r.id] as [string, string],
        })),
  },

  {
    id: 'R05',
    layer: '작업',
    name: '필수 작업 미귀결',
    kls: '고장',
    base: 'P0',
    desc: 'required ∧ DUE인데 FULFILLED가 아니다',
    dep: null,
    source: 'DB_LEDGER',
    run: (f) =>
      f.tasks
        /* plan 축을 빼면 계획에서 제외된(SKIPPED) 작업이 '미귀결'로 둔갑한다 —
         * 안 한 것이 아니라 할 일이 아니었다. 원장이 관대해지는 게 아니라 엄해지는 쪽 오류다. */
        .filter((t) => t.required && t.plan_status !== 'SKIPPED' && t.task_outcome !== 'FULFILLED')
        .map((t) => ({
          target: t.task_key,
          title: t.task_key,
          metric: null,
          state: t.task_outcome ?? '판정 없음',
          why: `${t.stage} · ${t.pipeline_type} — ${
            t.task_outcome === 'FAILED' ? '실행됐으나 실패' : '상류 실패로 미실행(PENDING)'
          }`,
          cause: t.task_outcome !== 'FAILED',
          evidence: 'ops_expected_task.task_outcome',
          lastok: t.last_ok,
          okrate: t.ok_rate,
          runId: t.run_id,
          drill: ['run', 'task-' + t.task_key] as [string, string],
        })),
  },

  {
    id: 'R06',
    layer: '작업',
    name: '데이터 부분 유실',
    kls: '부분 유실',
    base: 'P1',
    desc: 'data_status가 INCOMPLETE·INVALID — 스텝이 스스로 유실을 판정했다',
    dep: null,
    source: 'DB_LEDGER',
    run: (f) =>
      f.tasks
        .filter((t) => ['INCOMPLETE', 'INVALID'].includes(t.data_status ?? '') && t.failed_records)
        .map((t) => ({
          target: t.task_key,
          title: t.task_key,
          metric: t.failed_records as number,
          unit: '건',
          why: 'ops.failed_records — 스텝의 유실 판정값이며 skipped_*를 직접 더한 값이 아니다. 무엇 1건인지는 잡마다 다르다',
          runId: t.run_id,
          evidence: 'ops_expected_task.data_status + failed_records',
          drill: ['run', 'task-' + t.task_key] as [string, string],
        })),
  },

  {
    id: 'R07',
    layer: '작업',
    name: '완전성 결손',
    kls: '결손',
    base: 'P0',
    desc: '분모가 있는 작업에서 received < expected',
    dep: '완전성 분모 배선(현재 ETF 3작업만)',
    source: 'DB_LEDGER',
    /* expected 가 null 인 작업은 위반이 아니라 평가 대상 아님 — 27개 중 24개가 그렇다 */
    note: (f) => {
      const wired = f.tasks.filter((t) => t.completeness_expected != null).length;
      return `분모 배선 작업 ${wired}/${f.tasks.length} — 나머지는 조건 자체가 평가 대상 아님`;
    },
    mockBacked: (f) => f.tasks.some((t) => t.completeness_expected != null && t.cmpl_mock),
    run: (f) =>
      f.tasks
        .filter(
          (t) =>
            t.completeness_expected != null &&
            (t.completeness_received ?? 0) < t.completeness_expected,
        )
        .map((t) => ({
          target: t.task_key,
          title: t.task_key,
          metric: (t.completeness_expected as number) - (t.completeness_received ?? 0),
          unit: '엔티티',
          why: `${t.completeness_received}/${t.completeness_expected} — 기대 대비 부족`,
          runId: t.run_id,
          evidence: 'ops_expected_task.completeness',
          mock: !!t.cmpl_mock,
          drill: ['run', 'task-' + t.task_key] as [string, string],
        })),
  },

  {
    id: 'R08',
    layer: '데이터셋',
    name: '신선도 위반',
    kls: 'STALE',
    base: 'P1',
    desc: 'actual_as_of가 expected_as_of보다 오래됐다',
    dep: 'DatasetContract actual_as_of writer',
    source: 'DB_LEDGER',
    /* actual 근거를 가진 데이터셋이 하나도 없으면 이 규칙은 돌지 못한 것(evaluated:false)이지
     * 조용한 것이 아니다 */
    canRun: (f) => f.datasets.some((d) => d.contract && d.actual_as_of != null),
    mockBacked: (f) => f.datasets.some((d) => d.actual_as_of != null && d.mock),
    run: (f) =>
      f.datasets
        .filter(
          (d) =>
            d.contract &&
            d.actual_as_of &&
            d.expected_as_of &&
            !d.window_contract &&
            d.actual_as_of < d.expected_as_of,
        )
        .map((d) => ({
          target: d.id,
          title: d.id,
          metric: null,
          state: 'STALE',
          why: `기대 ${d.expected_as_of} · 실제 ${d.actual_as_of} — 분석이 오래된 스냅샷 위에서 돈다`,
          evidence: 'DatasetContract.actual_as_of',
          mock: !!d.mock,
          drill: ['dataset', 'ds-' + d.id] as [string, string],
        })),
  },

  {
    id: 'R09',
    layer: '데이터셋',
    name: '신선도 판정 불가',
    kls: '미상',
    base: 'P2',
    desc: '계약이 없거나 actual 근거가 없어 FRESH/STALE을 가릴 수 없다',
    dep: null,
    source: 'DB_LEDGER',
    run: (f) =>
      f.datasets
        .filter((d) => d.unverifiable)
        .map((d) => ({
          target: d.id,
          title: d.id,
          metric: null,
          state: '판정 불가',
          why: d.unverifiable as string,
          evidence: '—',
          drill: ['dataset', 'ds-' + d.id] as [string, string],
        })),
  },

  {
    id: 'R10',
    layer: '흐름',
    name: '체인 손실',
    kls: '손실',
    base: 'P0',
    desc: '인접 단계에서 in > out이고 설계된 감소가 아니다',
    dep: '산출 체인 단계 집계(chain.feeds·stages)',
    source: 'DB_LEDGER',
    /* 비교할 점이 두 개 없으면 "인접 감소"를 물을 수 없다 — 조용한 것이 아니라 못 돈 것이다.
     * 실 응답에는 이 축의 소스가 아예 없어(계약 §축별 소스) 점이 0개가 된다. */
    canRun: (f) => chainPoints(f, 'batch') >= 2 || chainPoints(f, 'intraday') >= 2,
    /* ⚠️ `canRun` 은 **규칙 단위**라 갈래를 못 가른다. 한 갈래만 도착한 응답에서 나머지 갈래는
     * 점이 0개인데도 "손실 없음"으로 선다 — 계약상 그 형상(뉴스 갈래만 부분)이 먼저 온다.
     * `&&` 로 조이면 반대로 **볼 수 있는 P0 손실을 통째로 버린다**. 판정은 `||` 로 두고,
     * 무엇을 실제로 비교했는지는 여기서 밝힌다(리포트·규칙 표가 읽는다). */
    note: (f) => {
      const seen = (['batch', 'intraday'] as const).filter((src) => chainPoints(f, src) >= 2);
      if (seen.length === 2) return null;
      const label = (src: string) => (src === 'batch' ? '배치' : '장중');
      return `비교한 갈래 ${seen.map(label).join('·') || '없음'} — 나머지 갈래는 점이 2개 미만이라 비교 자체가 없었다(위반 0건이 "손실 없음"을 뜻하지 않는다)`;
    },
    run: (f) => {
      const out: ReturnType<Rule['run']> = [];
      const stages = f.chain.stages;
      (['batch', 'intraday'] as const).forEach((src) => {
        let prev: number | null | undefined =
          src === 'batch' ? f.chain.feeds[0]?.v : f.chain.feeds[1]?.v;
        let prevLabel = src === 'batch' ? '배치 트리거' : '장중 트리거';
        stages.forEach((s) => {
          if (s.blind) return; // 관측 불가 단계는 비교 축에서 제외 — 0 이 아니다
          /* `chainPoints`(canRun)와 **같은 술어**여야 한다. `!= null` 로 두면 수가 아닌 값이
           * `prev` 로 들어가 다음 비교가 언제나 거짓이 되고, canRun 이 센 점 수와 실제 비교 횟수가
           * 갈려 "평가됨 · 위반 0"이 선다 — 이 파일에서 두 번 난 바로 그 갈림이다. */
          if (!Number.isFinite(s[src])) return;
          const v = s[src] as number;
          if (prev != null && v < prev) {
            out.push({
              /* 대상은 어느 인접 단계에서 줄었는가다. `batch:c.res` 는 앵커용 id 라 targetId 로 내린다.
               * 세션 축과 **같은 합성**이라 같은 조각 가드를 탄다 — 단계 id 가 비면 `batch:` 라는
               * 정상처럼 보이는 사건 키가 나간다. */
              target: `${prevLabel} → ${s.label}`,
              targetId: compose(':', src, s.id),
              title: `${prevLabel} → ${s.label}`,
              metric: prev - v,
              unit: '건',
              why: `${prev} → ${v} 로 줄었다 — 설계된 감소가 아니다`,
              src,
              evidence: '산출 체인 인접 단계 비교',
              drill: ['chain', 'chain-' + s.id],
            });
          }
          prev = v;
          prevLabel = s.label;
        });
      });
      return out;
    },
  },

  {
    id: 'R11',
    layer: '큐',
    name: '소비자 부재',
    kls: '배포 공백',
    base: 'P0',
    desc: '대기 메시지가 있는데 in-flight 0이고 이 큐를 구독하는 서비스가 없다',
    dep: '큐→서비스 구독 매핑 선언',
    source: 'AWS_CONTROL',
    canRun: (f) => (f.queues ?? []).some((q) => q.subscribers != null),
    mockBacked: (f) => (f.queues ?? []).some((q) => q.sub_mock),
    run: (f) =>
      (f.queues ?? [])
        .filter((q) => q.visible > 0 && q.in_flight === 0 && (q.subscribers ?? []).length === 0)
        .map((q) => ({
          target: q.name,
          title: q.purpose || q.name,
          metric: q.visible,
          unit: '메시지',
          why: '대기 중인데 소비 자체가 시작되지 않음 — 런타임 실패가 아니라 배선 부재(발행은 성공)',
          evidence: 'SQS visible/in-flight + ECS 서비스 목록',
          mock: !!q.sub_mock,
          drill: ['chain', 'q-' + q.name] as [string, string],
        })),
  },

  {
    id: 'R12',
    layer: '큐',
    name: 'DLQ 유실',
    kls: '유실',
    base: 'P0',
    desc: 'DLQ에 메시지가 있다 — 재시도 소진',
    dep: null,
    source: 'AWS_CONTROL',
    canRun: (f) => f.queues != null,
    run: (f) =>
      (f.queues ?? [])
        .filter((q) => q.dlq > 0)
        .map((q) => ({
          target: q.name,
          title: q.name + ' DLQ',
          metric: q.dlq,
          unit: '메시지',
          why: '재시도 소진 — 유실 확정',
          evidence: 'SQS DLQ',
          drill: ['chain', 'q-' + q.name] as [string, string],
        })),
  },

  {
    id: 'R13',
    layer: '산출',
    name: '산출 이상',
    kls: '이상',
    base: 'P1',
    desc: '오늘 값이 직전 10영업일 중앙값에서 ±25% 이상 벗어났다',
    dep: '산출 일별 계열(outputs[].base — 직전 10영업일 중앙값)',
    source: 'DB_LEDGER',
    /* 기준이 있는 산출이 하나도 없으면 "분포 안"이 아니라 **분포를 모른다**. 실 응답은 일별
     * 계열을 주는 데가 없어 이 축이 통째로 빈다. `run()` 과 같은 truthy 검사를 쓴다 —
     * base 0 은 나눗셈이 성립하지 않아 양쪽 모두 평가 대상이 아니다. */
    canRun: (f) => (f.outputs ?? []).some(judgeable),
    /* 기준이 있는 산출이 하나라도 있으면 규칙은 돈다 — 기준 없는 산출은 판정에서 그냥 빠진다.
     * 그 사실을 안 밝히면 "분포 밖 0건"이 전 산출을 봤다는 뜻으로 읽힌다(R03·R10·R19 와 같은 형태). */
    note: (f) => {
      /* 빠지는 이유는 둘이다 — 기준이 없거나(`base`), 오늘 값이 수가 아니거나(`today`).
       * 한쪽만 세면 나머지 침묵이 남는다: `today: NaN` 은 비교가 언제나 거짓이라 위반도 안 되고
       * 이 문장에도 안 나와, 그 산출을 **봤다고 착각**하게 된다. */
      const blind = (f.outputs ?? [])
        .filter((o) => !judgeable(o))
        .map((o) => o.label);
      return blind.length ? `판정에서 빠진 산출 ${blind.join('·')} — 기준(중앙값)이나 오늘 값이 수가 아니다` : null;
    },
    run: (f) =>
      (f.outputs ?? [])
        /* `today` 도 유한수여야 한다 — `NaN` 이면 비교가 언제나 거짓이라 "분포 안"으로 인증된다 */
        .filter(
          (o) => judgeable(o) && Math.abs((o.today - (o.base as number)) / (o.base as number)) >= 0.25,
        )
        .map((o) => ({
          /* `o.pub` 은 산출 축의 내부 id 다 — 표에 서는 것은 라벨이고 id 는 앵커·런북 키로 남는다 */
          target: o.label,
          targetId: o.id,
          title: o.label,
          /* 편차율은 양이다 — 부호를 살린 수로 두고 단위는 %. 문자열 `'-50%'` 로 만들면
           * 정렬(크기순)과 표의 숫자 열이 이 값을 못 쓴다 */
          metric: Math.round(((o.today - (o.base as number)) / (o.base as number)) * 100),
          unit: '%',
          why: `오늘 ${o.today.toLocaleString('ko-KR')} · 평소(중앙값) ${(o.base as number).toLocaleString('ko-KR')} ${o.unit} — 분포 밖, 원인은 다른 규칙이 지목한다`,
          evidence: '30일 시계열 중앙값',
          drill: ['trend', 'out-' + o.id] as [string, string],
        })),
  },

  {
    id: 'R14',
    layer: '경계',
    name: '전달 정합 위반',
    kls: '정합 위반',
    base: 'P0',
    desc: '게시했는데 미발번, 또는 발번했는데 현재 비게시',
    dep: null,
    source: 'DB_LEDGER',
    run: (f) => {
      const b = f.boundary;
      const out: ReturnType<Rule['run']> = [];
      if (b.published_without_delivery > 0) {
        out.push({
          target: '게시됐는데 미발번',
          targetId: 'pub_no_delivery',
          title: '게시됐는데 미발번',
          metric: b.published_without_delivery,
          unit: '건',
          why: '같은 트랜잭션이라 구조상 0이어야 한다',
          evidence: 'explanation_result ⋈ tenant_delivery',
          drill: ['delivery', 'b-pub'],
        });
      }
      if (b.delivery_now_nonpublished > 0) {
        out.push({
          target: '전달됐는데 현재 비게시',
          targetId: 'delivery_nonpub',
          title: '전달됐는데 현재 비게시',
          metric: b.delivery_now_nonpublished,
          unit: '건',
          /* 규약 이후 `why` 는 문맥의 유일한 운반자다 — 빈 문자열이면 상세·ⓘ 에서 '왜'가 통째로
           * 빈다. seed_note 부재는 "사유가 없다"가 아니라 기록이 없는 것이다(R15 와 같은 처리) */
          why:
            b.seed_note ??
            '시드 유래 여부를 가릴 기록이 없다 — 전달·게시 어느 쪽이 뒤집혔는지는 이 사실이 답하지 않는다',
          seed: true,
          sev: 'P2',
          evidence: 'tenant_delivery ⋈ explanation_result',
          drill: ['delivery', 'b-dlv'],
        });
      }
      return out;
    },
  },

  {
    id: 'R15',
    layer: '산출',
    name: 'ETF 분석 실패',
    kls: '고장',
    base: 'P0',
    desc: 'ETF 단위로 분석이 실패해 설명이 만들어지지 않았다',
    dep: 'AnalyzeOne per-ETF outcome 원장',
    source: 'MOCK',
    canRun: (f) => f.etf_ledger != null,
    mockBacked: (f) => !!f.etf_ledger?.mock,
    run: (f) => {
      const bad = (f.etf_ledger?.rows ?? []).filter((r) => r.outcome === 'FAILED');
      return bad.length
        ? [
            {
              target: 'ETF 분석',
              targetId: 'analyze.failed',
              title: 'ETF 분석 실패',
              metric: bad.length,
              unit: 'ETF',
              /* error 가 null 인 것은 "오류가 없다"가 아니라 원장이 사유를 안 남긴 것이다 */
              why: bad[0].error
                ? `설명 미생성 — ${bad[0].error}`
                : '설명 미생성 — per-ETF 원장에 오류 사유 기록이 없다',
              list: bad.map((r) => r.name),
              mock: !!f.etf_ledger?.mock,
              evidence: 'ETF별 분석 원장(목)',
              drill: ['chain', 'etf-ledger'] as [string, string],
            },
          ]
        : [];
    },
  },

  {
    id: 'R16',
    layer: '작업',
    name: '재시도 소진',
    kls: '소진',
    base: 'P0',
    desc: '시도 수가 정책 상한에 도달했는데 아직 귀결되지 않았다',
    dep: 'CatalogEntry 재시도 정책 필드',
    source: 'DB_LEDGER',
    canRun: (f) => f.tasks.some((t) => retryCap(t) != null),
    mockBacked: (f) => f.tasks.some((t) => retryCap(t) != null && t.retry_mock),
    run: (f) =>
      f.tasks
        .filter((t) => {
          const cap = retryCap(t);
          return cap != null && (t.attempts ?? 0) >= cap && t.task_outcome !== 'FULFILLED';
        })
        .map((t) => ({
          target: t.task_key,
          title: t.task_key + ' 재시도 소진',
          metric: t.attempts ?? 0,
          unit: '시도',
          why: `정책 상한 ${retryCap(t)}회 도달 — 자동 회복 여지 없음, 수동 개입 필요`,
          mock: !!t.retry_mock,
          runId: t.run_id,
          evidence: 'ops_task_attempt 수 vs 정책 상한',
          drill: ['run', 'task-' + t.task_key] as [string, string],
        })),
  },
  /* ── 실시간(1분) 수집 R17~R19 ─────────────────────────────────────────────────
   * 왜 배치 규칙과 따로인가: 원장이 다르다(`minute_ingestion_session/window`). 그리고 이 축은
   * **facts-snapshot 에 없다** — 화면이 `/sources/minute` 응답을 실어 줄 때만 돈다. 안 실리면
   * canRun=false 라 "위반 0건"이 아니라 evaluated:false 로 남는다(조용한 규칙과 못 돈 규칙은 다르다).
   *
   * 지키는 선:
   *   · 무증거는 판정이지 원인이 아니다 — "실행체가 죽었다"고 쓰지 않는다. 조건과 다음 확인만.
   *   · 종료 국면(DRAINED·QC_RUNNING·FINALIZED)의 lease 만료는 정상이다. 실행체는 drain 뒤
   *     떠나는 게 맞고, 그걸 위반으로 세면 매일 장 마감마다 거짓 P0 가 뜬다.
   *   · lease 부재(null)는 만료(true)와 다른 사실이라 섞지 않는다 — 기동 증거 자체가 없는 것이고
   *     "끊겼다"고 말할 근거가 아니다.
   */
  {
    id: 'R17',
    /* 실시간의 제어 단위는 데이터셋 × 세션 날짜다 — 층 어휘에 '세션'이 없어 데이터셋으로 둔다 */
    layer: '데이터셋',
    name: '실시간 실행 증거 끊김',
    kls: '고장',
    base: 'P0',
    desc: '가동 중이어야 할 실시간 세션의 lease 가 만료됐거나 세션이 FAILED 다',
    dep: null,
    source: 'DB_LEDGER',
    canRun: (f) => f.minute != null,
    axis: 'minute',
    /* 임계값이 없다 — 이 사실 자체가 위반이다. 1분 레인은 끊기면 그 시간의 데이터가 영구 결손이라
     * "몇 건부터"를 둘 자리가 없다. */
    run: (f) =>
      (f.minute?.sessions ?? [])
        .filter((s) => {
          if (s.phase === 'FAILED') return true;
          if (s.phase === 'DRAINED' || s.phase === 'QC_RUNNING' || s.phase === 'FINALIZED') return false;
          return s.leaseExpired === true;
        })
        .map((s) => ({
          target: sessionLabel(s),
          targetId: sessionTarget(s),
          scope: f.minute!.date,
          title: `${sessionLabel(s)} 실행 증거 끊김`,
          metric: 1,
          unit: '세션',
          why:
            s.phase === 'FAILED'
              ? 'phase=FAILED — 세션이 실패로 닫혔다'
              : 'lease_expires_at 이 now() 보다 앞선다(서버 DB 시계 판정). 실행체 사망인지 배포·네트워크인지는 이 사실이 답하지 않는다',
          evidence: `minute_ingestion_session ${f.minute?.date} phase=${s.phase}`,
          drill: ['dataset', 'ds-' + s.dataset] as [string, string],
        })),
  },

  {
    id: 'R18',
    layer: '데이터셋',
    name: '실시간 무증거 창 누적',
    kls: '무증거',
    base: 'P1',
    desc: '기한이 지난 1분 창에 실행·결과 증거가 없다',
    dep: null,
    source: 'DB_LEDGER',
    canRun: (f) => f.minute != null,
    axis: 'minute',
    run: (f) =>
      (f.minute?.sessions ?? [])
        /* 임계 5창 — 1분 창이므로 최소 5분치 공백이다. 1~2창은 수집 지연의 흔들림으로 흡수되지만
         * 5분이면 그 구간 데이터가 없다는 뜻이고, 되돌릴 창구(소급 수집)가 소스마다 없다.
         * P1 인 이유: 이미 지나간 창이라 즉시 조치로 되살릴 수 없다 — R17(지금 끊김)과 다르다. */
        .filter((s) => s.overdueNoEvidence >= 5)
        .map((s) => ({
          target: sessionLabel(s),
          targetId: sessionTarget(s),
          scope: f.minute!.date,
          title: `${sessionLabel(s)} 무증거 창`,
          metric: s.overdueNoEvidence,
          unit: '창',
          why: '기한이 지났는데 증거가 없다 — 안 돌았는지 기록이 안 남았는지는 이 사실이 가르지 않는다',
          evidence: `minute_ingestion_window ${f.minute?.date} overdue_no_evidence`,
          drill: ['dataset', 'ds-' + s.dataset] as [string, string],
        })),
  },

  {
    id: 'R19',
    layer: '데이터셋',
    name: '실시간 후속 처리 유실',
    kls: '유실',
    base: 'P1',
    desc: '실시간 레인의 후속 처리 작업이 종료 상태 실패(DEAD)로 남았다',
    dep: null,
    source: 'DB_LEDGER',
    /* `deadJobs: null` 은 **모름**이다(그 데이터셋의 job 원장을 응답이 안 준다). 이 축이 없으면
     * 판정 대상에서 빠지는데, 그게 규칙 전체에 걸치면 `평가됨 · 위반 0`("봤고 괜찮다")이 된다 —
     * 어댑터가 지킨 구분이 판정 층에서 소멸하던 자리다(어휘 밖 데이터셋만 있는 날의 모양).
     * 세션이 **0건**인 것은 다르다: 그건 실측이고 잃을 후속 작업 자체가 없다. */
    canRun: (f) => {
      const ss = f.minute?.sessions;
      if (!ss) return false;
      /* 🔴 **축 선언이 없으면 못 돈다.** 타입은 `deadJobsByDate` 를 필수로 두지만 응답은 런타임
       * 검증을 안 거치고 온다 — 빠지면 `new Set(undefined)` 가 빈 집합이 되어 날짜 축 뉴스 DEAD 가
       * 조용히 세션 축으로 재분류되고, 벤더가 둘인 날 같은 3건이 두 사건으로 선다.
       * 그 재분류는 이 규칙이 막으려던 결함 자체라, 선언이 없으면 판정하지 않는다. */
      if (!Array.isArray(f.minute?.deadJobsByDate)) return false;
      /* `deadJobs` 는 유한수여야 안다 — `NaN`·`''`·`[]` 는 `!= null` 을 통과하지만 `>= 1` 이
       * 거짓이라 "봤고 0건"으로 인증된다. 모르는 값은 모름으로 접는다. */
      return ss.length === 0 || ss.some((s) => Number.isFinite(s.deadJobs));
    },
    axis: 'minute',
    /* 일부만 모르는 날은 규칙 단위 `못 돎` 으로 표현할 수 없다 — 어느 데이터셋이 판정에서
     * 빠졌는지 밝힌다. 안 밝히면 그 데이터셋의 유실이 "0건"에 흡수돼 보인다. */
    note: (f) => {
      const unknown = [
        ...new Set(
          (f.minute?.sessions ?? []).filter((s) => !Number.isFinite(s.deadJobs)).map((s) => s.dataset),
        ),
      ];
      return unknown.length
        ? `후속 처리 원장을 못 읽는 데이터셋 ${unknown.join('·')} — 그 유실은 이 판정에 없다(0건이 아니다)`
        : null;
      /* `unknown` 은 아래 필터와 같은 축(유한수가 아니면 모름)이라야 한다 — 갈리면 note 가
       * 침묵하는 사이 그 세션이 판정에서 빠진다 */
    },
    run: (f) => {
      const date = f.minute?.date ?? '';
      /* 축은 **데이터셋의 성질**이라 데이터셋 집합으로 온다 — 세션마다 실려 오면 같은
       * 데이터셋의 두 세션이 다른 축을 갖는 상태가 표현 가능해지고, 그게 안 난다는 보증이
       * 어댑터의 습관에 걸린다(`MinuteFacts.deadJobsByDate`). */
      /* `canRun` 이 배열임을 이미 확인했다 — 여기서 `?? []` 로 접으면 그 판정이 무의미해진다 */
      const byDateAxis = new Set(f.minute!.deadJobsByDate);
      const byDate = new Set<string>();
      return (
        (f.minute?.sessions ?? [])
          /* 임계 1건 — DEAD 는 재시도가 끝난 **종료 상태**다. 누적치가 아니라 유실 확정이라
           * "몇 건부터"를 둘 근거가 없다. P1 인 이유: 수집 자체는 살아 있고 재투입으로 복구 가능하다. */
          /* `null` 은 0이 아니라 **모름**이다 — 어느 원장을 읽어야 할지 모르는 데이터셋을
           * "봤고 괜찮다"로 접지 않는다. 판정 대상에서 빠질 뿐이다(R17·R18 은 그대로 돈다). */
          .filter((s) => Number.isFinite(s.deadJobs) && (s.deadJobs as number) >= 1)
          .flatMap((s) => {
            /* ⚠️ **값의 입도가 사건의 입도를 정한다.** 뉴스 job 은 세션 연결 컬럼이 없어
             * `(dataset, date)` 집계 하나뿐이다(어댑터가 `deadJobsByDate` 로 밝힌다). 그걸
             * 세션마다 내면 벤더 둘인 날 같은 3건이 두 사건으로 서서 6건으로 읽히고, 어느
             * 벤더 소관인지 근거도 없다 — 모르는 것을 벤더별로 복제하는 셈이다. */
            if (byDateAxis.has(s.dataset)) {
              if (byDate.has(s.dataset)) return [];
              byDate.add(s.dataset);
              return [
                {
                  target: s.dataset,
                  targetId: s.dataset,
                  scope: date,
                  title: `${s.dataset} 후속 처리 유실`,
                  metric: s.deadJobs,
                  unit: '건',
                  /* "못 가른다"가 아니라 **지금 응답이 안 가른다**. 원장에는 축이 있다
                   * (`news_extraction_job.source_code`) — 조회가 날짜 창만 걸고 GROUP BY 를
                   * 안 한다. 불가능으로 못박으면 아무도 그 쿼리를 고치지 않는다. */
                  why: '재시도가 끝난 종료 상태 실패다 — 재투입 전까지 그만큼이 유실이다. 이 수는 지금 응답이 날짜 축으로만 주어 벤더로 갈리지 않는다',
                  evidence: `후속 처리 작업 원장 ${date} dead (데이터셋·날짜 집계)`,
                  drill: ['dataset', 'ds-' + s.dataset] as [string, string],
                },
              ];
            }
            return [
              {
                target: sessionLabel(s),
                targetId: sessionTarget(s),
                scope: date,
                title: `${sessionLabel(s)} 후속 처리 유실`,
                metric: s.deadJobs,
                unit: '건',
                why: '재시도가 끝난 종료 상태 실패다 — 재투입 전까지 그만큼이 유실이다',
                evidence: `후속 처리 작업 원장 ${date} dead`,
                drill: ['dataset', 'ds-' + s.dataset] as [string, string],
              },
            ];
          })
      );
    },
  },
];

/* 인과 간선 — 어떤 위반이 어떤 위반의 결과인가. 카드 = 위반이 아니라 사건.
 *
 * 의도적으로 긋지 않은 간선(명세 §3): R07 수급 결손 → R03 투영 지연.
 * 같은 런에서 났다는 것은 인과가 아니다 — 간선은 메커니즘이 있는 곳에만 긋는다.
 *
 * R17(지금 끊김) → R18(무증거 창)·R19(DEAD) 도 긋지 않는다. 메커니즘은 그럴듯하지만 **시간 축이
 * 다르다** — R18·R19 는 그날 누적이고 R17 은 지금 시점의 사실이다. 응답에 해소 시각이 없어
 * (decisions §3-1) 이미 복구된 과거 공백과 지금 막힌 것을 가를 수 없다. 지금 끊김이 그날 창
 * 전부의 원인이라고 말할 근거가 없으므로 흡수하지 않는다. */
export const EDGES: Edge[] = [
  { c: 'R10', p: 'R11', when: (c) => c.src === 'intraday', why: '소비자가 없어 체인 진입 자체가 없었다' },
  { c: 'R10', p: 'R15', when: (c) => c.src === 'batch', why: '체인 감소분이 곧 분석 실패 ETF 수다' },
  { c: 'R05', p: 'R04', when: (c, p) => c.runId === p.targetId, why: '런이 죽어 작업이 귀결되지 못했다' },
  { c: 'R06', p: 'R04', when: (c, p) => c.runId === p.targetId, why: '같은 런의 유실' },
  { c: 'R16', p: 'R04', when: (c, p) => c.runId === p.targetId, why: '런 타임아웃 안에서 시도가 소진됐다' },
  {
    c: 'R05',
    p: 'R05',
    when: (c, p) => !!c.cause && !p.cause && c.runId === p.runId,
    why: '상류 실패로 미실행',
  },
  { c: 'R02', p: 'R03', when: (c, p) => c.targetId === p.targetId, why: '원장 투영이 안 돼 마감 판정이 열려 있다' },
];
