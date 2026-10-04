"""Order Management: target-weight -> order diff, idempotent submission, persistence, reconciliation."""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone

import pandas as pd

from alphaforge.db import session_scope
from alphaforge.db.models import Order, Trade
from .broker import Broker, OrderRequest, OrderStatus

log = logging.getLogger(__name__)


class OrderManager:
    def __init__(self, broker: Broker, min_notional: float = 50.0, min_rebalance_frac: float = 0.15, model_id: str | None = None):
        self.broker = broker
        self.min_notional = min_notional
        self.min_rebalance_frac = min_rebalance_frac  # ignore drift smaller than this fraction of target
        self.model_id = model_id
        self._open: dict[str, tuple[float, float, datetime]] = {}  # symbol -> (qty, avg_px, entry_ts)

    def rebalance(self, target_weights: pd.Series, marks: dict[str, float], ts: datetime | None = None, allow_order=lambda ts: True) -> list[OrderStatus]:
        ts = ts or datetime.now(timezone.utc)
        acct = self.broker.account()
        cur = {p.symbol: p.qty for p in self.broker.positions()}
        results = []
        for sym, w in target_weights.items():
            px = marks.get(sym)
            if px is None or px <= 0 or not math.isfinite(px):
                continue
            target_qty = math.floor(abs(w) * acct.equity / px) * (1 if w >= 0 else -1)
            cur_qty = cur.get(sym, 0.0)
            delta = target_qty - cur_qty
            if delta == 0:
                continue
            # skip tiny rebalances unless we are going flat
            if target_qty != 0 and abs(delta) * px < self.min_notional:
                continue
            if target_qty != 0 and abs(delta) < self.min_rebalance_frac * abs(target_qty):
                continue
            if not allow_order(ts):
                break
            req = OrderRequest(sym, "buy" if delta > 0 else "sell", abs(delta), reason=f"rebalance w={w:.3f}")
            st = self.broker.submit(req)
            self._persist(req, st)
            self._track_fills(sym, cur_qty, st, ts)
            results.append(st)
        return results

    def flatten(self, reason: str = "flatten") -> list[OrderStatus]:
        out = []
        for p in self.broker.positions():
            if p.qty == 0:
                continue
            req = OrderRequest(p.symbol, "sell" if p.qty > 0 else "buy", abs(p.qty), reason=reason)
            st = self.broker.submit(req)
            self._persist(req, st)
            self._track_fills(p.symbol, p.qty, st, datetime.now(timezone.utc))
            out.append(st)
        return out

    # ---- persistence -------------------------------------------------- #
    def _persist(self, req: OrderRequest, st: OrderStatus):
        try:
            with session_scope() as s:
                s.add(Order(client_order_id=req.client_order_id, broker_order_id=st.broker_order_id, symbol=req.symbol, side=req.side, qty=req.qty, order_type=req.order_type, limit_price=req.limit_price, status=st.status, filled_qty=st.filled_qty, avg_fill_price=st.avg_fill_price, reason=req.reason, mode=self.broker.mode))
        except Exception:
            log.exception("failed to persist order")

    def _track_fills(self, sym: str, prev_qty: float, st: OrderStatus, ts: datetime):
        if st.status != "filled" or st.avg_fill_price is None:
            return
        signed = st.filled_qty if st.side == "buy" else -st.filled_qty
        new_qty = prev_qty + signed
        if prev_qty == 0:
            self._open[sym] = (new_qty, st.avg_fill_price, ts)
            return
        if (new_qty > 0) == (prev_qty > 0) and abs(new_qty) > abs(prev_qty):
            q0, p0, t0 = self._open.get(sym, (prev_qty, st.avg_fill_price, ts))
            self._open[sym] = (new_qty, (abs(q0) * p0 + abs(signed) * st.avg_fill_price) / abs(new_qty), t0)
            return
        q0, p0, t0 = self._open.get(sym, (prev_qty, st.avg_fill_price, ts))
        closed = min(abs(prev_qty), abs(signed))
        pnl = (st.avg_fill_price - p0) * closed if prev_qty > 0 else (p0 - st.avg_fill_price) * closed
        try:
            with session_scope() as s:
                s.add(Trade(symbol=sym, side="long" if prev_qty > 0 else "short", qty=closed, entry_price=p0, exit_price=st.avg_fill_price, entry_time=t0, exit_time=ts, pnl=pnl, model_id=self.model_id, mode=self.broker.mode))
        except Exception:
            log.exception("failed to persist trade")
        if new_qty == 0:
            self._open.pop(sym, None)
        elif (new_qty > 0) != (prev_qty > 0):
            self._open[sym] = (new_qty, st.avg_fill_price, ts)
        else:
            self._open[sym] = (new_qty, p0, t0)

    def reconcile(self) -> dict:
        """Compare broker positions to our tracked opens; log and adopt broker truth."""
        broker_pos = {p.symbol: p for p in self.broker.positions()}
        diffs = {}
        for sym, p in broker_pos.items():
            q, _, _ = self._open.get(sym, (0.0, 0.0, None))
            if abs(q - p.qty) > 1e-9:
                diffs[sym] = {"ours": q, "broker": p.qty}
                self._open[sym] = (p.qty, p.avg_entry_price, datetime.now(timezone.utc))
        for sym in list(self._open):
            if sym not in broker_pos:
                diffs[sym] = {"ours": self._open[sym][0], "broker": 0.0}
                self._open.pop(sym)
        if diffs:
            log.warning("reconciliation differences: %s", diffs)
        return diffs
