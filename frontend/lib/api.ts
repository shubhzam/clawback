export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type CaseSummary = {
  id: number;
  retailer: string;
  deduction_ref: string;
  status: string;
  decision: string | null;
  reason_code: string | null;
  invoice_number: string | null;
  claimed_cents: number;
  invalid_cents: number;
  recovered_cents: number;
  confidence: number | null;
  deduction_date: string | null;
  window_deadline: string | null;
  updated_at: string | null;
};

export type Finding = {
  check: string;
  status: "invalid_claim" | "valid_claim" | "inconclusive";
  amount_invalid_cents: number;
  confidence: number;
  evidence: string[];
  detail: string;
};

export type AuditEvent = {
  ts: string;
  agent: string;
  action: string;
  reasoning: string;
};

export type DocumentRow = {
  filename: string;
  doc_type: string;
  classify_stage: string;
  classify_confidence: number;
  ocr_method: string;
};

export type Dispute = {
  status: string;
  amount_cents: number;
  letter: string;
  citations: { display_name: string; section: string; source: string }[];
  attachments: string[];
  deadline: string | null;
  confirmation_id: string | null;
  erp_memo_id: string | null;
  filed_at: string | null;
};

export type CaseDetail = CaseSummary & {
  summary: string | null;
  findings: Finding[];
  policy: {
    display_name?: string;
    window_days?: number;
    deadline?: string | null;
    days_remaining?: number | null;
    required_docs?: string[];
  };
  decision_detail: { rule?: string; reasons?: string[]; valid_cents?: number };
  documents: DocumentRow[];
  audit: AuditEvent[];
  dispute: Dispute | null;
};

export type Stats = {
  total_cases: number;
  by_status: Record<string, number>;
  claimed_cents: number;
  flagged_invalid_cents: number;
  recovered_cents: number;
  awaiting_review: number;
  disputes_resolved: number;
  dispute_win_rate: number | null;
  dollar_recovery_rate: number | null;
};

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    cache: "no-store",
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `request failed with ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export function money(cents: number | null | undefined): string {
  if (cents === null || cents === undefined) return "n/a";
  return (cents / 100).toLocaleString("en-US", { style: "currency", currency: "USD" });
}

export function pct(value: number | null | undefined): string {
  return value === null || value === undefined ? "n/a" : `${(value * 100).toFixed(0)}%`;
}

export function daysLeft(deadline: string | null): number | null {
  if (!deadline) return null;
  const ms = new Date(deadline + "T00:00:00").getTime() - new Date().setHours(0, 0, 0, 0);
  return Math.round(ms / 86_400_000);
}
