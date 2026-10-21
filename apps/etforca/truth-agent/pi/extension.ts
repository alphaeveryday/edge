// truth-agent 도구 확장. pi 의 기본 도구(bash·read·edit·write 등)는 실행 명령에서 모두 끄고 이 도구만 쓴다.
//
// 도구의 설명·매개변수는 Python(edge_truth_agent.toolspec)이 정본이다. 이 파일은 그 목록을 등록하고,
// 호출이 오면 `python -m edge_truth_agent tool <이름>` 을 세션 폴더에서 실행해 결과 글을 돌려줄 뿐이다.
// 도구가 실패하면(종료 코드 ≠ 0) 그 글을 오류로 던져 모델이 읽고 고치게 한다.
import { execFileSync, spawn } from "node:child_process";
import { Type } from "@earendil-works/pi-ai";
import { defineTool, type ExtensionAPI } from "@earendil-works/pi-coding-agent";

function required(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`truth-agent 확장: 환경변수 ${name} 가 없습니다`);
  return value;
}

const sessionDir = required("TRUTH_SESSION_DIR");
const python = required("TRUTH_PYTHON");
const mode = required("TRUTH_MODE");
const maxCalls = Number(process.env.TRUTH_MAX_TOOL_CALLS ?? "50");
// 예산 끝 몇 회는 마무리(노트·계획·검사·끝내기)에만 쓴다 — 조사하다 예산이 끝나 노트 없이 멈추지 않게
const reserve = Math.min(5, Math.floor(maxCalls / 4));
const WRAP_UP = new Set(["write_notes", "set_plan", "set_mover_verdict", "check", "finish"]);
const web = process.env.EXA_API_KEY ? "1" : "0";

type Spec = { name: string; description: string; parameters: Record<string, unknown> };

// 도구 프로세스에는 모델 키가 필요 없다 — pi 프로세스에만 둔다.
const { DEEPSEEK_API_KEY: _modelKey, ...toolEnv } = process.env;

function runTool(name: string, args: unknown, signal?: AbortSignal): Promise<{ ok: boolean; text: string }> {
  return new Promise((resolve, reject) => {
    const child = spawn(python, ["-m", "edge_truth_agent", "tool", name], { cwd: sessionDir, env: toolEnv, signal });
    let out = "";
    let err = "";
    child.stdout.setEncoding("utf8").on("data", (d: string) => (out += d));
    child.stderr.setEncoding("utf8").on("data", (d: string) => (err += d));
    child.on("error", reject);
    child.on("close", (code) => resolve({ ok: code === 0, text: (out || err).trim() }));
    child.stdin.end(JSON.stringify(args ?? {}));
  });
}

export default function (pi: ExtensionAPI) {
  const specs: Spec[] = JSON.parse(
    execFileSync(python, ["-m", "edge_truth_agent", "tool-specs", "--mode", mode, "--web", web], { cwd: sessionDir, encoding: "utf8" }),
  );
  let calls = 0;
  for (const spec of specs) {
    pi.registerTool(
      defineTool({
        name: spec.name,
        label: spec.name,
        description: spec.description,
        parameters: Type.Unsafe(spec.parameters),
        async execute(_toolCallId, params, signal) {
          calls += 1;
          if (calls > maxCalls - reserve && !WRAP_UP.has(spec.name)) {
            throw new Error(`도구 예산 ${maxCalls}회 중 마무리용 ${reserve}회만 남았습니다. ${[...WRAP_UP].join("·")} 로 마무리하세요.`);
          }
          const result = await runTool(spec.name, params, signal);
          const budget = `\n[도구 ${calls}/${maxCalls}회 사용 · 마지막 ${reserve}회는 마무리 전용]`;
          if (!result.ok) throw new Error((result.text || `${spec.name} 가 실패했습니다`) + budget);
          return { content: [{ type: "text", text: result.text + budget }], details: undefined, terminate: spec.name === "finish" };
        },
      }),
    );
  }
}
