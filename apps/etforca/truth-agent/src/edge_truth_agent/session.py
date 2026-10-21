"""사건 세션 하나를 pi 로 실행한다.

pi 는 기본 도구·확장·스킬·컨텍스트 파일을 모두 끄고(--no-*), 우리 확장(pi/extension.ts)만 싣는다.
세션이 끝났는지는 모델 글이 아니라 작업 폴더의 state.json(finish 도구가 쓴다)으로 판단한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
import os
from pathlib import Path
import signal
import subprocess

from .workspace import Workspace

log = logging.getLogger(__name__)
PROMPTS = Path(__file__).resolve().parent / "prompts"
SKILLS = {"plan": "plan-day.md", "update": "update-case.md", "discover": "discover-cases.md"}  # 세션 종류별 덧붙일 지침


@dataclass
class SessionResult:
    """세션 결과. done=True 이고 검사를 통과한 사건만 게시 후보가 된다."""

    case_id: str
    done: bool
    summary: str = ""
    error: str = ""
    tool_calls: int = 0
    tool_errors: int = 0
    usage: dict = field(default_factory=lambda: {"input": 0, "output": 0, "cacheRead": 0, "cost": 0.0})


def command(cfg, ws: Workspace, mode: str, prompt: str) -> list[str]:
    """pi 실행 명령. 허용 도구는 확장이 등록한 목록과 같다."""
    from .toolspec import specs
    names = ",".join(s["name"] for s in specs(mode, web=bool(cfg.exa_key)))
    cli = cfg.pi_dir / "node_modules" / "@earendil-works" / "pi-coding-agent" / "dist" / "cli.js"
    return ["node", str(cli), "--mode", "json", "--no-session", "--offline",
            "--no-extensions", "--no-mcp", "--no-skills", "--no-prompt-templates", "--no-themes", "--no-context-files",
            "-e", str(cfg.pi_dir / "extension.ts"), "--tools", names,
            "--model", cfg.model, "--thinking", cfg.thinking,
            "--system-prompt", str(PROMPTS / "system.md"),
            "--append-system-prompt", str(PROMPTS / SKILLS[mode]),
            prompt]


def _env(cfg, ws: Workspace, mode: str, corpus_path: Path, cases_dir: Path) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in ("DEEPSEEK_RESIST_NEWS_API_KEY",)}
    env.update({
        "DEEPSEEK_API_KEY": cfg.deepseek_key,  # pi 의 DeepSeek 제공자가 읽는 이름. 이 프로세스에만 넘긴다
        "PI_CODING_AGENT_DIR": str(ws.root / ".pi"), "PI_OFFLINE": "1", "PI_TELEMETRY": "0",
        "TRUTH_SESSION_DIR": str(ws.root), "TRUTH_MODE": mode, "TRUTH_PYTHON": cfg.python,
        "TRUTH_MAX_TOOL_CALLS": str(cfg.max_tool_calls), "TRUTH_CORPUS": str(corpus_path),
        "TRUTH_CASES_DIR": str(cases_dir), "TRUTH_WEB_DIR": str(cfg.web_dir),
    })
    if cfg.exa_key:
        env["EXA_API_KEY"] = cfg.exa_key
    else:
        env.pop("EXA_API_KEY", None)
    return env


def run(cfg, ws: Workspace, *, case_id: str, mode: str, prompt: str, corpus_path: Path, cases_dir: Path) -> SessionResult:
    """세션을 실행하고 결과를 돌려준다. 시간 초과·비정상 종료는 error 에 적는다(실행 전체를 멈추지 않는다)."""
    result = SessionResult(case_id=case_id, done=False)
    (ws.root / ".pi").mkdir(exist_ok=True)
    # 새 프로세스 그룹으로 띄워, 시간 초과 때 pi 가 띄운 도구 프로세스(Python·Chromium)까지 함께 끝낸다.
    proc = subprocess.Popen(command(cfg, ws, mode, prompt), cwd=ws.root, env=_env(cfg, ws, mode, corpus_path, cases_dir),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=os.name == "posix")
    try:
        stdout, stderr = proc.communicate(timeout=cfg.session_timeout)
        if proc.returncode != 0:
            result.error = f"pi 종료 코드 {proc.returncode}: {stderr.decode('utf-8', 'replace')[-500:]}"
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
        stdout, _ = proc.communicate()
        result.error = f"{cfg.session_timeout}초 안에 끝나지 않아 멈췄습니다."
    _read_events(stdout, result)
    state = ws.state
    result.done, result.summary = bool(state["done"]), state.get("summary", "")
    log.info(json.dumps({"event": "session_end", "case": case_id, "mode": mode, "done": result.done, "summary": result.summary,
                         "error": result.error, "toolCalls": result.tool_calls, "toolErrors": result.tool_errors,
                         "usage": result.usage}, ensure_ascii=False))
    return result


def _read_events(stdout: bytes, result: SessionResult) -> None:
    """pi JSON 이벤트에서 도구 호출·사용량·모델 오류를 모은다. 글자 단위 스트림 이벤트는 기록하지 않는다(로그 비용)."""
    last_error = ""
    for raw in stdout.split(b"\n"):  # 레코드 구분은 LF 뿐이다(문자열 안의 유니코드 줄바꿈은 구분이 아니다)
        raw = raw.rstrip(b"\r")
        if not raw:
            continue
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        kind = event.get("type")
        if kind == "tool_execution_end":
            result.tool_calls += 1
            failed = bool(event.get("isError"))
            result.tool_errors += failed
            entry = {"event": "tool", "case": result.case_id, "tool": event.get("toolName"), "error": failed}
            if failed:  # 실패 사유만 남긴다(성공 결과는 길고 사건 내용이라 로그에 싣지 않는다)
                text = " ".join(c.get("text", "") for c in (event.get("result") or {}).get("content", []) if isinstance(c, dict))
                entry["message"] = text[:300]
            log.info(json.dumps(entry, ensure_ascii=False))
        elif kind == "message_end" and event.get("message", {}).get("role") == "assistant":
            message = event["message"]
            usage = message.get("usage") or {}
            for key in ("input", "output", "cacheRead"):
                result.usage[key] += usage.get(key, 0) or 0
            result.usage["cost"] += (usage.get("cost") or {}).get("total", 0) or 0
            # pi 는 json 모드에서 모델 오류(키 만료·한도·5xx)에도 종료 코드 0 이다. 마지막 응답의 중단 사유로 드러낸다.
            stop = message.get("stopReason")
            last_error = f"모델 응답 {stop}: {str(message.get('errorMessage') or '')[:300]}" if stop in ("error", "aborted") else ""
    if last_error and not result.error:
        result.error = last_error
