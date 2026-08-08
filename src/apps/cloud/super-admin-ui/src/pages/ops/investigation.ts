/* 사건 → 조사 경로 (ALPHA-738).
 *
 * **모든 사건을 실행에 연결하지 않는다.** 사건마다 기준 축이 다르고, 축이 없는 사건을 억지로
 * 런에 매달면 무관한 최근 실행이 원인처럼 보인다. 그래서 이 모듈이 하는 일은 하나다 —
 * 위반이 실제로 들고 있는 식별자만으로 다음 조사 대상을 정하고, 없으면 없다고 말한다.
 *
 * 지키는 선:
 *   · 없는 run_id 를 지어내지 않는다. `runId` 나 drill 앵커가 준 것만 쓴다.
 *   · 런 행이 없는 슬롯(no_run_row)은 "실행"이 아니라 **예정 슬롯**이다 — 실행 조사로 보내되
 *     그 화면이 답할 것은 "런 행이 없다"는 근거뿐이라는 사실을 함께 넘긴다.
 *   · 큐·배포처럼 실행 자체가 없는 사건은 실행 화면을 거치지 않는다. 원장 근거도 없으면
 *     `ledger: null` 로 두고 화면이 "원장 근거 없음"이라고 쓰게 한다.
 *   · 조사 문맥은 **식별자**로만 넘긴다. 표시 문자열은 받는 화면이 실제 데이터로 조회한다.
 */
import type { Facts, Incident, Violation } from '../../rules/types';

/** 조사 대상의 종류 — 화면이 아니라 **무엇을 조사하는가**다 */
export type TargetKind = 'run' | 'slot' | 'session' | 'dataset' | 'queue' | 'output';

export interface Target {
  kind: TargetKind;
  /** 대상의 실제 식별자(런 키·데이터셋 id·큐 이름 등) */
  id: string;
  label: string;
  /** 왜 이 대상인가 — 추정이 아니라 위반이 들고 있던 축의 설명 */
  why: string;
  href: string;
}

/**
 * 원장 근거 화면에 넘길 문맥. 값이 있는 키만 필터가 된다 — 문맥이 하나도 없으면 `null` 이고,
 * 그때 원장 화면은 전체를 덤프하는 대신 "문맥 없이 열 수 없다"고 말한다.
 */
export interface LedgerContext {
  incident?: string;
  runKey?: string;
  task?: string;
  dataset?: string;
  /** 실시간 세션 축 — 세션 날짜(KST) */
  date?: string;
  /**
   * 실시간 세션 축 — 벤더. 세션 identity 가 `(dataset, sourceGroup, date)` 라 이게 없으면
   * 원장 화면이 데이터셋만 보고 **다른 벤더의 세션 행**을 집는다(사건은 벤더로 갈렸는데).
   */
  sourceGroup?: string;
}

export interface Investigation {
  targets: Target[];
  ledger: LedgerContext | null;
  /** 원장 근거가 없거나 제한적일 때의 사유. 있으면 화면이 그대로 보여준다 */
  ledgerNote: string | null;
}

/** 실시간 수집 데이터셋 — 어휘 정본은 data_pipeline/minute/states.py */
const REALTIME_DATASETS = new Set(['price_minute', 'news_minute']);

const q = (v: string) => encodeURIComponent(v);

/**
 * 사건 하나의 조사 경로. `facts` 는 런 행 존재 여부처럼 **화면이 추정하면 안 되는 사실**을
 * 확인하는 데만 쓴다(예: 계획됐는데 런 행이 없는 슬롯인가).
 */
