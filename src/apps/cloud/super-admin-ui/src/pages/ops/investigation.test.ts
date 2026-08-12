/* 사건 → 조사 경로 (ALPHA-738).
 *
 * 지키는 의도는 "모든 사건을 실행에 연결한다"가 **아니다**. 반대다 —
 *   · 위반이 실제로 들고 있는 식별자만 쓴다. 없으면 대상을 만들지 않는다.
 *   · 런 행이 없는 슬롯은 실행이 아니라 예정 슬롯이고, 원장 근거는 "행이 없다"까지다.
 *   · 큐·배포처럼 실행이 없는 사건은 실행 화면을 거치지 않고 원장 근거도 없다고 말한다.
 * 이 셋 중 하나라도 무너지면 무관한 최근 실행이 사건의 원인처럼 보인다.
 *
 * 실행: node --test src/pages/ops/investigation.test.ts
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync } from 'node:fs';
import { incidentHref, incidentOfVid, investigate, ledgerHref, minuteSessionHref, REALTIME_DATASETS } from './investigation.ts';
import { evaluate } from '../../rules/evaluate.ts';
import type { Facts, Incident, Violation } from '../../rules/types.ts';

const FACTS = {
  runs: [
    { id: 'etf-daily:2026-08-03T15:40', lane: 'etf-daily', kind: 'scheduled', trading_date: '2026-08-03' },
    { id: 'etf-daily:2026-07-28T15:40', lane: 'etf-daily', kind: 'scheduled', trading_date: '2026-07-28', planned: true, no_run_row: true },
  ],
  tasks: [
    {
      task_key: 'INVESTOR_COLLECTION_KIS',
      run_id: 'etf-daily:2026-08-03T15:40',
      pipeline_type: 'etf-daily',
      stage: 'raw',
      dataset: 'investor_flow',
      required: true,
      task_outcome: 'FULFILLED',
    },
  ],
  meta: { db: '', aws: '', today: '2026-08-03' },
} as unknown as Facts;

/* `targetId` 는 **스프레드 뒤에** 정규화한다 — `evaluate()` 가 하는 것과 같은 순서다.
 * 앞에 박아 두면 `violation({ target: 'q1' })` 이 엉뚱한 targetId 를 들고 돌아 픽스처가
 * 운영 형상을 재현하지 못한다(그러면 이 파일의 단언은 코드가 아니라 픽스처를 검사한다). */
const violation = (o: Partial<Violation>): Violation => {
  const v = base(o);
  return { ...v, targetId: v.targetId ?? v.target } as Violation;
};

test('규칙이 평가하는 모든 분봉 데이터셋은 실시간 원장으로 라우팅한다', () => {
  assert.deepEqual(
    [...REALTIME_DATASETS].sort(),
    ['disclosure_minute', 'etf_inav_minute', 'news_minute', 'price_minute', 'sector_index_minute'],
  );
});

const base = (o: Partial<Violation>) =>
  ({
    /* 엔진이 실제로 내는 모양(`${rule}:${targetId}[@${scope}]`)이다 — 여기에 옛 위치 인덱스
     * (`R99#0`)를 두면 이 파일이 프로덕션 형상을 한 번도 안 밟는다. */
    vid: VID,
    rule: 'R99',
    ruleName: '테스트',
    layer: '런',
    kls: '고장',
    sev: 'P0',
    dep: null,
    target: 't',
    title: '제목',
    metric: 1,
    unit: '건',
    why: '',
    evidence: '',
    drill: ['run', 'run-etf-daily:2026-08-03T15:40'],
    ...o,
  }) as Violation;

const VID = 'R99:t@etf-daily:2026-08-03T15:40';

const incident = (v: Violation): Incident => ({ root: v, members: [], sev: v.sev, size: 1 });

