/* 규칙 평가 결과를 UI 없이 뽑는 CLI (리뷰 계약 §5).
 *
 *   node src/rules/cli.ts [facts.json]     # 생략 시 동봉 스냅샷
 *
 * Node 23.6+ 의 네이티브 TS 실행을 전제한다(레포 Node 26). JSON 은 import attribute
 * 대신 fs 로 읽는다 — 번들러/노드 간 JSON 모듈 규약 차이를 피한다.
 */
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildReport } from './evaluate.ts';
import type { Facts } from './types.ts';

const factsPath =
  process.argv[2] ?? join(dirname(fileURLToPath(import.meta.url)), 'facts-snapshot.json');
const facts = JSON.parse(readFileSync(factsPath, 'utf8')) as Facts;
process.stdout.write(JSON.stringify(buildReport(facts), null, 2) + '\n');