export function investigate(incident: Incident, facts: Facts): Investigation {
  const v = incident.root;
  const [axis, anchor] = v.drill;
  const vid = v.vid;

  /* ── 실행·작업 축 ── */
  if (axis === 'run') {
    /* 작업 앵커는 런을 스스로 말하지 않는다 — 위반이 실은 runId 를 들고 있다.
     * 없으면 런을 추측하지 않고 작업 축까지만 조사한다(같은 task_key 의 다른 런이 섞인다). */
    if (anchor.startsWith('task-')) {
      const taskKey = anchor.slice(5);
      const runId = v.runId;
      if (!runId) {
        return {
          targets: [],
          ledger: null,
          ledgerNote: `이 위반은 작업(${taskKey})만 지목하고 어느 런의 것인지 기록하지 않았다 — 런을 추측해 연결하지 않는다.`,
        };
      }
      const task = facts.tasks.find((t) => t.run_id === runId && t.task_key === taskKey);
      return {
        targets: [
          {
            kind: 'run',
            id: runId,
            label: `${taskKey} · ${runId}`,
            why: '이 위반이 기록한 run_id 의 작업이다 — 같은 작업명의 다른 런은 열지 않는다',
            href: runHref(runId, { focus: anchor, fromIncident: vid }),
          },
        ],
        ledger: {
          incident: vid,
          runKey: runId,
          task: taskKey,
          ...(task?.dataset ? { dataset: task.dataset } : {}),
        },
        ledgerNote: null,
      };
    }

    if (anchor.startsWith('run-')) {
      const runId = anchor.slice(4);
      const run = facts.runs.find((r) => r.id === runId);
      /* 계획은 있는데 런 행이 없다 — 실행이 아니라 **예정 슬롯**이다. 실행 화면으로 보내되
       * 그 화면이 답할 것은 작업 목록이 아니라 "행이 없다"는 근거뿐이다. */
      if (run?.no_run_row) {
        return {
          targets: [
            {
              kind: 'slot',
              id: runId,
              label: `예정 슬롯 ${runId}`,
              why: '계획된 슬롯인데 ops_pipeline_run 행이 없다 — 조사 대상은 실행이 아니라 슬롯이다',
              href: runHref(runId, { fromIncident: vid }),
            },
          ],
          ledger: { incident: vid, runKey: runId },
          ledgerNote:
            '런 행 자체가 없으므로 원장 근거는 "이 슬롯에 행이 없다"까지다 — 작업·시도 행은 존재하지 않는다.',
        };
      }
      return {
        targets: [
          {
            kind: 'run',
            id: runId,
            label: `실행 ${runId}`,
            why: '이 위반이 지목한 런이다 — 최근 런 전체를 다시 훑지 않는다',
            href: runHref(runId, { fromIncident: vid }),
          },
        ],
        ledger: { incident: vid, runKey: runId },
        ledgerNote: null,
      };
    }
  }

  /* ── 데이터셋 축 ── 실시간 데이터셋이면 조사 대상은 그 날짜의 세션이다 */
  if (axis === 'dataset' && anchor.startsWith('ds-')) {
    const datasetId = anchor.slice(3);
    /* 실시간 세션의 날짜는 **세션 응답이 말한 날**이다 — meta.today(배치 스냅샷의 거래일)와
     * 다를 수 있고, 다르면 드릴다운이 없는 날짜의 세션을 연다. */
    const date = facts.minute?.date ?? facts.meta.today;
    /* `targetId` 는 세션의 대상 축 `dataset/sourceGroup` 이다 — 앵커(`ds-…`)에는 벤더가 없어
     * 그것만 보면 어느 벤더의 세션인지 못 좁힌다. 없으면 **안 싣는다**(부재를 값으로 만들지 않는다). */
    const vendor = v.targetId.split('/')[1];
    if (REALTIME_DATASETS.has(datasetId)) {
      return {
        targets: [
          {
            kind: 'session',
            id: datasetId,
            /* 사건이 지목한 것은 대개 벤더까지 갈린 세션이다(`targetId` = `dataset/sourceGroup`).
             * 다만 **벤더로 못 가르는 값**(날짜 축 집계)의 사건은 대상이 `(데이터셋, 날짜)` 라,
             * 라벨이 그걸 "세션"이라 부르면 없는 실체를 지목한다. */
            label: vendor
              ? `실시간 세션 ${v.targetId} · ${date}`
              : `실시간 데이터셋 ${datasetId} · ${date} (세션 전체)`,
            why:
              '실시간 데이터셋의 상위 단위는 데이터셋 × 세션 날짜다 — 1분 창은 그 세션의 하위 증거다.' +
              /* 라벨은 벤더까지 말하는데 세션 화면은 아직 벤더로 안 좁힌다 — 약속과 도착지가
               * 어긋난 상태를 문구로 밝힌다(좁히는 것은 화면 배선 PR 소관). */
              (vendor
                ? ' 세션 화면은 아직 벤더로 좁히지 못해 이 데이터셋의 세션을 모두 보여준다.'
                : ' 이 사건의 수는 벤더로 갈리지 않아 그 날짜의 세션 전체가 대상이다.'),
            href: `/minute?date=${q(date)}&dataset=${q(datasetId)}`,
          },
        ],
        /* `targetId` 는 `dataset/sourceGroup` 이다(세션의 대상 축) — 벤더를 여기서 꺼내 넘긴다.
         * 앵커(`ds-…`)에는 벤더가 없어 그것만 보면 어느 세션인지 못 좁힌다. */
        ledger: {
          incident: vid,
          dataset: datasetId,
          date,
          ...(vendor ? { sourceGroup: vendor } : {}),
        },
        /* ⚠️ 벤더가 없으면 **원장 화면이 세션 하나로 못 좁힌다**(벤더가 둘이면 고르기를 거부한다).
         * 그 사실을 여기서 밝히지 않으면, 도착한 화면이 "source_group 을 실어 주세요"라고
         * **이 사건에서는 불가능한 조치**를 지시한다 — 그 수가 벤더로 안 갈리는 값이라서다. */
        ledgerNote: vendor
          ? null
          : '이 사건의 수는 날짜 축 집계라 세션 하나로 좁혀지지 않는다 — 원장 화면에서 벤더를 골라야 세션 행이 선다.',
      };
    }
    return {
      targets: [
        {
          kind: 'dataset',
          id: datasetId,
          label: `데이터셋 ${datasetId}`,
          why: '이 사건의 기준 축은 데이터셋 계약이다 — 특정 실행에 매여 있지 않다',
          href: `/ops/datasets?focus=${q(anchor)}&fromIncident=${q(vid)}`,
        },
      ],
      ledger: { incident: vid, dataset: datasetId },
      ledgerNote:
        '이 사건은 실행에 매여 있지 않아 원장 근거는 데이터셋 축까지다 — 런·작업·시도 행으로 좁힐 식별자가 위반에 없다.',
    };
  }

  /* ── 큐 축 ── 실행체가 아예 없는 인프라 사건. 실행 화면을 거치지 않는다 */
  if (axis === 'chain' && anchor.startsWith('q-')) {
    return {
      targets: [
        {
          kind: 'queue',
          id: v.targetId,
          label: `큐 ${v.target}`,
          why: '실행이 아니라 큐·소비자 배선이 근거다 — 관련 실행이 존재하지 않는다',
          href: `/ops/chain?focus=${q(anchor)}&fromIncident=${q(vid)}`,
        },
      ],
      ledger: null,
      ledgerNote:
        '큐 깊이·소비자 배선은 AWS 제어면과 배포 상태가 근거다 — 운영 원장(ops_*)에 대응 행이 없다.',
    };
  }

  /* ── 그 밖의 산출·흐름 축 ── 지목 화면까지만 보낸다 */
  const route: Record<string, string> = {
    chain: '/ops/chain',
    trend: '/ops/trend',
    dataset: '/ops/datasets',
    delivery: '/ops/delivery',
  };
  const href = route[axis];
  return {
    targets: href
      ? [
          {
            kind: 'output',
            id: v.targetId,
            label: v.title,
            why: '이 사건의 기준 축은 산출·흐름이라 실행 하나로 좁혀지지 않는다',
            href: `${href}?focus=${q(anchor)}&fromIncident=${q(vid)}`,
          },
        ]
      : [],
    ledger: null,
    ledgerNote:
      '이 사건은 실행·작업 식별자를 들고 있지 않아 원장 근거로 좁힐 문맥이 없다 — 실행을 추측해 연결하지 않는다.',
  };
}

