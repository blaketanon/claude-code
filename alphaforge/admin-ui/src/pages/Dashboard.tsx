import { useState } from "react";
import { fmtMoney, fmtNum, fmtPct, fmtTs, get, post } from "../api";
import { EquityChart } from "../components/EquityChart";
import { Pill, Tile, usePoll } from "../components/ui";

export function Dashboard() {
  const [status, err, refresh] = usePoll(() => get("/api/trading/status"), 5000);
  const [equity] = usePoll(() => get("/api/trading/equity?hours=720"), 15000);
  const [busy, setBusy] = useState("");
  const [msg, setMsg] = useState("");
  const act = async (name: string, fn: () => Promise<any>) => {
    setBusy(name); setMsg("");
    try { const r = await fn(); setMsg(JSON.stringify(r).slice(0, 300)); } catch (e: any) { setMsg(`Error: ${e.message}`); } finally { setBusy(""); refresh(); }
  };
  const s = status || {};
  const killed = s.risk?.state?.killed;
  const first = equity?.[0]?.equity, last = s.account?.equity;
  const ret = first && last ? last / first - 1 : null;
  return (
    <div className="stack">
      {killed && <div className="banner">Kill switch engaged: {s.risk.state.kill_reason}. Reset it from the Risk page after review.</div>}
      {s.trading_mode === "live" && <div className={`banner ${s.live_enabled ? "" : "ok"}`}>{s.live_enabled ? "LIVE TRADING ENABLED - real money" : "Live mode configured but not acknowledged - running paper"}</div>}
      <div className="grid">
        <Tile label="Engine" value={<Pill v={s.state || "…"} />} sub={`mode: ${s.mode || "–"} · model: ${s.model_id || "none"}`} />
        <Tile label="Equity" value={fmtMoney(last)} sub={ret == null ? "" : `${fmtPct(ret)} since first point`} />
        <Tile label="Cash" value={fmtMoney(s.account?.cash)} sub={`buying power ${fmtMoney(s.account?.buying_power)}`} />
        <Tile label="Positions" value={s.positions?.length ?? "–"} sub={`market ${s.market_open ? "open" : "closed"} · last bar ${fmtTs(s.last_bar)}`} />
        <Tile label="Cycles" value={s.cycles ?? "–"} sub={s.last_error ? <span className="error">{s.last_error}</span> : "no errors"} />
      </div>
      <div className="card">
        <h3>Equity curve</h3>
        <EquityChart points={equity || []} />
      </div>
      <div className="card">
        <h3>Controls</h3>
        <div className="row">
          <button className="primary" disabled={!!busy} onClick={() => act("start", () => post("/api/trading/start"))}>Start engine</button>
          <button disabled={!!busy} onClick={() => act("stop", () => post("/api/trading/stop", { flatten: true }))}>Stop &amp; flatten</button>
          <button disabled={!!busy} onClick={() => act("step", () => post("/api/trading/step"))}>Run one paper step</button>
          <button className="danger" disabled={!!busy} onClick={() => confirm("Flatten everything and halt trading?") && act("kill", () => post("/api/trading/kill"))}>KILL SWITCH</button>
        </div>
        {(msg || err) && <p className="mono muted" style={{ marginBottom: 0 }}>{msg || err}</p>}
      </div>
      <div className="row" style={{ alignItems: "stretch" }}>
        <div className="card" style={{ flex: 1, minWidth: 320 }}>
          <h3>Open positions</h3>
          <table><thead><tr><th>Symbol</th><th className="num">Qty</th><th className="num">Avg entry</th><th className="num">Mkt value</th><th className="num">Unrealized</th></tr></thead>
            <tbody>{(s.positions || []).map((p: any) => <tr key={p.symbol}><td>{p.symbol}</td><td className="num">{p.qty}</td><td className="num">{fmtNum(p.avg_entry_price)}</td><td className="num">{fmtMoney(p.market_value)}</td><td className="num" style={{ color: p.unrealized_pnl >= 0 ? "var(--good)" : "var(--critical)" }}>{fmtMoney(p.unrealized_pnl)}</td></tr>)}
              {!s.positions?.length && <tr><td colSpan={5} className="muted">flat</td></tr>}</tbody></table>
        </div>
        <div className="card" style={{ flex: 1, minWidth: 320 }}>
          <h3>Latest signals</h3>
          <table><thead><tr><th>Symbol</th><th className="num">P(up)</th><th>Regime</th><th className="num">Target w</th><th className="num">LLM tilt</th></tr></thead>
            <tbody>{Object.entries(s.signals || {}).map(([sym, v]: any) => <tr key={sym}><td>{sym}</td><td className="num">{fmtNum(v.prob_up, 3)}</td><td>{v.regime}</td><td className="num">{fmtPct(v.weight, 1)}</td><td className="num">{v.sentiment == null ? "–" : fmtNum(v.sentiment)}</td></tr>)}
              {!Object.keys(s.signals || {}).length && <tr><td colSpan={5} className="muted">no cycle yet</td></tr>}</tbody></table>
        </div>
      </div>
    </div>
  );
}
