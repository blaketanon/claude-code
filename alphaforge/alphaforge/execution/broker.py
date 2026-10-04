"""Broker abstraction with a deterministic paper simulator and an Alpaca adapter."""
from __future__ import annotations

import logging
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone

from alphaforge.config import get_settings

log = logging.getLogger(__name__)


@dataclass
class OrderRequest:
    symbol: str
    side: str                       # buy|sell
    qty: float
    order_type: str = "market"      # market|limit
    limit_price: float | None = None
    time_in_force: str = "day"
    client_order_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    reason: str = ""


@dataclass
class OrderStatus:
    client_order_id: str
    broker_order_id: str | None
    symbol: str
    side: str
    qty: float
    status: str                     # accepted|filled|partially_filled|cancelled|rejected
    filled_qty: float = 0.0
    avg_fill_price: float | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class Position:
    symbol: str
    qty: float
    avg_entry_price: float
    market_value: float
    unrealized_pnl: float


@dataclass
class Account:
    equity: float
    cash: float
    buying_power: float
    day_trade_count: int = 0
    pattern_day_trader: bool = False


class Broker(ABC):
    name = "base"
    mode = "paper"

    @abstractmethod
    def account(self) -> Account: ...

    @abstractmethod
    def positions(self) -> list[Position]: ...

    @abstractmethod
    def submit(self, req: OrderRequest) -> OrderStatus: ...

    @abstractmethod
    def cancel_all(self) -> None: ...

    @abstractmethod
    def order_status(self, client_order_id: str) -> OrderStatus | None: ...

    def close_all_positions(self) -> list[OrderStatus]:
        out = []
        for p in self.positions():
            if p.qty == 0:
                continue
            side = "sell" if p.qty > 0 else "buy"
            out.append(self.submit(OrderRequest(p.symbol, side, abs(p.qty), reason="flatten")))
        return out

    def is_market_open(self) -> bool | None:
        return None  # unknown -> engine falls back to its own clock


class PaperBroker(Broker):
    """In-memory simulator. Fills market orders immediately at `mark + slippage`."""

    name = "paper"
    mode = "paper"

    def __init__(self, initial_cash: float | None = None, slippage_bps: float = 1.0, commission_per_share: float = 0.0):
        self.cash = initial_cash if initial_cash is not None else get_settings().initial_capital
        self.slippage_bps = slippage_bps
        self.commission = commission_per_share
        self._pos: dict[str, list[float]] = {}  # symbol -> [qty, avg_price]
        self._marks: dict[str, float] = {}
        self._orders: dict[str, OrderStatus] = {}
        self.fills: list[dict] = []

    def set_marks(self, marks: dict[str, float]):
        self._marks.update({k: float(v) for k, v in marks.items() if v is not None})

    def account(self) -> Account:
        mv = sum(q * self._marks.get(s, p) for s, (q, p) in self._pos.items())
        eq = self.cash + mv
        return Account(equity=eq, cash=self.cash, buying_power=max(0.0, self.cash) * 2)

    def positions(self) -> list[Position]:
        out = []
        for s, (q, p) in self._pos.items():
            if q == 0:
                continue
            m = self._marks.get(s, p)
            out.append(Position(s, q, p, q * m, (m - p) * q))
        return out

    def submit(self, req: OrderRequest) -> OrderStatus:
        mark = self._marks.get(req.symbol)
        if mark is None:
            st = OrderStatus(req.client_order_id, None, req.symbol, req.side, req.qty, "rejected", raw={"error": "no mark price"})
            self._orders[req.client_order_id] = st
            return st
        slip = self.slippage_bps / 1e4
        px = mark * (1 + slip) if req.side == "buy" else mark * (1 - slip)
        if req.order_type == "limit" and req.limit_price is not None:
            if (req.side == "buy" and px > req.limit_price) or (req.side == "sell" and px < req.limit_price):
                st = OrderStatus(req.client_order_id, uuid.uuid4().hex, req.symbol, req.side, req.qty, "accepted")
                self._orders[req.client_order_id] = st
                return st  # resting; simplified: never fills
            px = req.limit_price
        signed = req.qty if req.side == "buy" else -req.qty
        q0, p0 = self._pos.get(req.symbol, [0.0, 0.0])
        q1 = q0 + signed
        if q0 == 0 or (q0 > 0) == (signed > 0):
            p1 = (abs(q0) * p0 + abs(signed) * px) / abs(q1) if q1 else 0.0
        else:
            p1 = p0 if q1 != 0 and (q1 > 0) == (q0 > 0) else px
        fee = req.qty * self.commission
        self.cash -= signed * px + fee
        self._pos[req.symbol] = [q1, p1]
        st = OrderStatus(req.client_order_id, uuid.uuid4().hex, req.symbol, req.side, req.qty, "filled", req.qty, px)
        self._orders[req.client_order_id] = st
        self.fills.append({"ts": datetime.now(timezone.utc).isoformat(), "symbol": req.symbol, "side": req.side, "qty": req.qty, "price": px, "fee": fee, "reason": req.reason})
        return st

    def cancel_all(self) -> None:
        for st in self._orders.values():
            if st.status == "accepted":
                st.status = "cancelled"

    def order_status(self, client_order_id: str) -> OrderStatus | None:
        return self._orders.get(client_order_id)


