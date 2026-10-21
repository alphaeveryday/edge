"""실행 설정. 모두 환경변수에서 읽는다(ECS 태스크 정의·로컬 셸 공통)."""
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

    store: str                 # 비공개 저장소: s3://버킷[/접두어] 또는 로컬 폴더
    publish: str               # 공개 데이터 게시 위치: s3://랜딩버킷/truth/data 또는 로컬 폴더
    lake_bucket: str | None    # 뉴스 레이크 버킷. 없으면 뉴스 수집을 건너뛴다(로컬 시험용)
    deepseek_key: str          # 이 서비스 전용 키 — 다른 용도 키로 대체하지 않는다
    exa_key: str | None        # 없으면 웹 도구를 끈다
    model: str
    thinking: str
    web_dir: Path
    pi_dir: Path
    python: str
    max_sessions: int
    session_timeout: int
    max_tool_calls: int
    corpus_days: int
    kis_key: str | None = None      # 오늘의 급등주: truth 전용 KIS 실전 앱(데이터 파이프라인 키와 섞지 않는다)
    kis_secret: str | None = None
    dart_key: str | None = None     # 오늘의 급등주: truth 전용 OpenDART 키

    @classmethod
    def from_env(cls) -> "Config":
        """환경변수에서 읽는다. DEEPSEEK_RESIST_NEWS_API_KEY 는 필수다."""
        # 기본값은 저장소 배치(apps/etforca/truth-agent/src/edge_truth_agent/). 컨테이너는 환경변수로 덮는다.
        here = Path(__file__).resolve()
        default_web = here.parents[3] / "truth-web"
        return cls(
            store=_need("TRUTH_STORE"),
            publish=_need("TRUTH_PUBLISH"),
            lake_bucket=os.environ.get("TRUTH_LAKE_BUCKET") or None,
            deepseek_key=_need("DEEPSEEK_RESIST_NEWS_API_KEY"),
            exa_key=os.environ.get("EXA_API_KEY") or None,
            model=os.environ.get("TRUTH_MODEL", "deepseek/deepseek-flash"),
            thinking=os.environ.get("TRUTH_THINKING", "medium"),
            web_dir=Path(os.environ.get("TRUTH_WEB_DIR", default_web)),
            pi_dir=Path(os.environ.get("TRUTH_PI_DIR", here.parents[2] / "pi")),
            python=os.environ.get("TRUTH_PYTHON", sys.executable),
            max_sessions=int(os.environ.get("TRUTH_MAX_SESSIONS", "4")),  # 사건 세션은 하나씩 돈다: 4 × 600초 + 계획이 잠금(55분) 안에 든다
            session_timeout=int(os.environ.get("TRUTH_SESSION_TIMEOUT", "600")),
            max_tool_calls=int(os.environ.get("TRUTH_MAX_TOOL_CALLS", "50")),
            corpus_days=int(os.environ.get("TRUTH_CORPUS_DAYS", "21")),
            kis_key=os.environ.get("TRUTH_KIS_APP_KEY") or None,
            kis_secret=os.environ.get("TRUTH_KIS_APP_SECRET") or None,
            dart_key=os.environ.get("TRUTH_DART_API_KEY") or None,
        )