test('사건 조회는 흡수된 위반의 vid 로도 찾는다 — 뿌리만 보면 살아 있는 위반이 "없는 것"이 된다', () => {
  /* `incidents[]` 는 뿌리만 담는다. 소비자 4곳이 이 함수를 쓰는데 단언이 없으면, member 탐색을
   * 지우거나 `member` 를 항상 false 로 만들어도 아무것도 안 깨진다 — 후자는 **흡수된 vid 가
   * 뿌리 사건 상세를 통째로 그리게** 만들고(URL 은 멤버인데 내용은 뿌리), 안내는 영구 미표시다. */
  const root = base({ rule: 'R04', targetId: 'run-1', vid: 'R04:run-1' });
  const child = base({ rule: 'R05', targetId: 'T1', vid: 'R05:T1@run-1' });
  const I: Incident = { root, members: [{ v: child, why: '이 런이 실패해서' }], sev: 'P0', size: 2 };

  const asRoot = incidentOfVid([I], 'R04:run-1');
  assert.equal(asRoot?.incident, I);
  assert.equal(asRoot?.member, false, '뿌리인데 멤버라고 했다 — 화면이 "흡수됐다"를 잘못 낸다');

  const asMember = incidentOfVid([I], 'R05:T1@run-1');
  assert.equal(asMember?.incident, I, '흡수된 위반의 vid 로 사건을 못 찾았다');
  assert.equal(asMember?.member, true, '멤버인데 뿌리라고 했다 — 뿌리 상세가 멤버 주소로 그려진다');

  assert.equal(incidentOfVid([I], 'R13:o.pub'), null, '없는 vid 에 사건을 붙였다');
  assert.equal(incidentOfVid([], 'R04:run-1'), null);
});

test('런 축 사건은 그 런만 연다 — 최근 런 전체를 다시 훑게 하지 않는다', () => {
  const r = investigate(incident(violation({})), FACTS);
  assert.equal(r.targets.length, 1);
  assert.equal(r.targets[0].kind, 'run');
  /* 런 하나는 자기 페이지를 갖는다 — 목록의 선택 상태(?run_id=)가 아니라 경로로 지목한다 */
  assert.match(r.targets[0].href, /^\/ops\/runs\/etf-daily%3A2026-08-03T15%3A40(\?|$)/);
  assert.match(r.targets[0].href, new RegExp(`fromIncident=${encodeURIComponent(VID).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}`));
  assert.deepEqual(r.ledger, { incident: VID, runKey: 'etf-daily:2026-08-03T15:40' });
});

test('작업 축은 위반이 기록한 run_id 로만 연다 — 없으면 런을 추측하지 않는다', () => {
  const withRun = investigate(
    incident(violation({ drill: ['run', 'task-INVESTOR_COLLECTION_KIS'], runId: 'etf-daily:2026-08-03T15:40' })),
    FACTS,
  );
  assert.equal(withRun.targets[0].kind, 'run');
  /* 원장 문맥에 작업·데이터셋까지 실린다 — 원장이 그 범위로 좁혀야 근거가 된다 */
  assert.deepEqual(withRun.ledger, {
    incident: 'R99:t@etf-daily:2026-08-03T15:40',
    runKey: 'etf-daily:2026-08-03T15:40',
    task: 'INVESTOR_COLLECTION_KIS',
    dataset: 'investor_flow',
  });

  const noRun = investigate(incident(violation({ drill: ['run', 'task-INVESTOR_COLLECTION_KIS'] })), FACTS);
  assert.deepEqual(noRun.targets, [], '런을 모르면 실행 화면으로 보내지 않는다');
  assert.equal(noRun.ledger, null);
  assert.match(noRun.ledgerNote ?? '', /추측해 연결하지 않는다/);
});

test('런 행이 없는 슬롯은 실행이 아니라 예정 슬롯이고, 원장 근거는 "행 없음"까지다', () => {
  const r = investigate(
    incident(violation({ drill: ['run', 'run-etf-daily:2026-07-28T15:40'] })),
    FACTS,
  );
  assert.equal(r.targets[0].kind, 'slot');
  assert.deepEqual(r.ledger, { incident: VID, runKey: 'etf-daily:2026-07-28T15:40' });
  /* 작업·시도 행이 있는 것처럼 보이면 안 된다 */
  assert.match(r.ledgerNote ?? '', /행이 없다/);
});

