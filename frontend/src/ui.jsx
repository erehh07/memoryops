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

const TONES = {
  emerald: "bg-emerald-50 text-emerald-700 border-emerald-200",
  amber: "bg-amber-50 text-amber-800 border-amber-300",
  slate: "bg-slate-100 text-slate-700 border-slate-200",
  indigo: "bg-indigo-50 text-indigo-700 border-indigo-200",
  rose: "bg-rose-50 text-rose-700 border-rose-200",
  sky: "bg-sky-50 text-sky-700 border-sky-200",
};

export function Badge({ tone = "slate", title, children }) {
  return (
    <span title={title} className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-medium ${TONES[tone]}`}>
      {children}
    </span>
  );
}

const ACTION_TONE = {
  approve: "emerald", approved: "emerald",
  adjust: "sky", adjusted: "sky",
  reject: "rose", rejected: "rose",
  escalate: "amber", escalated: "amber",
};

export function ActionPill({ action }) {
  return <Badge tone={ACTION_TONE[action] || "slate"}>{action}</Badge>;
}

export function Button({ variant = "primary", className = "", ...props }) {
  const styles = {
    primary: "bg-indigo-600 text-white hover:bg-indigo-700",
    secondary: "bg-slate-800 text-white hover:bg-slate-900",
    ghost: "bg-white border border-slate-300 text-slate-700 hover:bg-slate-50",
  };
  return (
    <button
      {...props}
      className={`rounded-md px-3 py-1.5 text-sm font-medium disabled:opacity-50 disabled:cursor-not-allowed ${styles[variant]} ${className}`}
    />
  );
}

export function LearningChart({ batches }) {
  const rows = batches.filter((b) => b.status === "processed");
  if (!rows.length) return <p className="text-xs text-slate-400">Process a batch to start measuring.</p>;
  return (
    <div className="space-y-3">
      {rows.map((b) => (
        <div key={b.batch_id}>
          <div className="text-xs font-medium text-slate-600 mb-1">{b.name.split(" - ")[0]}</div>
          <Bar label="Escalated" value={b.escalation_rate} tone="bg-amber-400" extra={`${b.escalated}/${b.recommended}`} />
          <Bar
            label="Agreed"
            value={b.agreement_rate}
            tone="bg-emerald-500"
            extra={b.graded ? `${b.agreed}/${b.graded}` : "no decisions yet"}
          />
        </div>
      ))}
    </div>
  );
}

function Bar({ label, value, tone, extra }) {
  const pct = value === null || value === undefined ? null : Math.round(value * 100);
  return (
    <div className="flex items-center gap-2 text-xs">
      <span className="w-16 text-slate-500">{label}</span>
      <div className="flex-1 h-2.5 bg-slate-100 rounded">
        {pct !== null && <div className={`h-2.5 rounded ${tone}`} style={{ width: `${pct}%` }} />}
      </div>
      <span className="w-24 text-right tabular-nums text-slate-600">
        {pct === null ? "—" : `${pct}%`} <span className="text-slate-400">{extra}</span>
      </span>
    </div>
  );
}
