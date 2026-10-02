"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { api, daysLeft, money, pct } from "@/lib/api";
import { useLiveCases } from "@/lib/useLive";

const STATUS_TONE: Record<string, string> = {
  DISPUTE_DRAFTED: "good",
  DISPUTE_FILED: "good",
  RECOVERED: "good",
  PARTIALLY_RECOVERED: "good",
  ACCEPTED: "",
  ESCALATED: "warn",
  ERROR: "bad",
  LOST: "bad",
};

export default function Dashboard() {
  const router = useRouter();
  const { cases, stats, activity, connected, refresh } = useLiveCases();
  const [filter, setFilter] = useState<string>("ALL");
  const [syncing, setSyncing] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const statuses = useMemo(() => ["ALL", ...Array.from(new Set(cases.map((c) => c.status))).sort()], [cases]);
  const visible = filter === "ALL" ? cases : cases.filter((c) => c.status === filter);

  async function sync() {
    setSyncing(true);
    setMessage(null);
    try {
      const res = await api<{ discovered: number; created: number }>("/portals/sync", { method: "POST" });
      setMessage(`found ${res.discovered} deductions, ${res.created} new`);
      await refresh();
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "sync failed");
    } finally {
      setSyncing(false);
    }
  }

  return (
    <>
      <div className="row spread">
        <div>
          <h1>Deductions</h1>
          <span className="muted">
            <span className={`dot ${connected ? "on" : ""}`} />
            {connected ? "live" : "reconnecting"}
            {message ? ` - ${message}` : ""}
          </span>
        </div>
        <button className="primary" onClick={sync} disabled={syncing}>
          {syncing ? "Syncing" : "Sync retailer portals"}
        </button>
      </div>

      <div className="kpis">
        <div className="kpi">
          <div className="label">Deductions tracked</div>
          <div className="value">{stats?.total_cases ?? "-"}</div>
        </div>
        <div className="kpi">
          <div className="label">Flagged invalid</div>
          <div className="value">{money(stats?.flagged_invalid_cents)}</div>
        </div>
        <div className="kpi">
          <div className="label">Recovered</div>
          <div className="value">{money(stats?.recovered_cents)}</div>
        </div>
        <div className="kpi">
          <div className="label">Awaiting review</div>
          <div className="value">{stats?.awaiting_review ?? "-"}</div>
        </div>
        <div className="kpi">
          <div className="label">Dispute win rate</div>
          <div className="value">{pct(stats?.dispute_win_rate)}</div>
        </div>
      </div>

      <div className="chips">
        {statuses.map((s) => (
          <button key={s} className={`chip ${filter === s ? "active" : ""}`} onClick={() => setFilter(s)}>
            {s === "ALL" ? "All" : s.replace(/_/g, " ").toLowerCase()}
          </button>
        ))}
      </div>

      <table>
        <thead>
          <tr>
            <th>Reference</th>
            <th>Retailer</th>
            <th>Reason</th>
            <th className="num">Claimed</th>
            <th className="num">Invalid</th>
            <th className="num">Confidence</th>
            <th className="num">Days left</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {visible.map((c) => {
            const left = daysLeft(c.window_deadline);
            return (
              <tr key={c.id} className="link" onClick={() => router.push(`/cases/${c.id}`)}>
                <td>
                  <Link href={`/cases/${c.id}`}>{c.deduction_ref}</Link>
                </td>
                <td>{c.retailer}</td>
                <td>{c.reason_code ?? "-"}</td>
                <td className="num">{money(c.claimed_cents)}</td>
                <td className="num">{c.invalid_cents ? money(c.invalid_cents) : "-"}</td>
                <td className="num">{c.confidence === null ? "-" : c.confidence.toFixed(2)}</td>
                <td className="num">{left === null ? "-" : left}</td>
                <td>
                  <span className={`badge ${STATUS_TONE[c.status] ?? ""}`}>
                    {activity[c.id] ? `running ${activity[c.id]}` : c.status.replace(/_/g, " ").toLowerCase()}
                  </span>
                </td>
              </tr>
            );
          })}
          {visible.length === 0 && (
            <tr>
              <td colSpan={8} className="muted">
                No deductions yet. Generate sample data with the backend synth script, then sync.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </>
  );
}
