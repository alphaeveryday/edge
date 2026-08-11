/* 규칙 평가 결과를 UI 없이 뽑는 CLI (리뷰 계약 §5).
 *
 *   pnpm --filter super-admin-ui eval:rules            # src/ 워크스페이스 어디서나
 *   node src/rules/cli.ts [facts.json]                 # super-admin-ui 패키지 루트에서
 *
 * ⚠️ **Node 23.6+ 가 필요하다** — `.ts` 를 네이티브로 실행한다. 레포에 버전 핀(`engines`·
 * `.nvmrc`)이 없고 배포 워크플로는 Node 20 이라(번들러가 TS 를 지운다) 이 명령의 전제는
 * 로컬·CI(`ui-test.yml` 은 Node 24) 환경에만 선다. JSON 은 import attribute 대신 fs 로 읽는다 —
 * 번들러/노드 간 JSON 모듈 규약 차이를 피한다.
 */
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
/* `process` 를 전역으로 쓰지 않고 모듈에서 가져온다 — Node 진입점이 앰비언트 전역에 기대지
 * 않는 쪽이 맞다(`types` 를 어떻게 두든 이 파일은 그대로 선다).
 * ⚠️ 이게 **브라우저 코드를 지켜 주지는 않는다** — 실측하면 `process` 전역은 `rules/` 를
 * tsconfig 에서 통째로 빼도 이 패키지에서 타입 검사를 통과한다(`vite.config.ts` 경로로 Node
 * 타입이 이미 들어온다). 그 구멍을 막는 것은 이 import 가 아니다. */
import { argv, stdout } from 'node:process';
import { fileURLToPath } from 'node:url';
import { buildReport } from './evaluate.ts';
import type { Facts } from './types.ts';

const factsPath = argv[2] ?? join(dirname(fileURLToPath(import.meta.url)), 'facts-snapshot.json');
const facts = JSON.parse(readFileSync(factsPath, 'utf8')) as Facts;
stdout.write(JSON.stringify(buildReport(facts), null, 2) + '\n');
