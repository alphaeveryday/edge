"""Stage B -- temporal news regressor: same-day/7d attention + 30d CNN context.

Per (ticker, day) three views are built from cached FinBERT article embeddings
(``features.news_arm.NewsWindows``):

* ``today_mat`` (n_t x D)  -- same-day articles
* ``week_mat``  (n_w x D)  -- articles over the last 7 trading days (incl. today)
* ``month_seq`` (30 x D)   -- per-day MEAN embedding over the last 30 trading days

Short context (today, 7d) is pooled by the **same** learned attention mechanism
(shared weights -- "똑같은 매커니즘"). Long context (30d) is summarised by a 1D
temporal CNN. The three context vectors are concatenated and an MLP head
regresses the day's abnormal return directly (z-scored target) with Gaussian NLL,
emitting a point prediction and an uncertainty sigma. No sentiment/news score.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..features.returns import ZScore
from .news_nn import _SEED, _InputScaler, _gaussian_nll

EMB_DIM_DEFAULT = 768


def _build_temporal_module(emb_dim: int, proj: int, hidden: int, dropout: float, seq_len: int):
    import torch
    from torch import nn

    class TemporalNewsNet(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            # shared article attention (applied to today AND 7d sets)
            self.proj = nn.Linear(emb_dim, proj)
            self.attn_w = nn.Linear(proj, proj)
            self.attn_v = nn.Linear(proj, 1, bias=False)
            self.drop = nn.Dropout(dropout)
            # long-context temporal CNN over the 30d daily-mean sequence
            self.seq_proj = nn.Linear(emb_dim, proj)
            self.conv1 = nn.Conv1d(proj, proj, kernel_size=3, padding=1)
            self.conv2 = nn.Conv1d(proj, proj, kernel_size=3, padding=1)
            # head
            self.head = nn.Sequential(nn.Linear(3 * proj, hidden), nn.ReLU(), nn.Dropout(dropout))
            self.mu = nn.Linear(hidden, 1)
            self.log_var = nn.Linear(hidden, 1)

        def _attend(self, x, mask):
            h = self.drop(torch.relu(self.proj(x)))                   # (b, n, proj)
            s = self.attn_v(torch.tanh(self.attn_w(h))).squeeze(-1)   # (b, n)
            s = s.masked_fill(~mask, -1e9)
            alpha = torch.nan_to_num(torch.softmax(s, dim=1), nan=0.0)
            has = mask.any(dim=1, keepdim=True).float()               # (b, 1) -> 0 when empty
            return (alpha.unsqueeze(-1) * h).sum(dim=1) * has         # (b, proj)

        def _conv(self, seq):
            z = torch.relu(self.seq_proj(seq))                        # (b, L, proj)
            z = z.transpose(1, 2)                                     # (b, proj, L)
            z = torch.relu(self.conv1(z))
            z = torch.relu(self.conv2(z))
            return z.amax(dim=2)                                      # (b, proj) global max-pool

        def forward(self, xt, mt, xw, mw, seq):
            ctx = torch.cat([self._attend(xt, mt), self._attend(xw, mw), self._conv(seq)], dim=1)
            f = self.head(ctx)
            return self.mu(f).squeeze(-1), self.log_var(f).squeeze(-1).clamp(-8.0, 4.0)

    return TemporalNewsNet()


@dataclass(slots=True)
class TemporalNewsModel:
    """7d/today attention + 30d CNN -> direct abnormal-return regression (mu, sigma)."""

    target_col: str
    emb_dim: int = EMB_DIM_DEFAULT
    proj: int = 64
    hidden: int = 64
    dropout: float = 0.1
    seq_len: int = 30
    epochs: int = 120
    patience: int = 15
    lr: float = 1e-3
    weight_decay: float = 0.0
    batch_size: int = 128
    scaler: _InputScaler | None = None
    target_z: ZScore | None = None
    module: object | None = None
    history: list[float] = field(default_factory=list)

    # ---- sample assembly -------------------------------------------------- #
    def _samples(self, df: pd.DataFrame, windows):
        today, week, month, targets = [], [], [], []
        z = np.zeros((0, self.emb_dim), dtype="float32")
        for row in df.itertuples(index=False):
            t, w, m = windows.windows(row.ticker, pd.Timestamp(row.trade_date))
            has_t, has_w, has_m = (t is not None and len(t)), (w is not None and len(w)), (m is not None)
            if not (has_t or has_w or has_m):
                continue
            today.append(t.astype("float32") if has_t else z)
            week.append(w.astype("float32") if has_w else z)
            month.append(m.astype("float32") if has_m else np.zeros((self.seq_len, self.emb_dim), dtype="float32"))
            targets.append(float(getattr(row, self.target_col)))
        return today, week, month, np.asarray(targets, dtype="float64")

    def _scale_seq(self, seq: np.ndarray) -> np.ndarray:
        present = np.abs(seq).sum(axis=1) > 0
        out = self.scaler.transform(seq)
        out[~present] = 0.0
        return out.astype("float32")

    def _scale_all(self, today, week, month):
        st = [self.scaler.transform(m) if len(m) else m for m in today]
        sw = [self.scaler.transform(m) if len(m) else m for m in week]
        sm = [self._scale_seq(m) for m in month]
        return st, sw, sm

    @staticmethod
    def _pad(mats, dim):
        import torch

        n_max = max(1, max((m.shape[0] for m in mats), default=0))
        x = np.zeros((len(mats), n_max, dim), dtype="float32")
        mask = np.zeros((len(mats), n_max), dtype=bool)
        for i, m in enumerate(mats):
            if m.shape[0]:
                x[i, : m.shape[0]] = m
                mask[i, : m.shape[0]] = True
        return torch.from_numpy(x), torch.from_numpy(mask)

    def _batch(self, idx, today, week, month):
        import torch

        xt, mt = self._pad([today[i] for i in idx], self.emb_dim)
        xw, mw = self._pad([week[i] for i in idx], self.emb_dim)
        seq = torch.from_numpy(np.stack([month[i] for i in idx]))
        return xt, mt, xw, mw, seq

    # ---- fit / predict ---------------------------------------------------- #
    def fit(self, train_df: pd.DataFrame, windows, val_df: pd.DataFrame | None = None) -> "TemporalNewsModel":
        import torch

        torch.manual_seed(_SEED)
        np.random.seed(_SEED)

        today, week, month, targets = self._samples(train_df, windows)
        if not month:
            raise RuntimeError("No training samples with news context windows.")
        pieces = [m for m in today if len(m)] + [m for m in week if len(m)]
        pieces += [m[np.abs(m).sum(axis=1) > 0] for m in month]
        self.scaler = _InputScaler.fit(np.vstack([p for p in pieces if len(p)]))
        self.target_z = ZScore.fit(targets)
        today, week, month = self._scale_all(today, week, month)
        z = self.target_z.transform(targets).astype("float32")

        val_pack = None
        if val_df is not None and not val_df.empty:
            vt, vw, vm, vtar = self._samples(val_df, windows)
            if vm:
                vt, vw, vm = self._scale_all(vt, vw, vm)
                vz = torch.from_numpy(self.target_z.transform(vtar).astype("float32"))
                val_pack = (vt, vw, vm, vz)

        module = _build_temporal_module(self.emb_dim, self.proj, self.hidden, self.dropout, self.seq_len)
        opt = torch.optim.Adam(module.parameters(), lr=self.lr, weight_decay=self.weight_decay)

        n = len(month)
        order = np.arange(n)
        best_state, best_metric, stale = None, float("inf"), 0
        for _epoch in range(self.epochs):
            module.train()
            np.random.shuffle(order)
            for start in range(0, n, self.batch_size):
                idx = order[start : start + self.batch_size]
                xt, mt, xw, mw, seq = self._batch(idx, today, week, month)
                target = torch.from_numpy(z[idx])
                opt.zero_grad()
                mu, log_var = module(xt, mt, xw, mw, seq)
                loss = _gaussian_nll(mu, log_var, target)
                loss.backward()
                opt.step()
            metric = self._eval(module, val_pack, today, week, month, z)
            self.history.append(metric)
            if metric < best_metric - 1e-5:
                best_metric, best_state, stale = metric, {k: v.detach().clone() for k, v in module.state_dict().items()}, 0
            else:
                stale += 1
                if stale >= self.patience:
                    break
        if best_state is not None:
            module.load_state_dict(best_state)
        module.eval()
        self.module = module
        return self

    def _eval(self, module, val_pack, today, week, month, z) -> float:
        import torch

        module.eval()
        with torch.no_grad():
            if val_pack is not None:
                vt, vw, vm, vz = val_pack
                xt, mt, xw, mw, seq = self._batch(range(len(vm)), vt, vw, vm)
                mu, lv = module(xt, mt, xw, mw, seq)
                return float(_gaussian_nll(mu, lv, vz).item())
            k = min(512, len(month))
            xt, mt, xw, mw, seq = self._batch(range(k), today, week, month)
            mu, _lv = module(xt, mt, xw, mw, seq)
            return float(torch.mean((mu - torch.from_numpy(z[:k])) ** 2).item())

    def predict_abnormal(self, df: pd.DataFrame, windows) -> tuple[np.ndarray, np.ndarray]:
        """Return (abnormal_return, sigma_return) per df row, in return units."""
        import torch

        if self.module is None or self.scaler is None or self.target_z is None:
            raise RuntimeError("TemporalNewsModel is not fitted.")
        abn = np.zeros(len(df), dtype="float64")
        sig = np.full(len(df), float(self.target_z.std), dtype="float64")

        keys, today, week, month = [], [], [], []
        zempty = np.zeros((0, self.emb_dim), dtype="float32")
        for pos, row in enumerate(df.itertuples(index=False)):
            t, w, m = windows.windows(row.ticker, pd.Timestamp(row.trade_date))
            has_t, has_w, has_m = (t is not None and len(t)), (w is not None and len(w)), (m is not None)
            if not (has_t or has_w or has_m):
                continue
            keys.append(pos)
            today.append(self.scaler.transform(t.astype("float32")) if has_t else zempty)
            week.append(self.scaler.transform(w.astype("float32")) if has_w else zempty)
            month.append(self._scale_seq(m.astype("float32")) if has_m else np.zeros((self.seq_len, self.emb_dim), dtype="float32"))
        if keys:
            xt, mt, xw, mw, seq = self._batch(range(len(keys)), today, week, month)
            with torch.no_grad():
                mu, log_var = self.module(xt, mt, xw, mw, seq)
            abn_vals = self.target_z.inverse_transform(mu.cpu().numpy())
            sig_vals = self.target_z.inverse_std(np.exp(0.5 * log_var.cpu().numpy()))
            for j, pos in enumerate(keys):
                abn[pos] = abn_vals[j]
                sig[pos] = sig_vals[j]
        return abn, sig
