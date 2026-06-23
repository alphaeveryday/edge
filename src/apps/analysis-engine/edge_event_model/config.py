"""Static configuration: paths, fixed universe, thresholds, optional Postgres.

Primary data source is the local parquet mirror under ``data/`` (no network or
DB password required); Postgres ``edge`` is only a fallback for news.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

# --------------------------------------------------------------------------- #
# Paths -- data root is discovered by walking up to the dir containing ``data/``
# (so the package works both in the alphamale repo and standalone on a server),
# overridable via ``EDGE_DATA_ROOT``. The SQLite store lives inside the package
# dir (``analysis_module/db``).
# --------------------------------------------------------------------------- #
def _resolve_data_root() -> Path:
    env = os.environ.get("EDGE_DATA_ROOT")
    if env:
        return Path(env)
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "data").is_dir():
            return parent
    return here.parents[2]


PACKAGE_ROOT: Path = Path(__file__).resolve().parents[1]   # the analysis_module dir
ROOT: Path = _resolve_data_root()
DATA_DIR: Path = ROOT / "data"
PRICE_PARQUET: Path = DATA_DIR / "price" / "us_daily_data.parquet"
NEWS_PARQUET: Path = DATA_DIR / "news" / os.environ.get("EDGE_NEWS_FILE", "us_target_news.parquet")
ANALYSIS_OUTPUTS_DIR: Path = DATA_DIR / "analysis_outputs"
FF5_GLOB: str = "us_ff5_public_daily_*.parquet"

DB_DIR: Path = PACKAGE_ROOT / "db"
DB_PATH: Path = DB_DIR / "edge_analysis.sqlite"

CACHE_DIR: Path = DATA_DIR / "context_outputs"
EMBED_CACHE: Path = CACHE_DIR / os.environ.get("EDGE_EMBED_FILE", "news_embeddings_us.parquet")

# --------------------------------------------------------------------------- #
# Columns / labels
# --------------------------------------------------------------------------- #
FACTOR_COLUMNS: tuple[str, ...] = ("mkt_rf", "smb", "hml", "rmw", "cma")
RF_COLUMN: str = "rf"
DATE_COLUMN: str = "trade_date"
TICKER_COLUMN: str = "ticker"

LABEL_CLOSE: str = "close_logret"        # ln(close_t / close_{t-1})
LABEL_SPREAD: str = "spread"             # ln(high_t / close_t) >= 0
LABEL_ABNORMAL: str = "abnormal_return"  # FF5 residual (Stage A), Stage B/C target

# --------------------------------------------------------------------------- #
# Rolling FF5 (Stage A)
# --------------------------------------------------------------------------- #
ROLLING_WINDOW: int = 252
MIN_OBS: int = 120
MIN_OBS_FALLBACK: int = 60
ABS_ABNORMAL_THRESHOLD: float = 0.05     # |abnormal| event candidate / clean-window skip

# --------------------------------------------------------------------------- #
# Calibration gate
# --------------------------------------------------------------------------- #
CALIB_ERROR: float = 0.02                # |pred - real| < 2%

# --------------------------------------------------------------------------- #
# Chronological split
# --------------------------------------------------------------------------- #
TRAIN_RATIO: float = 0.70
VALID_RATIO: float = 0.15                # test = 1 - TRAIN - VALID

# --------------------------------------------------------------------------- #
# News timing
# --------------------------------------------------------------------------- #
MARKET_TZ: str = "America/New_York"
MARKET_CLOSE_HOUR: int = 16              # news after 16:00 ET -> next trading day
NEWS_PUBLISHED_TZ: str = "UTC"           # published_at is tz-naive UTC

# --------------------------------------------------------------------------- #
# Embedding / NN
# --------------------------------------------------------------------------- #
EMBED_MODEL: str = os.environ.get("EDGE_EMBED_MODEL", "ProsusAI/finbert")
EMBED_DIM: int = 768
EMBED_MAX_TOKENS: int = 64
EMBED_BATCH: int = 64
NEWS_WEEK_DAYS: int = 7                   # short attention context (trading days incl. today)
NEWS_MONTH_DAYS: int = 30                 # long CNN context (trading days incl. today)
FF5_FACTOR_UNIT: str = os.environ.get("FF5_FACTOR_UNIT", "auto")  # auto|percent|decimal

ANALYSIS_START: date = date(2021, 6, 1)
ANALYSIS_END: date = date(2026, 6, 20)


# --------------------------------------------------------------------------- #
# Fixed universe (data ticker = parquet/news key; canonical for display)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class UniverseAsset:
    ticker: str       # data/news key, e.g. "BRK-B"
    canonical: str    # display, e.g. "BRK.B"
    company: str
    sector: str


UNIVERSE: tuple[UniverseAsset, ...] = (
    UniverseAsset("NVDA", "NVDA", "NVIDIA", "IT"),
    UniverseAsset("AAPL", "AAPL", "Apple", "IT"),
    UniverseAsset("MSFT", "MSFT", "Microsoft", "IT"),
    UniverseAsset("CAT", "CAT", "Caterpillar", "Industrials"),
    UniverseAsset("GE", "GE", "GE Aerospace", "Industrials"),
    UniverseAsset("RTX", "RTX", "RTX", "Industrials"),
    UniverseAsset("BRK-B", "BRK.B", "Berkshire Hathaway", "Financials"),
    UniverseAsset("JPM", "JPM", "JPMorgan Chase", "Financials"),
    UniverseAsset("V", "V", "Visa", "Financials"),
)
TICKERS: tuple[str, ...] = tuple(a.ticker for a in UNIVERSE)
SECTOR_BY_TICKER: dict[str, str] = {a.ticker: a.sector for a in UNIVERSE}
CANONICAL_BY_TICKER: dict[str, str] = {a.ticker: a.canonical for a in UNIVERSE}


# --------------------------------------------------------------------------- #
# Optional Postgres fallback (news only)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class PgConfig:
    host: str
    port: int
    database: str
    user: str
    password: str | None
    schema: str = "etf"


def load_pg_config() -> PgConfig:
    return PgConfig(
        host=os.environ.get("PGHOST", "127.0.0.1"),
        port=int(os.environ.get("PGPORT", "15432")),
        database=os.environ.get("PGDATABASE", "edge"),
        user=os.environ.get("PGUSER", "edge"),
        password=os.environ.get("PGPASSWORD"),
        schema=os.environ.get("PGSCHEMA", "etf"),
    )
