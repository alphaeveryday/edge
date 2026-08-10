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
import { incidentHref, incidentOfVid, investigate, ledgerHref, runHref } from './investigation.ts';
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

/* 실 API 화면이 실행 상세로 보낼 때는 **자기 조회 창을 함께 싣는다** (ALPHA-738 축 E).
 *
 * 이 단언은 뒤집힌 것이다 — 축 E 전에는 같은 3파일이 `runHref` 를 **import 조차 못 하게**
 * 막고 있었다. 상세의 조회가 날짜 인자 없이 돌아 원장이 아는 가장 최근 날 하루만 싣던 동안은,
 * 임의 날짜를 다루는 이 화면들의 런이 전부 창 밖으로 나가 목적지가 부재를 말했기 때문이다
 * (그건 없다는 사실이 아니라 **안 물어봤다**는 사실인데 사용자는 전자로 읽는다).
 * 창은 여전히 하루다. 바뀐 것은 **어느 하루를 물을지 보낸 쪽이 정한다**는 것뿐이라,
 * `date` 를 빠뜨린 링크는 축 E 이전과 정확히 같은 오독으로 되돌아간다.
 *
 * 문구가 아니라 **구조**로 검사한다 — 본문 텍스트로 검사하면 이 파일이나 대상 파일의
 * 주석에 적힌 `runHref`·`/ops/runs/` 가 걸린다. 그래서 주석을 먼저 걷어낸다.
 *
 * 세 축을 함께 본다. 헬퍼 경유만 보면 주소를 **직접 조립**해 우회할 수 있고(`<Link
 * to={`/ops/runs/${runKey}`}>`), 호출 인자를 안 보면 `runHref(runKey)` 한 줄로 창이 사라진다.
 *
 * ⚠️ **이 검사는 소스 텍스트만 본다 — 값은 못 본다.** `date` 키가 **적혀 있는지**까지가
 * 한계라, `{ date: 무언가 }` 가 런타임에 `undefined` 로 풀리면 그대로 통과한다(리뷰가 짚었다).
 * 그 층은 넷이 나눠 막는다:
 *   · **어느 날이 맞는 날인가**(`tradingDate` 우선, 없을 때만 런 키) — `dateOfSlot` 의 단위
 *     테스트(`domains/sources/dailyRollup.test.ts`). 순수 함수라 갈리는 입력을 직접 넣는다.
 *   · **호출부가 축을 흘리지 않는가** — `dateOfSlot` 의 `tradingDate` 가 필수라
 *     `dateOfSlot({ runKey })` 는 **컴파일되지 않는다**(tsc 가 가드다). 거래일을 모르는 자리는
 *     `{ tradingDate: null, runKey }` 로 부재를 선언한다(폴백을 별도 export 로 빼면 그 이름이
 *     곧 우회로가 된다 — 6라운드에 그렇게 했다가 되돌렸다).
 *   · **그 값이 실제로 href 에 실렸는가** — 하네스가 렌더된 주소로 잰다(`verify-execution-unit`
 *     은 `date=` 가 응답의 거래일·그 격자 칸의 날짜와 같은지 + 눌러서 실제로 열리는지).
 *   · 🔴 **상세가 그 값을 조회에 쓰는가** — **위 셋은 전부 링크 생산자만 검사한다.** 소비 배선을
 *     되돌리는 변이가 tsc·단위 전건·나머지 하네스를 통과했다(리뷰 5라운드가 실증). 배선은 이
 *     파일 아래 테스트가, 값(실제로 그 하루가 조회됐는가)은 `verify-run-page-split` 이 본다.
 *   · **날짜를 못 구한 경우** — 호출부가 링크 자체를 안 만든다(`MinutePage.RunCard` 의 `!date` ·
 *     `HoldingsImpactPage` 의 `runDate` 분기).
 * 여기 단언을 값 검사로 키우려 들지 마라 — `.tsx` 를 텍스트로 평가하는 셈이라 다음 우회에 또
 * 진다. 스텁 픽스처의 한 응답만 비틀어 값을 재려는 것도 마찬가지다(응답끼리 모순된다 — 4라운드).
 *
 * 알려진 천장(의도한 것): ① 대상 3파일이 하드코딩이라 **새로 생기는 실 API 화면은 안 본다**
 * ② 괄호 스캐너는 문자열·템플릿 안의 괄호를 구문으로 안 가른다 — 인자에 괄호 든 문자열을 두면
 * 호출 경계가 어긋난다. 둘 다 막으려면 전 파일 스캔 + 파서가 필요해 값이 안 맞는다. */
/* 주석은 사실이 아니라 서술이다 — 단언의 입력에서 뺀다. 블록 주석과 줄 주석 둘 다 건다
 * (줄 주석은 줄머리만 — `http://` 같은 URL 을 잘라 뒤를 숨기지 않게). */
