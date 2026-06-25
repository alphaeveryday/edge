"""Stage B inputs -- per (ticker, trade_date) news embeddings (spec section 5.2).

Pipeline: load -> assign trade_date (no same-day-after-close leakage) -> dedup
(is_news_novelty) -> Stanza core-clause structuring -> FinBERT embedding (cached
by news_id) -> daily mean embedding + news_count.

Stanza/FinBERT are used directly (offline, HF cache) rather than importing the
heavy ``scripts.analysis.common`` package, keeping this module self-contained.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from .. import config
from ..errors import EmbeddingFailed


_WORD_RE = re.compile(r"[a-z0-9]+")
_EMB_COLS = tuple(f"e{i}" for i in range(config.EMBED_DIM))


# --------------------------------------------------------------------------- #
# Trade-date assignment (leakage-safe)
# --------------------------------------------------------------------------- #
def assign_trade_dates(news: pd.DataFrame, trading_dates: dict[str, np.ndarray]) -> pd.DataFrame:
    """Map each article to the trade_date whose close it can inform.

    published_at (tz-naive UTC) -> ET; if at/after 16:00 ET the news lands on the
    NEXT calendar day; then snap forward to the ticker's next available trading day.
    Articles after the last known trading day are dropped.
    """
    if news.empty:
        return news.assign(trade_date=pd.Series([], dtype="datetime64[ns]"))
    et = news["published_at"].dt.tz_localize("UTC").dt.tz_convert(config.MARKET_TZ)
    candidate = et.dt.normalize().dt.tz_localize(None)
    candidate = candidate + pd.to_timedelta((et.dt.hour >= config.MARKET_CLOSE_HOUR).astype(int), unit="D")

    out = news.copy()
    out["_candidate"] = candidate
    assigned: list[pd.Timestamp | None] = [None] * len(out)
    for ticker, idx in out.groupby("ticker", sort=False).groups.items():
        dates = trading_dates.get(ticker)
        if dates is None or len(dates) == 0:
            continue
        cand = out.loc[idx, "_candidate"].to_numpy(dtype="datetime64[ns]")
        pos = np.searchsorted(dates, cand, side="left")
        for local_i, p in zip(idx, pos):
            assigned[out.index.get_loc(local_i)] = None if p >= len(dates) else pd.Timestamp(dates[p])
    out["trade_date"] = assigned
    return out.drop(columns="_candidate").dropna(subset=["trade_date"]).reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Dedup -- is_news_novelty
# --------------------------------------------------------------------------- #
def _normalize_title(title: str) -> str:
    return " ".join(_WORD_RE.findall(str(title).lower()))


def dedupe_news(news: pd.DataFrame, *, window_days: int = 7) -> pd.DataFrame:
    """Drop repeats of the same normalized title per ticker within ``window_days``."""
    if news.empty:
        return news
    out = news.sort_values(["ticker", "published_at"]).reset_index(drop=True)
    out["norm_title"] = out["title"].map(_normalize_title)
    keep = np.ones(len(out), dtype=bool)
    last_seen: dict[tuple[str, str], pd.Timestamp] = {}
    for i, row in enumerate(out.itertuples(index=False)):
        norm = row.norm_title
        if not norm:
            keep[i] = False
            continue
        key = (row.ticker, norm)
        prev = last_seen.get(key)
        if prev is not None and (row.published_at - prev) <= pd.Timedelta(days=window_days):
            keep[i] = False
        else:
            last_seen[key] = row.published_at
    return out.loc[keep].drop(columns="norm_title").reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Stanza core-clause structuring (SER)
# --------------------------------------------------------------------------- #
_STANZA_PIPELINE = None


def _stanza_pipeline():
    global _STANZA_PIPELINE
    if _STANZA_PIPELINE is None:
        import stanza

        use_gpu = False
        try:
            import torch

            use_gpu = bool(torch.cuda.is_available())
        except Exception:
            use_gpu = False
        _STANZA_PIPELINE = stanza.Pipeline(
            lang="en",
            processors="tokenize,pos,lemma,depparse",
            tokenize_no_ssplit=False,
            use_gpu=use_gpu,
            verbose=False,
        )
    return _STANZA_PIPELINE


def structure_titles(titles: Iterable[str]) -> list[str]:
    """Reduce each title to its root predicate + direct dependents; identity on failure.

    Uses Stanza ``bulk_process`` so many titles parse per GPU batch (much faster
    than one pipeline call per title on large corpora).
    """
    titles = [str(t) for t in titles]
    if not titles:
        return []
    try:
        nlp = _stanza_pipeline()
    except Exception:
        return titles
    try:
        docs = list(nlp.bulk_process(titles))
    except Exception:
        docs = None
    if docs is None or len(docs) != len(titles):
        out: list[str] = []
        for title in titles:
            try:
                out.append(_core_clause(nlp(title)) or title)
            except Exception:
                out.append(title)
        return out
    result: list[str] = []
    for title, doc in zip(titles, docs):
        try:
            result.append(_core_clause(doc) or title)
        except Exception:
            result.append(title)
    return result


def _core_clause(doc) -> str:
    for sentence in doc.sentences:
        root = next((w for w in sentence.words if w.head == 0), None)
        if root is None:
            continue
        keep_ids = {root.id} | {w.id for w in sentence.words if w.head == root.id}
        tokens = [w.text for w in sentence.words if w.id in keep_ids]
        if tokens:
            return " ".join(tokens)
    return doc.text if hasattr(doc, "text") else ""


# --------------------------------------------------------------------------- #
# FinBERT embedding (cached by news_id)
# --------------------------------------------------------------------------- #
class TitleEmbedder:
    """Mean-pooled FinBERT embeddings; uses CUDA when available."""

    def __init__(self, model_name: str = config.EMBED_MODEL):
        self.model_name = model_name
        self._tokenizer = None
        self._model = None
        self._device = "cpu"

    def _ensure(self):
        if self._model is not None:
            return
        try:
            import torch
            from transformers import AutoModel, AutoTokenizer

            self._device = "cuda" if torch.cuda.is_available() else "cpu"
            self._tokenizer = AutoTokenizer.from_pretrained(self.model_name)
            self._model = AutoModel.from_pretrained(self.model_name).to(self._device)
            self._model.eval()
        except Exception as exc:  # pragma: no cover - environment dependent
            raise EmbeddingFailed(f"Could not load embedding model {self.model_name}: {exc}") from exc

    def embed(self, texts: list[str]) -> np.ndarray:
        self._ensure()
        import torch

        vectors: list[np.ndarray] = []
        for start in range(0, len(texts), config.EMBED_BATCH):
            batch = texts[start : start + config.EMBED_BATCH]
            enc = self._tokenizer(
                batch, padding=True, truncation=True, max_length=config.EMBED_MAX_TOKENS, return_tensors="pt",
            )
            enc = {key: value.to(self._device) for key, value in enc.items()}
            with torch.no_grad():
                out = self._model(**enc)
            hidden = out.last_hidden_state                      # (b, seq, dim)
            mask = enc["attention_mask"].unsqueeze(-1).float()  # (b, seq, 1)
            summed = (hidden * mask).sum(dim=1)
            counts = mask.sum(dim=1).clamp(min=1.0)
            mean = (summed / counts).cpu().numpy().astype("float32")
            vectors.append(mean)
        return np.vstack(vectors) if vectors else np.zeros((0, config.EMBED_DIM), dtype="float32")


def _load_embed_cache(path: Path) -> pd.DataFrame:
    if path.exists():
        return pd.read_parquet(path)
    return pd.DataFrame(columns=["news_id", *_EMB_COLS])


def _embed_with_cache(news: pd.DataFrame, embedder: TitleEmbedder, cache_path: Path) -> pd.DataFrame:
    """Return ``news_id`` + e0..eD-1 for every article, computing only cache misses."""
    cache = _load_embed_cache(cache_path)
    cached_ids = set(cache["news_id"].astype(str)) if not cache.empty else set()
    todo = news[~news["news_id"].astype(str).isin(cached_ids)].drop_duplicates("news_id")
    if not todo.empty:
        structured = structure_titles(todo["title"].tolist())
        matrix = embedder.embed(structured)
        fresh = pd.DataFrame(matrix, columns=list(_EMB_COLS))
        fresh.insert(0, "news_id", todo["news_id"].astype(str).to_numpy())
        cache = pd.concat([cache, fresh], ignore_index=True) if not cache.empty else fresh
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache.to_parquet(cache_path, index=False)
    return cache


def build_day_embeddings(
    news: pd.DataFrame,
    trading_dates: dict[str, np.ndarray],
    *,
    embedder: TitleEmbedder | None = None,
    cache_path: Path = config.EMBED_CACHE,
) -> tuple[pd.DataFrame, dict[tuple[str, pd.Timestamp], np.ndarray]]:
    """Per (ticker, trade_date): the matrix of that day's article embeddings + news_count.

    Returns ``(daily, day_emb)`` where ``daily`` has ``ticker, trade_date, news_count``
    and ``day_emb[(ticker, Timestamp)] = float32 array [n_articles, EMB_DIM]`` for
    attention pooling. Embeddings are cached by ``news_id`` (computed once).
    """
    empty = pd.DataFrame(columns=["ticker", "trade_date", "news_count"])
    if news.empty:
        return empty, {}
    dated = assign_trade_dates(news, trading_dates)
    deduped = dedupe_news(dated)
    if deduped.empty:
        return empty, {}
    embedder = embedder or TitleEmbedder()
    emb = _embed_with_cache(deduped, embedder, cache_path)
    merged = deduped.merge(emb, on="news_id", how="inner")
    day_emb: dict[tuple[str, pd.Timestamp], np.ndarray] = {}
    rows = []
    for (ticker, trade_date), group in merged.groupby(["ticker", "trade_date"], sort=False):
        day_emb[(ticker, pd.Timestamp(trade_date))] = group[list(_EMB_COLS)].to_numpy(dtype="float32")
        rows.append({"ticker": ticker, "trade_date": trade_date, "news_count": int(len(group))})
    daily = pd.DataFrame(rows)
    return daily, day_emb


# --------------------------------------------------------------------------- #
# Temporal context windows (same-day + 7d attention sets, 30d daily-mean sequence)
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class NewsWindows:
    """Lazy per-(ticker, trade_date) news context over cached article embeddings.

    ``windows(ticker, ts) -> (today_mat, week_mat, month_seq)``:
      * today_mat  : ``(n_t, D)`` same-day article embeddings, or None
      * week_mat   : ``(n_w, D)`` articles over the last ``week`` trading days
                     (incl. today), or None  -- the short attention set
      * month_seq  : ``(month, D)`` per-day MEAN embeddings over the last ``month``
                     trading days (incl. today), right-aligned, zeros for no-news
                     days; None if every day is empty  -- the long CNN sequence
    """

    today_emb: dict
    day_mean: dict
    trading_dates: dict
    week: int = config.NEWS_WEEK_DAYS
    month: int = config.NEWS_MONTH_DAYS
    emb_dim: int = config.EMBED_DIM

    def _pos(self, ticker: str, ts: pd.Timestamp):
        cal = self.trading_dates.get(ticker)
        if cal is None or len(cal) == 0:
            return None, None
        t = pd.Timestamp(ts).to_datetime64()
        pos = int(np.searchsorted(cal, t, side="right")) - 1
        if pos < 0:
            return None, None
        return cal, pos

    def windows(self, ticker: str, ts):
        cal, pos = self._pos(ticker, ts)
        if cal is None:
            return None, None, None
        ts = pd.Timestamp(ts)
        win = cal[max(0, pos - self.month + 1): pos + 1]
        seq = np.zeros((self.month, self.emb_dim), dtype="float32")
        offset = self.month - len(win)
        any_news = False
        for i, d in enumerate(win):
            v = self.day_mean.get((ticker, pd.Timestamp(d)))
            if v is not None:
                seq[offset + i] = v
                any_news = True
        month_seq = seq if any_news else None
        wmats = []
        for d in cal[max(0, pos - self.week + 1): pos + 1]:
            m = self.today_emb.get((ticker, pd.Timestamp(d)))
            if m is not None and len(m):
                wmats.append(m)
        week_mat = np.vstack(wmats).astype("float32") if wmats else None
        today_mat = self.today_emb.get((ticker, ts))
        return today_mat, week_mat, month_seq


def build_news_windows(
    news: pd.DataFrame,
    trading_dates: dict[str, np.ndarray],
    *,
    embedder: "TitleEmbedder | None" = None,
    cache_path: Path = config.EMBED_CACHE,
) -> "tuple[pd.DataFrame, NewsWindows]":
    """Per (ticker, trade_date): same-day news_count + a ``NewsWindows`` providing
    the same-day / 7d / 30d context. Embeddings are cached by ``news_id`` (computed
    once) and reused for every window -- no re-embedding for the longer contexts.
    """
    empty = pd.DataFrame(columns=["ticker", "trade_date", "news_count"])
    if news.empty:
        return empty, NewsWindows({}, {}, trading_dates)
    dated = assign_trade_dates(news, trading_dates)
    deduped = dedupe_news(dated)
    if deduped.empty:
        return empty, NewsWindows({}, {}, trading_dates)
    embedder = embedder or TitleEmbedder()
    emb = _embed_with_cache(deduped, embedder, cache_path)
    merged = deduped.merge(emb, on="news_id", how="inner")
    today_emb: dict[tuple[str, pd.Timestamp], np.ndarray] = {}
    day_mean: dict[tuple[str, pd.Timestamp], np.ndarray] = {}
    rows = []
    for (ticker, trade_date), group in merged.groupby(["ticker", "trade_date"], sort=False):
        mat = group[list(_EMB_COLS)].to_numpy(dtype="float32")
        key = (ticker, pd.Timestamp(trade_date))
        today_emb[key] = mat
        day_mean[key] = mat.mean(axis=0).astype("float32")
        rows.append({"ticker": ticker, "trade_date": trade_date, "news_count": int(len(group))})
    daily = pd.DataFrame(rows)
    return daily, NewsWindows(today_emb, day_mean, trading_dates)
