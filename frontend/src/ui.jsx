export const TYPE_LABEL = {
  price_variance: "Price variance",
  quantity_mismatch: "Quantity mismatch",
  tax_error: "Tax error",
  duplicate_invoice: "Duplicate invoice",
  missing_po: "Missing PO",
};

export function fmtSize(e) {
  if (e.type === "price_variance") return `${Number(e.variance_pct) > 0 ? "+" : ""}${e.variance_pct}% vs PO`;
  if (e.type === "quantity_mismatch") return `${Number(e.quantity_delta) > 0 ? "+" : ""}${e.quantity_delta} units vs received`;
  if (e.type === "tax_error") return `${e.details.implied_rate_pct}% charged vs ${e.details.expected_rate_pct}%`;
  if (e.type === "duplicate_invoice") return `copy of ${e.details.duplicate_of_number}`;
  if (e.type === "missing_po") return `${e.invoice.currency} ${money(e.invoice.total)} without PO`;
  return "";
}

export function money(v) {
  if (v === null || v === undefined) return "—";
  return Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

/* ── Status badge: square-ish, low-key ─────────────────────────────────── */
const TONES = {
  emerald: "bg-emerald-50 text-emerald-700 ring-1 ring-emerald-200",
  amber:   "bg-amber-50  text-amber-800  ring-1 ring-amber-200",
  slate:   "bg-slate-100 text-slate-600  ring-1 ring-slate-200",
  indigo:  "bg-indigo-50 text-indigo-700 ring-1 ring-indigo-200",
  rose:    "bg-rose-50   text-rose-700   ring-1 ring-rose-200",
  sky:     "bg-sky-50    text-sky-700    ring-1 ring-sky-200",
};

export function Badge({ tone = "slate", title, children }) {
  return (
    <span
      title={title}
      className={`inline-flex items-center rounded px-2 py-0.5 text-xs font-medium tracking-wide ${TONES[tone]}`}
    >
      {children}
    </span>
  );
}

/* ── Action indicator: dot + label, not a coloured pill ─────────────────── */
const ACTION_META = {
  approve:   { dot: "bg-emerald-500", label: "Approve"  },
  approved:  { dot: "bg-emerald-500", label: "Approved" },
  adjust:    { dot: "bg-sky-500",     label: "Adjust"   },
  adjusted:  { dot: "bg-sky-500",     label: "Adjusted" },
  reject:    { dot: "bg-rose-500",    label: "Reject"   },
  rejected:  { dot: "bg-rose-500",    label: "Rejected" },
  escalate:  { dot: "bg-amber-500",   label: "Escalate" },
  escalated: { dot: "bg-amber-500",   label: "Escalated"},
};

export function ActionPill({ action }) {
  const meta = ACTION_META[action] || { dot: "bg-slate-400", label: action };
  return (
    <span className="inline-flex items-center gap-1.5 text-xs font-medium text-slate-700">
      <span className={`inline-block w-1.5 h-1.5 rounded-full flex-shrink-0 ${meta.dot}`} />
      {meta.label}
    </span>
  );
}

/* ── Button ─────────────────────────────────────────────────────────────── */
export function Button({ variant = "primary", className = "", ...props }) {
  const styles = {
    primary:   "bg-indigo-600 text-white hover:bg-indigo-700 border border-indigo-600",
    secondary: "bg-slate-800 text-white hover:bg-slate-900 border border-slate-800",
    ghost:     "bg-white border border-slate-300 text-slate-700 hover:bg-slate-50",
  };
  return (
    <button
      {...props}
      className={`inline-flex items-center justify-center rounded px-3 py-1.5 text-sm font-medium
        disabled:opacity-40 disabled:cursor-not-allowed transition-colors ${styles[variant]} ${className}`}
    />
  );
}

/* ── Learning / metrics chart ───────────────────────────────────────────── */
export function LearningChart({ batches }) {
  const rows = batches.filter((b) => b.status === "processed");
  if (!rows.length)
    return <p className="text-xs text-slate-400 py-1">Process a batch to see metrics.</p>;
  return (
    <div className="space-y-4">
      {rows.map((b) => (
        <div key={b.batch_id}>
          <p className="text-xs font-medium text-slate-700 mb-1.5">{b.name.split(" - ")[0]}</p>
          <MetricRow label="Escalated" value={b.escalation_rate} color="bg-amber-400" count={`${b.escalated}/${b.recommended}`} />
          <MetricRow
            label="Agreed"
            value={b.agreement_rate}
            color="bg-emerald-500"
            count={b.graded ? `${b.agreed}/${b.graded}` : "—"}
          />
        </div>
      ))}
    </div>
  );
}

function MetricRow({ label, value, color, count }) {
  const pct = value == null ? null : Math.round(value * 100);
  return (
    <div className="flex items-center gap-2 text-xs mb-1">
      <span className="w-16 text-slate-500 shrink-0">{label}</span>
      <div className="flex-1 h-1.5 bg-slate-100 rounded-full overflow-hidden">
        {pct !== null && <div className={`h-full ${color}`} style={{ width: `${pct}%` }} />}
      </div>
      <span className="w-20 text-right tabular-nums text-slate-600 shrink-0">
        {pct === null ? "—" : `${pct}%`}
        <span className="text-slate-400 ml-1">{count}</span>
      </span>
    </div>
  );
}