test('큐 사건은 실행 화면을 거치지 않고 원장 근거도 없다고 말한다', () => {
  const r = investigate(
    /* R11 의 실제 형상 그대로다 — 큐는 이름 자체가 식별자이자 사람이 읽을 대상이라
     * `targetId` 를 따로 두지 않는다(엔진이 `target` 으로 폴백한다). 픽스처가 룰이 만들지 않는
     * 라벨을 지어내면 그 단언은 코드가 아니라 픽스처를 검사한다. */
    incident(
      violation({
        layer: '큐',
        drill: ['chain', 'q-price-explanation-realtime'],
        target: 'price-explanation-realtime',
      }),
    ),
    FACTS,
  );
  assert.equal(r.targets[0].kind, 'queue');
  /* 조사 문맥은 **식별자**로만 넘긴다(모듈 주석) — 받는 화면이 조회할 키가 있어야 한다 */
  assert.equal(r.targets[0].id, 'price-explanation-realtime');
  assert.doesNotMatch(r.targets[0].href, /\/ops\/runs/);
  assert.equal(r.ledger, null, '없는 원장 근거를 만들지 않는다');
  assert.equal(ledgerHref(r.ledger), null);
});

test('실시간 데이터셋 사건은 1분 창이 아니라 그 날짜의 세션을 연다', () => {
  const r = investigate(incident(violation({ drill: ['dataset', 'ds-price_minute'] })), FACTS);
  assert.equal(r.targets[0].kind, 'session');
  assert.equal(r.targets[0].href, '/minute?date=2026-08-03&dataset=price_minute');
  assert.deepEqual(r.ledger, { incident: VID, dataset: 'price_minute', date: '2026-08-03' });

  /* 벤더가 없는 대상은 **세션 하나로 안 좁혀진다**(원장 화면이 후보가 둘이면 고르기를 거부한다).
   * 그 사실을 여기서 안 실으면 도착한 화면이 "source_group 을 실어 주세요"라는 — 이 사건에서는
   * 불가능한 — 조치를 지시한다. 그리고 라벨이 그걸 "세션"이라 부르면 없는 실체를 지목한다.
   * 두 줄 다 이 라운드가 판 것이라 단언 없이 두면 다음 정리에서 조용히 되돌아간다. */
  assert.match(r.ledgerNote ?? '', /세션 하나로 좁혀지지 않는다/, '좁혀지지 않는 이유를 안 실었다');
  assert.match(r.targets[0].label, /실시간 데이터셋/, '벤더 없는 대상을 "세션"이라 불렀다');
});

test('실시간 세션 사건은 벤더를 원장 문맥에 싣는다 — 데이터셋만 넘기면 남의 세션 행이 선다', () => {
  /* 세션 identity 는 `(dataset, sourceGroup, date)` 다. 사건은 벤더로 갈렸는데 원장 근거가
   * 데이터셋만 받으면 `SourcesPage` 의 `.find(s => s.dataset === …)` 가 **첫 벤더**를 집는다 —
   * bigkinds 사건에서 naver 세션의 sessionId·phase·lease 가 아무 경고 없이 선다. */
  const r = investigate(
    incident(violation({ target: 'news_minute / bigkinds', targetId: 'news_minute/bigkinds', drill: ['dataset', 'ds-news_minute'] })),
    FACTS,
  );
  assert.equal(r.ledger?.sourceGroup, 'bigkinds');
  assert.match(ledgerHref(r.ledger)!, /sourceGroup=bigkinds/);
  /* 라벨도 벤더를 말해야 한다 — 목적지가 벤더를 좁히는 마당에 라벨만 데이터셋이면 어느
   * 세션을 여는지 모른 채 이동한다 */
  assert.match(r.targets[0].label, /news_minute\/bigkinds/);
  assert.match(r.targets[0].href, /sourceGroup=bigkinds/, '세션 화면 링크도 사건의 벤더를 보존한다');
});

test('실시간 세션 지름길도 벤더 축을 보존하고 구분자를 인코딩한다', () => {
  assert.equal(
    minuteSessionHref('2026-08-03', 'news_minute', 'vendor/a&b'),
    '/minute?date=2026-08-03&dataset=news_minute&sourceGroup=vendor%2Fa%26b',
  );
  const source = readFileSync(new URL('./IncidentsPage.tsx', import.meta.url), 'utf8');
  assert.match(source, /minuteSessionHref\(view\.date, s\.dataset, s\.sourceGroup\)/);
  assert.match(source, /const real = data !== undefined/, '실측 0건이나 보존된 직전 data를 MOCK으로 덮는다');
  assert.doesNotMatch(source, /const real =[^\n]*hasNoSignal/, '0건을 응답 부재로 다시 접었다');
});

