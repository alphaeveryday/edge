"""로컬 RoBERTa 태거 — 기사 1건 → LLM 과 같은 모양의 추출 dict (ALPHA-1446).

DeepSeek 호출(`llm.py`)을 대신한다. 모델 둘을 CPU 에서 돌린다:
  - 분류기: 제목+리드 → 문서 분류(5) + 사건 타입(53 + NO_EVENT)
  - QA 추출기: (타입·역할 질의, 제목+리드) → 역할별 원문 구간

산출은 `extract._parse_response` 가 내는 dict 와 같은 모양이다
(`{"doc_class", "events": [{"event_type_code", "predicate_code", "arguments", "confidence"}]}`).
그래서 검증(`extract._validate_event`)은 LLM 경로와 같은 코드를 지난다 — 허용 밖 역할은
거기서 떨어진다. 술어는 모델이 내지 않는다(None → 온톨로지 기본값, predicate_source 로 구분).

**모델 묶음**(`model_dir`)은 `scripts/export_tagger.py` 가 학습 산출물에서 만든다. 가중치는 미리 int8 로 변환한 state_dict 라
`weights_only=True` 로 읽는다(피클 실행 없음). 질의 문구는 학습 때와 글자까지 같아야
해서 코드가 아니라 묶음의 `meta.json` 에 실린다:

    meta.json            version · doc_classes · types · max_length · queries{type: [[role, query]]}
    classifier/          config.json + weights.pt   (인코더 + 문서분류·타입 머리)
    qa/                  config.json + weights.pt   (질의응답 구간 추출)
    tokenizer/           토크나이저 파일

정확도 근거(아웃 샘플 3,349건, int8): 타입 0.914, 인자 완화 F1 0.766 — fp32 와 차이 없음.

torch·transformers 를 모듈 맨 위에서 읽는다 — 호출부는 `TAGGER_BACKEND=roberta` 일 때만
이 모듈을 import 하므로(`tagging.backend`), LLM 경로·단위 테스트는 torch 없이 돈다.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import torch
from torch import nn
from transformers import AutoConfig, AutoModel, AutoModelForQuestionAnswering, AutoTokenizer
from transformers.modeling_utils import no_init_weights

# 모델 문서 분류 → 운영 어휘(extract.DOC_CLASSES). LIST(공시·종목 나열)는 운영에 없는
# 축이라 사건 없는 시세 나열로 합친다 — 둘 다 추출 대상이 아니라는 점이 같다.
DOC_CLASS_MAP = {
    "EVENT": "EVENT",
    "OPINION_OR_ANALYSIS": "OPINION_OR_ANALYSIS",
    "MARKET_COMMENTARY": "NO_EVENT_MARKET_COMMENTARY",
    "LIST": "NO_EVENT_MARKET_COMMENTARY",
    "PROMOTIONAL": "PROMOTIONAL_OR_SOLICITATION",
}
NO_EVENT = "NO_EVENT"
# 구간 후보: 시작·끝 상위 20개, 길이 40 토큰 이내 — 학습 쪽 decode 와 같은 값.
_TOP_SPANS = 20
_MAX_SPAN_TOKENS = 40


def _context(title: str, lead: str | None) -> str:
    """모델 입력 문맥 — 학습 때와 같은 형식이어야 한다."""
    return f"제목: {title}\n리드: {lead or ''}"


def _cpu_threads() -> int:
    """이 프로세스가 쓸 수 있는 CPU 수(컨테이너 할당 기준)."""
    try:
        return max(1, len(os.sched_getaffinity(0)))
    except AttributeError:  # sched_getaffinity 없는 OS(로컬 개발)
        return max(1, os.cpu_count() or 1)


class RobertaTagger:
    """모델 묶음을 한 번 읽어 두고 기사마다 `parse` 한다.

    추론은 락으로 한 번에 하나만 돈다 — 운영 태스크는 vCPU 1~2 개라 동시에 돌리면
    서로 느려질 뿐이고, 소비자·배치의 스레드 풀이 그대로 이 객체를 공유해도 안전하다.
    """

    def __init__(self, model_dir: str | Path, threads: int | None = None):
        root = Path(model_dir)
        meta = json.loads((root / "meta.json").read_text(encoding="utf-8"))
        self.version: str = meta["version"]
        self._doc_classes: list[str] = meta["doc_classes"]
        self._types: list[str] = meta["types"]
        self._max_length: int = meta["max_length"]
        self._queries: dict[str, list[list[str]]] = meta["queries"]
        unmapped = set(self._doc_classes) - DOC_CLASS_MAP.keys()
        if unmapped:
            # 운영 어휘로 못 옮기는 분류가 나오면 parse 가 KeyError 로 죽는다 — 기동 때 드러낸다.
            raise RuntimeError(f"운영 문서 분류로 옮길 수 없는 모델 분류: {sorted(unmapped)}")
        torch.set_num_threads(threads or _cpu_threads())
        self._lock = threading.Lock()
        # 뼈대는 초기화 없이 만든다 — 가중치는 곧 int8 로 덮이고, 무작위 초기화를 하면 쓰지도
        # 않을 fp32 1.4GB 를 실제 메모리에 채운다(초기화를 건너뛰면 그 페이지가 안 잡힌다).
        with no_init_weights():
            classifier = Classifier(AutoModel.from_config(AutoConfig.from_pretrained(root / "classifier")),
                                    len(self._doc_classes), len(self._types))
            qa = AutoModelForQuestionAnswering.from_config(AutoConfig.from_pretrained(root / "qa"))
        self._classifier = _quantized(classifier, root / "classifier" / "weights.pt")
        self._qa = _quantized(qa, root / "qa" / "weights.pt")
        self._tokenizer = AutoTokenizer.from_pretrained(root / "tokenizer")

    def parse(self, title: str, lead: str | None) -> dict:
        """기사 1건 → LLM 응답과 같은 모양의 dict. 사건이 없으면 events 는 빈 배열이다."""
        context = _context(title, lead)
        with self._lock, torch.inference_mode():
            doc_logits, type_logits = self._classifier(**self._tokenizer(
                [context], truncation=True, max_length=self._max_length, return_tensors="pt"))
            doc_class = self._doc_classes[int(doc_logits[0].argmax())]
            type_probs = type_logits[0].softmax(-1)
            top = int(type_probs.argmax())
            event_type, confidence = self._types[top], round(float(type_probs[top]), 4)
            events = []
            if doc_class == "EVENT" and event_type != NO_EVENT:
                events.append({"event_type_code": event_type, "predicate_code": None,
                               "arguments": self._arguments(event_type, context),
                               "confidence": confidence})
        return {"doc_class": DOC_CLASS_MAP[doc_class], "events": events}

    def _arguments(self, event_type: str, context: str) -> list[dict]:
        """그 타입의 역할마다 질의해 원문 구간을 고른다. 빈 답(null 점수가 더 큼)은 뺀다."""
        pairs = self._queries.get(event_type) or []
        if not pairs:
            return []
        enc = self._tokenizer([query for _role, query in pairs], [context] * len(pairs),
                              truncation="only_second", max_length=self._max_length,
                              padding=True, return_offsets_mapping=True, return_tensors="pt")
        offsets = enc.pop("offset_mapping")
        out = self._qa(**enc)
        arguments = []
        for i, (role, _query) in enumerate(pairs):
            text = _best_span(context, enc.sequence_ids(i), offsets[i].tolist(),
                              out.start_logits[i].tolist(), out.end_logits[i].tolist())
            if text:
                arguments.append({"role_code": role, "text": text})
        return arguments


class Classifier(nn.Module):
    """인코더 + 문서분류·타입 머리. CLS 벡터 하나로 둘을 함께 낸다 — 학습 쪽과 같은 층 이름(enc·dc·ty)."""

    def __init__(self, encoder: nn.Module, n_doc_classes: int, n_types: int):
        super().__init__()
        self.enc = encoder
        hidden = encoder.config.hidden_size
        self.dc = nn.Linear(hidden, n_doc_classes)
        self.ty = nn.Linear(hidden, n_types)

    def forward(self, **inputs):
        """(문서분류 logits, 타입 logits)."""
        cls_vector = self.enc(**inputs, return_dict=True).last_hidden_state[:, 0]
        return self.dc(cls_vector), self.ty(cls_vector)


def _quantized(model: nn.Module, weights: Path) -> nn.Module:
    """빈 fp32 뼈대를 그 자리에서 int8 로 바꾼 뒤 int8 가중치를 붓는다.

    fp32 가중치 파일을 읽고 변환하면 순간 메모리가 4.9GB 까지 오른다(연구 실측) —
    운영 태스크(3GB)에 안 들어간다. 가중치는 텐서만 읽는다(weights_only, 피클 실행 없음).
    """
    torch.ao.quantization.quantize_dynamic(model, {nn.Linear}, dtype=torch.qint8, inplace=True)
    model.load_state_dict(torch.load(weights, map_location="cpu", weights_only=True))
    return model.eval()


def _best_span(context: str, sequence_ids: list, offsets: list, start: list, end: list) -> str | None:
    """가장 점수 높은 문맥 구간. 그 점수가 '답 없음'(CLS 위치) 점수 이하면 None — 학습 쪽 decode 와 같다."""
    null_score = start[0] + end[0]
    idx = [j for j, s in enumerate(sequence_ids) if s == 1]
    top_start = sorted(idx, key=lambda j: -start[j])[:_TOP_SPANS]
    top_end = sorted(idx, key=lambda j: -end[j])[:_TOP_SPANS]
    spans = sorted(((start[a] + end[z], a, z) for a in top_start for z in top_end
                    if a <= z < a + _MAX_SPAN_TOKENS), reverse=True)
    for score, a, z in spans:
        text = context[offsets[a][0]:offsets[z][1]]
        if text:
            return (text.strip() or None) if score > null_score else None
    return None
