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
 * `vid` 를 낸 규칙 id — **`vidOf` 의 역함수라 같은 파일에 둔다.**
 *
 * 소비자(화면이 딥링크의 규칙을 되찾는 자리)가 구분자를 자기 쪽에 다시 적으면, 여기 구분자를
 * 바꾸는 순간 **조용히 아무것도 못 찾는다** — 그리고 화면은 "못 찾았다"를 "그 규칙은 돌았다"로
 * 읽는다(거짓 음성). 둘을 붙여 두면 왕복 단언 하나가 그 표류를 잡는다.
 *
 * 규칙 id 에 `:` 가 없다는 것이 전제고 `rules.test.ts` 가 단언한다. 형태가 아니면 빈 문자열 —
 * 호출자는 "모르는 식별자"로 갈라야지 "그 규칙은 돌았다"로 읽으면 안 된다.
 */
export function ruleOfVid(vid: string): string {
  const i = vid.indexOf(':');
  return i < 0 ? '' : vid.slice(0, i);
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

  for (const R of RULES) {
    let evaluated = R.canRun ? R.canRun(f) : true;
    /** 사건 식별자가 위반을 못 가른 사유 — 있으면 이 규칙은 `못 돎` 이 된다 */
    let collision: string | null = null;
    /* 규칙 단위로 모았다가 마지막에 편입한다 — 충돌이 나면 이 규칙의 위반은 **하나도** 안 싣기
     * 때문이다. 반쯤 실으면 어떤 사건이 빠졌는지 화면이 말할 수 없다. */
    const mine: Violation[] = [];
    if (evaluated) {
      for (const raw of R.run(f, { now })) {
        /* 규약: 표시용 target 과 키용 targetId 를 여기서 한 번만 정규화한다 —
         * 소비자(런북 키·간선·조사 경로)가 각자 폴백을 쓰면 한 곳만 빠뜨려도 키가 갈린다 */
        const targetId = raw.targetId ?? raw.target;
        /* 범위도 같은 규약으로 한 번만 정규화하고 **위반에 값으로 남긴다** — 계산만 하고 버리면
         * 범위를 알아야 하는 소비자(리포트 조인 등)가 `scope ?? runId` 를 각자 다시 쓴다. */
        const scope = raw.scope ?? raw.runId;
        /* 빈 문자열은 '범위 없음'이 아니라 **망가진 사실**이다. `??` 는 통과시키고 아래 truthy
         * 검사는 '없음'으로 읽어, 둘이 갈리면 범위 없는 vid 가 조용히 성립한다. 그 vid 는 같은
         * 스냅샷 안에서 겹치지 않으면 안 잡히고, 내일 다른 런이 또 `''` 로 오면 어제 공유한
         * 링크가 오늘 사건을 연다 — 충돌 검사로는 못 잡는 시간 축 충돌이다. */
        /* 대상 축도 같은 구멍이다 — `targetId: ''` 는 `??` 를 통과해 `R13:` 이라는 정상처럼
         * 보이는 vid 를 만든다. 위반이 하나면 충돌도 안 나 그대로 나간다. */
        if (targetId === '' || scope === '') {
          const axis = targetId === '' ? '대상 축(targetId)' : `범위 축(${targetId})`;
          collision = `${R.id}: ${axis}이 빈 문자열이다 — '없음'과 구분되지 않아 사건 키를 못 만든다`;
          break;
        }
        const vid = vidOf(R.id, targetId, scope);
        /* 겹치면 이 규칙을 `못 돎` 으로 세운다. 뒤에 온 위반을 버리거나 번호를 붙여 비키면
         * 위치 인덱스가 이름만 바꿔 되살아나므로 그 둘은 답이 아니다. 그렇다고 던지면 평가가
         * 통째로 죽어 **나머지 18규칙의 사건까지 화면에서 사라진다** — 파이프라인이 깨진 날
         * 콘솔이 통째로 오류 카드가 된다. vid 는 규칙 id 로 시작하니 충돌은 언제나 한 규칙
         * 안에서만 나고(규칙 간 검사는 죽은 분기다), 그 규칙만 세우면 사유는 그대로 보이면서
         * 나머지는 산다. */
        if (mine.some((v) => v.vid === vid)) {
          collision = `${R.id}: 사건 식별자 충돌 ${vid} — 대상 축이 이 위반들을 못 가른다(실어야 할 identity 축이 빠졌다)`;
          break;
        }
        mine.push({
          ...raw,
          targetId,
          scope,
          rule: R.id,
          ruleName: R.name,
          layer: R.layer,
          kls: raw.kls ?? R.kls,
          sev: raw.sev ?? R.base,
          dep: R.dep,
          vid,
        });
      }
    }
    if (collision) {
      /* 위반 0건이 아니라 **못 돎** 이다 — "봤고 괜찮다"와 같은 칸에 그리면 계약 위반이 정상으로 보인다 */
      evaluated = false;
      mine.length = 0;
    }
    violations.push(...mine);
    ruleResults.push({
      id: R.id,
      name: R.name,
      layer: R.layer,
      evaluated,
      /* 못 돎의 종류를 **구조로** 낸다 — 화면이 문구를 파싱해 추측하면 새 사유가 생길 때마다
       * 조용히 틀린 칸에 그린다. `identity` 는 계측 공백이 아니라 응답의 계약 위반이다. */
      notRun: evaluated ? undefined : collision ? 'identity' : 'axis',
      violations: mine.length,
      depends_on_mock:
        evaluated && (mine.some((v) => v.mock) || !!R.mockBacked?.(f)),
      /* 세 갈래가 **배타적**이다: 충돌 사유 / 돈 규칙의 주석 / 못 돈 규칙의 사유(`dep`).
       *
       * `R.note` 는 **돈 규칙에만** 부른다 — 축이 없어 `canRun` 이 막은 규칙의 `note` 가 그 축을
       * 읽으면 여기서 죽는다(`canRun` 이 못 막는 진입점이 이 한 줄이었다).
       * `R.dep` 는 반대로 **못 돈 규칙에만** 붙인다 — `dep` 은 "없는 계측"이라 못 돈 사유이지
       * 돌아간 규칙의 주석이 아니다. 폴백으로 두면 배선돼서 잘 도는 규칙 행에 "…배선"이라는
       * 주석과 `*` 표가 영구히 붙는다(R03·R10·R13 에 `dep` 을 넣자마자 실제로 그랬다).
       * ⚠️ 세 항을 통째로 `evaluated &&` 로 감싸면 **충돌 사유가 지워진다** — 충돌이 나면
       * 위에서 `evaluated` 를 이미 false 로 바꾸므로, `collision` 은 반드시 밖에 있어야 한다. */
      note: collision ?? (evaluated ? R.note?.(f) ?? null : R.dep),
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
    /** 사건 키 — `incidents[].root`·`members[].vid`·`absorbed_into` 와 조인하는 축 */
    vid: string;
    rule: string;
    /** 사람이 읽을 대상 */
    target: string;
    /** 안정 식별자 — **런북 키**가 쓰는 축(시점이 없다). `target` 과 같을 수 있다 */
    target_id: string;
    /** 시점 범위 — 배치는 런 키, 실시간은 세션 날짜. 없으면 범위 없는 대상이다 */
    scope: string | null;
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
    /** `root` 가 사건 키(`vid`)라 **멤버도 같은 축**으로 낸다 — 조인이 표시 문자열에 걸리면 안 된다.
     *  `rule`·`target_id` 만 내던 때가 있었는데, 같은 작업이 두 런에 걸리면 멤버 두 줄이 글자
     *  하나 안 틀리게 같아져 root 만 갈리고 멤버는 합쳐 보였다. */
    members: { vid: string; rule: string; target_id: string; cause: string }[];
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
      vid: v.vid,
      rule: v.rule,
      target: v.target,
      target_id: v.targetId,
      scope: v.scope ?? null,
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
      members: I.members.map((m) => ({
        vid: m.v.vid,
        rule: m.v.rule,
        target_id: m.v.targetId,
        cause: m.why,
      })),
    })),
  };
}
