import assert from 'node:assert/strict';
import { test } from 'node:test';
import { ALIASES, findEtf, normalize, transcribeKeywords } from './keywords.ts';
import { QUEUE, REPORTS } from '../data/reports.ts';

test('전사 표기(한글 읽기·띄어쓰기·영문)를 같은 꼴로 맞춘다', () => {
  assert.equal(normalize('코덱스 로봇 액티브'), 'kodex로봇액티브');
  assert.equal(normalize('KODEX 로봇액티브'), 'kodex로봇액티브');
  assert.equal(normalize('타이거 K방산&우주'), 'tigerk방산우주');
  assert.equal(normalize('에이스 케이반도체 탑투 플러스'), 'acek반도체top2플러스');
});

test('고객이 말한 종목을 찾는다 — 정식 이름과 말로 줄여 부르는 이름 모두', () => {
  assert.equal(findEtf('코덱스 로봇 액티브 오늘 왜 올랐어요?'), '445290');
  assert.equal(findEtf('그 반도체 후공정 들어간 거요'), '475310');
  assert.equal(findEtf('에이스 반도체 탑투는요'), '0210A0');
  assert.equal(findEtf('바이오 쪽은 어때요'), '261070');
  assert.equal(findEtf('오늘 날씨 좋네요'), null);
  assert.equal(findEtf('반도체가 요즘'), null, '"반도체" 하나로는 종목을 정할 수 없다');
});

test('겹치는 이름은 더 길게 맞는 쪽 — K방산&우주를 SOL K방산으로 잘못 보내지 않는다', () => {
  assert.equal(findEtf('타이거 케이방산앤우주'), '463250');
  assert.equal(findEtf('솔 케이방산'), '490480');
});

test('이름이 조각나 들어와도 이어 읽으면 더 긴 이름으로 바뀐다 — 실제 전사 조각("K방산&우" | "주는")', () => {
  assert.equal(findEtf('그럼 타이거 K방산&우'), '490480');
  assert.equal(findEtf('그럼 타이거 K방산&우주는 오늘'), '463250');
});

test('여러 종목이 나오면 가장 최근에 말한 종목', () => {
  assert.equal(findEtf('로봇 얘기는 됐고 방산은요'), '490480');
});

test('별칭 표는 데모 종목 전부를 덮고, 전사 용어는 서버 한도 안이다', () => {
  const codes = [...REPORTS, ...QUEUE].map((r) => r.code).sort();
  assert.deepEqual(Object.keys(ALIASES).sort(), codes);
  for (const r of [...REPORTS, ...QUEUE]) assert.equal(findEtf(r.name), r.code, r.name);
  const kw = transcribeKeywords([...REPORTS, ...QUEUE].map((r) => r.name));
  assert.ok(kw.length <= 80 && kw.every((k) => k.length <= 40));
});