/* 🔴 `const date = facts.minute?.date ?? facts.meta.today` 를 `facts.meta.today` 로 바꿔도
 * 전건 초록이었다(변이 실증, 2026-08-12) — 픽스처가 **두 날짜를 같은 값**으로 두고 있었다.
 * `:155-156` 주석이 이 축을 이미 명시한다: "세션 응답이 말한 날이다 — meta.today 와 다를 수
 * 있고, 다르면 드릴다운이 **없는 날짜의 세션**을 연다." 두 값을 갈라 놔야 그게 재진다. */
test('실시간 드릴다운 날짜는 세션 응답이 말한 날이다 — meta.today 로 대체하면 없는 날을 연다', () => {
  const f = {
    ...FACTS,
    meta: { db: '', aws: '', today: '2026-08-04' },
    minute: {
      /* 세션 응답이 하루 뒤처져 있다(폴링 시점 차) — 실측으로 도달하는 조합이다 */
      date: '2026-08-03',
      sessions: [
        { dataset: 'price_minute', sourceGroup: 'kis', phase: 'ACTIVE', leaseExpired: true, overdueNoEvidence: 0, deadJobs: 0 },
      ],
      deadJobsByDataset: {},
    },
  } as unknown as Facts;
  const r = investigate(
    incident(violation({ targetId: 'price_minute/kis', drill: ['dataset', 'ds-price_minute'] })),
    f,
  );
  assert.equal(r.ledger?.date, '2026-08-03', 'meta.today 가 세션 날짜를 덮었다');
  assert.match(r.targets[0].href, /date=2026-08-03/);
  assert.doesNotMatch(r.targets[0].href, /date=2026-08-04/);
});

test('사건 targetId의 첫 구분자 뒤를 벤더 전체로 보존한다', () => {
  const r = investigate(
    incident(violation({ targetId: 'news_minute/vendor/a&b', drill: ['dataset', 'ds-news_minute'] })),
    FACTS,
  );
  assert.equal(r.ledger?.sourceGroup, 'vendor/a&b');
  assert.match(r.targets[0].href, /sourceGroup=vendor%2Fa%26b/);
});

test('배치 데이터셋 사건은 실행에 매이지 않는다 — 원장을 런까지 좁히지 않는다', () => {
  const r = investigate(incident(violation({ drill: ['dataset', 'ds-investor_flow'] })), FACTS);
  assert.equal(r.targets[0].kind, 'dataset');
  assert.equal(r.ledger?.runKey, undefined, '런 키를 지어내지 않는다');
  assert.match(r.ledgerNote ?? '', /실행에 매여 있지 않아/);
});

test('산출 축 사건도 조사 대상 id 는 식별자다 — 표시 문구를 실어 보내지 않는다', () => {
  /* R13(산출 이상)은 `target` 이 라벨('게시 ETF')이고 `targetId` 가 산출 id('o.pub')다.
   * 라벨을 넘기면 추이 화면이 조회할 키가 없어 지목이 조용히 빈 화면이 된다. */
  const r = investigate(
    incident(violation({ layer: '산출', drill: ['trend', 'out-o.pub'], target: '게시 ETF', targetId: 'o.pub' })),
    FACTS,
  );
  assert.equal(r.targets[0].kind, 'output');
  assert.equal(r.targets[0].id, 'o.pub');
  assert.equal(r.ledger, null, '산출 축은 원장 근거로 좁힐 문맥이 없다');
});

