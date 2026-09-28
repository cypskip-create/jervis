import { useCallback, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import {
  Activity, AlertTriangle, ArrowUpRight, BarChart3,
  Bell, ChevronDown, CircleHelp, Clock3, Command, Gauge, LayoutDashboard,
  LogOut, Search, Settings2, ShieldCheck, ShieldAlert,
} from "lucide-react";

const apiBase = (import.meta.env.VITE_API_BASE ?? "http://localhost:8000/api/v1").replace(/\/$/, "");
const storedToken = () => sessionStorage.getItem("jervis_token") ?? "";

type User = { username: string; role: string };
type Overview = {
  mode: string; global_enabled: boolean; emergency_stop: boolean; open_positions: number;
  signals: number; completed_trades: number; updated_at: string;
  last_events: { severity: string; event_type: string; message: string; created_at: string }[];
};
type SymbolRow = { id: string; canonical: string; asset_class: string; enabled: boolean; disable_policy: string };
type StrategyRow = { id: string; key: string; display_name: string; enabled: boolean };
type AssetClassRow = { name: string; enabled: boolean };
type Position = { id: string; symbol_id: string; direction: string; volume: string; entry_price: string; stop_loss: string | null; take_profit: string | null; opened_at: string };
type Backtest = { id: string; status: string; data_hash: string; metrics: Record<string, unknown>; created_at: string };

export default function App() {
  const [token, setToken] = useState(storedToken);
  const [user, setUser] = useState<User | null>(null);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [symbols, setSymbols] = useState<SymbolRow[]>([]);
  const [strategies, setStrategies] = useState<StrategyRow[]>([]);
  const [assetClasses, setAssetClasses] = useState<AssetClassRow[]>([]);
  const [positions, setPositions] = useState<Position[]>([]);
  const [backtests, setBacktests] = useState<Backtest[]>([]);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState("");
  const [section, setSection] = useState("Overview");
  const [busy, setBusy] = useState(false);

  const request = useCallback(async <T,>(path: string, init: RequestInit = {}): Promise<T> => {
    const response = await fetch(`${apiBase}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}`, ...init.headers },
    });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(body.detail ?? `Request failed (${response.status})`);
    }
    return response.json() as Promise<T>;
  }, [token]);

  const refresh = useCallback(async () => {
    if (!token) return;
    try {
      const [me, state, symbolRows, strategyRows, classRows, openRows, backtestRows] = await Promise.all([
        request<User>("/me"), request<Overview>("/overview"), request<SymbolRow[]>("/symbols"),
        request<StrategyRow[]>("/strategies"), request<AssetClassRow[]>("/asset-classes"),
        request<Position[]>("/positions"), request<Backtest[]>("/backtests"),
      ]);
      setUser(me); setOverview(state); setSymbols(symbolRows); setStrategies(strategyRows);
      setAssetClasses(classRows); setPositions(openRows); setBacktests(backtestRows); setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not load platform data");
    }
  }, [request, token]);

  useEffect(() => { void refresh(); }, [refresh]);
  useEffect(() => {
    if (!token) return;
    const socketUrl = apiBase.replace(/^http/, "ws").replace(/\/api\/v1$/, "/api/v1/live");
    let socket: WebSocket | undefined;
    let retryTimer: number | undefined;
    let stopped = false;
    const connect = () => {
      if (stopped) return;
      socket = new WebSocket(socketUrl, ["bearer", token]);
      socket.onopen = () => setConnected(true);
      socket.onclose = (event) => {
        setConnected(false);
        if (event.code === 4401) {
          sessionStorage.removeItem("jervis_token");
          setToken("");
          setError("Your session expired. Sign in again.");
        } else if (!stopped) {
          retryTimer = window.setTimeout(connect, 3000);
        }
      };
      socket.onmessage = (event) => {
        try { setOverview(JSON.parse(event.data) as Overview); } catch { /* ignore malformed frames */ }
      };
    };
    connect();
    return () => {
      stopped = true;
      if (retryTimer !== undefined) window.clearTimeout(retryTimer);
      socket?.close();
    };
  }, [token]);

  async function login(username: string, password: string) {
    setBusy(true); setError("");
    try {
      const response = await fetch(`${apiBase}/auth/login`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail ?? "Sign in failed");
      sessionStorage.setItem("jervis_token", data.access_token);
      setToken(data.access_token);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Sign in failed"); }
    finally { setBusy(false); }
  }

  function logout() {
    sessionStorage.removeItem("jervis_token"); setToken(""); setUser(null); setOverview(null);
    setSymbols([]); setStrategies([]); setPositions([]); setBacktests([]);
    setAssetClasses([]);
  }

  async function setGlobal(enabled: boolean) {
    try { await request("/controls/global", { method: "PUT", body: JSON.stringify({ enabled }) }); await refresh(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Could not change bot state"); }
  }

  async function stopNow() {
    if (!window.confirm("Activate the emergency stop? New trade entries will be blocked.")) return;
    try { await request("/controls/emergency-stop", { method: "POST" }); await refresh(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Emergency stop failed"); }
  }

  async function resetStop() {
    if (!window.confirm("Reset emergency stop? New entries may resume if all other gates allow them.")) return;
    try { await request("/controls/emergency-stop/reset", { method: "POST", body: JSON.stringify({ confirm: true }) }); await refresh(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Emergency stop reset failed"); }
  }

  async function updateSymbol(row: SymbolRow, enabled: boolean) {
    try {
      await request(`/symbols/${row.id}/control`, {
        method: "PUT", body: JSON.stringify({ enabled, disable_policy: row.disable_policy }),
      }); await refresh();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Symbol update failed"); }
  }

  async function updateStrategy(row: StrategyRow, enabled: boolean) {
    try { await request(`/strategies/${row.id}/control`, { method: "PUT", body: JSON.stringify({ enabled }) }); await refresh(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Strategy update failed"); }
  }

  async function updateAssetClass(row: AssetClassRow, enabled: boolean) {
    try {
      await request(`/asset-classes/${encodeURIComponent(row.name)}/control`, {
        method: "PUT", body: JSON.stringify({ enabled }),
      }); await refresh();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Asset class update failed"); }
  }

  if (!token) return <Login onLogin={login} busy={busy} error={error} />;

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand"><div className="brand-mark"><Activity size={18} /></div><span>JERVIS<span className="brand-dot">.</span></span><button className="icon-btn collapse"><Command size={16} /></button></div>
        <div className="workspace"><div className="workspace-logo">J</div><div><b>Jervis Trading</b><small>Operations workspace</small></div><ChevronDown size={15} /></div>
        <div className="nav-label">WORKSPACE</div>
        <nav className="nav-list">
          <NavItem active={section === "Overview"} icon={<LayoutDashboard size={17} />} label="Overview" onClick={() => setSection("Overview")} />
          <NavItem active={section === "Markets"} icon={<Activity size={17} />} label="Markets" badge={symbols.length} onClick={() => setSection("Markets")} />
          <NavItem active={section === "Strategies"} icon={<Command size={17} />} label="Strategies" onClick={() => setSection("Strategies")} />
          <NavItem active={section === "Positions"} icon={<ArrowUpRight size={17} />} label="Positions" badge={positions.length} onClick={() => setSection("Positions")} />
          <NavItem active={section === "Backtests"} icon={<BarChart3 size={17} />} label="Backtests" onClick={() => setSection("Backtests")} />
        </nav>
        <div className="nav-label nav-label-spaced">SYSTEM</div>
        <NavItem active={section === "Settings"} icon={<Settings2 size={17} />} label="Settings" onClick={() => setSection("Settings")} />
        <div className="sidebar-bottom"><div className="help-card"><CircleHelp size={17} /><div><b>Need a hand?</b><small>Read the operator guide</small></div><ArrowUpRight size={14} /></div><div className="user-card"><div className="avatar">{user?.username.slice(0, 1).toUpperCase() ?? "U"}</div><div className="user-text"><b>{user?.username ?? "Operator"}</b><small>{user?.role ?? "viewer"}</small></div><button title="Sign out" className="icon-btn" onClick={logout}><LogOut size={16} /></button></div></div>
      </aside>

      <main className="main-area">
        <header className="topbar"><div className="breadcrumbs">Workspace <span>/</span> <b>{section}</b></div><div className="top-actions"><div className="searchbox"><Search size={15} /><span>Search anything</span><kbd>⌘ K</kbd></div><button className="icon-btn notification" aria-label="Notifications"><Bell size={17} /><i /></button><div className="live-status"><span className={connected ? "live-dot" : "offline-dot"} />{connected ? "Live updates" : "Connecting"}</div></div></header>
        <div className="page-wrap">
          {error && <div className="error-banner"><AlertTriangle size={17} />{error}<button onClick={() => setError("")}>Dismiss</button></div>}
          <div className="page-heading"><div><div className="eyebrow">{new Date().toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric", year: "numeric" }).toUpperCase()} <span>·</span> {Intl.DateTimeFormat().resolvedOptions().timeZone}</div><h1>{section === "Overview" ? "Good evening" : section}<span className="heading-period">{section === "Overview" ? ", operator." : ""}</span></h1><p>{section === "Overview" ? "Here’s the latest from your trading system." : pageSubtitle(section)}</p></div><div className="heading-actions"><button className="button quiet" onClick={() => void refresh()}><Activity size={15} /> Refresh</button><button className="button danger" onClick={() => void stopNow()}><ShieldAlert size={15} /> Emergency stop</button></div></div>
          <div className="mode-banner"><div className="mode-icon"><ShieldCheck size={18} /></div><div><b>Paper environment</b><span>Simulated execution only · Live trading is unavailable</span></div><span className="mode-pill">{overview?.mode?.toUpperCase() ?? "PAPER"}</span><span className="mode-divider" /><span className="last-sync"><Clock3 size={14} /> Updated {overview?.updated_at ? new Date(overview.updated_at).toLocaleTimeString() : "—"}</span></div>
          {section === "Overview" && <OverviewPage overview={overview} symbols={symbols} positions={positions} onGlobal={setGlobal} onSection={setSection} onResetStop={resetStop} canReset={user?.role === "admin"} />}
          {section === "Markets" && <MarketsPage symbols={symbols} assetClasses={assetClasses} onToggle={updateSymbol} onClassToggle={updateAssetClass} />}
          {section === "Strategies" && <StrategiesPage strategies={strategies} onToggle={updateStrategy} />}
          {section === "Positions" && <PositionsPage positions={positions} symbols={symbols} />}
          {section === "Backtests" && <BacktestsPage backtests={backtests} />}
          {section === "Settings" && <SettingsPage overview={overview} />}
          <footer className="footer"><span>Jervis Platform <b>v0.2.0</b></span><span><span className="footer-dot" /> Research stack ready</span><span>Help center <ArrowUpRight size={12} /></span></footer>
        </div>
      </main>
    </div>
  );
}

function Login({ onLogin, busy, error }: { onLogin: (username: string, password: string) => Promise<void>; busy: boolean; error: string }) {
  const [username, setUsername] = useState(""); const [password, setPassword] = useState("");
  return <div className="login-page"><div className="login-card"><div className="brand login-brand"><div className="brand-mark"><Activity size={18} /></div><span>JERVIS<span className="brand-dot">.</span></span></div><div className="eyebrow">TRADING OPERATIONS</div><h1>Welcome back</h1><p>Sign in to your secure operations workspace.</p>{error && <div className="login-error">{error}</div>}<form onSubmit={(event) => { event.preventDefault(); void onLogin(username, password); }}><label>Username<input autoComplete="username" value={username} onChange={(event) => setUsername(event.target.value)} required /></label><label>Password<input type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} required /></label><button className="button primary login-button" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button></form><small className="login-foot"><ShieldCheck size={14} /> Protected operator access</small></div></div>;
}

function NavItem({ active, icon, label, badge, onClick }: { active: boolean; icon: ReactNode; label: string; badge?: number; onClick: () => void }) {
  return <button className={`nav-item ${active ? "active" : ""}`} onClick={onClick}>{icon}<span>{label}</span>{badge !== undefined && <small>{badge}</small>}</button>;
}

function OverviewPage({ overview, symbols, positions, onGlobal, onSection, onResetStop, canReset }: { overview: Overview | null; symbols: SymbolRow[]; positions: Position[]; onGlobal: (enabled: boolean) => Promise<void>; onSection: (name: string) => void; onResetStop: () => Promise<void>; canReset: boolean }) {
  const enabled = symbols.filter((symbol) => symbol.enabled).length;
  const classTotals = useMemo(() => symbols.reduce<Record<string, number>>((result, row) => { result[row.asset_class] = (result[row.asset_class] ?? 0) + 1; return result; }, {}), [symbols]);
  return <>
    <div className="metric-grid"><Metric label="BOT STATUS" value={overview?.global_enabled ? "Active" : "Paused"} delta={overview?.global_enabled ? "Entry controls enabled" : "New entries disabled"} positive={Boolean(overview?.global_enabled)} icon={<Gauge size={17} />} accent={overview?.global_enabled ? "green" : "amber"} /><Metric label="OPEN POSITIONS" value={String(overview?.open_positions ?? 0).padStart(2, "0")} delta="Across all markets" icon={<ArrowUpRight size={17} />} accent="blue" /><Metric label="STRATEGY SIGNALS" value={String(overview?.signals ?? 0).padStart(2, "0")} delta="Recorded decisions" icon={<Activity size={17} />} accent="violet" /><Metric label="COMPLETED TRADES" value={String(overview?.completed_trades ?? 0).padStart(2, "0")} delta="Journaled results" icon={<BarChart3 size={17} />} accent="teal" /></div>
    <div className="content-grid"><section className="panel market-panel"><PanelTitle title="Market watch" subtitle={`${enabled} of ${symbols.length} symbols enabled`} action="All markets" onAction={() => onSection("Markets")} /><div className="table-head"><span>INSTRUMENT</span><span>CLASS</span><span>STATUS</span><span>CONTROL</span></div>{symbols.slice(0, 6).map((row, index) => <div className="market-row" key={row.id}><div className="instrument"><div className={`coin coin-${index % 4}`}>{row.canonical.slice(0, 1)}</div><b>{row.canonical}</b></div><span className="asset-class">{row.asset_class}</span><span className={`status-text ${row.enabled ? "status-on" : "status-off"}`}><i />{row.enabled ? "Enabled" : "Disabled"}</span><MiniToggle checked={row.enabled} onChange={() => onSection("Markets")} /></div>)}{symbols.length === 0 && <Empty text="No symbols configured yet." />}</section>
      <section className="panel health-panel"><PanelTitle title="System health" subtitle="Service overview" action="View details" onAction={() => onSection("Settings")} /><div className="health-score"><div className="health-ring"><span><b>3</b><small>/ 3</small></span></div><div><b>Research stack ready</b><small>MT5 execution is not implemented yet.</small></div></div><div className="service-list"><ServiceRow name="Database" detail="Connected" /><ServiceRow name="Risk engine" detail="Ready" /><ServiceRow name="Paper execution" detail="Available" /><ServiceRow name="MT5 adapter" detail="Not implemented" muted /></div></section>
      <section className="panel control-panel"><PanelTitle title="Quick controls" subtitle="Changes are audited" /><div className="control-line"><div className="control-symbol"><div className="control-icon"><Command size={16} /></div><div><b>Global bot</b><small>Allow new trade entries</small></div></div><MiniToggle checked={Boolean(overview?.global_enabled)} onChange={() => void onGlobal(!overview?.global_enabled)} /></div><div className="control-line"><div className="control-symbol"><div className="control-icon emergency"><ShieldAlert size={16} /></div><div><b>Emergency stop</b><small>{overview?.emergency_stop ? "Active · reset requires admin" : "Not active"}</small></div></div>{overview?.emergency_stop && canReset ? <button className="small-tag tag-danger" onClick={() => void onResetStop()}>RESET</button> : <span className={`small-tag ${overview?.emergency_stop ? "tag-danger" : "tag-safe"}`}>{overview?.emergency_stop ? "STOPPED" : "READY"}</span>}</div><div className="risk-note"><ShieldCheck size={15} /><span>Central risk approval is required before any paper order.</span></div></section>
      <section className="panel positions-panel"><PanelTitle title="Open positions" subtitle="Live exposure overview" action="View positions" onAction={() => onSection("Positions")} />{positions.slice(0, 3).map((row) => <PositionRow key={row.id} position={row} symbol={symbols.find((item) => item.id === row.symbol_id)?.canonical ?? "Unknown"} />)}{positions.length === 0 && <Empty text="No open positions. Position updates appear here." />}</section>
      <section className="panel allocation-panel"><PanelTitle title="Market allocation" subtitle="Enabled symbols by class" /><div className="allocation-chart"><div className="allocation-donut"><div><b>{enabled}</b><small>symbols</small></div></div><div className="allocation-legend">{Object.entries(classTotals).map(([name, value], index) => <div key={name}><i className={`legend-dot legend-${index % 4}`} /><span>{name}</span><b>{value}</b></div>)}</div></div></section>
      <section className="panel activity-panel"><PanelTitle title="Recent activity" subtitle="Latest system events" action="View all" onAction={() => onSection("Settings")} />{overview?.last_events?.slice(0, 4).map((item, index) => <div className="activity-row" key={`${item.event_type}-${index}`}><span className={`activity-icon activity-${item.severity.toLowerCase()}`}><Activity size={14} /></span><div><b>{item.message}</b><small>{item.event_type.replaceAll("_", " ")}</small></div><time>{new Date(item.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</time></div>)}{!overview?.last_events?.length && <Empty text="No system events recorded." />}</section>
    </div>
    <div className="disclaimer"><AlertTriangle size={15} /><span>Paper mode only. Historical performance and simulated fills do not guarantee future results.</span></div>
  </>;
}

function MarketsPage({ symbols, assetClasses, onToggle, onClassToggle }: { symbols: SymbolRow[]; assetClasses: AssetClassRow[]; onToggle: (row: SymbolRow, enabled: boolean) => Promise<void>; onClassToggle: (row: AssetClassRow, enabled: boolean) => Promise<void> }) {
  return <><section className="panel full-panel"><PanelTitle title="Asset class controls" subtitle="Class switches provide an additional entry gate." />{assetClasses.map((row) => <div className="strategy-row" key={row.name}><div className="strategy-icon"><BarChart3 size={17} /></div><div className="strategy-copy"><b>{row.name}</b><small>Asset class entry permission · saved persistently</small></div><span className={`small-tag ${row.enabled ? "tag-safe" : "tag-neutral"}`}>{row.enabled ? "ENABLED" : "DISABLED"}</span><MiniToggle checked={row.enabled} onChange={(enabled) => void onClassToggle(row, enabled)} /></div>)}{assetClasses.length === 0 && <Empty text="Seed reference records to configure asset classes." />}</section><section className="panel full-panel markets-detail"><PanelTitle title="Markets & symbols" subtitle="Symbol switches persist across restarts. Disabling blocks new entries." /><div className="table-head market-table-head"><span>INSTRUMENT</span><span>ASSET CLASS</span><span>DISABLE POLICY</span><span>STATUS</span><span>CONTROL</span></div>{symbols.map((row) => <div className="market-row wide-row" key={row.id}><div className="instrument"><div className="coin">{row.canonical.slice(0, 1)}</div><b>{row.canonical}</b></div><span className="asset-class">{row.asset_class}</span><span className="asset-class">{row.disable_policy.replaceAll("_", " ")}</span><span className={`status-text ${row.enabled ? "status-on" : "status-off"}`}><i />{row.enabled ? "Enabled" : "Disabled"}</span><MiniToggle checked={row.enabled} onChange={(enabled) => void onToggle(row, enabled)} /></div>)}{symbols.length === 0 && <Empty text="Create symbol records in the database to manage market controls." />}<div className="warning-box"><AlertTriangle size={16} /><div><b>Position close controls are unavailable</b><span>A validated broker adapter is required before the dashboard can close an open position.</span></div></div></section></>;
}

function StrategiesPage({ strategies, onToggle }: { strategies: StrategyRow[]; onToggle: (row: StrategyRow, enabled: boolean) => Promise<void> }) {
  return <section className="panel full-panel"><PanelTitle title="Strategy controls" subtitle="Strategies propose setups; the central risk engine approves or rejects them." />{strategies.map((row) => <div className="strategy-row" key={row.id}><div className="strategy-icon"><Command size={17} /></div><div className="strategy-copy"><b>{row.display_name}</b><small>{row.key} · proposal-only engine</small></div><span className={`small-tag ${row.enabled ? "tag-safe" : "tag-neutral"}`}>{row.enabled ? "ENABLED" : "DISABLED"}</span><MiniToggle checked={row.enabled} onChange={(enabled) => void onToggle(row, enabled)} /></div>)}{strategies.length === 0 && <Empty text="No strategy records configured yet." />}</section>;
}

function PositionsPage({ positions, symbols }: { positions: Position[]; symbols: SymbolRow[] }) {
  return <section className="panel full-panel"><PanelTitle title="Open positions" subtitle="Paper positions with their protective levels" />{positions.map((row) => <PositionRow key={row.id} position={row} symbol={symbols.find((item) => item.id === row.symbol_id)?.canonical ?? "Unknown"} />)}{positions.length === 0 && <Empty text="No open positions." />}</section>;
}

function BacktestsPage({ backtests }: { backtests: Backtest[] }) {
  return <section className="panel full-panel"><PanelTitle title="Backtest research" subtitle="Persisted replay results · data hash and split metadata retained" />{backtests.map((row) => <div className="backtest-row" key={row.id}><div className="strategy-icon"><BarChart3 size={17} /></div><div className="strategy-copy"><b>Run {row.id.slice(0, 8)}</b><small>{new Date(row.created_at).toLocaleString()} · {row.data_hash.slice(0, 16)}…</small></div><span className="small-tag tag-safe">{row.status.toUpperCase()}</span><div className="backtest-metrics">{Object.entries(row.metrics).slice(0, 3).map(([key, value]) => <span key={key}>{key.replaceAll("_", " ")} <b>{String(value)}</b></span>)}</div></div>)}{backtests.length === 0 && <Empty text="No replay results are saved yet. Run a backtest from the Python research interface." />}<div className="warning-box"><BarChart3 size={16} /><div><b>Research controls</b><span>In-sample, out-of-sample, and walk-forward partitions should be reviewed separately. The dashboard does not rank strategies by profit.</span></div></div></section>;
}

function SettingsPage({ overview }: { overview: Overview | null }) {
  return <section className="panel full-panel"><PanelTitle title="Platform settings" subtitle="Runtime facts and safety boundary" /><div className="settings-grid"><div className="setting-item"><span>Environment</span><b>{overview?.mode?.toUpperCase() ?? "PAPER"}</b></div><div className="setting-item"><span>Live trading</span><b className="danger-text">Unavailable</b></div><div className="setting-item"><span>Broker terminal</span><b>Not connected</b></div><div className="setting-item"><span>Authentication</span><b>Signed 8-hour bearer token</b></div><div className="setting-item"><span>Emergency stop</span><b>{overview?.emergency_stop ? "Active" : "Ready"}</b></div><div className="setting-item"><span>Global entries</span><b>{overview?.global_enabled ? "Enabled" : "Disabled"}</b></div></div><div className="warning-box"><ShieldCheck size={16} /><div><b>Change credentials outside this page</b><span>Use the server-side admin bootstrap utility and environment secret configuration. Passwords and signing keys are never sent to the browser.</span></div></div></section>;
}

function Metric({ label, value, delta, icon, accent, positive }: { label: string; value: string; delta: string; icon: ReactNode; accent: string; positive?: boolean }) {
  return <div className="metric-card"><div className="metric-top"><span>{label}</span><i className={`metric-icon accent-${accent}`}>{icon}</i></div><div className="metric-value">{value}</div><div className={`metric-foot ${positive ? "positive" : ""}`}>{positive && <ArrowUpRight size={13} />}{delta}</div></div>;
}

function PanelTitle({ title, subtitle, action, onAction }: { title: string; subtitle: string; action?: string; onAction?: () => void }) {
  return <div className="panel-title"><div><h2>{title}</h2><p>{subtitle}</p></div>{action && <button className="text-action" onClick={onAction}>{action}<ArrowUpRight size={13} /></button>}</div>;
}

function MiniToggle({ checked, onChange }: { checked: boolean; onChange: (value: boolean) => void }) {
  return <button role="switch" aria-checked={checked} className={`toggle ${checked ? "checked" : ""}`} onClick={() => onChange(!checked)}><span /></button>;
}

function ServiceRow({ name, detail, muted }: { name: string; detail: string; muted?: boolean }) {
  return <div className="service-row"><span className={`service-dot ${muted ? "muted" : ""}`} /><span>{name}</span><small className={muted ? "muted-text" : ""}>{detail}</small></div>;
}

function PositionRow({ position, symbol }: { position: Position; symbol: string }) {
  return <div className="position-row"><div className="instrument"><div className="coin">{symbol.slice(0, 1)}</div><b>{symbol}</b></div><span className={position.direction === "long" ? "long-text" : "short-text"}>{position.direction.toUpperCase()}</span><span className="asset-class">{position.volume} lots</span><span className="position-price">{position.entry_price}</span><span className="asset-class">SL {position.stop_loss ?? "—"} · TP {position.take_profit ?? "—"}</span></div>;
}

function Empty({ text }: { text: string }) { return <div className="empty-state">{text}</div>; }
function pageSubtitle(section: string) { return ({ Markets: "Manage market availability and symbol policies.", Strategies: "Review strategy switches and setup generators.", Positions: "Review currently open paper positions.", Backtests: "Compare research runs with reproducible datasets.", Settings: "Platform state, authentication, and safety configuration." } as Record<string, string>)[section] ?? "Trading system operations."; }