function readSrc(rel: string): string {
  return readFileSync(new URL(rel, import.meta.url), 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/^[ \t]*\/\/.*$/gm, '');
}

/* `name(...)` 호출을 **괄호 깊이로** 잘라낸다 — 인자에 객체·중첩 호출·템플릿이 들어 정규식
 * 으로는 못 끊는다. 천장: 문자열·템플릿 안의 괄호를 구문으로 안 가른다(인자에 괄호 든 문자열을
 * 두면 호출 경계가 어긋난다). 파서를 들일 값이 안 맞아 그대로 둔다. */
function callsOf(src: string, name: string): string[] {
  const calls: string[] = [];
  for (let i = src.indexOf(`${name}(`); i >= 0; i = src.indexOf(`${name}(`, i + 1)) {
    let depth = 0;
    let j = i + name.length;
    for (; j < src.length; j++) {
      if (src[j] === '(') depth++;
      else if (src[j] === ')' && --depth === 0) break;
    }
    calls.push(src.slice(i, j + 1));
  }
  return calls;
}

test('실 API 화면 3곳은 실행 상세로 보낼 때 조회 창(date)을 함께 싣는다', () => {
  const REAL_API_PAGES = ['../GridPage.tsx', '../MinutePage.tsx', '../HoldingsImpactPage.tsx'];
  for (const rel of REAL_API_PAGES) {
    const src = readSrc(rel);

    /* 경로 뒤 접미사를 반드시 허용한다 — 이 레포는 `from './investigation.ts'` 처럼
     * 확장자를 명시하는 곳이 있어서, `investigation` 에서 닫으면 그게 통째로 새 나간다. */
    const mod = /['"][^'"]*ops\/investigation[^'"]*['"]/.source;
    const named = [...src.matchAll(new RegExp(String.raw`import\s*\{([^}]*)\}\s*from\s*${mod}`, 'g'))]
      .flatMap((m) => m[1].split(',').map((s) => s.trim().split(/\s+as\s+/)[0]));
    assert.ok(
      named.includes('runHref'),
      `${rel} 이 runHref 를 안 쓴다 — 실행 상세로 가는 길이 끊겼거나 주소를 직접 만든다`,
    );
    /* 헬퍼를 안 쓰고 주소를 직접 조립하는 우회. 실행 **목록** `/ops/runs`(끝 슬래시 없음)는
     * 정당하므로 안 걸린다 — 걸리는 건 특정 런을 지목하는 `/ops/runs/` 뿐이다. */
    assert.ok(
      !src.includes('/ops/runs/'),
      `${rel} 이 실행 상세 주소를 직접 조립한다 — 그 주소에는 조회 창이 안 실린다`,
    );

    /* 호출마다 본다. 파일에 `date` 를 넘기는 호출이 하나 있어도 나머지가 안 넘기면
     * 그 링크만 조용히 최신 날로 간다 — "한 군데는 하니까 괜찮다"로 접히는 자리다. */
    const calls = callsOf(src, 'runHref');
    assert.ok(calls.length > 0, `${rel} 에 runHref 호출이 없다 — import 만 남았다`);
    for (const call of calls) {
      assert.ok(
        /* `date` 라는 낱말이 아니라 **객체 키 자리**에 쓰였는지 본다.
         * ⚠️ 앞을 안 보면 문자열·템플릿 안의 `date:` 가 걸린다 — 주석은 걷었지만 리터럴은
         * 남으므로 `focus: `date:${d}:task-…`` 같은 인자가 통과했다(리뷰 8라운드).
         * `{` 나 `,` 뒤라야 프로퍼티 자리다.
         * ⚠️ **`date: undefined` 는 키가 있어도 축이 없는 것**이다 — `runHref` 가 falsy 를
         * 버리므로 주소에 `?date=` 가 안 실린다. 키만 세면 이 변이가 tsc(`extra` 의 값 타입이
         * `string | undefined`)와 이 검사를 **둘 다 통과**한다(리뷰 11라운드). */
        /[{,]\s*date\s*[,:}]/.test(call) && !/[{,]\s*date\s*:\s*undefined\b/.test(call),
        `${rel} 의 ${call.replace(/\s+/g, ' ')} 가 date 를 안 싣는다 — 상세가 가장 최근 날만 본다`,
      );
    }
  }
});

/* ── 🔴 소비 배선 — 상세가 그 `date` 를 **조회에 넘기는가** ────────────────────────────
 * 위 가드도, `dateOfSlot` 단위 테스트도, tsc 도 전부 링크 **생산자**만 본다. 소비 쪽을
 * 되돌리는 변이(`useConsoleEvaluation(date)` → `useConsoleEvaluation()`)는 인자가 옵셔널이라
 * tsc 를 통과하고 단위 전건도 통과했다(리뷰 5라운드가 실증). 그때 유일한 판별자는 브라우저
 * 하네스였는데 그건 `.dev/` 라 **레포에도 CI 에도 없다** — 링크만 고쳐 놓고 소비를 안 하면
 * 화면은 멀쩡해 보이면서 축 E 이전과 정확히 같은 창을 본다. 그래서 여기 못을 박는다.
 *
 * 값이 아니라 **배선**을 본다: 주소에서 읽은 그 식별자가 훅으로 들어가는가. 값(어느 날이 실제로
 * 조회됐는가)은 하네스 소관이고, 이 검사는 그게 없는 환경에서의 바닥이다.
 *
 * ⚠️ **이 파일도 CI 가 안 돌린다** — `.github/workflows/` 에 super-admin-ui 를 도는 PR
 * 워크플로가 없다(JVM·파이썬은 있다). 여기 있는 것은 *로컬에서 돌릴 때* 도는 가드다. */
test('실행 상세는 주소의 date 를 조회에 넘긴다 — 링크만 실어서는 아무것도 안 바뀐다', () => {
  const src = readSrc('./RunAxisPage.tsx');

  /* 훅에 무엇이 들어가는지 보려면 먼저 **무엇이 주소에서 나오는지** 잡아야 한다. 이름을 여기
   * 하드코딩하면 리팩터링에 조용히 죽으므로 바인딩에서 캐낸다. */
  const bound = src.match(/const\s+(\w+)\s*=\s*params\.get\('date'\)/)?.[1];
  assert.ok(bound, 'RunAxisPage 가 주소에서 date 를 안 읽는다 — 보낸 쪽이 실은 창이 버려진다');

  const calls = callsOf(src, 'useConsoleEvaluation');
  assert.ok(calls.length > 0, 'RunAxisPage 에 useConsoleEvaluation 호출이 없다');
  for (const call of calls) {
    assert.match(
      call,
      new RegExp(String.raw`\b${bound}\b`),
      `${call} 가 주소의 date(${bound})를 안 넘긴다 — 상세가 원장이 아는 가장 최근 날만 본다`,
    );
  }

  /* 🔴 **훅 하나로 끝나지 않는다 — 사슬이다.** `date` 는 `useConsoleEvaluation` 에서 배치 축
   * (`useConsoleFactsQuery` → `useConsoleFacts`, 여기가 실제 요청을 만든다)과 실시간 축
   * (`useMinuteStatus`)으로 갈라져 내려간다. 어느 한 홉만 떨어뜨리는 변이는 나머지 자리에
   * 인자가 살아 있어 tsc 미사용 경고도 안 뜨고 위 검사도 통과했다(홉마다 실증).
   * 배치 축이 끊기면 **부재 문구가 거짓말을 한다** — 물어본 적 없는 날을 두고 "그 날을
   * 물었는데 없었다"고 말해 운영자가 원장·런키를 의심하며 헛수사한다. */
  /* 홉은 **요청을 만드는 데까지** 이어야 한다. 중간에서 끊으면 그 아래가 그대로 구멍이다 —
   * 특히 `useConsoleFacts` 는 `queryKey` 에 날짜를 넣으므로, 거기서 축을 흘리면 react-query 가
   * **날짜별로 캐시를 나눠 두고 내용은 전부 최신 하루**를 담는다. 운영자가 7/31 과 8/3 을 나란히
   * 놓고 같은 사실을 다른 라벨로 비교하고, 새로고침으로도 안 풀린다(키가 달라 재검증이 안 붙는다). */
  for (const [file, owner, hooks] of [
    ['./shared.tsx', 'useConsoleEvaluation', ['useConsoleFactsQuery', 'useMinuteStatus']],
    ['./shared.tsx', 'useConsoleFactsQuery', ['useConsoleFacts']],
    ['../../domains/console/hooks.ts', 'useConsoleFacts', ['consoleRepository.facts']],
    ['../../domains/sources/hooks.ts', 'useMinuteStatus', ['sourcesRepository.minuteStatus']],
  ] as const) {
    const chain = readSrc(file);
    /* ⚠️ **정의를 호출로 세면 안 된다** — 이 파일들은 같은 훅을 정의도 하고 부르기도 한다.
     * 정의부 `function useConsoleFactsQuery(date?: string)` 가 매개변수 이름 때문에 그냥 통과해,
     * 실제 호출이 축을 떨어뜨려도 못 잡는다(이 검사를 처음 쓸 때 실제로 그랬다). */
    const body = chain.replace(/\bfunction\s+\w+\s*\(/g, 'function DECL(');

    /* 홉마다 "그 함수가 받은 인자 이름"이 다르므로 정의부에서 캐낸다.
     * ⚠️ 못 캐냈으면 **이 검사가 낡은 것**이지 코드가 틀린 게 아니다 — 리네임·제네릭 추가 같은
     * 회귀 없는 변경이 여기로 떨어진다. "날짜 인자를 안 받는다"로 말하면 다음 사람이 엉뚱한
     * 곳을 고치므로 사유를 그대로 밝힌다. */
    const arg = chain.match(
      new RegExp(String.raw`(?:function\s+${owner}|${owner}\s*=)\s*\(\s*(\w+)`),
    )?.[1];
    assert.ok(
      arg,
      `${file} 의 ${owner} 정의에서 날짜 인자 이름을 못 캐냈다 — 함수 형태가 바뀌었으면 ` +
        `**이 검사를 고쳐라**(회귀가 아니다). 인자를 정말 없앴다면 그건 축 유실이다`,
    );
    for (const hook of hooks) {
      const chainCalls = callsOf(body, hook);
      assert.ok(chainCalls.length > 0, `${file} 에 ${hook} 호출이 없다`);
      /* `.some()` 인 이유(위 3파일 검사는 전건인 것과 다르다): 이 파일들은 훅 정의를 겸해서
       * 같은 이름의 정당한 호출이 여러 자리에 설 수 있다. 전건을 요구하면 그런 추가 호출이
       * 회귀 없이 빨개진다. 대신 천장이 생긴다 — 날짜 없는 두 번째 호출이 첫 번째를 가린다. */
      assert.ok(
        chainCalls.some((c) => new RegExp(String.raw`\b${arg}\b`).test(c)),
        `${owner} 이 ${hook} 로 ${arg} 를 안 넘긴다 — 그 축만 오늘로 남는다`,
      );
    }
  }

  /* 구성종목 결손의 조사 경로는 **사건을 푸는 창**과 **링크가 싣는 창**이 같아야 한다(둘이
   * 갈리면 목적지가 같은 vid 를 못 찾아 breadcrumb 이 "최근 런"으로 퇴행한다). 위 홉 표는
   * `Crumb` 을 못 본다 — 인자가 구조분해라 이름을 캐낼 수 없어 따로 못을 박는다. */
  const holdings = readSrc('../HoldingsImpactPage.tsx');
  for (const call of callsOf(holdings, 'useConsoleEvaluation')) {
    assert.match(
      call,
      /\brunDate\b/,
      `${call} 가 링크와 다른 창에서 사건을 푼다 — 목적지가 그 vid 를 못 찾아 돌아갈 곳이 사라진다`,
    );
  }

  /* 알려진 천장(의도한 것): 이 표는 **하드코딩된 사슬**이라 ① `useConsoleEvaluation` 에 세 번째
   * 축이 붙어도 아무 요구를 안 하고 ② 마지막 홉인 `repository.real.ts` 의 URL 조립(값을 쓰되
   * 안 싣는 형태·인코딩 누락)은 안 본다. 둘 다 `apiClient` 를 물어 순수 단언이 안 나오는 자리라
   * 값이 안 맞는다 — 하네스가 렌더된 주소로 재는 쪽이 맞다. */
});

/* `runHref` 가 실제로 그 쿼리를 **낸다**는 것 — 위 검사들은 전부 *호출부가 뭐라고 적었나*만
 * 본다. 헬퍼 자신이 `date` 를 버리면(필터 한 줄) 3파일의 링크가 동시에, 그리고 조용히 창 밖으로
 * 나가는데 소스 텍스트 검사는 전부 통과한다. 산출물을 재는 단언이 레포 안에 이것뿐이다. */
test('runHref 가 조회 창을 실제 주소에 싣는다 — 호출부가 적었다고 실리는 게 아니다', () => {
  assert.equal(
    runHref('etf-daily:2026-08-03T15:40', { date: '2026-08-03' }),
    '/ops/runs/etf-daily%3A2026-08-03T15%3A40?date=2026-08-03',
  );
  /* 날짜를 안 주면 쿼리가 통째로 없다 — `?date=` 빈 값으로 나가면 서버가 400 을 내고, 그건
   * "안 물어봤다"와 다른 사실이 된다. 그래서 **빈 문자열도 같이 잰다**: falsy 필터를
   * `v !== undefined` 로 풀면 빈 쿼리가 나가는데, 위 두 경우만으로는 그게 안 잡힌다. */
  assert.equal(
    runHref('etf-daily:2026-08-03T15:40'),
    '/ops/runs/etf-daily%3A2026-08-03T15%3A40',
  );
  assert.equal(runHref('k', { date: '' }), '/ops/runs/k');
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
