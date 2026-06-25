from __future__ import annotations

import sys
from pathlib import Path

MODROOT = Path(__file__).resolve().parents[1]  # analysis_module
if str(MODROOT) not in sys.path:
    sys.path.insert(0, str(MODROOT))
