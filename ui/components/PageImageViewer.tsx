"use client";

import { useState } from "react";
import { FieldValueOut, PageOut, pageImageUrl } from "@/lib/api";
import { cn } from "@/lib/format";

function boxTone(confidence: number | null) {
  if (confidence === null) return "border-taupe";
  if (confidence >= 0.75) return "border-sage";
  if (confidence >= 0.5) return "border-amber";
  return "border-brick";
}

export default function PageImageViewer({
  documentId,
  pages,
  fields,
  activeField,
  onActiveFieldChange,
}: {
  documentId: string;
  pages: PageOut[];
  fields: FieldValueOut[];
  activeField: string | null;
  onActiveFieldChange: (field: string | null) => void;
}) {
  const [naturalSizes, setNaturalSizes] = useState<Record<number, { w: number; h: number }>>({});

  if (pages.length === 0) {
    return (
      <div className="flex items-center justify-center rounded-2xl border border-dashed border-sand bg-surface/60 py-16 text-sm text-ink-faint">
        Pages haven&apos;t been parsed yet.
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {pages.map((page) => {
        const size = naturalSizes[page.page_no];
        const boxes = fields.filter((f) => f.page_no === page.page_no && f.bbox);

        return (
          <div
            key={page.page_no}
            className="relative overflow-hidden rounded-2xl border border-sand bg-surface-raised shadow-sm shadow-black/5"
          >
            <div className="flex items-center justify-between border-b border-sand bg-sand-soft/50 px-3 py-1.5 text-xs text-ink-faint">
              <span>Page {page.page_no}</span>
              <span className="uppercase tracking-wide">{page.text_source.replace("_", " ")}</span>
            </div>
            <div className="relative">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={pageImageUrl(documentId, page.page_no)}
                alt={`Page ${page.page_no}`}
                className="block w-full"
                onLoad={(e) => {
                  const img = e.currentTarget;
                  setNaturalSizes((prev) => ({
                    ...prev,
                    [page.page_no]: { w: img.naturalWidth, h: img.naturalHeight },
                  }));
                }}
              />
              {size &&
                boxes.map((f) => {
                  const [x0, y0, x1, y1] = f.bbox!;
                  const isActive = activeField === f.field;
                  return (
                    <button
                      key={f.field}
                      type="button"
                      onMouseEnter={() => onActiveFieldChange(f.field)}
                      onMouseLeave={() => onActiveFieldChange(null)}
                      onFocus={() => onActiveFieldChange(f.field)}
                      onBlur={() => onActiveFieldChange(null)}
                      className={cn(
                        "absolute rounded-[3px] border-2 transition-all",
                        boxTone(f.confidence),
                        isActive
                          ? "z-10 bg-terracotta/10 shadow-[0_0_0_3px_rgba(193,89,46,0.25)]"
                          : "bg-transparent"
                      )}
                      style={{
                        left: `${(x0 / size.w) * 100}%`,
                        top: `${(y0 / size.h) * 100}%`,
                        width: `${((x1 - x0) / size.w) * 100}%`,
                        height: `${((y1 - y0) / size.h) * 100}%`,
                      }}
                      title={`${f.field}${f.confidence !== null ? ` (${Math.round(f.confidence * 100)}%)` : ""}`}
                    />
                  );
                })}
            </div>
          </div>
        );
      })}
    </div>
  );
}
