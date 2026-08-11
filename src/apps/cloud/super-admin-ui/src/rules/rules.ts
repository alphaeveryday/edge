/* 규칙 R01~R19 + 인과 간선 7개 (ALPHA-738).
 *
 * ⚠️ 명세 원문(`edge-console-rules.md`)은 **이 레포에 없다** — 레퍼런스 HTML 에서 추출한 것이
 * 출처다(경위는 README "명세와 다르게 구현한 지점" 1). 주석의 `§2`·`§3` 인용은 그 추출본 기준이라
 * 새 체크아웃에서 대조할 원문이 없다.
 *
 * R17~R19 는 그 이후에 붙인 실시간(1분) 레인 규칙이다 — 그 절 아래 주석에 임계값 근거를 적었다.
 *
 * 규칙은 선언 데이터(id/layer/name/desc/kls/base/dep/source) + 조건 함수(run)다.
 * 이 배열이 규칙 목록의 정본이다 — 화면의 "규칙 실행 결과" 바도 여기서 자동 생성될 예정이지만
 * **그 화면은 아직 없다**(뒤 조각). 지금 이 배열을 읽는 것은 `evaluate`·CLI·테스트뿐이다.
 *
 * 시각 비교는 ctx.now 를 쓴다(벽시계 직접 참조 금지) — 스냅샷 평가가 재현 가능해야 한다.
 */
import type {
  DatasetFact,
  Edge,
  Facts,
  MinuteSessionFact,
  OutputFact,
  Rule,
  RunFact,
  TaskFact,
} from './types.ts';

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
 * **음수도 아니다** — 산출 다섯은 전부 count 라 음수가 성립하지 않는다. 유한성만 검사하면
 * `base:-100·today:-100` 이 `평가됨 · 위반 0`("평소와 같다")으로 인증된다.
 *
 * ⚠️ **`base: 0` 은 "표본이 없다"가 아니다.** 서버 `median()` 은 표본이 있고 값이 전부 0이면
 * `0.0` 을 준다 — 관측된 기준이다. 비율 판정이 성립하지 않아 판정에서 빠질 뿐이므로, 사유를
 * "표본 없음"이라 쓰면 관측을 미관측으로 위조한다. `note` 가 둘을 갈라 적는다.
 */
const hasBase = (o: OutputFact): boolean => Number.isFinite(o.base) && (o.base as number) > 0;

/**
 * R13 이 **실제로 판정할 수 있는** 산출인가 — 기준과 오늘 값이 둘 다 수여야 한다.
 *
 * `canRun` 이 기준만 보면, 기준은 있는데 오늘 값이 수가 아닌 산출뿐인 응답이 `평가됨 · 위반 0`
 * ("전부 분포 안")으로 선다 — 하나도 판정 못 했는데. R19 가 "원장을 하나도 못 읽으면 못 돎"인 것과
 * 같은 형태다. `canRun`·`run()`·`note` 세 곳이 이 한 술어를 공유한다.
 *
 * `today` 도 **셈으로 성립해야** 한다(음수 count 는 손상이지 관측이 아니다) — `base:100·today:-1`
 * 을 그냥 재면 −101% 라는 없는 편차가 P1 로 선다.
 */
/**
 * R08 이 **실제로 신선도를 가릴 수 있는** 데이터셋인가 — `canRun`·`run()` 이 같은 술어를 본다.
 *
 * 창 계약은 as-of 비교 대상이 아니고, 기대일이 없으면 비교 자체가 성립하지 않는다.
 *
 * ⚠️ `window_contract` 는 실 응답에 없어 이 배제항은 실 API 에서 무력하다 — 다만 원장에 창 계약이
 * 하나도 없어(계약은 `ETF_HOLDINGS_KRX_EOD` 하나, as-of 계약이다) 지금은 무해하다. 첫 항의
 * `contract` 가 먼저 걸러 주기 때문이기도 하다. `DatasetFact.window_contract` 주석을 보라.
 */
const freshnessJudgeable = (d: DatasetFact): boolean =>
  !!d.contract && !!d.actual_as_of && !!d.expected_as_of && !d.window_contract;

