"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { CaseDetail, Finding, api, money } from "@/lib/api";

const FINDING_TONE: Record<Finding["status"], string> = {
  invalid_claim: "good",
  valid_claim: "",
  inconclusive: "warn",
};

const FINDING_LABEL: Record<Finding["status"], string> = {
  invalid_claim: "deduction invalid",
  valid_claim: "deduction valid",
  inconclusive: "needs a human",
};

export default function CasePage({ params }: { params: { id: string } }) {
  const [data, setData] = useState<CaseDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [reviewer, setReviewer] = useState("");
  const [note, setNote] = useState("");
  const [amount, setAmount] = useState("");
  const [outcome, setOutcome] = useState("won");
  const [recovered, setRecovered] = useState("");

  const load = useCallback(async () => {
    try {
      setData(await api<CaseDetail>(`/cases/${params.id}`));
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed to load");
    }
  }, [params.id]);

  useEffect(() => {
    load();
  }, [load]);

  async function act(path: string, body?: object) {
    setBusy(true);
    setError(null);
    try {
      setData(await api<CaseDetail>(`/cases/${params.id}${path}`, { method: "POST", body: body ? JSON.stringify(body) : undefined }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "action failed");
    } finally {
      setBusy(false);
    }
  }

  const toCents = (text: string) => Math.round(parseFloat(text) * 100);

  if (!data) return <p className="muted">{error ?? "Loading"}</p>;
  const d = data.dispute;

  return (
    <>
      <p>
        <Link href="/" className="muted">
          Back to deductions
        </Link>
      </p>
      <div className="row spread">
        <div>
          <h1>
            {data.deduction_ref} <span className="badge">{data.retailer}</span>
          </h1>
          <span className="muted">
            {data.reason_code} on invoice {data.invoice_number ?? "n/a"}, deducted {data.deduction_date ?? "n/a"}
          </span>
        </div>
        <div className="row">
          <span className="badge">{data.status.replace(/_/g, " ").toLowerCase()}</span>
          <button onClick={() => act("/reprocess")} disabled={busy}>
            Reprocess
          </button>
        </div>
      </div>

      <div className="kpis" style={{ gridTemplateColumns: "repeat(4, 1fr)" }}>
        <div className="kpi">
          <div className="label">Claimed</div>
          <div className="value">{money(data.claimed_cents)}</div>
        </div>
        <div className="kpi">
          <div className="label">Found invalid</div>
          <div className="value">{money(data.invalid_cents)}</div>
        </div>
        <div className="kpi">
          <div className="label">Recovered</div>
          <div className="value">{money(data.recovered_cents)}</div>
        </div>
        <div className="kpi">
          <div className="label">Dispute window</div>
          <div className="value">{data.policy.days_remaining ?? "n/a"} days</div>
          <div className="muted">closes {data.policy.deadline ?? "n/a"}</div>
        </div>
      </div>

      {error && <p className="error">{error}</p>}

      <div className="panel">
        <h2>
          Decision: {data.decision ?? "pending"} <span className="muted">({data.decision_detail.rule ?? "no rule"})</span>
        </h2>
        <ul className="reasons">
          {(data.decision_detail.reasons ?? []).map((r) => (
            <li key={r}>{r}</li>
          ))}
        </ul>
      </div>

      {data.status === "ESCALATED" && (
        <div className="panel">
          <h2>Human review</h2>
          <div className="row" style={{ flexWrap: "wrap" }}>
            <input placeholder="your name" value={reviewer} onChange={(e) => setReviewer(e.target.value)} />
            <input placeholder="note" value={note} onChange={(e) => setNote(e.target.value)} style={{ minWidth: 220 }} />
            <input
              placeholder={`dispute amount (default ${(data.invalid_cents / 100).toFixed(2)})`}
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              style={{ minWidth: 220 }}
            />
            <button
              className="primary"
              disabled={busy || !reviewer || (!data.invalid_cents && !amount)}
              onClick={() => act("/review", { action: "approve_dispute", reviewer, note, amount_cents: amount ? toCents(amount) : undefined })}
            >
              Approve dispute
            </button>
            <button disabled={busy || !reviewer} onClick={() => act("/review", { action: "accept_deduction", reviewer, note })}>
              Accept deduction
            </button>
          </div>
        </div>
      )}

      <div className="grid2">
        <div className="panel">
          <h2>Findings</h2>
          {data.findings.map((f) => (
            <div key={f.check} className="finding">
              <div className="row spread">
                <strong>{f.check.replace(/_/g, " ")}</strong>
                <span className={`badge ${FINDING_TONE[f.status]}`}>{FINDING_LABEL[f.status]}</span>
              </div>
              <div className="muted">{f.detail}</div>
              <ul>
                {f.evidence.map((e) => (
                  <li key={e}>{e}</li>
                ))}
              </ul>
              <div className="muted" style={{ marginTop: 6 }}>
                invalid {money(f.amount_invalid_cents)}, confidence {f.confidence.toFixed(2)}
              </div>
            </div>
          ))}
        </div>

        <div className="panel">
          <h2>Documents</h2>
          <table>
            <thead>
              <tr>
                <th>File</th>
                <th>Type</th>
                <th>Matched by</th>
              </tr>
            </thead>
            <tbody>
              {data.documents.map((doc) => (
                <tr key={doc.filename}>
                  <td>{doc.filename}</td>
                  <td>{doc.doc_type.replace(/_/g, " ")}</td>
                  <td>
                    {doc.classify_stage} ({doc.classify_confidence.toFixed(2)})
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="muted">Required for this reason code: {(data.policy.required_docs ?? []).join(", ") || "none"}</p>
        </div>
      </div>

      {d && (
        <div className="panel">
          <div className="row spread">
            <h2>
              Dispute for {money(d.amount_cents)} <span className="badge">{d.status.toLowerCase()}</span>
            </h2>
            {d.confirmation_id && <span className="muted">confirmation {d.confirmation_id}</span>}
          </div>
          <pre className="letter">{d.letter}</pre>
          <p className="muted">
            Policy cited: {d.citations.map((c) => `${c.display_name} ${c.section}`).join("; ") || "none"}. Attachments: {d.attachments.join(", ")}
          </p>
          {d.status === "DRAFTED" && (
            <button className="primary" disabled={busy} onClick={() => act("/dispute/file")}>
              File dispute with retailer
            </button>
          )}
          {d.status === "FILED" && (
            <div className="row" style={{ flexWrap: "wrap" }}>
              <select value={outcome} onChange={(e) => setOutcome(e.target.value)}>
                <option value="won">retailer credited in full</option>
                <option value="partial">partial credit</option>
                <option value="lost">retailer rejected</option>
              </select>
              {outcome === "partial" && (
                <input placeholder="credited amount" value={recovered} onChange={(e) => setRecovered(e.target.value)} />
              )}
              <button
                className="primary"
                disabled={busy || (outcome === "partial" && !recovered)}
                onClick={() =>
                  act("/dispute/outcome", { outcome, recovered_cents: outcome === "partial" ? toCents(recovered) : 0 })
                }
              >
                Record outcome and post to ERP
              </button>
            </div>
          )}
          {d.erp_memo_id && <p className="muted">ERP credit memo {d.erp_memo_id}</p>}
        </div>
      )}

      <div className="panel">
        <h2>Audit trail</h2>
        <ul className="timeline">
          {data.audit.map((e, i) => (
            <li key={`${e.ts}-${i}`}>
              <strong>{e.agent}</strong> <span className="muted">{e.action}</span> - {e.reasoning}
              <div className="muted">{new Date(e.ts).toLocaleString()}</div>
            </li>
          ))}
        </ul>
      </div>
    </>
  );
}
