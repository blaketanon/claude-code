import { useEffect, useState } from "react";
import { getToken, login, setToken } from "./api";
import { Autopilot } from "./pages/Autopilot";
import { Backtests } from "./pages/Backtests";
import { Dashboard } from "./pages/Dashboard";
import { Logs } from "./pages/Logs";
import { Models } from "./pages/Models";
import { Risk } from "./pages/Risk";
import { Training } from "./pages/Training";

const PAGES = { Dashboard, Models, Training, Backtests, Autopilot, Risk, Logs } as const;
type Page = keyof typeof PAGES;

function Login({ onDone }: { onDone: () => void }) {
  const [u, setU] = useState("admin"); const [p, setP] = useState(""); const [err, setErr] = useState("");
  const go = async (e: React.FormEvent) => { e.preventDefault(); try { await login(u, p); onDone(); } catch (x: any) { setErr(x.message); } };
  return <div className="login"><form className="card stack" onSubmit={go}><h2 style={{ margin: 0 }}>AlphaForge Admin</h2>
    <div><label>Username</label><input value={u} onChange={(e) => setU(e.target.value)} autoFocus /></div>
    <div><label>Password</label><input type="password" value={p} onChange={(e) => setP(e.target.value)} /></div>
    {err && <div className="error">{err}</div>}<button className="primary">Sign in</button></form></div>;
}

export default function App() {
  const [authed, setAuthed] = useState(!!getToken());
  const [page, setPage] = useState<Page>(() => (location.hash.slice(1) as Page) in PAGES ? (location.hash.slice(1) as Page) : "Dashboard");
  const [theme, setTheme] = useState<string>(() => { try { return localStorage.getItem("alphaforge.theme") || ""; } catch { return ""; } });
  useEffect(() => { const h = () => setAuthed(false); window.addEventListener("alphaforge:logout", h); return () => window.removeEventListener("alphaforge:logout", h); }, []);
  useEffect(() => { location.hash = page; }, [page]);
  useEffect(() => { theme ? document.documentElement.setAttribute("data-theme", theme) : document.documentElement.removeAttribute("data-theme"); try { localStorage.setItem("alphaforge.theme", theme); } catch { /* ignore */ } }, [theme]);
  if (!authed) return <Login onDone={() => setAuthed(true)} />;
  const Current = PAGES[page];
  return <div className="shell">
    <nav className="nav"><div className="brand">⚡ AlphaForge</div>
      {(Object.keys(PAGES) as Page[]).map((k) => <button key={k} className={k === page ? "active" : ""} onClick={() => setPage(k)}>{k}</button>)}
      <div className="spacer" />
      <button onClick={() => setTheme(theme === "dark" ? "light" : "dark")}>{theme === "dark" ? "Light mode" : "Dark mode"}</button>
      <button onClick={() => { setToken(null); setAuthed(false); }}>Sign out</button></nav>
    <main className="main"><h1>{page}</h1><Current /></main></div>;
}
