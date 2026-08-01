"""정적 층 — 인과귀속 파이프라인에서 LLM 이전에 코드가 고정하는 전부.

frame(기계 프레임) → windows(결정론 창) → tree(합=1 분해) → gates(3값·A–E)
→ sem(창별 회귀+합 제약) → render(한 표). duck 이 S3·RDB·백필을 한 표면으로.
vocab 이 닫힌 어휘와 전역 상수 — 구체화 사상 φ 의 정의역.

설계 SSOT: docs/analysis-engine/causal-attribution-design.md
P0–P9 와의 관계: P1 지문을 먹이고 P2 어휘를 닫는다 (설계 §20).
"""
from .frame import PathVerdict, validate_edge
from .gates import EdgeVerdict, GateInputs, edge_gate, route
from .render import Row, render
from .sem import EdgeEstimate, clip_to_share, exposure_slope, rank_with_ties
from .tree import Share, decompose
from .vocab import (CHANNELS, ExposureSource, Feature, HypothesisTuple,
                    SERIES_FAMILIES, TRANSFORMS, Trigger, VocabError, Vulnerability)
from .windows import Window, build_windows

__all__ = [
    "CHANNELS", "EdgeEstimate", "EdgeVerdict", "ExposureSource", "Feature",
    "GateInputs", "HypothesisTuple", "PathVerdict", "Row", "SERIES_FAMILIES",
    "Share", "TRANSFORMS", "Trigger", "VocabError", "Vulnerability", "Window",
    "build_windows", "clip_to_share", "decompose", "edge_gate", "exposure_slope",
    "rank_with_ties", "render", "route", "validate_edge"]
