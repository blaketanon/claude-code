"""Sequence model (Temporal Convolutional Network) in PyTorch. Optional dependency.

Consumes the last `seq_len` feature rows per prediction. Trains with early stopping
on a chronological validation tail.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import torch
import torch.nn as nn

from .base import SignalModel, register


class _TCNBlock(nn.Module):
    def __init__(self, c_in, c_out, k, dilation, dropout):
        super().__init__()
        pad = (k - 1) * dilation
        self.conv1 = nn.Conv1d(c_in, c_out, k, padding=pad, dilation=dilation)
        self.conv2 = nn.Conv1d(c_out, c_out, k, padding=pad, dilation=dilation)
        self.drop = nn.Dropout(dropout)
        self.down = nn.Conv1d(c_in, c_out, 1) if c_in != c_out else nn.Identity()
        self.pad = pad

    def forward(self, x):
        y = torch.relu(self.conv1(x))[:, :, : -self.pad or None]
        y = self.drop(y)
        y = torch.relu(self.conv2(y))[:, :, : -self.pad or None]
        return torch.relu(y + self.down(x))


class _TCN(nn.Module):
    def __init__(self, n_feat, hidden=64, levels=4, k=3, dropout=0.1):
        super().__init__()
        layers, c = [], n_feat
        for i in range(levels):
            layers.append(_TCNBlock(c, hidden, k, 2**i, dropout))
            c = hidden
        self.net = nn.Sequential(*layers)
        self.head = nn.Linear(hidden, 1)

    def forward(self, x):  # x: (B, T, F)
        h = self.net(x.transpose(1, 2))[:, :, -1]
        return self.head(h).squeeze(-1)


@register
class TCNModel(SignalModel):
    model_type = "tcn"
    DEFAULTS = dict(seq_len=32, hidden=64, levels=4, dropout=0.1, lr=1e-3, epochs=30, batch_size=256, patience=5)

    def __init__(self, feature_names, hyperparams=None):
        super().__init__(feature_names, {**self.DEFAULTS, **(hyperparams or {})})
        self._net = None
        self._mu = None
        self._sd = None

    def _windows(self, Xp: pd.DataFrame):
        T = int(self.hyperparams["seq_len"])
        arr = ((Xp - self._mu) / self._sd).fillna(0).to_numpy(dtype=np.float32)
        if len(arr) < T:
            return np.zeros((0, T, arr.shape[1]), dtype=np.float32), np.array([], dtype=int)
        idx = np.arange(T - 1, len(arr))
        win = np.stack([arr[i - T + 1 : i + 1] for i in idx])
        return win, idx

    def fit(self, X, y, sample_weight=None):
        Xp = self._prep(X)
        self._mu, self._sd = Xp.mean(), Xp.std().replace(0, 1.0)
        win, idx = self._windows(Xp)
        yb = (y.to_numpy()[idx] > 0).astype(np.float32)
        valid = ~np.isnan(y.to_numpy()[idx])
        win, yb = win[valid], yb[valid]
        n_val = max(1, int(len(win) * 0.15))
        tr_x, va_x = torch.tensor(win[:-n_val]), torch.tensor(win[-n_val:])
        tr_y, va_y = torch.tensor(yb[:-n_val]), torch.tensor(yb[-n_val:])
        hp = self.hyperparams
        self._net = _TCN(len(self.feature_names), hp["hidden"], hp["levels"], 3, hp["dropout"])
        opt = torch.optim.AdamW(self._net.parameters(), lr=hp["lr"], weight_decay=1e-4)
        lossf = nn.BCEWithLogitsLoss()
        best, best_state, bad = float("inf"), None, 0
        bs = int(hp["batch_size"])
        for _ in range(int(hp["epochs"])):
            self._net.train()
            perm = torch.randperm(len(tr_x))
            for i in range(0, len(perm), bs):
                b = perm[i : i + bs]
                opt.zero_grad()
                loss = lossf(self._net(tr_x[b]), tr_y[b])
                loss.backward()
                nn.utils.clip_grad_norm_(self._net.parameters(), 1.0)
                opt.step()
            self._net.eval()
            with torch.no_grad():
                vl = lossf(self._net(va_x), va_y).item()
            if vl < best - 1e-4:
                best, bad = vl, 0
                best_state = {k: v.clone() for k, v in self._net.state_dict().items()}
            else:
                bad += 1
                if bad >= int(hp["patience"]):
                    break
        if best_state:
            self._net.load_state_dict(best_state)
        self.is_fitted = True
        return self

    def predict_proba(self, X):
        Xp = self._prep(X)
        out = np.full(len(Xp), 0.5)
        win, idx = self._windows(Xp)
        if len(idx):
            self._net.eval()
            with torch.no_grad():
                out[idx] = torch.sigmoid(self._net(torch.tensor(win))).numpy()
        return out
