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

from ..lake import tagger_model_prefix
from .extract import ROBERTA_TAGGER_VERSION, TAGGER_BACKEND

logger = logging.getLogger(__name__)

MODEL_PREFIX = tagger_model_prefix(ROBERTA_TAGGER_VERSION)


def load_tagger(storage):
    """활성 백엔드의 태거. `llm` 이면 None(호출부가 complete_fn 을 쓴다)."""
    if TAGGER_BACKEND == "llm":
        return None
    model_dir = os.environ.get("TAGGER_MODEL_DIR", "").strip() or _download(storage)
    from .roberta import RobertaTagger  # torch 는 roberta 일 때만 읽는다

    tagger = RobertaTagger(model_dir)
    if tagger.version != ROBERTA_TAGGER_VERSION:
        # 코드가 기대한 모델과 다른 묶음이다 — 그대로 돌면 tagger_version 이 거짓말을 한다.
        raise RuntimeError(
            f"모델 묶음 버전 불일치: 묶음={tagger.version!r} 코드={ROBERTA_TAGGER_VERSION!r}")
    return tagger


def _download(storage) -> str:
    """레이크의 모델 묶음을 임시 디렉터리로 내려받는다. 비었으면 기동을 거부한다."""
    keys = storage.list_keys(MODEL_PREFIX)
    if not keys:
        raise RuntimeError(f"모델 묶음이 없다: {MODEL_PREFIX} — 업로드 후 기동해야 한다")
    root = Path(tempfile.mkdtemp(prefix="tagger-")).resolve()
    for key in keys:
        path = (root / key[len(MODEL_PREFIX):]).resolve()
        if not path.is_relative_to(root):
            # 키에 `..` 가 섞이면 묶음 밖 파일을 덮어쓴다 — 받지 않는다.
            raise RuntimeError(f"모델 묶음 밖을 가리키는 키: {key}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(storage.get_bytes(key))
    logger.info("모델 묶음 %d개 파일 내려받음: %s → %s", len(keys), MODEL_PREFIX, root)
    return str(root)
