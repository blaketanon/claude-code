import { useEffect, useState } from "react";
import { del, fmtTs, get, post, put } from "../api";
import { Field, JsonBlock, Pill, usePoll } from "../components/ui";

export function Autopilot() {
  const [cfg, , refreshCfg] = usePoll(() => get("/api/autopilot/config"), 30000);
  const [exps, , refreshExps] = usePoll(() => get("/api/autopilot/experiments"), 5000);
  const [jobs] = usePoll(() => get("/api/training/jobs?kind=autopilot&limit=5"), 4000);
  const [draft, setDraft] = useState<any>(null);
  const [msg, setMsg] = useState("");
  const [hyp, setHyp] = useState("");
  const [expCfg, setExpCfg] = useState('{"model_type": "lightgbm", "hpo_trials": 5}');
  useEffect(() => { if (cfg && !draft) setDraft(cfg); }, [cfg, draft]);
  const save = async () => { try { await put("/api/autopilot/config", draft); setMsg("saved"); refreshCfg(); } catch (e: any) { setMsg(e.message); } };
  const run = async () => { try { const r = await post("/api/autopilot/run"); setMsg(`cycle queued: job ${r.job_id}`); } catch (e: any) { setMsg(e.message); } };
  const queue = async () => { try { await post("/api/autopilot/experiments", { hypothesis: hyp, config: JSON.parse(expCfg) }); setHyp(""); refreshExps(); } catch (e: any) { setMsg(e.message); } };
  const d = draft || {};
  const num = (k: string, step = 0.01) => <Field label={k}><input type="number" step={step} value={d[k] ?? ""} onChange={(e) => setDraft({ ...d, [k]: +e.target.value })} /></Field>;
  const bool = (k: string, label: string) => <label style={{ display: "flex", gap: 6, alignItems: "center" }}><input type="checkbox" style={{ width: "auto" }} checked={!!d[k]} onChange={(e) => setDraft({ ...d, [k]: e.target.checked })} />{label}</label>;
  return (
    <div className="stack">
      <div className="card"><h3>Self-training loop</h3>
        <p className="muted" style={{ marginTop: 0 }}>Runs nightly after the close (and on demand). Each cycle syncs data, measures feature drift and live decay, trains retrain / HPO / drift-pruned / ensemble challengers plus any queued or Claude-proposed experiments under one walk-forward harness, and promotes only when every gate passes.</p>
        <div className="row">{bool("enabled", "Autopilot enabled")}{bool("require_manual_approval", "Require manual approval (mark as challenger instead of promoting)")}{bool("use_llm_research", "Ask Claude for experiment proposals")}</div>
        <div className="row" style={{ marginTop: 10 }}>{num("hpo_trials", 1)}{num("lookback_days", 1)}{num("min_sharpe_improvement", 0.05)}{num("min_dsr")}{num("max_drawdown_tolerance")}{num("max_promotions_per_week", 1)}{num("psi_retrain", 0.05)}{num("llm_proposals", 1)}</div>
        <div className="row" style={{ marginTop: 12 }}><button className="primary" onClick={save}>Save</button><button onClick={run}>Run cycle now</button><span className="muted">{msg}</span></div>
      </div>
      <div className="card"><h3>Recent cycles</h3>
        {(jobs || []).map((j: any) => <div key={j.job_id} style={{ marginBottom: 8 }}><Pill v={j.status} /> <span className="mono">{j.job_id}</span> <span className="muted">{fmtTs(j.created_at)}</span>{j.status === "running" && <progress value={j.progress} max={1} />}{j.result?.candidates && <JsonBlock v={{ promoted: j.result.promoted, champion: j.result.champion, candidates: j.result.candidates, drift_top: j.result.drift_top }} />}</div>)}
        {!jobs?.length && <span className="muted">no cycles yet</span>}
      </div>
      <div className="card"><h3>Queue an experiment</h3>
        <div className="row"><Field label="Hypothesis"><input value={hyp} onChange={(e) => setHyp(e.target.value)} placeholder="Shorter holding period improves calibration in volatile regime" /></Field>
          <div style={{ flex: 1, minWidth: 300 }}><label>TrainingConfig overrides (JSON)</label><textarea rows={2} value={expCfg} onChange={(e) => setExpCfg(e.target.value)} /></div>
          <button onClick={queue} disabled={!hyp}>Queue</button></div>
        <table style={{ marginTop: 10 }}><thead><tr><th>Source</th><th>Hypothesis</th><th>Status</th><th>Why / result</th><th>Model</th><th>When</th><th></th></tr></thead>
          <tbody>{(exps || []).map((e: any) => <tr key={e.experiment_id}><td>{e.source}</td><td>{e.hypothesis}</td><td><Pill v={e.status} /></td><td className="mono">{e.result?.why || e.result?.error || ""}{e.result?.walk_forward && ` · sharpe ${Number(e.result.walk_forward.sharpe).toFixed(2)} dsr ${Number(e.result.walk_forward.dsr).toFixed(2)}`}</td><td className="mono">{e.model_id || ""}</td><td>{fmtTs(e.created_at)}</td><td>{e.status === "proposed" && e.is_active && <button onClick={() => del(`/api/autopilot/experiments/${e.experiment_id}`).then(refreshExps)}>Drop</button>}</td></tr>)}
            {!exps?.length && <tr><td colSpan={7} className="muted">no experiments yet</td></tr>}</tbody></table>
      </div>
    </div>
  );
}
