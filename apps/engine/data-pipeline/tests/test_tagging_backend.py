"""태거 선택 테스트 (ALPHA-1446).

의도: TAGGER_BACKEND 가 LLM 경로를 기본으로 지키고(배포만으로 동작이 바뀌지 않는다), roberta 일 때
모델 묶음이 없으면 기동을 거부해야 한다 — 조용히 LLM 으로 떨어지거나 빈 태거로 돌면 어느 모델이
판정했는지가 거짓이 된다. RobertaTagger 자체(torch)는 여기서 읽지 않는다.
"""

import os
import subprocess
import sys

import pytest

from data_pipeline.lake import LocalStorage
from data_pipeline.tagging import backend


def test_default_backend_is_llm_when_env_is_unset():
    # WHY: 기본값이 roberta 로 바뀌면 모델 묶음 업로드·태스크 크기 조정 전에 배포된 이미지가 바로
    # 로컬 모델로 기동하다 죽는다. 값은 import 시점에 정해지므로 env 를 지운 새 프로세스에서 본다.
    env = {k: v for k, v in os.environ.items() if k != "TAGGER_BACKEND"}
    out = subprocess.run(
        [sys.executable, "-c", "from data_pipeline.tagging import extract as e; "
                               "print(e.TAGGER_BACKEND, e.TAGGER_VERSION == e.LLM_TAGGER_VERSION)"],
        env=env, capture_output=True, text=True, check=True)
    assert out.stdout.split() == ["llm", "True"]


def test_llm_backend_keeps_the_complete_fn_path(monkeypatch):
    # WHY: 기본값이 llm 이라 모델 업로드·태스크 크기 조정 전에 배포돼도 기존 경로 그대로다.
    monkeypatch.setattr(backend, "TAGGER_BACKEND", "llm")
    assert backend.load_tagger(storage=None) is None


def test_roberta_backend_refuses_to_start_without_the_bundle(monkeypatch, tmp_path):
    # WHY: 묶음 없이 기동하면 job 마다 실패해 재시도 예산만 태운다 — 기동 시점에 크게 죽어야 한다.
    monkeypatch.setattr(backend, "TAGGER_BACKEND", "roberta")
    monkeypatch.delenv("TAGGER_MODEL_DIR", raising=False)
    with pytest.raises(RuntimeError, match="모델 묶음이 없다"):
        backend.load_tagger(LocalStorage(root=str(tmp_path)))


def test_download_keeps_the_bundle_layout(tmp_path):
    # WHY: RobertaTagger 는 meta.json·classifier/·qa/·tokenizer/ 상대 경로로 읽는다.
    storage = LocalStorage(root=str(tmp_path / "lake"))
    storage.put_bytes(backend.MODEL_PREFIX + "meta.json", b"{}")
    storage.put_bytes(backend.MODEL_PREFIX + "qa/weights.pt", b"w")
    root = backend._download(storage)
    assert (tmp_path / root / "meta.json").read_bytes() == b"{}"
    assert (tmp_path / root / "qa" / "weights.pt").read_bytes() == b"w"


def test_download_refuses_keys_that_escape_the_bundle(tmp_path):
    # WHY: 키에 `..` 가 섞이면 묶음 밖(컨테이너의 다른 파일)을 덮어쓴다 — 받지 않고 기동을 멈춘다.
    class _Storage:
        def list_keys(self, prefix):
            return [prefix + "../escape.txt"]

        def get_bytes(self, key):
            return b"x"

    with pytest.raises(RuntimeError, match="묶음 밖"):
        backend._download(_Storage())


def test_threads_come_from_the_deployed_task_size(monkeypatch):
    # WHY: Fargate 1 vCPU 태스크는 CPU 가 2개로 보이고 할당량도 컨테이너에 안 보인다 — 보이는 수로
    # 스레드를 띄우면 서로 다퉈 기사당 4.1초(1개면 2.2초)라 9시 피크(시간당 약 1,000건)를 못 따라간다.
    monkeypatch.setenv("TAGGER_THREADS", "1")
    assert backend.cpu_threads() == 1
    monkeypatch.delenv("TAGGER_THREADS")
    assert backend.cpu_threads() is None          # 안 주면 보이는 CPU 수(RobertaTagger 기본)
    monkeypatch.setenv("TAGGER_THREADS", "0")
    with pytest.raises(RuntimeError, match="1 이상"):
        backend.cpu_threads()
