import React, { useEffect, useState } from "react";

export function Tile({ label, value, sub }: { label: string; value: React.ReactNode; sub?: React.ReactNode }) {
  return <div className="card tile"><div className="label">{label}</div><div className="value">{value}</div>{sub && <div className="sub">{sub}</div>}</div>;
}

export function Pill({ v }: { v: string }) { return <span className={`pill ${v}`}>{v}</span>; }

export function usePoll<T>(fn: () => Promise<T>, ms: number, deps: any[] = []): [T | undefined, string, () => void] {
  const [data, setData] = useState<T>();
  const [err, setErr] = useState("");
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let alive = true;
    const run = () => fn().then((d) => alive && (setData(d), setErr(""))).catch((e) => alive && setErr(String(e.message || e)));
    run();
    const id = setInterval(run, ms);
    return () => { alive = false; clearInterval(id); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tick, ...deps]);
  return [data, err, () => setTick((t) => t + 1)];
}

export function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <div style={{ minWidth: 140 }}><label>{label}</label>{children}</div>;
}

export function JsonBlock({ v }: { v: any }) { return <pre>{JSON.stringify(v, null, 1)}</pre>; }
