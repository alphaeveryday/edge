"""정적 층 — 인과귀속 파이프라인에서 LLM 이전에 코드가 고정하는 전부.

frame(기계 프레임) → windows(결정론 창) → tree(합=1 분해) → gates(3값·A–E)
→ render(한 표). duck 이 S3·RDB·백필을 한 표면으로. 크기는 ATT 경로(trial)가
주장하고 가법 제약(narrate.AdditiveBudget)이 예산으로 검산한다 - SEM 폐기.
vocab 이 닫힌 어휘와 전역 상수 — 구체화 사상 φ 의 정의역.

설계 SSOT: docs/analysis-engine/causal-attribution-design.md
P0–P9 와의 관계: P1 지문을 먹이고 P2 어휘를 닫는다 (설계 §20).
"""
from .core.frame import PathVerdict, validate_edge
from .core.gates import EdgeVerdict, GateInputs, edge_gate, route
from .core.narrate import BaseRate, Conditional, NarrationError, narrate
from .core.render import Row, render
from .core.sem import rank_with_ties
from .core.tree import Share, decompose
from .core.vocab import (CHANNELS, ExposureSource, HypothesisTuple,
                    SERIES_FAMILIES, TRANSFORMS, Trigger, VocabError, Condition)
from .core.windows import Window, build_windows

__all__ = [
    "BaseRate", "CHANNELS", "Conditional", "EdgeVerdict",
    "ExposureSource", "GateInputs", "HypothesisTuple", "NarrationError",
    "PathVerdict", "Row", "SERIES_FAMILIES", "Share", "TRANSFORMS", "Trigger",
    "VocabError", "Condition", "Window", "build_windows",
    "decompose", "edge_gate", "narrate", "rank_with_ties",
    "render", "route", "validate_edge"]
