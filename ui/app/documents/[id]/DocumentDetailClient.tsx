"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import Header from "@/components/Header";
import StatusBadge from "@/components/StatusBadge";
import ConfidenceMeter from "@/components/ConfidenceMeter";
import PageImageViewer from "@/components/PageImageViewer";
import FieldsPanel from "@/components/FieldsPanel";
import IssuesList from "@/components/IssuesList";
import {
  ApiError,
  DocumentDetail,
  TERMINAL_STATUSES,
  getDocument,
  submitCorrection,
} from "@/lib/api";
import { formatCost, formatDateTime } from "@/lib/format";

export default function DocumentDetailClient({ id }: { id: string }) {
  const [doc, setDoc] = useState<DocumentDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [activeField, setActiveField] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [correctionMessage, setCorrectionMessage] = useState<string | null>(null);
  const statusRef = useRef<DocumentDetail["status"] | null>(null);

  const refresh = useCallback(async () => {
    try {
      const result = await getDocument(id);
      setDoc(result);
      statusRef.current = result.status;
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? String(err.message) : "Could not reach the API");
    }
  }, [id]);

  useEffect(() => {
    async function poll() {
      await refresh();
    }
    poll();
    const interval = setInterval(() => {
      if (statusRef.current && TERMINAL_STATUSES.includes(statusRef.current)) {
        clearInterval(interval);
        return;
      }
      poll();
    }, 2000);
    return () => clearInterval(interval);
  }, [refresh]);

  async function handleCorrection(reviewer: string, corrections: Record<string, unknown>) {
    setSubmitting(true);
    setCorrectionMessage(null);
    try {
      const result = await submitCorrection(id, reviewer, corrections);
      setCorrectionMessage(`Saved ${result.fields_corrected} correction(s). Document approved.`);
      await refresh();
    } catch (err) {
      setCorrectionMessage(err instanceof ApiError ? String(err.message) : "Failed to save corrections");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex min-h-full flex-1 flex-col bg-paper">
      <Header />
      <main className="mx-auto w-full max-w-5xl flex-1 px-6 py-10">
        <Link href="/" className="text-sm text-ink-soft hover:text-terracotta">
          ← All documents
        </Link>

        {error && !doc && (
          <p className="mt-6 rounded-lg bg-brick-soft px-4 py-3 text-sm text-brick">{error}</p>
        )}

        {doc && (
          <>
            <div className="mt-4 flex flex-wrap items-start justify-between gap-4">
              <div>
                <h1 className="font-display text-2xl text-ink">{doc.filename}</h1>
                <p className="mt-1 text-sm text-ink-faint">
                  {doc.tenant_id} · uploaded {formatDateTime(doc.created_at)}
                </p>
              </div>
              <div className="flex items-center gap-4">
                <ConfidenceMeter score={doc.confidence} />
                <StatusBadge status={doc.status} />
              </div>
            </div>

            {doc.split_from_document_id && (
              <p className="mt-3 text-sm text-ink-soft">
                Split from{" "}
                <Link
                  href={`/documents/${doc.split_from_document_id}`}
                  className="text-terracotta hover:underline"
                >
                  another upload
                </Link>
                .
              </p>
            )}
            {doc.child_document_ids.length > 0 && (
              <p className="mt-3 text-sm text-ink-soft">
                This upload was split into {doc.child_document_ids.length} additional document
                {doc.child_document_ids.length === 1 ? "" : "s"}:{" "}
                {doc.child_document_ids.map((childId, i) => (
                  <span key={childId}>
                    {i > 0 && ", "}
                    <Link href={`/documents/${childId}`} className="text-terracotta hover:underline">
                      view
                    </Link>
                  </span>
                ))}
              </p>
            )}

            {correctionMessage && (
              <p className="mt-4 rounded-lg bg-sage-soft px-4 py-2.5 text-sm text-sage">
                {correctionMessage}
              </p>
            )}

            <div className="mt-8 grid grid-cols-1 gap-8 lg:grid-cols-[1fr_360px]">
              <PageImageViewer
                documentId={doc.document_id}
                pages={doc.pages}
                fields={doc.extraction?.fields ?? []}
                activeField={activeField}
                onActiveFieldChange={setActiveField}
              />

              <div className="space-y-6">
                {doc.extraction ? (
                  <>
                    <FieldsPanel
                      fields={doc.extraction.fields}
                      editable={doc.status === "needs_review"}
                      activeField={activeField}
                      onActiveFieldChange={setActiveField}
                      onSubmit={handleCorrection}
                      submitting={submitting}
                    />

                    <div className="rounded-2xl border border-sand bg-surface-raised p-4 shadow-sm shadow-black/5">
                      <h3 className="font-display text-lg text-ink">Validation issues</h3>
                      <div className="mt-3">
                        <IssuesList issues={doc.extraction.issues} />
                      </div>
                    </div>

                    <div className="rounded-2xl border border-sand bg-surface-raised p-4 shadow-sm shadow-black/5">
                      <h3 className="font-display text-lg text-ink">Run details</h3>
                      <dl className="mt-3 space-y-1.5 text-sm">
                        <Row label="Model" value={doc.extraction.model} />
                        <Row label="Attempt" value={String(doc.extraction.attempt_no)} />
                        <Row label="Cost" value={formatCost(doc.extraction.cost_usd)} />
                        <Row label="Latency" value={`${doc.extraction.latency_ms}ms`} />
                        <Row
                          label="Tokens"
                          value={`${doc.extraction.tokens.input ?? 0} in / ${doc.extraction.tokens.output ?? 0} out`}
                        />
                      </dl>
                    </div>
                  </>
                ) : (
                  <div className="rounded-2xl border border-dashed border-sand bg-surface/60 p-6 text-sm text-ink-faint">
                    <p className="text-center">
                      {doc.status === "rejected"
                        ? "Blocked by a safety check before extraction ever ran."
                        : doc.status === "failed"
                          ? "Extraction never completed - this job exhausted its retries."
                          : "No extraction yet - still working through earlier stages."}
                    </p>
                    {doc.last_job?.last_error && (
                      <pre className="mt-3 overflow-x-auto rounded-lg bg-brick-soft p-3 text-left text-xs text-brick whitespace-pre-wrap">
                        {doc.last_job.last_error}
                      </pre>
                    )}
                  </div>
                )}
              </div>
            </div>
          </>
        )}
      </main>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-4">
      <dt className="text-ink-faint">{label}</dt>
      <dd className="font-mono text-ink">{value}</dd>
    </div>
  );
}
