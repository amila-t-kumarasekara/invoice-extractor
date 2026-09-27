"use client";

import { useRouter } from "next/navigation";
import { useRef, useState } from "react";
import { ApiError, uploadDocument } from "@/lib/api";
import { cn } from "@/lib/format";

export default function UploadCard({
  tenantId,
  onTenantIdChange,
}: {
  tenantId: string;
  onTenantIdChange: (value: string) => void;
}) {
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleFile(file: File | undefined) {
    if (!file) return;
    setError(null);
    setIsUploading(true);
    try {
      const result = await uploadDocument(tenantId, file);
      router.push(`/documents/${result.document_id}`);
      router.refresh();
    } catch (err) {
      if (err instanceof ApiError) {
        const reasons =
          err.detail && typeof err.detail === "object" && "reasons" in err.detail
            ? (err.detail as { reasons: string[] }).reasons
            : null;
        setError(reasons ? reasons.join("; ") : String(err.message));
      } else {
        setError("Upload failed - is the API running?");
      }
    } finally {
      setIsUploading(false);
    }
  }

  return (
    <div className="rounded-2xl border border-sand bg-surface-raised p-6 shadow-sm shadow-black/5">
      <h2 className="font-display text-xl text-ink">Upload an invoice</h2>
      <p className="mt-1 text-sm text-ink-soft">
        PDF, PNG, or JPEG. It&apos;ll move through parsing, classification, extraction,
        and validation automatically.
      </p>

      <label className="mt-4 block text-xs font-medium uppercase tracking-wide text-ink-faint">
        Tenant
      </label>
      <input
        value={tenantId}
        onChange={(e) => onTenantIdChange(e.target.value)}
        className="mt-1 w-full rounded-lg border border-sand bg-surface px-3 py-2 text-sm text-ink outline-none focus:border-terracotta focus:ring-1 focus:ring-terracotta"
        placeholder="demo"
      />

      <div
        onDragOver={(e) => {
          e.preventDefault();
          setIsDragging(true);
        }}
        onDragLeave={() => setIsDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setIsDragging(false);
          void handleFile(e.dataTransfer.files?.[0]);
        }}
        onClick={() => inputRef.current?.click()}
        className={cn(
          "mt-4 flex cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed px-6 py-10 text-center transition-colors",
          isDragging
            ? "border-terracotta bg-terracotta-soft/60"
            : "border-sand bg-paper hover:border-terracotta-strong/50 hover:bg-sand-soft/40"
        )}
      >
        <svg
          xmlns="http://www.w3.org/2000/svg"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={1.5}
          className="h-8 w-8 text-terracotta"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5M16.5 7.5 12 3m0 0L7.5 7.5M12 3v13.5"
          />
        </svg>
        <p className="mt-3 text-sm font-medium text-ink">
          {isUploading ? "Uploading…" : "Drop a file here, or click to browse"}
        </p>
        <input
          ref={inputRef}
          type="file"
          accept="application/pdf,image/png,image/jpeg"
          className="hidden"
          onChange={(e) => void handleFile(e.target.files?.[0])}
        />
      </div>

      {error && (
        <p className="mt-3 rounded-lg bg-brick-soft px-3 py-2 text-sm text-brick">{error}</p>
      )}
    </div>
  );
}
