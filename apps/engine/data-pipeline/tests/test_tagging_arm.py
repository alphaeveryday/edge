"""aarch64 에서 로컬 RoBERTa 태거를 돌리는 엔진 선택 테스트.

의도: aarch64 의 기본 int8 엔진(x86)으로는 묶음을 읽다 기동 때 죽는다. 그래서 arm 에서만 qnnpack 엔진으로
바꾸고, 버전에 접미사를 붙여 다른 엔진의 판정이 한 이름 아래 섞이지 않게 한다. x86 은 운영(AWS) 경로라
엔진·버전이 한 글자도 바뀌면 안 된다. torch 는 CI 에 없어서 가짜 모듈로 바꿔 읽는다.
"""

import contextlib
import importlib
import json
import os
import subprocess
import sys
import types

import pytest

BUNDLE = "tagging-roberta-y9-int8"


def _versions(machine: str, backend: str = "roberta") -> list[str]:
    """platform.machine 을 바꾼 새 프로세스에서 extract 를 읽는다 — 값이 import 시점에 정해진다."""
    env = {k: v for k, v in os.environ.items() if k != "TAGGER_BACKEND"}
    env["TAGGER_BACKEND"] = backend
    code = (f"import platform; platform.machine = lambda: {machine!r}; "
            "from data_pipeline.tagging import extract as e; "
            "print(e.TAGGER_VERSION, e.ROBERTA_RUNTIME_VERSION, e.ARM)")
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True)
    return out.stdout.split()


@pytest.mark.parametrize("machine", ["x86_64", "AMD64", "arm64"])
def test_non_aarch64_version_is_the_bundle_name(machine):
    # WHY: x86 은 운영 중인 AWS 경로다. 버전이 바뀌면 job 정체성이 갈리고, 이미 태깅한 기사가 '다른
    # 태거의 판정'이 돼 복구 실행·assemble-events 에서 다시 추론된다. macOS arm64(개발)도 기존 그대로다.
    assert _versions(machine) == [BUNDLE, BUNDLE, "False"]


def test_aarch64_records_its_engine_in_the_version():
    # WHY: qnnpack 은 x86 엔진과 숫자가 다를 수 있다 — 같은 이름으로 기록하면 두 엔진의 판정이 섞여 구분이 안 된다.
    assert _versions("aarch64") == [f"{BUNDLE}-arm-qnnpack", f"{BUNDLE}-arm-qnnpack", "True"]


def test_llm_backend_version_does_not_change_on_aarch64():
    # WHY: arm 접미사는 로컬 모델의 엔진 표시다 — LLM 판정의 버전에 붙으면 LLM 경로 기사가 재태깅 대상이 된다.
    assert _versions("aarch64", backend="llm")[0] == "tagging-v1"


# ---- RobertaTagger: 가짜 torch 로 엔진 선택과 기록 버전을 본다 ----

class _Module:
    def __init__(self, *args, **kwargs):
        self.loaded = None

    def named_children(self):
        return []

    def load_state_dict(self, state):
        # 엔진은 int8 Linear 를 만들 때(바꿔 끼우기·적재) 이미 정해져 있어야 한다 — 그 시점 값을 남긴다.
        self.loaded = (state, sys.modules["torch"].backends.quantized.engine)

    def eval(self):
        return self


