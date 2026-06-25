"""Stage B -- attention news regressor (spec: attention pooling + direct regression).

A day can carry many headlines. Per (ticker, day) we hold the matrix of article
embeddings ``X in R^{n x 768}`` (FinBERT). The model:

    project   : h_i = Dropout(ReLU(W_p x_i))               (n x 64)
    attention : a_i = v . tanh(W_a h_i);  alpha = softmax(a) over the day's articles
    pool      : c   = sum_i alpha_i h_i                    (64)   -- learned weighted avg
    head      : c -> ReLU(32) -> (mu, log_var)

The head **regresses the day's abnormal return directly** (z-scored target, inverse
on output) with Gaussian NLL, so it emits both a point abnormal-return prediction
and an uncertainty sigma. There is no separate "news score" or downstream LR.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..features.returns import ZScore

_SEED = 17
EMB_DIM_DEFAULT = 768


@dataclass(slots=True)
class _InputScaler:
    mean: np.ndarray = field(default_factory=lambda: np.zeros(0))
    std: np.ndarray = field(default_factory=lambda: np.ones(0))

    @classmethod
    def fit(cls, x: np.ndarray) -> "_InputScaler":
        mean = x.mean(axis=0)
        std = x.std(axis=0)
        std = np.where(np.isfinite(std) & (std > 1e-8), std, 1.0)
        return cls(mean.astype("float32"), std.astype("float32"))

    def transform(self, x: np.ndarray) -> np.ndarray:
        return ((x - self.mean) / self.std).astype("float32")


def _build_module(input_dim: int, proj: int, hidden: int, dropout: float):
    import torch
    import torch.nn as nn

    class AttnNewsNet(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.proj = nn.Sequential(nn.Linear(input_dim, proj), nn.ReLU(), nn.Dropout(dropout))
            self.attn_w = nn.Linear(proj, proj)
            self.attn_v = nn.Linear(proj, 1, bias=False)
            self.head = nn.Sequential(nn.Linear(proj, hidden), nn.ReLU())
            self.mu_head = nn.Linear(hidden, 1)
            self.logvar_head = nn.Linear(hidden, 1)

        def forward(self, x, mask):
            # x: (B, N, D), mask: (B, N) True where real article
            h = self.proj(x)                                   # (B, N, P)
            score = self.attn_v(torch.tanh(self.attn_w(h))).squeeze(-1)  # (B, N)
            score = score.masked_fill(~mask, float("-inf"))
            alpha = torch.softmax(score, dim=1)                # (B, N)
            context = (alpha.unsqueeze(-1) * h).sum(dim=1)     # (B, P)
            z = self.head(context)
            mu = self.mu_head(z).squeeze(-1)
            log_var = self.logvar_head(z).squeeze(-1).clamp(-8.0, 4.0)
            return mu, log_var

    return AttnNewsNet()


def _gaussian_nll(mu, log_var, target):
    import torch

    return 0.5 * torch.mean(log_var + (target - mu) ** 2 / torch.exp(log_var))


@dataclass(slots=True)
class AttentionNewsModel:
    """Attention pooling over a day's article embeddings -> direct abnormal-return regression."""

    target_col: str
    emb_dim: int = EMB_DIM_DEFAULT
    proj: int = 64
    hidden: int = 32
    dropout: float = 0.1
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
    def _samples(self, df: pd.DataFrame, day_emb: dict) -> tuple[list[np.ndarray], np.ndarray]:
        mats, targets = [], []
        for row in df.itertuples(index=False):
            key = (row.ticker, pd.Timestamp(row.trade_date))
            mat = day_emb.get(key)
            if mat is None or len(mat) == 0:
                continue
            mats.append(mat.astype("float32"))
            targets.append(float(getattr(row, self.target_col)))
        return mats, np.asarray(targets, dtype="float64")

    @staticmethod
    def _pad(mats: list[np.ndarray]):
        import torch

        n_max = max(m.shape[0] for m in mats)
        dim = mats[0].shape[1]
        x = np.zeros((len(mats), n_max, dim), dtype="float32")
        mask = np.zeros((len(mats), n_max), dtype=bool)
        for i, m in enumerate(mats):
            x[i, : m.shape[0]] = m
            mask[i, : m.shape[0]] = True
        return torch.from_numpy(x), torch.from_numpy(mask)

    # ---- fit / predict ---------------------------------------------------- #
    def fit(self, train_df: pd.DataFrame, day_emb: dict, val_df: pd.DataFrame | None = None) -> "AttentionNewsModel":
        import torch

        torch.manual_seed(_SEED)
        np.random.seed(_SEED)

        mats, targets = self._samples(train_df, day_emb)
        if not mats:
            raise RuntimeError("No training samples with news embeddings.")
        self.scaler = _InputScaler.fit(np.vstack(mats))
        self.target_z = ZScore.fit(targets)
        mats = [self.scaler.transform(m) for m in mats]
        z = self.target_z.transform(targets).astype("float32")

        val_pack = None
        if val_df is not None and not val_df.empty:
            vmats, vtargets = self._samples(val_df, day_emb)
            if vmats:
                vmats = [self.scaler.transform(m) for m in vmats]
                vx, vmask = self._pad(vmats)
                vz = torch.from_numpy(self.target_z.transform(vtargets).astype("float32"))
                val_pack = (vmats, vx, vmask, vz)

        module = _build_module(self.emb_dim, self.proj, self.hidden, self.dropout)
        opt = torch.optim.Adam(module.parameters(), lr=self.lr, weight_decay=self.weight_decay)

        n = len(mats)
        order = np.arange(n)
        best_state, best_metric, stale = None, float("inf"), 0
        for _epoch in range(self.epochs):
            module.train()
            np.random.shuffle(order)
            for start in range(0, n, self.batch_size):
                idx = order[start : start + self.batch_size]
                x, mask = self._pad([mats[i] for i in idx])
                target = torch.from_numpy(z[idx])
                opt.zero_grad()
                mu, log_var = module(x, mask)
                loss = _gaussian_nll(mu, log_var, target)
                loss.backward()
                opt.step()
            metric = self._eval(module, val_pack, mats, z)
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

    def _eval(self, module, val_pack, train_mats, z) -> float:
        import torch

        module.eval()
        with torch.no_grad():
            if val_pack is not None:
                _vmats, vx, vmask, vz = val_pack
                mu, lv = module(vx, vmask)
                return float(_gaussian_nll(mu, lv, vz).item())
            # fall back to a train-batch metric (mean-squared on z)
            x, mask = self._pad(train_mats[: min(512, len(train_mats))])
            mu, _lv = module(x, mask)
            return float(torch.mean((mu - torch.from_numpy(z[: mu.shape[0]])) ** 2).item())

    def predict_abnormal(self, df: pd.DataFrame, day_emb: dict) -> tuple[np.ndarray, np.ndarray]:
        """Return (abnormal_return, sigma_return) per df row (in return units)."""
        import torch

        if self.module is None or self.scaler is None or self.target_z is None:
            raise RuntimeError("AttentionNewsModel is not fitted.")
        abn = np.zeros(len(df), dtype="float64")
        sig = np.full(len(df), float(self.target_z.std), dtype="float64")  # no-news -> unconditional std

        keys, rows = [], []
        for pos, row in enumerate(df.itertuples(index=False)):
            mat = day_emb.get((row.ticker, pd.Timestamp(row.trade_date)))
            if mat is not None and len(mat) > 0:
                keys.append(pos)
                rows.append(self.scaler.transform(mat.astype("float32")))
        if rows:
            x, mask = self._pad(rows)
            with torch.no_grad():
                mu, log_var = self.module(x, mask)
            mu = mu.cpu().numpy()
            sigma_z = np.exp(0.5 * log_var.cpu().numpy())
            abn_vals = self.target_z.inverse_transform(mu)
            sig_vals = self.target_z.inverse_std(sigma_z)
            for j, pos in enumerate(keys):
                abn[pos] = abn_vals[j]
                sig[pos] = sig_vals[j]
        return abn, sig


@dataclass(slots=True)
class ZeroNewsModel:
    """Fallback when there is too little news to fit: zero abnormal, unconditional sigma."""

    sigma: float = 0.0

    def predict_abnormal(self, df: pd.DataFrame, day_emb: dict) -> tuple[np.ndarray, np.ndarray]:
        n = len(df)
        return np.zeros(n, dtype="float64"), np.full(n, self.sigma, dtype="float64")
