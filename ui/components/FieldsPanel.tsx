"use client";

import { useMemo, useState } from "react";
import { FieldValueOut } from "@/lib/api";
import { cn, formatValue } from "@/lib/format";
import ConfidenceMeter from "./ConfidenceMeter";

export default function FieldsPanel({
  fields,
  editable,
  activeField,
  onActiveFieldChange,
  onSubmit,
  submitting,
}: {
  fields: FieldValueOut[];
  editable: boolean;
  activeField: string | null;
  onActiveFieldChange: (field: string | null) => void;
  onSubmit?: (reviewer: string, corrections: Record<string, unknown>) => void;
  submitting?: boolean;
}) {
  const shownFields = useMemo(() => fields.filter((f) => f.field !== "line_items"), [fields]);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [reviewer, setReviewer] = useState("");

  function currentValue(f: FieldValueOut): string {
    return edits[f.field] ?? formatValue(f.value).replace(/^—$/, "");
  }

  function handleSubmit() {
    if (!onSubmit) return;
    // Only send fields the user actually touched - not the whole form. Every
    // <input> value is a string, so an untouched numeric field (e.g. 128.0)
    // resent as "128" would look like a real edit to a naive equality check
    // on the backend and silently corrupt its type from number to string.
    const corrections: Record<string, unknown> = {};
    for (const field of Object.keys(edits)) {
      const original = shownFields.find((f) => f.field === field);
      const raw = edits[field].trim();
      if (raw === "") {
        corrections[field] = null;
      } else if (original && typeof original.value === "number") {
        const num = Number(raw);
        corrections[field] = Number.isNaN(num) ? raw : num;
      } else {
        corrections[field] = raw;
      }
    }
    onSubmit(reviewer || "anonymous", corrections);
  }

  return (
    <div className="rounded-2xl border border-sand bg-surface-raised shadow-sm shadow-black/5">
      <div className="border-b border-sand px-4 py-3">
        <h3 className="font-display text-lg text-ink">Extracted fields</h3>
      </div>
      <div className="divide-y divide-sand/70">
        {shownFields.map((f) => (
          <div
            key={f.field}
            onMouseEnter={() => onActiveFieldChange(f.field)}
            onMouseLeave={() => onActiveFieldChange(null)}
            className={cn(
              "grid grid-cols-[minmax(0,1fr)_auto] items-center gap-3 px-4 py-3 transition-colors",
              activeField === f.field && "bg-terracotta-soft/40"
            )}
          >
            <div className="min-w-0">
              <div className="text-xs font-medium uppercase tracking-wide text-ink-faint">
                {f.field.replace(/_/g, " ")}
              </div>
              {editable ? (
                <input
                  value={currentValue(f)}
                  onChange={(e) => setEdits((prev) => ({ ...prev, [f.field]: e.target.value }))}
                  className="mt-1 w-full rounded-md border border-sand bg-surface px-2 py-1 text-sm text-ink outline-none focus:border-terracotta focus:ring-1 focus:ring-terracotta"
                />
              ) : (
                <div className="mt-0.5 truncate text-sm text-ink" title={formatValue(f.value)}>
                  {formatValue(f.value)}
                </div>
              )}
              {!f.bbox && f.value !== null && (
                <div className="mt-0.5 text-xs text-brick">not grounded on page</div>
              )}
            </div>
            <ConfidenceMeter score={f.confidence} size="sm" />
          </div>
        ))}
      </div>

      {editable && (
        <div className="border-t border-sand p-4">
          <label className="text-xs font-medium uppercase tracking-wide text-ink-faint">
            Reviewer name
          </label>
          <input
            value={reviewer}
            onChange={(e) => setReviewer(e.target.value)}
            placeholder="you@company.com"
            className="mt-1 w-full rounded-md border border-sand bg-surface px-2 py-1.5 text-sm text-ink outline-none focus:border-terracotta focus:ring-1 focus:ring-terracotta"
          />
          <button
            onClick={handleSubmit}
            disabled={submitting}
            className="mt-3 w-full rounded-lg bg-terracotta px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-terracotta-strong disabled:opacity-50"
          >
            {submitting ? "Saving…" : "Save corrections & approve"}
          </button>
        </div>
      )}
    </div>
  );
}
