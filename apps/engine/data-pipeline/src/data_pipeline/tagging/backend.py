"""태거 선택 — 배포 설정 `TAGGER_BACKEND` 가 LLM(DeepSeek) 과 로컬 RoBERTa 중 하나를 고른다 (ALPHA-1446).

진입점(run.py 의 tag-news·assemble-events, news-consumer 팩토리)이 이 모듈로 태거를 얻는다.
`llm` 이면 None 을 돌려 기존 complete_fn 배선을 그대로 쓰고, `roberta` 면 모델 묶음을 레이크
버킷에서 내려받아 `RobertaTagger` 를 만든다. 되돌리기는 env 하나다(`TAGGER_BACKEND=llm`).

모델 묶음 위치: 레이크 버킷 `models/tagger/<ROBERTA_TAGGER_VERSION>/` — 태스크 롤이 이미
버킷 전체를 읽을 수 있어 권한을 늘리지 않는다. 로컬 실행은 `TAGGER_MODEL_DIR` 로 디렉터리를
직접 준다(그때는 버킷을 안 본다).
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

from ..lake import relevance_model_prefix, tagger_model_prefix
from .extract import ROBERTA_TAGGER_VERSION, TAGGER_BACKEND

logger = logging.getLogger(__name__)

MODEL_PREFIX = tagger_model_prefix(ROBERTA_TAGGER_VERSION)

# 뉴스 관련성 판정기(ALPHA-1476) — 스레드에 붙기 전에 그 회사의 사건이 아닌 기사를 거른다. 기본은 꺼짐:
# 모델 묶음 업로드 전에 이 코드가 배포돼도 동작이 바뀌지 않는다. 되돌리기는 env 하나다(off).
# 버전은 묶음 meta.json 의 version 과 같아야 기동된다.
RELEVANCE_VERSION = "relevance-klue-base-ep1-fp32"
RELEVANCE_PREFIX = relevance_model_prefix(RELEVANCE_VERSION)


def load_tagger(storage):
    """활성 백엔드의 태거. `llm` 이면 None(호출부가 complete_fn 을 쓴다)."""
    if TAGGER_BACKEND == "llm":
        return None
    model_dir = os.environ.get("TAGGER_MODEL_DIR", "").strip() or _download(storage)
    from .roberta import RobertaTagger  # torch 는 roberta 일 때만 읽는다

    tagger = RobertaTagger(model_dir, threads=cpu_threads())
    if tagger.version != ROBERTA_TAGGER_VERSION:
        # 코드가 기대한 모델과 다른 묶음이다 — 그대로 돌면 tagger_version 이 거짓말을 한다.
        raise RuntimeError(
            f"모델 묶음 버전 불일치: 묶음={tagger.version!r} 코드={ROBERTA_TAGGER_VERSION!r}")
    return tagger


def relevance_enabled() -> bool:
    """RELEVANCE_FILTER env — on|off(기본 off). 그 밖의 값은 기동 거부(오타가 조용히 꺼짐이 되면 안 된다)."""
    raw = os.environ.get("RELEVANCE_FILTER", "").strip() or "off"
    if raw not in ("on", "off"):
        raise RuntimeError(f"RELEVANCE_FILTER 는 on 또는 off 다: {raw!r}")
    return raw == "on"


def load_relevance(storage):
    """켜져 있으면 관련성 판정기, 꺼져 있으면 None(호출부가 거르지 않는다)."""
    if not relevance_enabled():
        return None
    model_dir = os.environ.get("RELEVANCE_MODEL_DIR", "").strip() or _download(storage, RELEVANCE_PREFIX)
    from .relevance import RelevanceJudge  # torch 는 켜졌을 때만 읽는다

    judge = RelevanceJudge(model_dir, threads=cpu_threads())
    if judge.version != RELEVANCE_VERSION:
        raise RuntimeError(
            f"관련성 모델 묶음 버전 불일치: 묶음={judge.version!r} 코드={RELEVANCE_VERSION!r}")
    return judge


def cpu_threads() -> int | None:
    """추론 스레드 수 — 배포가 `TAGGER_THREADS` 로 준 값(태스크 vCPU 수). 없으면 None(보이는 CPU 수).

    Fargate 1 vCPU 태스크는 CPU 가 2개로 보이고, 할당량은 태스크 단위라 컨테이너 cgroup 에도 안
    보인다(cpu.cfs_quota_us = -1, 2026-10-24 실측). 보이는 수대로 스레드 2개를 띄우면 1 vCPU 안에서
    서로 다퉈 기사당 4.1초, 1개면 2.2초다. 크기를 아는 쪽(terraform)이 값을 넣는다.
    """
    raw = os.environ.get("TAGGER_THREADS", "").strip()
    if not raw:
        return None
    threads = int(raw)
    if threads < 1:
        raise RuntimeError(f"TAGGER_THREADS 는 1 이상이다: {raw!r}")
    return threads


def _download(storage, prefix: str = MODEL_PREFIX) -> str:
    """레이크의 모델 묶음을 임시 디렉터리로 내려받는다. 비었으면 기동을 거부한다."""
    # 끝이 `/` 인 키는 S3 콘솔이 만드는 폴더 표시(0바이트)라 파일이 아니다.
    keys = [k for k in storage.list_keys(prefix) if not k.endswith("/")]
    if not keys:
        raise RuntimeError(f"모델 묶음이 없다: {prefix} — 업로드 후 기동해야 한다")
    root = Path(tempfile.mkdtemp(prefix="tagger-")).resolve()
    for key in keys:
        path = (root / key[len(prefix):]).resolve()
        if not path.is_relative_to(root):
            # 키에 `..` 가 섞이면 묶음 밖 파일을 덮어쓴다 — 받지 않는다.
            raise RuntimeError(f"모델 묶음 밖을 가리키는 키: {key}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(storage.get_bytes(key))
    logger.info("모델 묶음 %d개 파일 내려받음: %s → %s", len(keys), prefix, root)
    return str(root)
