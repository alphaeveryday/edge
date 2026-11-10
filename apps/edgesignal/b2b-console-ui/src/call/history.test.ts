import assert from 'node:assert/strict';
import { test } from 'node:test';
import { EMPTY, addText, currentCode, flush, latestAnswer, meaningful, pendingTurn, setAnswer, splitSentences, startCall } from './history.ts';

test('문장이 끝나야 내역에 올라가고, 끝나지 않은 꼬리는 남겨 둔다', () => {
  assert.deepEqual(splitSentences('안녕하세요. 오늘 왜 올랐어요? 지금 더'), { done: ['안녕하세요.', '오늘 왜 올랐어요?'], rest: '지금 더' });
  let h = addText(startCall(EMPTY, 0), '코덱스 로봇 액', 1);
  assert.equal(h.segments[0].sentences.length, 0);
  h = addText(h, '티브 오늘 왜 올랐어요? 지금', 2);
  assert.equal(h.segments[0].sentences[0].text, '코덱스 로봇 액티브 오늘 왜 올랐어요?');
  assert.equal(h.partial, '지금');
});

test('문장부호 없이 길게 이어지면 띄어쓰기에서 끊는다', () => {
  const long = '그러니까 '.repeat(40);
  const { done, rest } = splitSentences(long);
  assert.equal(done.length, 1);
  assert.ok(done[0].length <= 120 && rest.length < long.length);
});

test('종목이 바뀔 때마다 새 구간 — 같은 종목 이야기는 한 구간에 이어진다', () => {
  let h = startCall(EMPTY, 0);
  h = addText(h, '여보세요. 저 김지현인데요. ', 1);
  h = addText(h, '코덱스 로봇 액티브 왜 올랐어요? 많이 올랐네요. ', 2);
  h = addText(h, '아 그렇군요. 타이거 케이방산 앤 우주는요? ', 3);
  assert.deepEqual(
    h.segments.map((s) => [s.code, s.sentences.length]),
    [
      [null, 2],
      ['445290', 3], // "아 그렇군요."는 아직 로봇 이야기다
      ['463250', 1],
    ],
  );
  assert.equal(currentCode(h), '463250');
});

test('통화마다 구간이 새로 시작된다 — 지난 통화 내역은 남는다', () => {
  let h = addText(startCall(EMPTY, 0), '로봇 왜 올랐어요? ', 1);
  h = startCall(h, 10);
  h = addText(h, '바이오는요? ', 11);
  assert.deepEqual(
    h.segments.map((s) => [s.call, s.code]),
    [
      [1, '445290'],
      [2, '261070'],
    ],
  );
});

test('답변 단위 — 종목이 없어도 답할 말이면 차례가 되고, 맞장구는 빠진다', () => {
  let h = addText(startCall(EMPTY, 0), '여보세요? 네. ', 1);
  assert.equal(pendingTurn(h), null, '인사·맞장구에는 답하지 않는다');
  h = addText(h, '요즘 시장이 많이 오르던데 수수료는 얼마예요? ', 2);
  const t = pendingTurn(h)!;
  assert.equal(t.code, null, '종목이 없으면 시장 전체로 답한다');
  assert.ok(t.asked);
  h = setAnswer(h, t.sentence.id, { answer: '답', answering: false });
  assert.equal(pendingTurn(h), null, '답한 뒤엔 새 말이 쌓일 때까지 차례가 없다');
  h = addText(h, '아 그렇군요. 로봇 쪽도 좀 궁금해서요. 요즘 뉴스를 보니까 많이 나오더라고요. ', 3);
  const t2 = pendingTurn(h)!;
  assert.equal(t2.code, '445290');
  assert.equal(t2.asked, false, '질문이 없으면 화면이 말이 멈추길 기다린다');
  assert.equal(t2.text, '로봇 쪽도 좀 궁금해서요. 요즘 뉴스를 보니까 많이 나오더라고요.', '차례 전체를 한 번에 넘긴다');
});

test('맞장구 판정', () => {
  for (const x of ['네.', '아, 그렇군요.', '감사합니다!', '여보세요?', '음...']) assert.equal(meaningful(x), false, x);
  for (const x of ['수수료는 얼마예요?', '지금 더 사도 괜찮을까요?']) assert.equal(meaningful(x), true, x);
});

test('지난 통화에서 답한 말은 이번 통화 차례에 섞이지 않는다', () => {
  let h = addText(startCall(EMPTY, 0), '로봇 왜 올랐어요? ', 1);
  h = startCall(h, 10);
  assert.equal(pendingTurn(h), null);
  h = addText(h, '바이오는 어때요? ', 11);
  assert.equal(pendingTurn(h)?.code, '261070');
});

test('숫자의 점("1.5%")에서 문장을 끊지 않는다 — 조각이 "1." 에서 끊겨 와도', () => {
  assert.deepEqual(splitSentences('오늘 1.5% 올랐는데 왜 그래요? 그리고'), { done: ['오늘 1.5% 올랐는데 왜 그래요?'], rest: '그리고' });
  let h = addText(startCall(EMPTY, 0), '로봇이 2.', 1);
  assert.equal(h.segments[0].sentences.length, 0);
  h = addText(h, '84% 올랐네요. ', 2);
  assert.equal(h.segments[0].sentences[0].text, '로봇이 2.84% 올랐네요.');
});

test('통화가 끝나면 문장부호 없이 남은 말도 내역에 남는다', () => {
  const h = flush(addText(startCall(EMPTY, 0), '네 알겠습니다 감사합니다', 1), 2);
  assert.equal(h.partial, '');
  assert.equal(h.segments[0].sentences[0].text, '네 알겠습니다 감사합니다');
});

test('두 번째 통화의 탭에는 지난 고객에게 추천한 답변을 보여 주지 않는다', () => {
  let h = addText(startCall(EMPTY, 0), '로봇 왜 올랐어요? ', 1);
  h = setAnswer(h, pendingTurn(h)!.sentence.id, { answer: '지난 고객 답변', answering: false });
  h = addText(startCall(h, 10), '로봇 얘기 좀 해 주세요. ', 11);
  assert.equal(latestAnswer(h, '445290'), null);
});

