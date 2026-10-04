import { useEffect, useState } from "react";
import { fmtTs, get, post, put } from "../api";
import { Field, Pill, usePoll } from "../components/ui";

export function Risk() {
  const [status, , refresh] = usePoll(() => get("/api/trading/status"), 5000);
  const [events] = usePoll(() => get("/api/trading/risk-events?limit=50"), 5000);
  const [audit] = usePoll(() => get("/api/trading/audit?limit=50"), 10000);
  const [d, setD] = useState<any>(null);
  const [msg, setMsg] = useState("");
  const limits = status?.risk?.limits;
  useEffect(() => { if (limits && !d) setD({ ...limits, min_confidence: 0.55, target_vol: 0.15 }); }, [limits, d]);
  const save = async () => { try { await put("/api/trading/risk", d); setMsg("limits updated"); refresh(); } catch (e: any) { setMsg(e.message); } };
  const num = (k: string, step = 0.01) => <Field label={k}><input type="number" step={step} value={d?.[k] ?? ""} onChange={(e) => setD({ ...d, [k]: +e.target.value })} /></Field>;
  const st = status?.risk?.state || {};
  return (
    <div className="stack">
      {st.killed && <div className="banner">Kill switch engaged: {st.kill_reason} <button style={{ marginLeft: 12 }} onClick={() => post("/api/trading/kill/reset").then(refresh)}>Reset kill switch</button></div>}
      {st.halted_for_day && !st.killed && <div className="banner">Halted for the day: daily loss limit reached. Resumes next session.</div>}
      <div className="card"><h3>Limits (apply immediately to the live engine)</h3>
        <div className="row">{num("max_position_pct")}{num("max_gross_exposure", 0.05)}{num("daily_loss_limit_pct", 0.005)}{num("max_drawdown_kill_pct")}{num("kelly_fraction", 0.05)}{num("min_confidence")}{num("target_vol")}</div>
        <div className="row" style={{ marginTop: 10 }}>
          <label style={{ display: "flex", gap: 6, alignItems: "center" }}><input type="checkbox" style={{ width: "auto" }} checked={!!d?.allow_short} onChange={(e) => setD({ ...d, allow_short: e.target.checked })} />Allow shorts</label>
          <label style={{ display: "flex", gap: 6, alignItems: "center" }}><input type="checkbox" style={{ width: "auto" }} checked={!!d?.pdt_guard} onChange={(e) => setD({ ...d, pdt_guard: e.target.checked })} />PDT guard (&lt; $25k accounts)</label>
          <button className="primary" onClick={save} disabled={!d}>Save</button><span className="muted">{msg}</span></div>
        <p className="muted mono">peak equity {st.peak_equity} · day start {st.day_start_equity} · day {st.current_day}</p>
      </div>
      <div className="row" style={{ alignItems: "stretch" }}>
        <div className="card" style={{ flex: 1, minWidth: 360 }}><h3>Risk events</h3>
          <table><thead><tr><th>When</th><th>Level</th><th>Code</th><th>Message</th></tr></thead><tbody>{(events || []).map((e: any, i: number) => <tr key={i}><td>{fmtTs(e.ts)}</td><td><Pill v={e.level} /></td><td className="mono">{e.code}</td><td>{e.message}</td></tr>)}{!events?.length && <tr><td colSpan={4} className="muted">none</td></tr>}</tbody></table></div>
        <div className="card" style={{ flex: 1, minWidth: 360 }}><h3>Audit log</h3>
          <table><thead><tr><th>When</th><th>Actor</th><th>Action</th><th>Detail</th></tr></thead><tbody>{(audit || []).map((a: any, i: number) => <tr key={i}><td>{fmtTs(a.ts)}</td><td>{a.actor}</td><td className="mono">{a.action}</td><td className="mono">{JSON.stringify(a.detail)}</td></tr>)}{!audit?.length && <tr><td colSpan={4} className="muted">none</td></tr>}</tbody></table></div>
      </div>
    </div>
  );
}