@pytest.fixture
def roberta(monkeypatch):
    """torch·transformers 를 가짜로 바꾼 뒤 roberta 모듈을 새로 읽는다. 가짜 torch 의 기본 엔진은 x86."""
    torch = types.ModuleType("torch")
    torch.nn = types.ModuleType("torch.nn")
    torch.nn.Module = torch.nn.Linear = _Module
    torch.backends = types.SimpleNamespace(quantized=types.SimpleNamespace(engine="x86"))
    torch.load = lambda *a, **k: {"dc._packed_params._packed_params": "int8"}
    torch.set_num_threads = lambda n: None
    hidden = types.SimpleNamespace(hidden_size=4)
    transformers = types.ModuleType("transformers")
    transformers.AutoConfig = types.SimpleNamespace(from_pretrained=lambda p: hidden)
    transformers.AutoModel = types.SimpleNamespace(from_config=lambda c: types.SimpleNamespace(config=hidden))
    transformers.AutoModelForQuestionAnswering = types.SimpleNamespace(from_config=lambda c: _Module())
    transformers.AutoTokenizer = types.SimpleNamespace(from_pretrained=lambda p: None)
    utils = types.ModuleType("transformers.modeling_utils")
    utils.no_init_weights = contextlib.nullcontext
    for name, mod in {"torch": torch, "torch.nn": torch.nn, "transformers": transformers,
                      "transformers.modeling_utils": utils}.items():
        monkeypatch.setitem(sys.modules, name, mod)
    monkeypatch.delitem(sys.modules, "data_pipeline.tagging.roberta", raising=False)
    yield importlib.import_module("data_pipeline.tagging.roberta")
    # 가짜 torch 로 읽은 모듈이 다른 테스트에 새지 않게 패키지 속성까지 지운다.
    sys.modules.pop("data_pipeline.tagging.roberta", None)
    vars(sys.modules["data_pipeline.tagging"]).pop("roberta", None)


def _tagger(roberta, monkeypatch, tmp_path, arm: bool, suffix: str):
    monkeypatch.setattr(roberta, "ARM", arm)
    monkeypatch.setattr(roberta, "ARM_VERSION_SUFFIX", suffix)
    (tmp_path / "meta.json").write_text(json.dumps({
        "version": BUNDLE, "doc_classes": ["EVENT"], "types": ["NO_EVENT"], "max_length": 8,
        "queries": {}}), encoding="utf-8")
    return roberta.RobertaTagger(tmp_path, threads=1)


def test_x86_keeps_the_default_engine_and_the_bundle_version(roberta, monkeypatch, tmp_path):
    # WHY: x86 운영 경로 — 엔진을 건드리면 판정 숫자가, 버전을 건드리면 재태깅 축이 바뀐다.
    tagger = _tagger(roberta, monkeypatch, tmp_path, False, "")
    assert tagger.version == BUNDLE
    assert tagger._qa.loaded == ({"dc._packed_params._packed_params": "int8"}, "x86")
    assert tagger._classifier.loaded[1] == "x86"


def test_aarch64_switches_to_qnnpack_before_loading_int8(roberta, monkeypatch, tmp_path):
    # WHY: 엔진을 적재 뒤에 바꾸면 이미 x86 엔진으로 prepack 하다 죽은 뒤다. 두 모델 모두 qnnpack 으로 읽혀야 한다.
    tagger = _tagger(roberta, monkeypatch, tmp_path, True, "-arm-qnnpack")
    assert tagger.version == f"{BUNDLE}-arm-qnnpack"
    assert tagger._classifier.loaded[1] == "qnnpack"
    assert tagger._qa.loaded == ({"dc._packed_params._packed_params": "int8"}, "qnnpack")


def test_load_tagger_accepts_the_arm_version_and_still_refuses_a_wrong_bundle(monkeypatch, tmp_path):
    # WHY: 묶음 검사가 접미사를 모르면 arm 은 늘 기동 거부되고, 접미사만 보면 다른 묶음이 통과한다.
    from data_pipeline.tagging import backend

    class _Tagger:
        version = f"{BUNDLE}-arm-qnnpack"

        def __init__(self, model_dir, threads=None):
            pass

    fake = types.ModuleType("data_pipeline.tagging.roberta")
    fake.RobertaTagger = _Tagger
    monkeypatch.setitem(sys.modules, "data_pipeline.tagging.roberta", fake)
    monkeypatch.setattr(backend, "TAGGER_BACKEND", "roberta")
    monkeypatch.setattr(backend, "ROBERTA_RUNTIME_VERSION", f"{BUNDLE}-arm-qnnpack")
    monkeypatch.setenv("TAGGER_MODEL_DIR", str(tmp_path))
    assert backend.load_tagger(storage=None).version == f"{BUNDLE}-arm-qnnpack"

    _Tagger.version = "tagging-roberta-other-arm-qnnpack"
    with pytest.raises(RuntimeError, match="모델 묶음 버전 불일치"):
        backend.load_tagger(storage=None)