/** 원장 근거 화면 주소 — 문맥이 없으면 `null`(문맥 없는 원장 열기를 만들지 않는다) */
export function ledgerHref(ctx: LedgerContext | null): string | null {
  if (!ctx) return null;
  const p = new URLSearchParams();
  if (ctx.incident) p.set('incident', ctx.incident);
  if (ctx.runKey) p.set('runKey', ctx.runKey);
  if (ctx.task) p.set('task', ctx.task);
  if (ctx.dataset) p.set('dataset', ctx.dataset);
  if (ctx.date) p.set('date', ctx.date);
  if (ctx.sourceGroup) p.set('sourceGroup', ctx.sourceGroup);
  const qs = p.toString();
  return qs ? `/sources?${qs}` : null;
}

/**
 * 실행 상세로 못 가는 이유 — 실 API 화면에서 이 문구를 그대로 쓴다.
 *
 * 실행 상세(`/ops/runs/:runId`)는 앱 번들 안 `rules/facts-snapshot.json` 의 런 6건만 해소한다.
 * 실 API 화면(실행 이력 `/grid` · 현재 실행 `/minute` · 구성종목 결손)의 런은 거기 없어
 * 그 화면이 **"이 화면은 이 실행을 읽지 못합니다"** 로 끝난다 — 없는 게 아니라 아직 못 읽는
 * 것이다. facts 엔드포인트가 붙으면(ADR-0049) 링크를 되살린다.
 *
 * 판별 기준은 "상세냐 목록이냐"가 **아니다** — 그 자리에서 사용자가 **특정 런 identity 를
 * 쥐고 있는가**다. 쥔 자리에서 스냅샷 런 축으로 내보내면 "이 실행은 없다"로 읽힌다. 안 쥔
 * 자리(조사 문맥이 아직 없는 화면)에서 `/ops/runs` 목록은 정확히 그 용도라 대상이 아니다.
 */
