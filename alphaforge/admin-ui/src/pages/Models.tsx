import { useState } from "react";
import { fmtNum, fmtPct, fmtTs, get, post } from "../api";
import { JsonBlock, Pill, usePoll } from "../components/ui";

export function Models() {
  const [models, err, refresh] = usePoll(() => get("/api/models"), 10000);
  const [sel, setSel] = useState<any>(null);
  const [msg, setMsg] = useState("");
  const act = async (fn: () => Promise<any>) => { try { await fn(); setMsg("ok"); } catch (e: any) { setMsg(e.message); } refresh(); };
  return (
    <div className="stack">
      <div className="card">
        <h3>Registry</h3>
        <div className="row" style={{ marginBottom: 10 }}>
          <button onClick={() => act(() => post("/api/models/rollback"))}>Roll back champion</button>
          <span className="muted">{msg || err}</span>
        </div>
        <table>
          <thead><tr><th>Model</th><th>Type</th><th>Status</th><th className="num">OOS Sharpe</th><th className="num">DSR</th><th className="num">Max DD</th><th className="num">Hit rate</th><th className="num">CV AUC</th><th>Created</th><th></th></tr></thead>
          <tbody>
            {(models || []).map((m: any) => {
              const wf = m.metrics?.walk_forward || {};
              return <tr key={m.model_id} style={{ cursor: "pointer" }} onClick={() => setSel(m)}>
                <td className="mono">{m.model_id}</td><td>{m.model_type}</td><td><Pill v={m.status} /></td>
                <td className="num">{fmtNum(wf.sharpe)}</td><td className="num">{fmtNum(wf.dsr)}</td><td className="num">{fmtPct(wf.max_drawdown)}</td><td className="num">{fmtPct(wf.hit_rate, 1)}</td><td className="num">{fmtNum(m.metrics?.cv?.auc, 3)}</td>
                <td>{fmtTs(m.created_at)}</td>
                <td onClick={(e) => e.stopPropagation()}>
                  {m.status !== "champion" && <button onClick={() => confirm(`Promote ${m.model_id} to champion? The live engine hot-swaps immediately.`) && act(() => post(`/api/models/${m.model_id}/promote`))}>Promote</button>}
                  {m.status === "candidate" && <button style={{ marginLeft: 6 }} onClick={() => act(() => post(`/api/models/${m.model_id}/status`, { status: "retired" }))}>Retire</button>}
                </td></tr>;
            })}
            {!models?.length && <tr><td colSpan={10} className="muted">No models yet. Train one from the Training page.</td></tr>}
          </tbody>
        </table>
      </div>
      {sel && <div className="card"><h3>{sel.model_id} <span className="muted">· {sel.notes}</span></h3>
        <div className="row" style={{ alignItems: "flex-start" }}>
          <div style={{ flex: 1, minWidth: 300 }}><h3>Walk-forward (OOS)</h3><JsonBlock v={sel.metrics?.walk_forward} /></div>
          <div style={{ flex: 1, minWidth: 300 }}><h3>Cross-validation</h3><JsonBlock v={sel.metrics?.cv} /><h3>Hyperparameters</h3><JsonBlock v={sel.hyperparams} /></div>
          <div style={{ flex: 1, minWidth: 300 }}><h3>Feature importance</h3>
            <table><tbody>{Object.entries(sel.metrics?.feature_importance || {}).sort((a: any, b: any) => b[1] - a[1]).slice(0, 15).map(([k, v]: any) => <tr key={k}><td>{k}</td><td className="num">{fmtPct(v, 1)}</td></tr>)}</tbody></table></div>
        </div></div>}
    </div>
  );
}
