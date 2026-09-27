"use client";

import { useEffect, useState } from "react";
import Header from "@/components/Header";
import UploadCard from "@/components/UploadCard";
import DocumentTable from "@/components/DocumentTable";
import { DocumentSummary, listDocuments } from "@/lib/api";

const STATUS_FILTERS = [
  { value: "", label: "All" },
  { value: "needs_review", label: "Needs review" },
  { value: "approved", label: "Approved" },
  { value: "failed", label: "Failed" },
  { value: "rejected", label: "Rejected" },
];

export default function Home() {
  const [tenantId, setTenantId] = useState("demo");
  const [statusFilter, setStatusFilter] = useState("");
  const [documents, setDocuments] = useState<DocumentSummary[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;

    async function refresh() {
      try {
        const result = await listDocuments({
          tenantId: tenantId || undefined,
          status: statusFilter || undefined,
        });
        if (!cancelled) setDocuments(result.documents);
      } catch {
        // API not reachable - leave the previous list in place.
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    void refresh();
    const interval = setInterval(refresh, 4000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [tenantId, statusFilter]);

  return (
    <div className="flex min-h-full flex-1 flex-col bg-paper">
      <Header />
      <main className="mx-auto w-full max-w-5xl flex-1 px-6 py-10">
        <div className="mb-10">
          <h1 className="font-display text-3xl text-ink">Documents</h1>
          <p className="mt-1 text-ink-soft">
            Upload invoices and watch them move through parsing, classification,
            extraction, and validation.
          </p>
        </div>

        <div className="grid grid-cols-1 gap-8 lg:grid-cols-[360px_1fr]">
          <UploadCard tenantId={tenantId} onTenantIdChange={setTenantId} />

          <div>
            <div className="mb-4 flex flex-wrap items-center gap-2">
              {STATUS_FILTERS.map((filter) => (
                <button
                  key={filter.value}
                  onClick={() => setStatusFilter(filter.value)}
                  className={`rounded-full px-3 py-1.5 text-sm font-medium transition-colors ${
                    statusFilter === filter.value
                      ? "bg-terracotta text-white"
                      : "bg-surface-raised text-ink-soft hover:bg-sand-soft"
                  }`}
                >
                  {filter.label}
                </button>
              ))}
              <span className="ml-auto text-xs text-ink-faint">
                {loading ? "Loading…" : `${documents.length} document${documents.length === 1 ? "" : "s"}`}
              </span>
            </div>
            <DocumentTable documents={documents} />
          </div>
        </div>
      </main>
    </div>
  );
}
