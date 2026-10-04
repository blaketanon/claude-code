import { useState } from "react";
import { fmtMoney, fmtNum, fmtPct, fmtTs, get, post } from "../api";
import { Pill, usePoll } from "../components/ui";

export function Logs() {
  const [tab, setTab] = useState<"trades" | "orders" | "signals" | "data">("trades");
  const [trades] = usePoll(() => get("/api/trading/trades?limit=200"), 10000);
  const [orders] = usePoll(() => get("/api/trading/orders?limit=200"), 10000);
  const [signals] = usePoll(() => get("/api/trading/signals?limit=200"), 10000);
  const [cov, , refreshCov] = usePoll(() => get("/api/data/coverage"), 15000);
  const [msg, setMsg] = useState("");
  const pnl = (trades || []).reduce((a: number, t: any) => a + t.pnl, 0);
  return (
    <div className="stack">
      <div className="row">{(["trades", "orders", "signals", "data"] as const).map((t) => <button key={t} className={tab === t ? "primary" : ""} onClick={() => setTab(t)}>{t}</button>)}</div>
      {tab === "trades" && <div className="card"><h3>Closed trades · realized {fmtMoney(pnl)} over {trades?.length ?? 0}</h3>
        <table><thead><tr><th>Exit</th><th>Symbol</th><th>Side</th><th className="num">Qty</th><th className="num">Entry</th><th className="num">Exit</th><th className="num">P&amp;L</th><th>Model</th><th>Mode</th></tr></thead>
          <tbody>{(trades || []).map((t: any) => <tr key={t.id}><td>{fmtTs(t.exit_time)}</td><td>{t.symbol}</td><td>{t.side}</td><td className="num">{t.qty}</td><td className="num">{fmtNum(t.entry_price)}</td><td className="num">{fmtNum(t.exit_price)}</td><td className="num" style={{ color: t.pnl >= 0 ? "var(--good)" : "var(--critical)" }}>{fmtMoney(t.pnl)}</td><td className="mono">{t.model_id}</td><td>{t.mode}</td></tr>)}</tbody></table></div>}
      {tab === "orders" && <div className="card"><h3>Orders</h3>
        <table><thead><tr><th>Created</th><th>Symbol</th><th>Side</th><th className="num">Qty</th><th>Type</th><th>Status</th><th className="num">Fill</th><th>Reason</th><th>Mode</th></tr></thead>
          <tbody>{(orders || []).map((o: any) => <tr key={o.id}><td>{fmtTs(o.created_at)}</td><td>{o.symbol}</td><td>{o.side}</td><td className="num">{o.qty}</td><td>{o.order_type}</td><td><Pill v={o.status} /></td><td className="num">{fmtNum(o.avg_fill_price)}</td><td className="mono">{o.reason}</td><td>{o.mode}</td></tr>)}</tbody></table></div>}
      {tab === "signals" && <div className="card"><h3>Signals</h3>
        <table><thead><tr><th>Bar</th><th>Symbol</th><th className="num">P(up)</th><th className="num">Confidence</th><th>Regime</th><th className="num">Target w</th><th className="num">LLM</th><th>Model</th></tr></thead>
          <tbody>{(signals || []).map((s: any) => <tr key={s.id}><td>{fmtTs(s.ts)}</td><td>{s.symbol}</td><td className="num">{fmtNum(s.prob_up, 3)}</td><td className="num">{fmtNum(s.confidence, 3)}</td><td>{s.regime}</td><td className="num">{fmtPct(s.target_weight, 1)}</td><td className="num">{s.llm_sentiment == null ? "–" : fmtNum(s.llm_sentiment)}</td><td className="mono">{s.model_id}</td></tr>)}</tbody></table></div>}
      {tab === "data" && <div className="card"><h3>Bar store · {cov?.timeframe} via {cov?.provider}</h3>
        <div className="row" style={{ marginBottom: 10 }}><button onClick={() => post("/api/data/sync", { lookback_days: 60 }).then((r) => { setMsg(`sync job ${r.job_id}`); setTimeout(refreshCov, 3000); })}>Sync universe (60d)</button><span className="muted">{msg}</span></div>
        <table><thead><tr><th>Symbol</th><th className="num">Bars</th><th>From</th><th>To</th></tr></thead><tbody>{(cov?.symbols || []).map((s: any) => <tr key={s.symbol}><td>{s.symbol}</td><td className="num">{s.rows}</td><td>{fmtTs(s.from)}</td><td>{fmtTs(s.to)}</td></tr>)}{!cov?.symbols?.length && <tr><td colSpan={4} className="muted">no bars stored yet</td></tr>}</tbody></table></div>}
    </div>
  );
}
