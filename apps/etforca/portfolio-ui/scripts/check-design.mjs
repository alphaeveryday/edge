import { readFileSync, readdirSync, existsSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../src');
function files(dir) { return readdirSync(dir, { withFileTypes: true }).flatMap(entry => entry.isDirectory() ? files(resolve(dir, entry.name)) : [resolve(dir, entry.name)]); }
const failures = [];
for (const file of files(root).filter(file => /\.(tsx|css)$/.test(file))) {
  const text = readFileSync(file, 'utf8');
  const name = file.slice(root.length + 1).replaceAll('\\', '/');
  if (name !== 'design-system/tokens.css' && /#[\da-f]{3,8}\b|rgba?\(/i.test(text)) failures.push(`${name}: 색상은 design-system/tokens.css에서 정의하세요.`);
  if (name !== 'design-system/tokens.css' && /:root\s*\{/.test(text)) failures.push(`${name}: 토큰 원천을 중복하지 마세요.`);
  if (name.endsWith('.tsx') && name !== 'design-system/components.tsx' && /<(?:button|input|select|textarea)\b/.test(text)) failures.push(`${name}: 공통 조작부를 사용하세요.`);
}
for (const name of ['theme.css', 'report.css', 'dashboard.css']) if (existsSync(resolve(root, name))) failures.push(`${name}: 별도 테마 대신 공통 토큰과 화면 스타일을 수정하세요.`);

const tokens = Object.fromEntries([...readFileSync(resolve(root, 'design-system/tokens.css'), 'utf8').matchAll(/(--[\w-]+):\s*(#[\da-f]{6})\b/gi)].map(match => [match[1], match[2]]));
function luminance(hex) { return hex.slice(1).match(/../g).map(n => parseInt(n, 16) / 255).map(v => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4).reduce((s, v, i) => s + v * [.2126, .7152, .0722][i], 0); }
function contrast(a, b) { assert(tokens[a] && tokens[b], `정의되지 않은 대비 토큰: ${a}, ${b}`); const values = [luminance(tokens[a]), luminance(tokens[b])].sort((a, b) => a - b); return (values[1] + .05) / (values[0] + .05); }
for (const fg of ['--color-text', '--color-text-secondary', '--color-text-muted', '--color-buy', '--color-sell']) {
  for (const bg of ['--color-surface', '--color-canvas']) if (contrast(fg, bg) < 4.5) failures.push(`${fg} / ${bg}: 본문 대비 4.5:1 미달`);
}
if (contrast('--color-on-primary', '--color-primary') < 4.5) failures.push('주요 버튼 대비 4.5:1 미달');
if (contrast('--color-primary', '--color-canvas') < 4.5) failures.push('보조 버튼 hover 대비 4.5:1 미달');
for (const fg of ['--color-border-strong', '--color-primary']) {
  for (const bg of ['--color-surface', '--color-surface-subtle']) if (contrast(fg, bg) < 3) failures.push(`${fg} / ${bg}: 입력 경계·초점 대비 3:1 미달`);
}
for (let i = 1; i <= 6; i++) if (contrast(`--chart-${i}`, '--color-surface') < 3) failures.push(`차트 ${i}: 그래픽 대비 3:1 미달`);
assert.deepEqual(failures, [], failures.join('\n'));
console.log('Orca UI: 단일 토큰 원천·공통 조작부·금융 색상 대비 검사 통과');
