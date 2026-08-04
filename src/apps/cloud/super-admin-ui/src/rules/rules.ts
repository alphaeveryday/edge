/* 규칙 R01~R16 + 인과 간선 7개 — 명세 edge-console-rules.md §2·§3 의 정식 구현 (ALPHA-738).
 *
 * 규칙은 선언 데이터(id/layer/name/desc/kls/base/dep/source) + 조건 함수(run)다.
 * 화면 하단 "규칙 실행 결과" 바는 이 배열에서 자동 생성된다 — 여기 없는 규칙은 화면에 없다.
 *
 * 시각 비교는 ctx.now 를 쓴다(벽시계 직접 참조 금지) — 스냅샷 평가가 재현 가능해야 한다.
 */
import type { Edge, Rule, TaskFact } from './types.ts';

/** 재시도 정책 상한 — 없으면 null.
 *
 * 원장은 정책 미선언을 `0` 으로 적는다(SFN Retry 블록이 0개라 상한이라는 개념 자체가 없다).
 * 이 `0` 을 상한 0회로 읽으면 두 가지가 동시에 틀린다 — 화면이 `1/0` 이라는 없는 분모를 그리고,
 * R16 이 "평가됨"이라 주장한다. 정책 없음과 상한 0회는 다르므로 여기서 한 번만 정규화한다. */
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
          metric: '미귀결',
          unit: r.kind === 'manual' ? '수동 런' : '정규 런',
          why: `마감 ${kst(r.deadline)} 경과, 원장 상태 없음 (AWS는 ${r.aws_status || '—'})`,
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
    dep: null,
    source: 'AWS_CONTROL+DB_LEDGER',
    run: (f) =>
      f.runs
        .filter((r) => r.aws_status && r.ledger_status && r.aws_status !== r.ledger_status)
        .map((r) => ({
          target: r.id,
          title: '두 표면이 다름',
          metric: `${r.aws_status} ≠ ${r.ledger_status}`,
          unit: 'AWS · 원장',
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
            ['FAILED', 'TIMED_OUT', 'ABORTED'].includes(r.ledger_status ?? '') ||
            /* 원장이 비고 AWS만 실패면 정규 런에 한해서만 — 수동·백필 런의 원장 공백은
             * 실패 단정 근거가 아니다(경계 케이스, 명세 §2 R04) */
            (!r.ledger_status &&
              ['FAILED', 'TIMED_OUT'].includes(r.aws_status ?? '') &&
              r.kind === 'scheduled'),
        )
        .map((r) => ({
          target: r.id,
          title: '런 ' + (r.ledger_status || r.aws_status),
          metric: (r.ledger_status || r.aws_status) as string,
          unit: r.lane,
          why: `${r.lane} ${r.id.split('T')[1] || ''} 슬롯`,
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
        .filter((t) => t.required && t.task_outcome !== 'FULFILLED')
        .map((t) => ({
          target: t.task_key,
          title: t.task_key,
          metric: t.task_outcome,
          unit: `${t.stage} · ${t.pipeline_type}`,
          why: t.task_outcome === 'FAILED' ? '실행됐으나 실패' : '상류 실패로 미실행(PENDING)',
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
          unit: '유실 단위(잡별 상이)',
          why: 'ops.failed_records — 스텝의 유실 판정값이며 skipped_*를 직접 더한 값이 아니다',
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
          unit: '엔티티 결손',
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
          metric: 'STALE',
          unit: `기대 ${d.expected_as_of} · 실제 ${d.actual_as_of}`,
          why: '분석이 오래된 스냅샷 위에서 돈다',
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
          metric: '판정 불가',
          unit: '신선도',
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
    dep: null,
    source: 'DB_LEDGER',
    run: (f) => {
      const out: ReturnType<Rule['run']> = [];
      const stages = f.chain.stages;
      (['batch', 'intraday'] as const).forEach((src) => {
        let prev: number | null | undefined =
          src === 'batch' ? f.chain.feeds[0]?.v : f.chain.feeds[1]?.v;
        let prevLabel = src === 'batch' ? '배치 트리거' : '장중 트리거';
        stages.forEach((s) => {
          if (s.blind) return; // 관측 불가 단계는 비교 축에서 제외 — 0 이 아니다
          const v = s[src];
          if (v == null) return;
          if (prev != null && v < prev) {
            out.push({
              target: `${src}:${s.id}`,
              title: `${prevLabel} → ${s.label}`,
              metric: prev - v,
              unit: '유실',
              why: `${prev} → ${v}`,
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
          unit: '메시지 대기',
          why: '발행은 성공, 소비 자체가 시작되지 않음 — 런타임 실패가 아니라 배선 부재',
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
    dep: null,
    source: 'DB_LEDGER',
    run: (f) =>
      f.outputs
        .filter((o) => o.base && Math.abs((o.today - o.base) / o.base) >= 0.25)
        .map((o) => ({
          target: o.id,
          title: o.label,
          metric: `${Math.round(((o.today - (o.base as number)) / (o.base as number)) * 100)}%`,
          unit: `${o.today.toLocaleString('ko-KR')} / 평소 ${(o.base as number).toLocaleString('ko-KR')} ${o.unit}`,
          why: '분포 밖 — 원인은 다른 규칙이 지목한다',
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
          target: 'pub_no_delivery',
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
          target: 'delivery_nonpub',
          title: '전달됐는데 현재 비게시',
          metric: b.delivery_now_nonpublished,
          unit: '건',
          why: b.seed_note ?? '',
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
              target: 'analyze.failed',
              title: 'ETF 분석 실패',
              metric: bad.length,
              unit: 'ETF 설명 미생성',
              why: bad[0].error ?? '',
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
          metric: `${t.attempts}/${retryCap(t)}`,
          unit: '시도 / 상한',
          why: '자동 회복 여지 없음 — 수동 개입 필요',
          mock: !!t.retry_mock,
          runId: t.run_id,
          evidence: 'ops_task_attempt 수 vs 정책 상한',
          drill: ['run', 'task-' + t.task_key] as [string, string],
        })),
  },
];

/* 인과 간선 — 어떤 위반이 어떤 위반의 결과인가. 카드 = 위반이 아니라 사건.
 *
 * 의도적으로 긋지 않은 간선(명세 §3): R07 수급 결손 → R03 투영 지연.
 * 같은 런에서 났다는 것은 인과가 아니다 — 간선은 메커니즘이 있는 곳에만 긋는다. */
export const EDGES: Edge[] = [
  { c: 'R10', p: 'R11', when: (c) => c.src === 'intraday', why: '소비자가 없어 체인 진입 자체가 없었다' },
  { c: 'R10', p: 'R15', when: (c) => c.src === 'batch', why: '체인 감소분이 곧 분석 실패 ETF 수다' },
  { c: 'R05', p: 'R04', when: (c, p) => c.runId === p.target, why: '런이 죽어 작업이 귀결되지 못했다' },
  { c: 'R06', p: 'R04', when: (c, p) => c.runId === p.target, why: '같은 런의 유실' },
  { c: 'R16', p: 'R04', when: (c, p) => c.runId === p.target, why: '런 타임아웃 안에서 시도가 소진됐다' },
  {
    c: 'R05',
    p: 'R05',
    when: (c, p) => !!c.cause && !p.cause && c.runId === p.runId,
    why: '상류 실패로 미실행',
  },
  { c: 'R02', p: 'R03', when: (c, p) => c.target === p.target, why: '원장 투영이 안 돼 마감 판정이 열려 있다' },
];
