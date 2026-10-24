"""종목 하나를 pi 세션으로 조사한다. 실행·이벤트 읽기는 truth-agent 세션과 같고, 명령·환경만 이 에이전트 것이다."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import signal
import subprocess

from edge_truth_agent.session import SessionResult, _read_events
from edge_truth_agent.workspace import Workspace

log = logging.getLogger(__name__)
PROMPTS = Path(__file__).resolve().parent / "prompts"
__all__ = ["SessionResult", "run", "command"]


def command(cfg, prompt: str, *, graph: bool = False, system: Path | None = None) -> list[str]:
    """pi 실행 명령. 허용 도구는 확장이 등록한 목록과 같다(그래프 도구는 graph 일 때만)."""
    from .toolspec import specs
    names = ",".join(s["name"] for s in specs(web=bool(cfg.exa_key), graph=graph))
    cli = cfg.pi_dir / "node_modules" / "@earendil-works" / "pi-coding-agent" / "dist" / "cli.js"
    return ["node", str(cli), "--mode", "json", "--no-session", "--offline",
            "--no-extensions", "--no-mcp", "--no-skills", "--no-prompt-templates", "--no-themes", "--no-context-files",
            "-e", str(cfg.pi_dir / "extension.ts"), "--tools", names,
            "--model", cfg.model, "--thinking", cfg.thinking,
            "--system-prompt", str(system or PROMPTS / "system.md"),
            prompt]


def _graph(cfg, ws: Workspace) -> bool:
    """그래프 도구를 여는가: 국내 종목 세션이고 그래프 DB 주소가 있을 때(사건 그래프는 국내 뉴스·공시로 만든다)."""
    from .markets import US, of_exchange
    return bool(cfg.graph_uri) and of_exchange((ws.mover() or {}).get("market", "")) is not US


def _system(ws: Workspace) -> Path:
    """그래프 도구를 여는 세션의 시스템 지시: 기본 지시 + 사건 메뉴(코드가 온톨로지에서 만든다)."""
    from . import graph
    path = ws.root / ".pi" / "system.md"
    path.write_text((PROMPTS / "system.md").read_text(encoding="utf-8").rstrip() +
                    "\n\n## 사건 메뉴 (company_events·find_events 의 타입·수치 코드)\n"
                    "형식: 타입코드 라벨 | 술어 | 역할(*필수) | 수치코드(단위계열)\n" + graph.menu() + "\n", encoding="utf-8")
    return path


def _env(cfg, ws: Workspace, corpus_path: Path) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in ("DEEPSEEK_RESIST_NEWS_API_KEY", "TRUTH_DB_PASSWORD")}
    env.update({
        "DEEPSEEK_API_KEY": cfg.deepseek_key,  # pi 의 DeepSeek 제공자가 읽는 이름. 이 프로세스에만 넘긴다
        "PI_CODING_AGENT_DIR": str(ws.root / ".pi"), "PI_OFFLINE": "1", "PI_TELEMETRY": "0",
        "TRUTH_SESSION_DIR": str(ws.root), "TRUTH_PYTHON": cfg.python, "TRUTH_MAX_TOOL_CALLS": str(cfg.max_tool_calls),
        "TRUTH_CORPUS": str(corpus_path), "TRUTH_WEB_CALL_LIMIT": str(cfg.web_calls),
    })
    env["TRUTH_GRAPH"] = "1" if _graph(cfg, ws) else "0"  # pi 확장이 그래프 도구를 등록할지
    if cfg.exa_key:
        env["EXA_API_KEY"] = cfg.exa_key
    else:
        env.pop("EXA_API_KEY", None)
    return env


def run(cfg, ws: Workspace, *, code: str, prompt: str, corpus_path: Path) -> SessionResult:
    """세션을 실행하고 결과를 돌려준다. 시간 초과·비정상 종료는 error 에 적는다(실행 전체를 멈추지 않는다)."""
    result = SessionResult(case_id=code, done=False)
    (ws.root / ".pi").mkdir(exist_ok=True)
    graph = _graph(cfg, ws)
    cmd = command(cfg, prompt, graph=graph, system=_system(ws) if graph else None)
    proc = subprocess.Popen(cmd, cwd=ws.root, env=_env(cfg, ws, corpus_path),
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
    log.info(json.dumps({"event": "session_end", "code": code, "done": result.done, "error": result.error,
                         "toolCalls": result.tool_calls, "toolErrors": result.tool_errors, "usage": result.usage}, ensure_ascii=False))
    return result
