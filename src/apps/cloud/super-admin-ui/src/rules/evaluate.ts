/* 평가기 — 위반 수집 → 인과 병합(사건) → 정렬. 규칙·간선은 rules.ts, 사실은 호출자가 준다.
 *
 * 흡수된 위반은 지우지 않는다 — 사건 members 에 인과 문구와 함께 남는다(명세 §2-2).
 * 정렬: 심각도 → 연쇄 크기 → 대표 위반의 수치 (명세 §2-3).
 */
import { EDGES, RULES } from './rules.ts';
import type { Evaluation, Facts, Incident, RuleResult, Severity, Violation } from './types.ts';

const SEV: Record<Severity, number> = { P0: 0, P1: 1, P2: 2 };

/** 스냅샷 평가의 기준 시각 — 벽시계가 아니라 스냅샷 채취 시각(재현 가능해야 한다) */
export function snapshotNow(f: Facts): Date {
  return new Date(f.meta.db);
}

export function evaluate(f: Facts, now: Date = snapshotNow(f)): Evaluation {
  const violations: Violation[] = [];
  const ruleResults: RuleResult[] = [];

  for (const R of RULES) {
    const evaluated = R.canRun ? R.canRun(f) : true;
    let count = 0;
    if (evaluated) {
      for (const raw of R.run(f, { now })) {
        violations.push({
          ...raw,
          /* 규약: 표시용 target 과 키용 targetId 를 여기서 한 번만 정규화한다 —
           * 소비자(런북 키·간선·조사 경로)가 각자 폴백을 쓰면 한 곳만 빠뜨려도 키가 갈린다 */
          targetId: raw.targetId ?? raw.target,
          rule: R.id,
          ruleName: R.name,
          layer: R.layer,
          kls: raw.kls ?? R.kls,
          sev: raw.sev ?? R.base,
          dep: R.dep,
          vid: `${R.id}#${count}`,
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
    /** `root` 가 키(`rule:targetId`)라 멤버도 같은 축으로 낸다 — 조인이 표시 문자열에 걸리면 안 된다 */
    members: { rule: string; target_id: string; cause: string }[];
  }[];
}

export function buildReport(f: Facts, now: Date = snapshotNow(f)): Report {
  const ev = evaluate(f, now);
  const key = (v: Violation) => `${v.rule}:${v.targetId}`;
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
