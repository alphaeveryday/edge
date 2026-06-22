"""data-pipeline — 외부 데이터 수집/적재 파이프라인.

설정 진입점은 ALPHA-103 수집 로직이 사용한다:

    from data_pipeline import load_settings
    settings = load_settings()
"""

from .config import ConfigError, Settings, load_settings

__all__ = ["ConfigError", "Settings", "load_settings"]
