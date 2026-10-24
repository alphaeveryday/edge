"""실행 설정. 모두 환경변수에서 읽는다(ECS 태스크 정의·로컬 셸 공통). 키는 truth-agent 와 같은 시크릿을 쓴다."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import sys


class ConfigError(RuntimeError):
    """필수 설정이 없다."""


def _need(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(f"환경변수 {name} 가 필요합니다.")
    return value


@dataclass(frozen=True)
class Config:
    """한 번의 실행 설정."""

    store: str                 # 비공개 저장소(truth-agent 와 같은 버킷): 뉴스 말뭉치를 읽고 급등주 상태를 쓴다
    publish: str               # 공개 데이터 게시 위치: s3://랜딩버킷/truth/data 또는 로컬 폴더
    deepseek_key: str
    exa_key: str | None        # 없으면 웹 도구를 끈다
    model: str
    thinking: str
    pi_dir: Path
    python: str
    session_timeout: int
    max_tool_calls: int
    web_calls: int             # 세션당 웹 검색·원문 가져오기 횟수
    premise_timeout: int = 300     # 전제 검증 세션(ACH 동점일 때 한 번 더)의 추가 예산: 시간(초)
    premise_tool_calls: int = 24   # ·도구 호출 수
    premise_web_calls: int = 6     # ·웹 검색·원문 가져오기 횟수

    @classmethod
    def from_env(cls) -> "Config":
        """환경변수에서 읽는다. DEEPSEEK_RESIST_NEWS_API_KEY 는 필수다."""
        here = Path(__file__).resolve()
        return cls(
            store=_need("TRUTH_STORE"),
            publish=_need("TRUTH_PUBLISH"),
            deepseek_key=_need("DEEPSEEK_RESIST_NEWS_API_KEY"),
            exa_key=os.environ.get("EXA_API_KEY") or None,
            model=os.environ.get("TRUTH_MODEL", "deepseek/deepseek-flash"),
            thinking=os.environ.get("TRUTH_THINKING", "medium"),
            pi_dir=Path(os.environ.get("TRUTH_PI_DIR", here.parents[2] / "pi")),
            python=os.environ.get("TRUTH_PYTHON", sys.executable),
            session_timeout=int(os.environ.get("TRUTH_SESSION_TIMEOUT", "420")),
            max_tool_calls=int(os.environ.get("TRUTH_MAX_TOOL_CALLS", "40")),
            web_calls=int(os.environ.get("TRUTH_WEB_CALL_LIMIT", "10")),
            premise_timeout=int(os.environ.get("TRUTH_PREMISE_TIMEOUT", "300")),
            premise_tool_calls=int(os.environ.get("TRUTH_PREMISE_TOOL_CALLS", "24")),
            premise_web_calls=int(os.environ.get("TRUTH_PREMISE_WEB_CALLS", "6")),
        )