class AlpacaBroker(Broker):
    """Alpaca Trading API v2 via REST. Paper or live depending on settings."""

    name = "alpaca"

    def __init__(self, api_key: str | None = None, api_secret: str | None = None, paper: bool | None = None):
        import httpx

        s = get_settings()
        self.key = api_key or s.alpaca_api_key
        self.secret = api_secret or s.alpaca_api_secret
        paper = s.alpaca_paper if paper is None else paper
        if not paper and not s.live_enabled:
            raise RuntimeError("Live trading requires ALPHAFORGE_TRADING_MODE=live and ALPHAFORGE_LIVE_TRADING_ACK=I_UNDERSTAND_THE_RISK")
        if not self.key or not self.secret:
            raise RuntimeError("Alpaca credentials missing")
        self.mode = "paper" if paper else "live"
        base = "https://paper-api.alpaca.markets" if paper else "https://api.alpaca.markets"
        self._c = httpx.Client(base_url=base, headers={"APCA-API-KEY-ID": self.key, "APCA-API-SECRET-KEY": self.secret}, timeout=20)

    def _get(self, path, **params):
        r = self._c.get(path, params=params)
        r.raise_for_status()
        return r.json()

    def account(self) -> Account:
        a = self._get("/v2/account")
        return Account(float(a["equity"]), float(a["cash"]), float(a["buying_power"]), int(a.get("daytrade_count", 0)), bool(a.get("pattern_day_trader", False)))

    def positions(self) -> list[Position]:
        return [Position(p["symbol"], float(p["qty"]), float(p["avg_entry_price"]), float(p["market_value"]), float(p["unrealized_pl"])) for p in self._get("/v2/positions")]

    def submit(self, req: OrderRequest) -> OrderStatus:
        body = {"symbol": req.symbol, "qty": str(req.qty), "side": req.side, "type": req.order_type, "time_in_force": req.time_in_force, "client_order_id": req.client_order_id}
        if req.order_type == "limit":
            body["limit_price"] = str(req.limit_price)
        r = self._c.post("/v2/orders", json=body)
        if r.status_code >= 400:
            return OrderStatus(req.client_order_id, None, req.symbol, req.side, req.qty, "rejected", raw={"error": r.text})
        return self._to_status(r.json())

    def cancel_all(self) -> None:
        self._c.delete("/v2/orders").raise_for_status()

    def order_status(self, client_order_id: str) -> OrderStatus | None:
        r = self._c.get(f"/v2/orders:by_client_order_id", params={"client_order_id": client_order_id})
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return self._to_status(r.json())

    def is_market_open(self) -> bool | None:
        try:
            return bool(self._get("/v2/clock")["is_open"])
        except Exception:
            return None

    @staticmethod
    def _to_status(o: dict) -> OrderStatus:
        status_map = {"new": "accepted", "accepted": "accepted", "pending_new": "accepted", "filled": "filled", "partially_filled": "partially_filled", "canceled": "cancelled", "rejected": "rejected", "expired": "cancelled"}
        return OrderStatus(o.get("client_order_id"), o.get("id"), o["symbol"], o["side"], float(o["qty"]), status_map.get(o["status"], o["status"]), float(o.get("filled_qty") or 0), float(o["filled_avg_price"]) if o.get("filled_avg_price") else None, raw=o)


def get_broker(name: str | None = None, **kwargs) -> Broker:
    s = get_settings()
    name = name or ("alpaca" if s.alpaca_api_key else "paper")
    if name == "paper":
        return PaperBroker(**kwargs)
    if name == "alpaca":
        return AlpacaBroker(**kwargs)
    raise ValueError(name)