test('원장 주소는 문맥이 있을 때만 만든다 — 문맥 없는 원장 열기를 만들지 않는다', () => {
  assert.equal(ledgerHref(null), null);
  assert.equal(ledgerHref({}), null);
  assert.equal(
    /* 사건 키는 **프로덕션 형상**으로 쓴다(`${rule}:${targetId}@${scope}`). 옛 위치 인덱스
     * (`R07#0`)를 두면 이 파일이 실제 vid 를 한 번도 안 밟고, `#` → `%23` 만 증명한 채
     * 실제로 도는 `:` → `%3A` · `@` → `%40` 왕복은 아무 단언도 안 잡는다. */
    ledgerHref({
      incident: 'R07:INVESTOR_COLLECTION_KIS@etf-daily:2026-08-03T15:40',
      runKey: 'etf-daily:2026-08-03T15:40',
      task: 'A',
      dataset: 'd',
    }),
    '/sources?incident=R07%3AINVESTOR_COLLECTION_KIS%40etf-daily%3A2026-08-03T15%3A40' +
      '&runKey=etf-daily%3A2026-08-03T15%3A40&task=A&dataset=d',
  );
});

/* 실 API 화면은 실행 상세로 링크하지 않는다 (ALPHA-738 단계 3).
 *
 * 이 단언이 지키는 의도: 실행 상세는 실 원장을 읽지만(ALPHA-738 D) **날짜 인자 없이** 조회해
 * 원장이 아는 **가장 최근 날 하루**만 싣는다. 실 API 화면 셋은 임의의 날짜를 다루므로 그 창 밖
 * 런으로 보내면 목적지가 부재를 말하는데, 그건 **없다는 사실이 아니라 안 물어봤다는 사실**이다.
 * 링크가 남아 있으면 사용자는 전자로 읽는다.
 * ⏭ 되살리는 조건은 `runHref` 가 그 화면의 거래일을 싣고 상세가 그 날을 조회하는 것이다(축 E).
 * 그때 이 단언은 **반대 방향으로 뒤집혀야** 한다 — 지우는 게 아니라.
 *
 * 문구가 아니라 **구조**로 검사한다 — 본문 텍스트로 부재를 검사하면 이 파일이나 대상 파일의
 * 주석에 적힌 `runHref`·`/ops/runs/` 가 걸린다. 그래서 블록 주석을 먼저 걷어낸다.
 *
 * 두 축을 함께 본다. import 바인딩만 보면 주소를 **직접 조립**해 우회할 수 있고(`<Link
 * to={`/ops/runs/${runKey}`}>`), 리터럴만 보면 헬퍼 경유를 놓친다.
 *
 * 알려진 천장(의도한 것): ① 대상 3파일이 하드코딩이라 **새로 생기는 실 API 화면은 안 본다**
 * ② 배럴 재수출 경유는 못 잡는다. 둘 다 지금 형상에 없고, 막으려면 전 파일 스캔 + 화면
 * 분류가 필요해 값이 안 맞는다. */
