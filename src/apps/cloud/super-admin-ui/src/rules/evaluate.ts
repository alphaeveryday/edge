/* 평가기 — 위반 수집 → 인과 병합(사건) → 정렬. 규칙·간선은 rules.ts, 사실은 호출자가 준다.
 *
 * 흡수된 위반은 지우지 않는다 — 사건 members 에 인과 문구와 함께 남는다(명세 §2-2).
 * 정렬: 심각도 → 연쇄 크기 → 대표 위반의 수치 (명세 §2-3).
 */
import { EDGES, RULES } from './rules.ts';
import type {
  Evaluation,
  Facts,
  Incident,
  RuleResult,
  RunbookEntry,
  Severity,
  Violation,
} from './types.ts';

const SEV: Record<Severity, number> = { P0: 0, P1: 1, P2: 2 };

/** 스냅샷 평가의 기준 시각 — 벽시계가 아니라 스냅샷 채취 시각(재현 가능해야 한다) */
export function snapshotNow(f: Facts): Date {
  return new Date(f.meta.db);
}

/**
 * 사건 식별자 — 위치가 아니라 **대상 + 시점 범위**가 정한다(`Violation.vid` 규약 참조).
 * 같은 `task_key` 가 여러 런에 걸리므로 범위를 실어야 갈린다.
 */
function vidOf(rule: string, targetId: string, scope?: string): string {
  return `${rule}:${targetId}` + (scope ? `@${scope}` : '');
}

/**
 * 런북 조회 — 대상 단위(`R05.LOAD_DOCUMENTS`) 우선, 없으면 규칙 단위 폴백.
 *
 * 키가 **`targetId` 만** 쓰는 것이 규약이다(시점 축 `scope` 는 안 들어간다) — 날짜가 섞이면
 * 키가 매일 달라져 어떤 조치도 등록할 수 없다. 화면이 이 공식을 따로 적으면 그 복제본이 낡으므로
 * 규칙 층에 한 벌만 둔다.
 */
export function runbookOf(f: Facts, v: Violation): RunbookEntry | undefined {
  return f.runbook[`${v.rule}.${v.targetId}`] ?? f.runbook[v.rule];
}

export function evaluate(f: Facts, now: Date = snapshotNow(f)): Evaluation {
  const violations: Violation[] = [];
  const ruleResults: RuleResult[] = [];
  const seen = new Set<string>();

  for (const R of RULES) {
    const evaluated = R.canRun ? R.canRun(f) : true;
    let count = 0;
    if (evaluated) {
      for (const raw of R.run(f, { now })) {
        /* 규약: 표시용 target 과 키용 targetId 를 여기서 한 번만 정규화한다 —
         * 소비자(런북 키·간선·조사 경로)가 각자 폴백을 쓰면 한 곳만 빠뜨려도 키가 갈린다 */
        const targetId = raw.targetId ?? raw.target;
        /* 범위 정규화도 `targetId` 와 같은 규약 — 엔진이 한 번만 한다. 배치는 런 키가
         * 범위를 겸하고, 런이 없는 실시간만 `scope`(세션 날짜)를 따로 싣는다. */
        const vid = vidOf(R.id, targetId, raw.scope ?? raw.runId);
        /* fail loud — 겹치면 딥링크가 조용히 다른 사건을 연다. 그게 위치 인덱스를 버린 이유이므로
         * 뒤에 온 위반을 버리거나 번호를 붙여 비키면 같은 결함을 다른 모양으로 되살린다.
         * 화면은 AdminLayout 의 ErrorBoundary 가 받는다(소비자 6곳 전부 그 경계 안이다). */
        if (seen.has(vid)) {
          throw new Error(
            `사건 식별자 충돌: ${vid} — ${R.id} 의 대상 축(targetId${raw.scope ?? raw.runId ? '·범위' : ''})이 ` +
              `이 위반들을 못 가른다. 규칙이 실어야 할 identity 축이 빠졌다.`,
          );
        }
        seen.add(vid);
        violations.push({
          ...raw,
          targetId,
          rule: R.id,
          ruleName: R.name,
          layer: R.layer,
          kls: raw.kls ?? R.kls,
          sev: raw.sev ?? R.base,
          dep: R.dep,
          vid,
        });
        count++;
      }
    }
    ruleResults.push({
      id: R.id,
      name: R.name,
      layer: R.layer,
      evaluated,
      violations: count,
      depends_on_mock:
        evaluated &&
        (violations.some((v) => v.rule === R.id && v.mock) || !!R.mockBacked?.(f)),
      note: R.note?.(f) ?? R.dep,
    });
  }

  // 부모 결정 — 자식 하나당 부모 하나(첫 매칭 간선). 간선 순서가 우선순위다.
  const parent = new Map<Violation, { p: Violation; why: string }>();
  for (const c of violations) {
    for (const e of EDGES) {
      if (e.c !== c.rule) continue;
      const p = violations.find((x) => x.rule === e.p && x !== c && e.when(c, x));
      if (p) {
        parent.set(c, { p, why: e.why });
        break;
      }
    }
  }

  // 사건 = 부모 사슬의 뿌리. 사슬 깊이 8 제한(간선 실수로 인한 순환 방어).
  const rootOf = (v: Violation): Violation => {
    let x = v;
    let guard = 0;
    while (parent.has(x) && guard++ < 8) x = parent.get(x)!.p;
    return x;
  };
  const byRoot = new Map<Violation, Incident>();
  for (const v of violations) {
    const r = rootOf(v);
    if (!byRoot.has(r)) byRoot.set(r, { root: r, members: [], sev: r.sev, size: 1 });
    if (v !== r) byRoot.get(r)!.members.push({ v, why: parent.get(v)?.why ?? '' });
  }
  const incidents = [...byRoot.values()];
  for (const I of incidents) {
    I.size = 1 + I.members.length;
    // 사건 심각도 = 뿌리와 구성원 중 최고 심각도(연쇄가 뿌리보다 심각할 수 있다)
    I.sev = I.members.reduce<Severity>((s, m) => (SEV[m.v.sev] < SEV[s] ? m.v.sev : s), I.root.sev);
  }
  /* 정렬은 크기순이다 — 부호가 아니라 절댓값을 쓴다. `metric` 이 수로 정규화되면서
   * 편차율(-50%)이 들어오는데, 원값으로 재면 큰 감소가 목록 맨 아래로 간다.
   * 세는 값이 없는 위반(metric:null)은 0 — 크기로 다투지 않고 심각도·연쇄 크기로 갈린다. */
  const mag = (v: Violation) => Math.abs(v.metric ?? 0);
  incidents.sort(
    (a, b) => SEV[a.sev] - SEV[b.sev] || b.size - a.size || mag(b.root) - mag(a.root),
  );

  return { violations, incidents, rules: ruleResults };
}

