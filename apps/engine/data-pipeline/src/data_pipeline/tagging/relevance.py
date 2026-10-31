"""뉴스 관련성 판정기 — (회사, 사건 타입, 기사) → "이 회사가 당사자인 이 타입의 사건 기사"일 확률 (ALPHA-1476).

태거(`roberta.py`)가 기사에서 사건 타입과 참여자를 뽑아도, 그 회사의 사건이 아닌 기사가 많다 — 자회사·계열사의
일, 다른 종류의 일, 지나가는 언급. 이 판정기는 스레드 주체 회사와 기사를 문장 쌍으로 받아 klue/roberta-base +
선형 머리 하나로 P(관련)을 낸다. 학습은 이 저장소 밖에서 했다(에이전트 라벨 5,213쌍, 자회사·계열사는 모든
타입에서 관련 없음).

**모델 묶음**(`model_dir`)은 `scripts/export_relevance.py` 가 학습 체크포인트에서 만든다. 입력 문구는 학습
때와 글자까지 같아야 해서 코드가 아니라 묶음의 `meta.json` 에 실린다:

    meta.json     version · threshold · max_length · types{event_type_code: [유형 한글, 당사자 설명]}
    model/        config.json + weights.pt   (인코더 + 머리, fp32 state_dict)
    tokenizer/    토크나이저 파일

입력: A = "{회사명} | {유형 한글} | 당사자: {당사자 설명}", B = "제목: {제목}\\n리드: {리드}".
`types` 밖 타입은 학습하지 않아 판정하지 않는다(None).

가중치는 fp32 다. 태거처럼 nn.Linear 를 동적 int8 로 바꾸면 연구 예측 1,567쌍 중 104쌍(6.6%)의 통과 여부가
바뀐다(최대 확률 차 0.59, 2026-10-31 실측) — 기준값 0.05 근처의 낮은 확률이 양자화 오차에 민감하다. fp32 는
최대 차 1e-5, 뒤바뀜 0 이다.

torch·transformers 를 모듈 맨 위에서 읽는다 — 호출부는 `RELEVANCE_FILTER=on` 일 때만 이 모듈을 import
한다(`tagging.backend.load_relevance`).
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import torch
from torch import nn
from transformers import AutoConfig, AutoModel, AutoTokenizer
from transformers.modeling_utils import no_init_weights

from .roberta import _context, _cpu_threads

# 한 번에 모델에 넣는 쌍 수 — 패딩 낭비와 호출 횟수 사이. 배치 하루치(수백 건)도 메모리는 수십 MB 다.
_BATCH = 16


class RelevanceHead(nn.Module):
    """인코더 [CLS] → 선형 1개 — 학습 쪽과 같은 층 이름(enc·head). dropout 은 추론에 없다."""

    def __init__(self, encoder: nn.Module):
        super().__init__()
        self.enc = encoder
        self.head = nn.Linear(encoder.config.hidden_size, 1)

    def forward(self, **inputs):
        """logit 하나(쌍마다)."""
        return self.head(self.enc(**inputs, return_dict=True).last_hidden_state[:, 0]).squeeze(-1)


class RelevanceJudge:
    """모델 묶음을 한 번 읽어 두고 (회사, 타입, 제목, 리드) 쌍마다 확률을 낸다.

    추론은 락으로 한 번에 하나만 돈다 — 태거와 같은 태스크에서 같은 CPU 를 나눠 쓴다(`RobertaTagger` 와 같은 이유).
    """

    def __init__(self, model_dir: str | Path, threads: int | None = None):
        root = Path(model_dir)
        meta = json.loads((root / "meta.json").read_text(encoding="utf-8"))
        self.version: str = meta["version"]
        self.threshold: float = float(meta["threshold"])
        if not 0.0 < self.threshold < 1.0:
            # NaN·무한대·0·1 은 필터를 끄거나 전부 숨긴다 — 잘못 만든 묶음은 기동 때 막는다.
            raise ValueError(f"관련성 기준값이 (0, 1) 밖: {meta['threshold']!r}")
        self._max_length: int = meta["max_length"]
        self._types: dict[str, list[str]] = meta["types"]
        torch.set_num_threads(threads or _cpu_threads())
        self._lock = threading.Lock()
        # 뼈대는 초기화 없이 만들고 가중치 텐서를 그대로 끼운다(assign) — 무작위 초기화 + 복사면 fp32 440MB 가
        # 두 벌 잡혀, 태거(피크 약 2.2GB)와 같은 3GB 태스크에서 여유가 사라진다. 텐서만 읽는다(weights_only).
        with no_init_weights():
            model = RelevanceHead(AutoModel.from_config(AutoConfig.from_pretrained(root / "model")))
        state = torch.load(root / "model" / "weights.pt", map_location="cpu", weights_only=True, mmap=True)
        model.load_state_dict(state, assign=True)
        self._model = model.eval()
        self._tokenizer = AutoTokenizer.from_pretrained(root / "tokenizer")

    def judges(self, event_type_code: str) -> bool:
        """이 타입을 판정하는가 — 학습한 타입만."""
        return event_type_code in self._types

    def score(self, items: list[tuple[str, str, str, str | None]]) -> list[float | None]:
        """(회사명, 타입, 제목, 리드) 목록 → P(관련) 목록. 판정하지 않는 타입은 None."""
        out: list[float | None] = [None] * len(items)
        todo = [i for i, (_c, t, _ti, _l) in enumerate(items) if t in self._types]
        for start in range(0, len(todo), _BATCH):
            chunk = todo[start:start + _BATCH]
            first = [self._pair_a(items[i][0], items[i][1]) for i in chunk]
            second = [_context(items[i][2], items[i][3]) for i in chunk]
            # RoBERTa 는 type_vocab_size 1 — 학습도 token_type_ids 를 넘기지 않았다.
            enc = self._tokenizer(first, second, truncation="only_second", max_length=self._max_length,
                                  padding=True, return_token_type_ids=False, return_tensors="pt")
            with self._lock, torch.inference_mode():
                probs = torch.sigmoid(self._model(**enc)).tolist()
            for i, p in zip(chunk, probs):
                out[i] = float(p)
        return out

    def _pair_a(self, company: str, event_type_code: str) -> str:
        ko, party = self._types[event_type_code]
        return f"{company} | {ko} | 당사자: {party}"
