export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export type DocumentStatus =
  | "uploaded"
  | "parsed"
  | "classified"
  | "extracted"
  | "validated"
  | "approved"
  | "needs_review"
  | "failed"
  | "rejected";

export const TERMINAL_STATUSES: DocumentStatus[] = [
  "approved",
  "needs_review",
  "failed",
  "rejected",
];

export interface DocumentSummary {
  document_id: string;
  tenant_id: string;
  filename: string;
  doc_type: string | null;
  status: DocumentStatus;
  confidence: number | null;
  created_at: string;
}

export interface FieldValueOut {
  field: string;
  value: unknown;
  page_no: number | null;
  bbox: [number, number, number, number] | null;
  confidence: number | null;
}

export interface IssueOut {
  field: string | null;
  rule: string;
  severity: "error" | "warning";
  message: string;
}

export interface ExtractionOut {
  model: string;
  attempt_no: number;
  data: Record<string, unknown>;
  fields: FieldValueOut[];
  issues: IssueOut[];
  tokens: { input?: number; output?: number };
  cost_usd: number;
  latency_ms: number;
}

export interface PageOut {
  page_no: number;
  text_source: "text_layer" | "ocr";
}

export interface LastJobOut {
  stage: string;
  status: string;
  attempts: number;
  last_error: string | null;
}

export interface DocumentDetail {
  document_id: string;
  tenant_id: string;
  status: DocumentStatus;
  doc_type: string | null;
  confidence: number | null;
  filename: string;
  created_at: string;
  split_from_document_id: string | null;
  child_document_ids: string[];
  pages: PageOut[];
  extraction: ExtractionOut | null;
  last_job: LastJobOut | null;
}

export interface UploadResponse {
  document_id: string;
  status: DocumentStatus;
  duplicate: boolean;
}

async function asJson<T>(res: Response): Promise<T> {
  const text = await res.text();
  let body: unknown = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = text;
  }
  if (!res.ok) {
    const detail =
      body && typeof body === "object" && "detail" in body
        ? (body as { detail: unknown }).detail
        : body;
    throw new ApiError(res.status, detail);
  }
  return body as T;
}

export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, detail: unknown) {
    super(typeof detail === "string" ? detail : JSON.stringify(detail));
    this.status = status;
    this.detail = detail;
  }
}

export function pageImageUrl(documentId: string, pageNo: number): string {
  return `${API_BASE_URL}/documents/${documentId}/pages/${pageNo}/image`;
}

export async function listDocuments(params: {
  tenantId?: string;
  status?: string;
}): Promise<{ count: number; documents: DocumentSummary[] }> {
  const search = new URLSearchParams();
  if (params.tenantId) search.set("tenant_id", params.tenantId);
  if (params.status) search.set("status", params.status);
  const res = await fetch(`${API_BASE_URL}/documents?${search.toString()}`, {
    cache: "no-store",
  });
  return asJson(res);
}

export async function getDocument(id: string): Promise<DocumentDetail> {
  const res = await fetch(`${API_BASE_URL}/documents/${id}`, { cache: "no-store" });
  return asJson(res);
}

export async function uploadDocument(
  tenantId: string,
  file: File
): Promise<UploadResponse> {
  const body = new FormData();
  body.set("tenant_id", tenantId);
  body.set("file", file);
  const res = await fetch(`${API_BASE_URL}/documents`, { method: "POST", body });
  return asJson(res);
}

export async function submitCorrection(
  documentId: string,
  reviewer: string,
  corrections: Record<string, unknown>
): Promise<{ document_id: string; status: DocumentStatus; fields_corrected: number }> {
  const res = await fetch(`${API_BASE_URL}/review/${documentId}/correct`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reviewer, corrections }),
  });
  return asJson(res);
}
