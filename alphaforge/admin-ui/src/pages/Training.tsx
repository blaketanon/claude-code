import { useState } from "react";
import { fmtTs, get, post } from "../api";
import { Field, JsonBlock, Pill, usePoll } from "../components/ui";

export function Training() {
  const [types] = usePoll(() => get("/api/models/types"), 60000);
  const [features] = usePoll(() => get("/api/training/features"), 60000);
  const [jobs, , refresh] = usePoll(() => get("/api/training/jobs?limit=30"), 3000);
  const [form, setForm] = useState({ symbols: "", model_type: "lightgbm", hpo_trials: 10, walk_forward_folds: 3, lookback_days: 60, provider: "", notes: "", pt_mult: 1.5, sl_mult: 1.0, max_holding: 12 });
  const [feat, setFeat] = useState<string[] | null>(null);
  const [sel, setSel] = useState<any>(null);
  const [msg, setMsg] = useState("");
  const submit = async () => {
    setMsg("");
    try {
      const body: any = { model_type: form.model_type, hpo_trials: +form.hpo_trials, walk_forward_folds: +form.walk_forward_folds, lookback_days: +form.lookback_days, notes: form.notes, label: { pt_mult: +form.pt_mult, sl_mult: +form.sl_mult, max_holding: +form.max_holding } };
      if (form.symbols.trim()) body.symbols = form.symbols.split(",").map((s) => s.trim().toUpperCase()).filter(Boolean);
      if (form.provider) body.provider = form.provider;
      if (feat) body.feature_set = feat;
      const r = await post("/api/training/jobs", body);
      setMsg(`queued job ${r.job_id}`); refresh();
    } catch (e: any) { setMsg(e.message); }
  };
  const allFeat: string[] = features || [];
  const active = feat ?? allFeat;
  return (
    <div className="stack">
      <div className="card">
        <h3>New training job</h3>
        <div className="row">
          <Field label="Symbols (blank = configured universe)"><input value={form.symbols} onChange={(e) => setForm({ ...form, symbols: e.target.value })} placeholder="SPY,QQQ,AAPL" /></Field>
          <Field label="Model"><select value={form.model_type} onChange={(e) => setForm({ ...form, model_type: e.target.value })}>{(types || ["lightgbm"]).map((t: string) => <option key={t}>{t}</option>)}</select></Field>
          <Field label="HPO trials (Optuna)"><input type="number" value={form.hpo_trials} onChange={(e) => setForm({ ...form, hpo_trials: +e.target.value })} /></Field>
          <Field label="Walk-forward folds"><input type="number" value={form.walk_forward_folds} onChange={(e) => setForm({ ...form, walk_forward_folds: +e.target.value })} /></Field>
          <Field label="Lookback days"><input type="number" value={form.lookback_days} onChange={(e) => setForm({ ...form, lookback_days: +e.target.value })} /></Field>
          <Field label="Data provider"><select value={form.provider} onChange={(e) => setForm({ ...form, provider: e.target.value })}><option value="">default</option><option>synthetic</option><option>yfinance</option><option>alpaca</option></select></Field>
        </div>
        <div className="row" style={{ marginTop: 10 }}>
          <Field label="Profit-take × vol"><input type="number" step="0.1" value={form.pt_mult} onChange={(e) => setForm({ ...form, pt_mult: +e.target.value })} /></Field>
          <Field label="Stop-loss × vol"><input type="number" step="0.1" value={form.sl_mult} onChange={(e) => setForm({ ...form, sl_mult: +e.target.value })} /></Field>
          <Field label="Max holding (bars)"><input type="number" value={form.max_holding} onChange={(e) => setForm({ ...form, max_holding: +e.target.value })} /></Field>
          <Field label="Notes"><input value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} /></Field>
          <button className="primary" onClick={submit}>Train</button>
          <span className="muted">{msg}</span>
        </div>
        <details style={{ marginTop: 10 }}><summary className="muted">Feature set ({active.length}/{allFeat.length})</summary>
          <div className="row" style={{ marginTop: 8 }}>{allFeat.map((f) => <label key={f} style={{ display: "flex", gap: 6, alignItems: "center", width: 160 }}><input type="checkbox" style={{ width: "auto" }} checked={active.includes(f)} onChange={(e) => setFeat(e.target.checked ? [...active, f] : active.filter((x) => x !== f))} />{f}</label>)}</div>
        </details>
      </div>
      <div className="card">
        <h3>Jobs</h3>
        <table><thead><tr><th>Job</th><th>Kind</th><th>Status</th><th style={{ width: 180 }}>Progress</th><th>Created</th><th>Result</th><th></th></tr></thead>
          <tbody>{(jobs || []).map((j: any) => <tr key={j.job_id} style={{ cursor: "pointer" }} onClick={() => setSel(j)}>
            <td className="mono">{j.job_id}</td><td>{j.kind}</td><td><Pill v={j.status} /></td>
            <td><progress value={j.progress} max={1} /></td><td>{fmtTs(j.created_at)}</td>
            <td className="mono">{j.result?.model_id || j.result?.run_id || j.result?.promoted || (j.error ? <span className="error">{j.error.split("\n")[0].slice(0, 80)}</span> : "")}</td>
            <td onClick={(e) => e.stopPropagation()}>{(j.status === "running" || j.status === "queued") && <button onClick={() => post(`/api/training/jobs/${j.job_id}/cancel`).then(refresh)}>Cancel</button>}</td></tr>)}
            {!jobs?.length && <tr><td colSpan={7} className="muted">no jobs yet</td></tr>}</tbody></table>
      </div>
      {sel && <div className="card"><h3>Job {sel.job_id}</h3><pre>{sel.log || "(no log yet)"}</pre>{sel.error && <pre className="error">{sel.error}</pre>}{sel.result && <JsonBlock v={sel.result} />}</div>}
    </div>
  );
}
