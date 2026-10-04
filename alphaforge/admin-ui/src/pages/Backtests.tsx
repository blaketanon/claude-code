import { useState } from "react";
import { fmtNum, fmtPct, fmtTs, get, post } from "../api";
import { EquityChart } from "../components/EquityChart";
import { Field, JsonBlock, usePoll } from "../components/ui";

export function Backtests() {
  const [models] = usePoll(() => get("/api/models"), 30000);
  const [runs, , refresh] = usePoll(() => get("/api/backtests"), 5000);
  const [form, setForm] = useState({ model_id: "", slippage_bps: 1, min_confidence: 0.55, max_weight: 0.2, target_vol: 0.15 });
  const [sel, setSel] = useState<any>(null);
  const [msg, setMsg] = useState("");
  const submit = async () => {
    try {
      const r = await post("/api/backtests", { model_id: form.model_id || models?.[0]?.model_id, slippage_bps: +form.slippage_bps, policy: { min_confidence: +form.min_confidence, max_weight: +form.max_weight, target_vol: +form.target_vol } });
      setMsg(`queued job ${r.job_id} - results appear below when done`); refresh();
    } catch (e: any) { setMsg(e.message); }
  };
  const open = async (run_id: string) => setSel(await get(`/api/backtests/${run_id}`));
  return (
    <div className="stack">
      <div className="card"><h3>Run backtest on stored bars</h3>
        <div className="row">
          <Field label="Model"><select value={form.model_id} onChange={(e) => setForm({ ...form, model_id: e.target.value })}>{(models || []).map((m: any) => <option key={m.model_id} value={m.model_id}>{m.model_id} ({m.status})</option>)}</select></Field>
          <Field label="Slippage (bps)"><input type="number" step="0.5" value={form.slippage_bps} onChange={(e) => setForm({ ...form, slippage_bps: +e.target.value })} /></Field>
          <Field label="Min confidence"><input type="number" step="0.01" value={form.min_confidence} onChange={(e) => setForm({ ...form, min_confidence: +e.target.value })} /></Field>
          <Field label="Max weight"><input type="number" step="0.01" value={form.max_weight} onChange={(e) => setForm({ ...form, max_weight: +e.target.value })} /></Field>
          <Field label="Target vol"><input type="number" step="0.01" value={form.target_vol} onChange={(e) => setForm({ ...form, target_vol: +e.target.value })} /></Field>
          <button className="primary" onClick={submit} disabled={!models?.length}>Run</button><span className="muted">{msg}</span>
        </div></div>
      <div className="card"><h3>Runs</h3>
        <table><thead><tr><th>Run</th><th>Model</th><th className="num">Return</th><th className="num">Sharpe</th><th className="num">Sortino</th><th className="num">Max DD</th><th className="num">Trades</th><th className="num">Hit</th><th className="num">PF</th><th>When</th></tr></thead>
          <tbody>{(runs || []).map((r: any) => <tr key={r.run_id} style={{ cursor: "pointer" }} onClick={() => open(r.run_id)}>
            <td className="mono">{r.run_id}</td><td className="mono">{r.model_id}</td><td className="num">{fmtPct(r.metrics.total_return)}</td><td className="num">{fmtNum(r.metrics.sharpe)}</td><td className="num">{fmtNum(r.metrics.sortino)}</td><td className="num">{fmtPct(r.metrics.max_drawdown)}</td><td className="num">{r.trade_count}</td><td className="num">{fmtPct(r.metrics.hit_rate, 1)}</td><td className="num">{fmtNum(r.metrics.profit_factor)}</td><td>{fmtTs(r.created_at)}</td></tr>)}
            {!runs?.length && <tr><td colSpan={10} className="muted">no runs yet</td></tr>}</tbody></table></div>
      {sel && <div className="card"><h3>Run {sel.run_id}</h3><EquityChart points={(sel.equity_curve || []).map(([ts, equity]: any) => ({ ts, equity }))} /><JsonBlock v={sel.metrics} /></div>}
    </div>
  );
}