const judgeable = (o: OutputFact): boolean =>
  hasBase(o) && Number.isFinite(o.today) && o.today >= 0;

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

/**
 * 레인을 못 읽었을 때 쓰는 표기. **"계측 없음"이 아니다** — 원장은 `scope_key` 를 기록하고 있고,
 * 그 키가 Planner 의 형식과 갈려 서버가 파싱을 못 한 것이다(서버는 그때 경고를 남긴다).
 * 계측 부재로 그리면 형상 불일치라는 장애 단서가 "아직 안 만든 기능"으로 읽힌다(리뷰 지적).
 */
export const LANE_UNKNOWN = '레인 미상';

/**
 * 문장에 쓰는 레인 이름. **`null` 을 그대로 끼우면 `"null"` 이 렌더된다** — 템플릿 리터럴은
 * 컴파일러가 안 잡는 자리라 표기를 한 곳에 둔다. 레인이 없는 것은 런 행이 없는 계획 슬롯의
 * 키를 못 읽었다는 뜻이고, 지어내지 않는 것이 서버의 판단이다.
 */
export const laneOf = (r: RunFact) => r.lane ?? LANE_UNKNOWN;

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
 * 데이터셋만으로는 안 갈린다 — `price_minute` 는 소스가 `toss`·`kis` 둘이다(파이프라인의
 * `SOURCE_GROUPS_BY_DATASET` 이 정본. `news_minute` 는 지금 `bigkinds` 하나뿐이라 예가 못 된다).
 * 날짜를 여기 넣으면 런북 키 `${rule}.${targetId}` 가 매일 달라져 어떤 조치도 등록할 수 없다 — 시점은 `scope` 다.
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
          why: `${laneOf(r)} ${r.trading_date ?? '거래일 미상'} 슬롯에 ops_pipeline_run 행이 없다`,
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
     * 불리언으로 표현할 수 없는 축이라 **응답에 없는 축**(관측한 런 수)이 답해야 한다 — 계약에
     * 그런 필드는 아직 없고, 그때까지는 아래 `note` 가 몇 런을 봤는지 밝히는 것이 전부다.
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
    /* 원장·제어면 **둘 다** 읽는다(아래 분기가 AWS 단독 실패도 잡는다) — `DB_LEDGER` 라고만
     * 적으면 원장이 빈 채로 AWS 만 실패한 위반의 출처가 거짓이 된다. R03 과 같은 축이다. */
    source: 'AWS_CONTROL+DB_LEDGER',
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
          why: `${laneOf(r)} ${r.id.split('T')[1] || ''} 슬롯 · ${runKind(r)}`,
          /* 근거는 **어느 표면이 실패를 말했는가**로 갈린다 — 원장이 빈 위반에
           * `orchestration_status` 를 근거로 대면 운영자가 빈 컬럼을 보러 간다. */
          evidence: r.ledger_status
            ? 'ops_pipeline_run.orchestration_status'
            : 'stepfunctions 최종 상태 (원장은 비어 있다)',
          drill: ['run', 'run-' + r.id] as [string, string],
        })),
  },

  {
    id: 'R05',
    layer: '작업',
    name: '필수 작업 미귀결',
    kls: '고장',
    base: 'P0',
    /* 'required ∧ DUE' 라고 쓰던 문장이다. 필터는 **DUE 를 요구하지 않고 SKIPPED 만 뺀다** —
     * 그게 의도다(모름은 배제 근거가 아니다: `plan_status` 를 모른다고 필수 작업의 미귀결을
     * 버리면 P0 거짓 음성이다. R04 의 `kind` 와 같은 판단). 문장을 코드에 맞춘다. */
    desc: 'required 이고 계획에서 빠지지(SKIPPED) 않았는데 FULFILLED가 아니다',
    dep: null,
    source: 'DB_LEDGER',
    /* 배제는 **아는 것으로만** 한다 — 그러니 모르는 것이 몇 건인지는 밝혀야 한다. 안 밝히면
     * plan 축이 통째로 빠진 응답에서 전 작업이 'DUE 였다'는 가정 위에 판정된 줄 아무도 모른다. */
    note: (f) => {
      const KNOWN = ['DUE', 'SKIPPED'];
      const unknown = f.tasks.filter((t) => t.required && !KNOWN.includes(t.plan_status ?? ''));
      return unknown.length
        ? `계획 상태를 모르는 필수 작업 ${unknown.length}/${f.tasks.filter((t) => t.required).length} — 계획에서 빠진 것인지 못 가른 채 판정했다`
        : null;
    },
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
    /* 판정은 `data_status` 가 내린다 — **건수는 그 판정을 가르는 축이 아니다.**
     * `&& t.failed_records` 로 조이던 때가 있었는데, 그러면 스텝이 스스로 INCOMPLETE 를 선언했지만
     * 건수를 안 남긴 작업(계약상 `failedRecords` 는 nullable 이고 `data_status` 와의 결합 제약이
     * 없다)이 **위반 0건**으로 접힌다 — 유실을 선언한 원장이 "봤고 괜찮다"가 되는 자리다.
     * 건수가 없으면 위반을 버리는 게 아니라 `metric` 을 비우고 판정을 `state` 로 낸다(필드 규약). */
    run: (f) =>
      f.tasks
        .filter((t) => ['INCOMPLETE', 'INVALID'].includes(t.data_status ?? ''))
        .map((t) => {
          /* ⚠️ **`failed_records: 0` 은 "0건 유실"이 아니다.** 생산자(`derive_data_status`)는
           * `received < expected` 만으로도 INCOMPLETE 를 내므로, 이 카운터가 0인데 상태가
           * INCOMPLETE 인 작업이 정상적으로 존재한다 — 유실은 완전성 축에 있고 R07 이 센다.
           * 그걸 `metric: 0 · unit: '건'` 으로 그리면 "유실 0건인 부분 유실"이라는 자기모순이
           * 표에 선다. 규모로 쓸 수 있는 것은 **양수일 때뿐**이고, 나머지는 판정만 낸다.
           * (그렇다고 위반을 버리지는 않는다 — 그게 이 규칙이 조용했던 원래 결함이다.) */
          const n = t.failed_records;
          const lost = Number.isFinite(n) && (n as number) > 0 ? (n as number) : null;
          return {
            target: t.task_key,
            title: t.task_key,
            metric: lost,
            ...(lost != null ? { unit: '건' } : { state: t.data_status as string }),
            why:
              lost != null
                ? 'ops.failed_records — 스텝의 유실 판정값이며 skipped_*를 직접 더한 값이 아니다. 무엇 1건인지는 잡마다 다르다'
                : n === 0
                  ? `${t.data_status} 인데 failed_records 는 0이다 — 이 카운터 밖(완전성 결손 등)에서 온 판정이라 규모는 R07 이 센다`
                  : n == null
                    ? `${t.data_status} 인데 failed_records 가 없다 — 스텝이 유실을 선언했으나 규모를 안 남겼다`
                    : `${t.data_status} 인데 failed_records 가 셈으로 성립하지 않는다(${String(n)}) — 원장 writer 결함이라 규모를 못 쓴다`,
            runId: t.run_id,
            evidence: 'ops_expected_task.data_status + failed_records',
            drill: ['run', 'task-' + t.task_key] as [string, string],
          };
        }),
  },

  {
    id: 'R07',
    layer: '작업',
    name: '완전성 결손',
    kls: '결손',
    base: 'P0',
    desc: '분모가 있는 작업에서 received < expected',
    /* 배선 규모를 여기 적지 않는다 — 수를 산문에 박으면 원장이 늘 때마다 낡는다(실제로 낡았다).
     * 현재 값은 `note` 가 사실에서 세어 낸다. */
    dep: '완전성 분모 배선(일부 수집 작업만)',
    source: 'DB_LEDGER',
    /* expected 가 null 인 작업은 위반이 아니라 평가 대상 아님 */
    note: (f) => {
      /* `!= null` 이 아니라 `Number.isFinite` 다 — 검증 안 된 JSON 의 `"100"` 은 `!= null` 을
       * 통과하고 비교·뺄셈에서 100 으로 강제 변환돼 **실측 결손처럼** 보고된다. */
      const wired = f.tasks.filter((t) => Number.isFinite(t.completeness_expected));
      /* 분모는 배선됐는데 **분자가 없는** 작업 — 판정에서 빠지지만 "결손 0"이 아니다.
       * 안 밝히면 그 작업의 결손이 위반 0건에 흡수돼 보인다(R03·R10·R13·R19 와 같은 형태). */
      const blind = wired.filter((t) => !Number.isFinite(t.completeness_received)).map((t) => t.task_key);
      return (
        `분모 배선 작업 ${wired.length}/${f.tasks.length} — 나머지는 조건 자체가 평가 대상 아님` +
        (blind.length ? ` · 분자가 없어 판정에서 빠진 작업 ${blind.join('·')}` : '')
      );
    },
    mockBacked: (f) => f.tasks.some((t) => t.completeness_expected != null && t.cmpl_mock),
    run: (f) =>
      f.tasks
        /* `received ?? 0` 이었다 — **`null` 은 0이 아니라 집계 없음**이다(계약 §부재를 싣는 규약).
         * 0으로 접으면 분모가 100 인 정상 작업이 "100건 결손" P0 로 서고, 그 수는 실측처럼 보인다.
         * 이 모듈이 없애려는 칸 혼동 그 자체라 판정에서 빼고 `note` 가 밝힌다. */
        .filter(
          (t) =>
            Number.isFinite(t.completeness_expected) &&
            Number.isFinite(t.completeness_received) &&
            (t.completeness_received as number) < (t.completeness_expected as number),
        )
        .map((t) => ({
          target: t.task_key,
          title: t.task_key,
          metric: (t.completeness_expected as number) - (t.completeness_received as number),
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
    /* `contract && actual_as_of != null` 만 보던 자리다 — `run()` 은 거기에 `expected_as_of` 와
     * 창 계약 배제까지 요구하므로 **둘이 갈렸다**. 갈리면 판정 가능한 데이터셋이 하나도 없는
     * 응답(창 계약뿐이거나 기대일이 없는)이 `평가됨 · 위반 0`("전부 신선하다")으로 선다 —
     * 이 파일이 R13 에서 두 번 겪고 `judgeable` 로 묶은 바로 그 갈림이다. 술어를 하나로 둔다. */
    canRun: (f) => f.datasets.some(freshnessJudgeable),
    mockBacked: (f) => f.datasets.some((d) => d.actual_as_of != null && d.mock),
    run: (f) =>
      f.datasets
        .filter(
          (d) =>
            freshnessJudgeable(d) &&
            (d.actual_as_of as string) < (d.expected_as_of as string),
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
      const stages = f.chain?.stages ?? [];
      (['batch', 'intraday'] as const).forEach((src) => {
        /* ⚠️ **피드 값에도 단계와 같은 술어를 건다.** `chainPoints`(canRun)는 유한수만 점으로
         * 세는데 여기서 그냥 실으면 손상된 피드(검증 안 된 JSON 의 `"20"`·`Infinity`)가 `prev` 가
         * 되고, 아래 `v < prev` 가 강제 변환·무한 비교로 **거짓 P0 체인 손실**을 낸다
         * (`metric` 이 `Infinity` 인 위반까지 나간다). 단계 쪽만 막고 여기를 빠뜨렸던 자리다 —
         * `compose` 가드 때와 같은 "한곳만 고침"이다. */
        const feed = src === 'batch' ? f.chain?.feeds[0]?.v : f.chain?.feeds[1]?.v;
        let prev: number | null | undefined = Number.isFinite(feed) ? feed : null;
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
    /* `canRun` 은 **규칙 단위**라 큐 하나만 매핑돼도 참이다 — 나머지 큐는 선언을 못 본 것인데
     * 판정에서는 그냥 섞인다. 몇 큐를 실제로 봤는지 여기서 밝힌다(R03·R10 과 같은 형태). */
    note: (f) => {
      const qs = f.queues ?? [];
      const seen = qs.filter((q) => q.subscribers != null).length;
      return seen === qs.length
        ? null
        : `구독 매핑을 가진 큐 ${seen}/${qs.length} — 나머지는 판정하지 않았다(위반 0건이 "소비자가 있다"는 뜻이 아니다)`;
    },
    run: (f) =>
      (f.queues ?? [])
        /* `(q.subscribers ?? []).length === 0` 이었다 — **필드 부재를 빈 목록으로 접으면
         * "아무도 안 셌다"가 "구독자가 없다"라는 P0 단정이 된다.** 매핑이 일부 큐에만 붙은 응답
         * (계약상 정상 형상)에서 미계측 큐가 전부 '소비자 부재'로 섰다. 선언을 가진 큐만 판정하고
         * 나머지는 `note` 가 밝힌다. */
        .filter(
          (q) =>
            q.visible > 0 && q.in_flight === 0 && q.subscribers != null && q.subscribers.length === 0,
        )
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
    /* `null` 이었다 — `canRun` 이 있는데 사유가 비면 못 돈 규칙이 **왜** 못 돌았는지 아무 데도
     * 없다(`evaluate` 는 못 돈 규칙의 `note` 를 `dep` 하나로만 채운다). 큐 축은 계약에 아예
     * 없어 dev 에서 매일 못 도는 규칙이라 그 침묵이 상시였다. R11 의 사유(구독 매핑)와 다른
     * 축이다 — 저건 필드가 없는 것이고 이건 축 자체가 안 온다. */
    dep: 'SQS 큐 지표 조회 배선(queues[] — 계약에 이 축이 없다)',
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
    /* '직전 10영업일' 이라 못 박지 않는다 — 서버 `baseDays()` 는 기준일 후보를 최대 10개로
     * 자를 뿐이라 신규 환경·이른 과거 조회에서는 1~9개로도 중앙값이 나온다. */
    desc: '오늘 값이 최근 기준일들의 중앙값에서 ±25% 이상 벗어났다',
    /* **미배선이 아니다** — 서버는 `outputs[].base` 를 보내고, `null` 은 **관측된 부재**다
     * (계약 §기준 표본). 전에 여기 '…계열 배선' 이라고 적혀 있었는데, 그러면 표본이 아직 없는
     * 신규 환경에서 R13 이 "아직 안 만든 기능"이라고 말한다 — 장애·공백을 미배선으로 읽는
     * 오독이고 이 모듈이 없애려는 것 그 자체다.
     * ⚠️ **그 부재를 "표본이 없다"로 좁혀 읽지 마라**(ALPHA-946) — 사유가 셋이고 와이어는 하나로
     * 온다(`OutputFact.base` 주석이 정본). 여기 그렇게 적혀 있었고 `note` 가 그걸 그대로 베꼈다. */
    /* ⚠️ 이 문장은 **`canRun` 이 거짓인 모든 경우에 참**이어야 한다 — `evaluate` 는 못 돈 규칙의
     * `note` 를 안 부르므로(`R.note` 는 돈 규칙 전용) 아래 세 갈래 사유는 그때 안 보인다.
     * '표본이 없다'로만 적었더니 전 산출이 `base:0`(관측된 0)인 응답에서 거짓말이 됐다.
     *
     * ⚠️ **오늘 화면의 기본 사유가 여기로 왔다**(ALPHA-946). 서버가 조회일이 KST 오늘이면 다섯
     * 전부의 `base` 를 안 보내므로(그 날의 **적재 창이 아직 안 지났다** — 지난 날의 값도 소급
     * 적재·정정으로 움직이므로 "완결"이 아니다) 당일 조회는 늘 이 문장을 띄운다. **원인을 '표본이 없다'로만 적으면 안 된다** — 원장에 표본은 있고
     * 그 날이 안 끝났을 뿐이라, 운영자가 없는 결손을 찾아 나선다. 갈래를 여기서 못 가르는
     * 이유는 와이어가 `base: null` 하나로 오기 때문이고, **가르려고 이 층에서 날짜를 다시
     * 재면 판정 주체가 둘이 된다**(서버와 갈리는 날 어느 쪽도 안 고쳐진다). 그래서 갈래를
     * **나열만** 한다 — 어느 하나를 지목하면 나머지 형상에서 거짓말이 된다(아래 테스트가 그
     * 방향을 못 박는다: "기준이 멀쩡한데 기준 탓으로 적었다"). */
    dep: '편차를 판정할 수 있는 산출이 하나도 없다'
      + '(산출이 없거나, 그 날이 아직 안 끝났거나, 기준·오늘 값이 셈으로 성립하지 않는다)',
    source: 'DB_LEDGER',
    /* 기준이 있는 산출이 하나도 없으면 "분포 안"이 아니라 **분포를 모른다**. `run()` 과 같은
     * 술어를 쓴다 — base 0 은 나눗셈이 성립하지 않아 양쪽 모두 평가 대상이 아니다. */
    canRun: (f) => (f.outputs ?? []).some(judgeable),
    /* 기준이 있는 산출이 하나라도 있으면 규칙은 돈다 — 기준 없는 산출은 판정에서 그냥 빠진다.
     * 그 사실을 안 밝히면 "분포 밖 0건"이 전 산출을 봤다는 뜻으로 읽힌다(R03·R10·R19 와 같은 형태). */
    note: (f) => {
      /* 빠지는 이유는 둘이다 — 기준이 없거나(`base`), 오늘 값이 수가 아니거나(`today`).
       * 한쪽만 세면 나머지 침묵이 남는다: `today: NaN` 은 비교가 언제나 거짓이라 위반도 안 되고
       * 이 문장에도 안 나와, 그 산출을 **봤다고 착각**하게 된다. */
      /* 사유가 여럿이고 뜻이 다르다 — 접으면 관측된 0 이 "아무도 안 셌다"로 읽힌다.
       *
       * ⚠️ **분류를 `judgeable` 의 여집합에서 뽑는다.** 갈래를 따로 나열했더니 새 가드
       * (`base > 0`·`today >= 0`)를 더한 순간 **어느 갈래에도 안 걸리는 산출**이 생겼고, 판정에서
       * 빠졌는데 note 에도 안 나와 "봤다"고 착각하게 됐다. 여집합에서 뽑으면 그 구멍이 구조적으로
       * 안 생긴다 — 빠진 산출은 반드시 어느 사유엔가 담긴다(`reason` 이 전건을 덮는다). */
      const reason = (o: OutputFact): string => {
        const b = o.base;
        /* ⚠️ **`null` 을 "표본 없음"이라 단정하지 않는다**(ALPHA-946). 사유가 셋인데 와이어는
         * 하나로 온다: 표본 부재 · 장 안 서는 날의 장 산출 · 그 날이 안 끝남
         * (`OutputFact.base` 주석이 정본). 전에 '기준 표본 없음' 이라고 적었는데, **지난 주말을
         * 조회하면** 서버가 뉴스 셋에는 기준을 주고 `o.pub`·`o.trig` 만 비우므로 이 규칙이 돌면서
         * 그 둘에 거짓 사유를 단다 — 원장에 표본은 멀쩡히 있다. 어느 하나를 지목하려면 이 층이
         * 날짜·달력을 다시 재야 하고, 그러면 판정 주체가 서버와 둘이 된다(`dep` 과 같은 판단). */
        if (b == null) return '기준이 없다(표본 부재·장 안 서는 날·그 날이 안 끝남 중 하나)';
        /* 값이 왔는데 수가 아닌 것은 **표본 부재가 아니라 계약 손상**이다(`base: "100"` 같은 것).
         * 응답은 런타임 검증을 안 거치고 오므로 타입 선언이 못 막는다 — 접으면 writer 결함이
         * "아직 표본이 없구나"로 읽혀 아무도 안 고친다. */
        if (!Number.isFinite(b)) return `기준이 수가 아니다(${typeof b}) — 표본 부재가 아니라 계약 손상`;
        if (b === 0) return '평소가 0이라 비율이 안 선다(관측된 0이지 미관측이 아니다)';
        if ((b as number) < 0) return '기준이 음수라 셈으로 성립하지 않는다';
        if (!Number.isFinite(o.today)) return '오늘 값이 수가 아니다';
        if (o.today < 0) return '오늘 값이 음수라 셈으로 성립하지 않는다';
        return '판정 대상이 아니다';
      };
      const byReason = new Map<string, string[]>();
      for (const o of (f.outputs ?? []).filter((o) => !judgeable(o))) {
        byReason.set(reason(o), [...(byReason.get(reason(o)) ?? []), o.label]);
      }
      if (!byReason.size) return null;
      return [...byReason]
        .map(([why, labels]) => `판정에서 빠진 산출 ${labels.join('·')} — ${why}`)
        .join(' · ');
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
          /* '직전 10거래일' 이라 쓰면 서버가 실제로 세는 것보다 강한 주장이다 — 표본은
           * `(런이 있던 날 ∪ 계획 결손일) − 장 안 서는 날` 의 최근 10개라, 평일 휴장에 Planner 까지
           * 실패한 날은 거래일로 오인돼 섞인다(계약 문서가 남은 불확실로 적어 둔 것). */
          evidence: '최근 기준일(최대 10개) 중앙값 — 서버 표본 규칙',
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
           * 빈다. `seed_note` 는 **이 수가 로컬 시드 유래라는 사실의 유일한 신호**다. */
          /* 🔴 **이 값은 합계다.** `seed_note` 는 그중 일부를 설명하는 문장일 뿐이고 "전량 시드"라는
           * 불변식은 타입에도 계약에도 없다 — 그래서 시드로 **덮지 않는다**.
           *
           * 세 번에 걸쳐 좁혔다(리뷰 1~3라운드): ① `seed: true`·`sev: 'P2'` 무조건 → 실 응답의
           * 진짜 P0 가 강등됐다. ② `seed_note` 있으면 강등 → 시드 1건 + 진짜 누락 1건인 합계 2 를
           * 통째로 내렸다. ③ `seed` 표시만 남김 → 그것도 합계 전체의 **출처**를 SEED 로 바꿔
           * (`buildReport` 의 `source`) 운영자가 실제 누락까지 "운영 데이터가 아니다"로 읽는다.
           *
           * 남은 형태: 판정 사유는 규칙이 말하고, 기록이 있으면 **덧붙인다**. 사실이 못 가르는
           * 것을 규칙이 가르는 척하지 않는다. */
          why:
            '발번됐는데 현재 비게시이고 무효화 통지도 안 갔다 — 테넌트가 철회를 못 받은 상태다' +
            (b.seed_note ? ` · 기록: ${b.seed_note}` : ''),
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
    /* `source: 'MOCK'` 이었다 — 규칙의 `source` 는 **정적**이라, 실 원장이 붙은 뒤에도 리포트가
     * `mock:false` 와 `source:'MOCK'` 을 동시에 낸다(모순). 목 여부는 사실이 `etf_ledger.mock` 로
     * 말하고 위반의 `mock` 이 나르므로, 여기는 축의 성격만 적는다. */
    source: 'DB_LEDGER',
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
              evidence: `ETF별 분석 원장${f.etf_ledger?.mock ? '(목)' : ''}`,
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
    /* 판정할 원장을 **하나도** 못 읽으면 못 돎이다. 두 축을 다 센다 — 날짜 축(데이터셋 맵)과
     * 세션 축(그 맵에 없는 데이터셋의 세션). 세션도 맵도 비었으면 그건 실측이다(그날 실시간
     * 레인이 없었고 잃을 후속 작업도 없다) — 못 돎으로 세면 안 도는 날마다 거짓 경보가 뜬다. */
    canRun: (f) => {
      const m = f.minute;
      if (!m) return false;
      /* 🔴 축 선언(맵) 자체가 없으면 못 돈다 — 응답은 런타임 검증을 안 거치고 오고, 빠진 것을
       * 빈 맵으로 접으면 날짜 축 데이터셋이 조용히 세션 축으로 재분류된다(벤더 수만큼 복제). */
      if (!m.deadJobsByDataset || typeof m.deadJobsByDataset !== 'object') return false;
      const by = m.deadJobsByDataset;
      if (m.sessions.length === 0 && Object.keys(by).length === 0) return true;
      return (
        Object.values(by).some((v) => Number.isFinite(v)) ||
        m.sessions.some((x) => !(x.dataset in by) && Number.isFinite(x.deadJobs))
      );
    },
    axis: 'minute',
    /* 일부만 모르는 날은 규칙 단위 `못 돎` 으로 표현할 수 없다 — 어느 데이터셋이 판정에서
     * 빠졌는지 밝힌다. 안 밝히면 그 데이터셋의 유실이 "0건"에 흡수돼 보인다. */
    note: (f) => {
      const m = f.minute;
      if (!m) return null;
      const by = m.deadJobsByDataset ?? {};
      const unknown = new Set<string>();
      for (const [d, v] of Object.entries(by)) if (!Number.isFinite(v)) unknown.add(d);
      for (const x of m.sessions) {
        if (!(x.dataset in by) && !Number.isFinite(x.deadJobs)) unknown.add(x.dataset);
      }
      return unknown.size
        ? `후속 처리 원장을 못 읽는 데이터셋 ${[...unknown].join('·')} — 그 유실은 이 판정에 없다(0건이 아니다)`
        : null;
    },
    run: (f) => {
      const date = f.minute?.date ?? '';
      const by = f.minute?.deadJobsByDataset ?? {};
      const out: ReturnType<Rule['run']> = [];
      /* ⚠️ **값의 입도가 사건의 입도를 정한다.** 뉴스 job 은 세션 연결 컬럼이 없어
       * `(dataset, date)` 집계 하나뿐이다. 그걸 세션마다 내면 벤더 둘인 날 같은 3건이 두
       * 사건으로 서서 6건으로 읽히고, 어느 벤더 소관인지 근거도 없다.
       * 그리고 **세션을 순회하지 않는다** — 그날 그 데이터셋의 세션이 없어도(아침 planner 전 ·
       * 비거래일 · 뉴스 계획만 실패한 날) 유실은 있다. 순회로 읽으면 하필 그날 조용해진다. */
      for (const [dataset, dead] of Object.entries(by)) {
        if (!Number.isFinite(dead) || (dead as number) < 1) continue;
        out.push({
          target: dataset,
          targetId: dataset,
          scope: date,
          title: `${dataset} 후속 처리 유실`,
          metric: dead as number,
          unit: '건',
          /* "못 가른다"가 아니라 **지금 응답이 안 가른다**. 원장에는 축이 있다
           * (`news_extraction_job.source_code`) — 조회가 날짜 창만 걸고 GROUP BY 를 안 한다.
           * 불가능으로 못박으면 아무도 그 쿼리를 고치지 않는다. */
          why: '재시도가 끝난 종료 상태 실패다 — 재투입 전까지 그만큼이 유실이다. 이 수는 지금 응답이 날짜 축으로만 주어 벤더로 갈리지 않는다',
          evidence: `후속 처리 작업 원장 ${date} dead (데이터셋·날짜 집계)`,
          drill: ['dataset', 'ds-' + dataset] as [string, string],
        });
      }
      /* 세션 축 — 날짜 축으로 이미 낸 데이터셋은 건너뛴다(같은 사실을 두 번 내지 않는다) */
      for (const s of f.minute?.sessions ?? []) {
        if (s.dataset in by) continue;
        /* `null`·수 아닌 값은 0이 아니라 **모름**이다 — 어느 원장을 읽어야 할지 모르는
         * 데이터셋을 "봤고 괜찮다"로 접지 않는다. 판정 대상에서 빠질 뿐이고 `note` 가 밝힌다. */
        if (!Number.isFinite(s.deadJobs) || (s.deadJobs as number) < 1) continue;
        out.push({
          target: sessionLabel(s),
          targetId: sessionTarget(s),
          scope: date,
          title: `${sessionLabel(s)} 후속 처리 유실`,
          metric: s.deadJobs as number,
          unit: '건',
          why: '재시도가 끝난 종료 상태 실패다 — 재투입 전까지 그만큼이 유실이다',
          evidence: `후속 처리 작업 원장 ${date} dead`,
          drill: ['dataset', 'ds-' + s.dataset] as [string, string],
        });
      }
      return out;
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