/* ── 리뷰 계약 JSON (요청문 §5) — UI 없이 CLI/엔드포인트로 뽑는 형태 ──
 * evaluated:false(못 돈 규칙)와 violations:0(돌았는데 조용한 규칙)을 반드시 구분한다. */

export interface Report {
  as_of: { db: string; aws: string; trade_date: string };
  rules: RuleResult[];
  violations: {
    rule: string;
    /** 사람이 읽을 대상 */
    target: string;
    /** 안정 식별자 — 런북 키·사건 키가 쓰는 축. `target` 과 같을 수 있다 */
    target_id: string;
    severity: Severity;
    /** 세는 값. 양이 아닌 위반은 `null` 이고 판정은 `state` 에 있다 */
    metric: number | null;
    unit: string | null;
    state: string | null;
    why: string;
    evidence: string;
    run_id: string | null;
    source: string;
    mock: boolean;
    seed?: boolean;
    absorbed_into: string | null;
  }[];
  incidents: {
    root: string;
    severity: Severity;
    size: number;
    /** `root` 가 사건 키(`vid`)라 멤버도 같은 축으로 낸다 — 조인이 표시 문자열에 걸리면 안 된다 */
    members: { rule: string; target_id: string; cause: string }[];
  }[];
}

export function buildReport(f: Facts, now: Date = snapshotNow(f)): Report {
  const ev = evaluate(f, now);
  /* 사건 키는 화면과 같은 축(`vid`)이다 — 리포트가 자기 키를 따로 조립하면 런 축이 빠져
   * 같은 작업의 다른 런이 한 사건으로 보인다(화면에서는 갈리는데 리포트에서만 합쳐진다) */
  const key = (v: Violation) => v.vid;
  const absorbedInto = new Map<Violation, Violation>();
  for (const I of ev.incidents) for (const m of I.members) absorbedInto.set(m.v, I.root);
  const ruleSource = new Map(RULES.map((R) => [R.id, R.source]));

  return {
    as_of: { db: f.meta.db, aws: f.meta.aws, trade_date: f.meta.today },
    rules: ev.rules,
    violations: ev.violations.map((v) => ({
      rule: v.rule,
      target: v.target,
      target_id: v.targetId,
      severity: v.sev,
      metric: v.metric,
      unit: v.unit ?? null,
      state: v.state ?? null,
      why: v.why,
      evidence: v.evidence,
      run_id: v.runId ?? null,
      source: v.seed ? 'SEED' : v.mock ? 'MOCK' : ruleSource.get(v.rule) ?? 'DB_LEDGER',
      mock: !!v.mock,
      ...(v.seed ? { seed: true } : {}),
      absorbed_into: absorbedInto.has(v) ? key(absorbedInto.get(v)!) : null,
    })),
    incidents: ev.incidents.map((I) => ({
      root: key(I.root),
      severity: I.sev,
      size: I.size,
      members: I.members.map((m) => ({ rule: m.v.rule, target_id: m.v.targetId, cause: m.why })),
    })),
  };
}