export const RUN_DETAIL_UNAVAILABLE = '실행 상세는 아직 실 데이터를 읽지 못합니다';

/**
 * 실행 상세 주소 — 런 하나는 **자기 페이지**를 갖는다(목록의 선택 상태가 아니다).
 *
 * 주소를 한 곳에서 만든다: 예전에는 `/ops/runs?run_id=` 를 5곳이 각자 조립했고, 경로가 바뀌면
 * 그중 하나가 조용히 남는다. `run_id` 형태의 옛 주소는 목록 페이지가 이 주소로 넘긴다.
 *
 * ⚠️ **실 API 화면에서 부르지 마라** — 위 `RUN_DETAIL_UNAVAILABLE` 참조. 지금 이 함수의
 * 정당한 호출처는 스냅샷 런만 다루는 자리(사건 조사 경로 · 실행 목록의 선택)뿐이다.
 *
 * ⚠️ **사건 딥링크와 달리 경로형으로 남는다** — 그리고 그건 런 키 모양에 기대고 있다.
 * CloudFront SPA fallback(`spa-rewrite.js`)은 마지막 경로 조각의 점(.)으로 정적 파일을
 * 가르는데, `ops_pipeline_run.run_key` 는 `etf-daily:2026-08-03T15:40` 꼴(레인·콜론·날짜)이라
 * 점이 없다. **원장이 그 불변식을 보증하지는 않는다** — 런 키에 점이 들 수 있게 되면(벤더 축이
 * 붙는 등) 이 주소도 `incidentHref` 처럼 쿼리로 옮겨야 한다.
 *
 * @param extra `focus`·`fromIncident` 처럼 조사 문맥을 실어 보내는 쿼리
 */
export function runHref(runId: string, extra?: Record<string, string | undefined>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(extra ?? {})) if (v) p.set(k, v);
  const qs = p.toString();
  return `/ops/runs/${q(runId)}${qs ? `?${qs}` : ''}`;
}

/**
 * 사건 상세 주소 — vid 가 사건의 식별자다(root 위반 id).
 *
 * **경로가 아니라 쿼리다.** CloudFront 의 SPA fallback 은 마지막 경로 조각의 점(.)으로
 * 정적 파일 여부를 가르는데(`spa-rewrite.js`), 대상 id 에 점이 든 사건이 있어 경로에 두면
 * 공유 링크가 하드로드에서 죽는다. 쿼리스트링은 그 판별을 안 탄다.
 */
export const incidentHref = (v: Violation): string =>
  `/ops/incidents/detail?vid=${q(v.vid)}`;

/**
 * 이 `vid` 를 담은 사건 — **뿌리든 흡수된 멤버든**.
 *
 * `incidents[]` 는 뿌리만 담는다. 그래서 `i.root.vid === vid` 만 보면, 인과 간선으로 흡수된
 * 위반의 링크가 "그런 사건 없음"으로 떨어진다 — 그 위반은 **지금도 규칙에 걸려 있는데**.
 * 어제 뿌리였던 vid 는 오늘 부모가 걸리면 멤버로 내려가므로, 어제 공유한 주소가 정확히 그
 * 경로로 온다. 소비자마다 다시 짜면 한 곳만 빠뜨려도 그 화면에서만 사건이 사라진다.
 *
 * `member` 는 "찾긴 했는데 이 vid 가 뿌리가 아니다"라는 뜻이다 — 화면이 그 사실을 밝혀야
 * 운영자가 "왜 다른 제목이 뜨지"를 묻지 않는다.
 */
export function incidentOfVid(
  incidents: Incident[],
  vid: string,
): { incident: Incident; member: boolean } | null {
  /* 빈 vid 를 따로 막지 않는다 — 아래 두 조회가 자연히 못 찾는다(`evaluate` 가 빈 축을
   * `identity` 로 세우므로 vid 가 `''` 인 위반은 애초에 없다). 가드를 두면 "검증됐다"로 읽힌다. */
  const root = incidents.find((i) => i.root.vid === vid);
  if (root) return { incident: root, member: false };
  const owner = incidents.find((i) => i.members.some((m) => m.v.vid === vid));
  return owner ? { incident: owner, member: true } : null;
}