test('실 API 화면 3곳은 실행 상세로 가는 길을 만들지 않는다 — 상세는 하루 창만 조회한다', () => {
  const REAL_API_PAGES = ['../GridPage.tsx', '../MinutePage.tsx', '../HoldingsImpactPage.tsx'];
  for (const rel of REAL_API_PAGES) {
    /* 주석은 사실이 아니라 서술이다 — 부재 단언의 입력에서 뺀다. 블록 주석과 줄 주석
     * 둘 다 건다(줄 주석은 줄머리만 — `http://` 같은 URL 을 잘라 뒤를 숨기지 않게). */
    const src = readFileSync(new URL(rel, import.meta.url), 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '')
      .replace(/^[ \t]*\/\/.*$/gm, '');

    /* 경로 뒤 접미사를 반드시 허용한다 — 이 레포는 `from './investigation.ts'` 처럼
     * 확장자를 명시하는 곳이 있어서, `investigation` 에서 닫으면 그게 통째로 새 나간다. */
    const mod = /['"][^'"]*ops\/investigation[^'"]*['"]/.source;
    const named = [...src.matchAll(new RegExp(String.raw`import\s*\{([^}]*)\}\s*from\s*${mod}`, 'g'))]
      .flatMap((m) => m[1].split(',').map((s) => s.trim().split(/\s+as\s+/)[0]));
    assert.ok(
      !named.includes('runHref'),
      `${rel} 이 runHref 를 import 한다 — 실행 상세는 이 화면의 런을 해소하지 못한다`,
    );
    /* 네임스페이스·동적 import 는 바인딩 이름이 안 보여 위 검사를 통과한다 */
    assert.ok(
      !new RegExp(String.raw`import\s*\*\s*as\s+\w+\s*from\s*${mod}`).test(src) &&
        !new RegExp(String.raw`import\s*\(\s*${mod}`).test(src),
      `${rel} 이 investigation 을 통째로 들여온다 — runHref 를 이름 없이 쓸 수 있다`,
    );
    /* 헬퍼를 안 쓰고 주소를 직접 조립하는 우회. 실행 **목록** `/ops/runs`(끝 슬래시 없음)는
     * 정당하므로 안 걸린다 — 걸리는 건 특정 런을 지목하는 `/ops/runs/` 뿐이다. */
    assert.ok(
      !src.includes('/ops/runs/'),
      `${rel} 이 실행 상세 주소를 직접 조립한다 — 헬퍼를 우회해도 결과는 같다`,
    );
  }
});


/* ── 사건 딥링크는 CDN 을 통과해야 한다 ──────────────────────────────────────────
 * CloudFront 의 SPA fallback(`infra/terraform/modules/static-site/spa-rewrite.js`)은
 * **마지막 경로 조각에 점(.)이 있으면 정적 파일**로 갈라 index.html 을 안 준다. 대상 id 에
 * 점이 든 사건이 실재하므로(산출 `o.pub` · ETF 원장 `analyze.failed` · 체인 `batch:c.pub`)
 * vid 를 경로에 두면 **공유 링크와 새로고침만** 죽는다 — 앱 안 클릭은 서버를 안 타서 멀쩡하다.
 * 그게 vid 를 안정화한 목적 자체라, 여기서 경로 모양을 못박는다. */
test('사건 딥링크 — 점 든 대상이어도 경로 조각에 점이 없다 (CDN SPA fallback 통과)', () => {
  const dotted = violation({ rule: 'R13', target: '게시', targetId: 'o.pub' });
  const href = incidentHref({ ...dotted, vid: 'R13:o.pub' });

  const path = href.split('?')[0];
  assert.doesNotMatch(
    path.split('/').pop()!,
    /\./,
    `경로 마지막 조각에 점이 있으면 CDN 이 정적 파일로 읽는다 — ${href}`,
  );
  /* 점을 없애는 가장 쉬운 방법은 경로를 목록 주소로 만드는 것인데, 그러면 **목록 화면이
   * 잡고 vid 를 무시한다** — 딥링크가 조용히 다른 것을 여는, 이 변경이 없애려던 결함이다.
   * 점 검사만 두면 그 회귀가 통과한다(변이로 실증). */
  assert.notEqual(path, '/ops/incidents', `목록 주소다 — 사건 상세가 아니라 목록이 열린다 (${href})`);
  /* 값이 사라지지도 않아야 한다 — 점 없는 경로를 만드느라 식별자를 잘라내면 딥링크가 무의미하다 */
  assert.equal(new URLSearchParams(href.split('?')[1]).get('vid'), 'R13:o.pub');
});

test('생산자↔소비자 왕복 — 조사 문맥이 실제 세션 행과 맞는다 (문자열 모양이 아니라 사실로 검사한다)', () => {
  /* 여기까지의 단언은 전부 **손으로 만든 위반**을 넣는다 — 그러면 규칙이 합성 순서를 뒤집어도
   * (`kis/price_minute`) 이 파일은 전건 통과한다. 엔진이 실제로 낸 위반을 넣고, 나온 문맥이
   * `f.minute.sessions` 의 **실물 행과 일치하는지**로 검사한다. 두 층 사이의 규약이 이 왕복이다. */
  const f: Facts = {
    ...FACTS,
    /* `FACTS` 는 `as unknown as Facts` 라 필수 축이 비어 있다 — `investigate()` 만 부르는
     * 단언들에는 충분하지만 `evaluate()` 는 그 축들을 읽는다. 여기서만 채운다. */
    datasets: [],
    chain: { feeds: [], stages: [] },
    outputs: [],
    boundary: { published_without_delivery: 0, delivery_now_nonpublished: 0 },
    runbook: {},
    minute: {
      date: '2026-08-03',
      sessions: [
        { dataset: 'price_minute', sourceGroup: 'kis', phase: 'ACTIVE', leaseExpired: true, overdueNoEvidence: 0, deadJobs: 0 },
        { dataset: 'news_minute', sourceGroup: 'bigkinds', phase: 'ACTIVE', leaseExpired: false, overdueNoEvidence: 0, deadJobs: null },
      ],
      /* 뉴스 DEAD 는 날짜 축 집계다 — 그 사건만 벤더를 안 지목한다 */
      deadJobsByDataset: { news_minute: 3 },
    },
  };
  const ev = evaluate(f, new Date('2026-08-03T16:21:00+09:00'));
  const minute = ev.incidents.filter((i) => ['R17', 'R18', 'R19'].includes(i.root.rule));
  assert.equal(minute.length, 2, '픽스처가 실시간 사건을 안 만든다 — 이 단언이 헛돈다');

  for (const I of minute) {
    const ctx = investigate(I, f).ledger!;
    if (ctx.sourceGroup) {
      assert.ok(
        f.minute!.sessions.some((s) => s.dataset === ctx.dataset && s.sourceGroup === ctx.sourceGroup),
        `조사 문맥 ${ctx.dataset}/${ctx.sourceGroup} 에 해당하는 세션 행이 없다`,
      );
    } else {
      /* 벤더를 못 싣는 사건은 그 사실을 문장으로 밝혀야 한다 — 안 밝히면 도착한 화면이
       * "source_group 을 실어 주세요"라는 이 사건에서는 불가능한 조치를 지시한다.
       *
       * ⚠️ **규칙 id 로 못박지 않는다.** 예전엔 `R19` 로 고정했는데, 문구를 "날짜 축 집계라"에서
       * 내린 **이유가 바로** 같은 데이터셋 앵커로 계약 축 규칙(R08·R09)도 벤더 없이 여기 온다는
       * 것이었다 — 못박으면 그 경로에서 이 단언이 **틀린 메시지로** 깨진다(아래 별도 테스트). */
      const r = investigate(I, f);
      assert.match(r.ledgerNote ?? '', /벤더를 지목하지 않아/);
      assert.doesNotMatch(r.targets[0].label, /실시간 세션/, '벤더 없는 대상을 "세션"이라 불렀다');
    }
  }
});

test('벤더 없는 실시간 사건은 R19 만이 아니다 — 계약 축 규칙(R08)도 같은 앵커로 온다', () => {
  /* `price_minute` 은 **데이터셋 사실에도 있다**(동봉 스냅샷). 그 계약이 STALE 이면 R08 이
   * `targetId: 'price_minute'` · `drill: ['dataset','ds-price_minute']` 를 내고, 조사 경로의
   * 실시간 분기로 들어온다 — 벤더 없이. 예전 안내 문구는 여기서 "이 **수**는 날짜 축 집계라"
   * 라고 말했는데, R08 은 세는 값이 아예 없어(`metric: null`) 그 문장이 거짓이었다.
   * 문장을 아는 것까지만으로 내린 이유가 이 경로다. */
  const f: Facts = {
    ...FACTS,
    datasets: [
      {
        id: 'price_minute',
        contract: true,
        expected_as_of: '2026-08-03',
        actual_as_of: '2026-07-30',
      },
    ],
    chain: { feeds: [], stages: [] },
    outputs: [],
    boundary: { published_without_delivery: 0, delivery_now_nonpublished: 0 },
    runbook: {},
  };
  const ev = evaluate(f, new Date('2026-08-03T16:21:00+09:00'));
  const r08 = ev.incidents.find((i) => i.root.rule === 'R08');
  assert.ok(r08, '픽스처가 R08 을 안 만든다 — 이 단언이 헛돈다');
  const r = investigate(r08, f);
  assert.equal(r.ledger?.sourceGroup, undefined, '없는 벤더를 지어냈다');
  /* 세는 값이 없는 사건에 "이 수는 …" 이라고 쓰지 않는다 */
  assert.equal(r08.root.metric, null);
  assert.doesNotMatch(r.ledgerNote ?? '', /수는 날짜 축 집계/, '이 사건에서 거짓인 사유를 말했다');
  assert.match(r.ledgerNote ?? '', /벤더를 지목하지 않아/);
});
