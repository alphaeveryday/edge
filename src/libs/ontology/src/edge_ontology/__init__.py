"""이벤트 온톨로지 SSOT lib (ALPHA-539).

어휘 정본은 이 패키지의 resources/ 다. 갱신 규약: 실험실(event-ontology repo)에서 확정한
리소스를 **통째 교체**하고(부분 발췌·현지 수정 금지 — 구 alphamale 스냅샷 정책 승계),
어휘가 바뀌었으면 constants.ONTOLOGY_VERSION 을 함께 올린다.
"""
from .bundle import load_ontology_bundle
from .constants import DEFAULT_VERSION, ONTOLOGY_VERSION
from .domain import TypeSpec
from .features import load_feature_registry
from .profiles import load_profiles
from .registry import Registry, load_registry
from .unified import load_common_features, load_lifecycle_models, load_type_definitions
from .view import OntologyView, TypeView, load_ontology_view

__all__ = [
    "DEFAULT_VERSION",
    "ONTOLOGY_VERSION",
    "OntologyView",
    "Registry",
    "TypeSpec",
    "TypeView",
    "load_common_features",
    "load_feature_registry",
    "load_lifecycle_models",
    "load_ontology_bundle",
    "load_ontology_view",
    "load_profiles",
    "load_registry",
    "load_type_definitions",
]
