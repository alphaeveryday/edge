/* 데이터셋 신선도 판정 (ALPHA-738 D).
 *
 * ⚠️ **JSX 를 쓰지 않는다.** `DatasetPage.tsx` 안에 있던 동안은 `node --test` 가 import 을 못 해
 * 이 판단의 변이가 **하나도 안 잡혔다** — `notRun`·`minuteFacts`·`awsObservation` 을 내린 것과
 * 같은 이유다. 기준은 "화면 도메인을 아는가"가 아니라 **"JSX 를 쓰는가"** 다.
 *
 * 이 함수의 유일한 일: **근거가 없으면 FRESH 라고 말하지 않는다.** 초록은 "봤고 괜찮다"라
 * 판정 불가·기준 부재를 그 칸에 그리면 계측 공백이 정상으로 읽힌다.
 */
import type { BadgeTone } from 'ui-kit';
import type { DatasetFact, TaskFact } from '../../rules/types.ts';

/** 계획에서 제외된 작업은 미귀결이 아니다. 실행 대상이었던 작업만 결과를 요구한다. */
export function taskRollup(tasks: TaskFact[]): 'fulfilled' | 'skipped' | 'unresolved' | 'failed' | 'blocked' {
  const due = tasks.filter((task) => task.plan_status !== 'SKIPPED');
  if (due.length === 0) return 'skipped';
  if (due.some((task) => task.task_outcome === 'FAILED' || task.task_outcome === 'MISSED')) return 'failed';
  if (due.some((task) => task.task_outcome === 'BLOCKED')) return 'blocked';
  if (due.some((task) => task.task_outcome == null || task.task_outcome === 'PENDING')) return 'unresolved';
  return due.every((task) => task.task_outcome === 'FULFILLED') ? 'fulfilled' : 'unresolved';
}

export interface DatasetRunFlow {
  dataset: string;
  runId: string;
  tasks: TaskFact[];
}

/** 데이터셋 흐름은 실행별 사실이다. 같은 날의 재실행을 한 화살표로 합치지 않는다. */
export function datasetRunFlows(tasks: TaskFact[]): DatasetRunFlow[] {
  const groups = new Map<string, DatasetRunFlow>();
  for (const task of tasks) {
    const dataset = task.dataset ?? '—';
    const key = `${dataset}\u0000${task.run_id}`;
    if (!groups.has(key)) groups.set(key, { dataset, runId: task.run_id, tasks: [] });
    groups.get(key)!.tasks.push(task);
  }
  const stage = (value: string) => ({ raw: 0, normalize: 1, feature: 2 }[value] ?? 3);
  for (const group of groups.values()) {
    group.tasks.sort((a, b) => stage(a.stage) - stage(b.stage) || a.task_key.localeCompare(b.task_key));
  }
  return [...groups.values()].sort(
    (a, b) => a.dataset.localeCompare(b.dataset) || a.runId.localeCompare(b.runId),
  );
}

export interface Freshness {
  label: string;
  tone: BadgeTone;
  tip: string;
}

/**
 * 판정 순서가 곧 규약이다 — **넓은 부재부터** 좁혀 간다.
 *
 * ① 원장이 판정 불가라고 말했다 → 그 사유가 답이다(서버가 `unverifiable` 로 준다).
 * ② 계약이 없다 → 기대 기준일이라는 개념 자체가 없다.
 * ③ 창 계약 → as-of 비교 대상이 아니다.
 *    🔴 **이 분기는 지금 죽어 있고, 서버가 축을 실어도 저절로 살아나지 않는다.**
 *    `windowContract` 는 `DATASET_FIELDS`·`ConsoleFactsDto`·`toFacts` 어디에도 없다 — 모르는
 *    필드는 거부하지 않는 규약이라 응답은 통과하고 그 값만 조용히 버려진다. 그러면 두 날짜가
 *    다 있는 창 계약 데이터셋이 ④⑤를 지나 **FRESH 초록**으로 선다.
 *    되살리려면 **세 자리를 같이** 고쳐야 한다(검사표 · DTO 타입 · 어댑터). 그리고 이 파일의
 *    테스트는 `DatasetFact` 를 직접 만들어서 그 단절을 못 본다 — 와이어 왕복은
 *    `consoleFacts.test.ts` 의 집합 불변식이 볼 자리다.
 * ④ **기대 기준일이 없다** → 비교할 대상이 없다. 🔴 이 갈래가 없으면 ⑤로 떨어져 **비교하지
 *    않은 것이 초록으로** 선다: 서버는 actual 이 없을 때만 `unverifiable` 을 달아서
 *    (`contract: true` + actual 있음 + **expected 없음**)이 그대로 여기 도달한다.
 * ⑤ **실제 기준일이 없다** → 비교의 나머지 절반이 없다. 🔴 서버 규약상 그때 `unverifiable` 이
 *    붙지만(①에서 걸린다), **그 규약이 어긋난 응답에서 여기로 샌다** — 그리고 마지막 줄로
 *    떨어지면 근거가 하나도 없는 행이 **초록**으로 선다. 화면이 서버 규약을 믿고 초록을 그리면,
 *    규약이 깨진 날 그 사실이 정상으로 보인다. 이 함수는 자기가 본 것만으로 답한다.
 * ⑥ actual < expected → STALE / 아니면 FRESH.
 */
export function freshness(d: DatasetFact): Freshness {
  if (d.unverifiable) return { label: '판정 불가', tone: 'neutral', tip: d.unverifiable };
  if (!d.contract)
    return {
      label: '계약 없음',
      tone: 'neutral',
      tip: 'DatasetContract 등록이 없어 기대 기준일 자체가 없다',
    };
  if (d.window_contract)
    return {
      label: '창 계약',
      tone: 'warn',
      tip: '기준일이 아니라 창으로 계약된 데이터셋 — as-of 비교 대상이 아니다',
    };
  if (d.expected_as_of == null)
    return {
      label: '기준 없음',
      tone: 'neutral',
      tip: '계약은 있는데 기대 기준일이 원장에 없다 — 비교할 대상이 없어 FRESH 도 STALE 도 아니다',
    };
  if (d.actual_as_of == null)
    return {
      label: '근거 없음',
      tone: 'neutral',
      tip:
        '기대 기준일은 있는데 실제 기준일이 없다 — 비교의 나머지 절반이 없어 FRESH 도 STALE 도 ' +
        '아니다. 서버가 이때 판정 불가 사유를 함께 주기로 돼 있으므로, 이 칸이 서면 그 규약이 ' +
        '어긋난 것이다.',
    };
  if (d.actual_as_of < d.expected_as_of)
    return { label: 'STALE', tone: 'blocked', tip: '분석이 오래된 스냅샷 위에서 돈다' };
  return { label: 'FRESH', tone: 'active', tip: 'actual_as_of 가 기대 기준일 이상' };
}
