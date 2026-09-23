const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    cache: "no-store",
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `Request failed: ${res.status}`);
  }
  if (res.status === 204) {
    return undefined as T;
  }
  const text = await res.text();
  if (!text) {
    return undefined as T;
  }
  return JSON.parse(text) as T;
}

function requestJson<T>(
  path: string,
  method: "POST" | "PATCH",
  body: unknown
): Promise<T> {
  return request<T>(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export type AssessmentListItem = {
  id: string;
  name: string;
  status: string;
  workflow_stage: string;
  created_at: string;
  updated_at: string;
  document_count: number;
};

export type DocumentOut = {
  id: string;
  filename: string;
  content_type: string;
  doc_type: string;
  precedence: number;
  page_count: number | null;
  created_at: string;
};

export type Assessment = {
  id: string;
  name: string;
  status: string;
  workflow_stage: string;
  error_message: string | null;
  metrics: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
  documents: DocumentOut[];
};

export type Claim = {
  id: string;
  entity_type: string;
  entity_key: string;
  attribute: string;
  value: string;
  confidence: number;
  evidence_refs: string[];
  evidence_quote: string | null;
  source_document_id: string | null;
  evidence: Evidence[];
  needs_human_review: boolean;
  unsupported: boolean;
  review_status: string;
  override_value: string | null;
  review_notes: string | null;
  is_selected: boolean;
};

export type Evidence = {
  chunk_id: string;
  document_id: string;
  filename: string;
  doc_type: string;
  page: number | null;
  quote: string | null;
};

export type Entity = {
  id: string;
  name: string;
  normalized_key: string;
  attributes: Record<string, string> | null;
  confidence: number;
  entity_type: string;
};

export type GraphOut = {
  nodes: {
    id: string;
    type: string;
    label: string;
    confidence: number;
    attributes: Record<string, unknown>;
    centrality: number;
  }[];
  edges: {
    id: string;
    source: string;
    target: string;
    relationship: string;
    confidence: number;
    needs_human_review: boolean;
    rationale: string | null;
  }[];
};

export type BlastRadius = GraphOut & {
  center: string;
  depth: number;
  source: string;
};

export type Report = {
  assessment_id: string;
  readiness_summary: string;
  gaps: string[];
  assumptions: string[];
  inventory: Record<string, unknown>;
  dependencies: unknown[];
  evidence_appendix: {
    claim_id: string;
    entity: string;
    attribute: string;
    value: string;
    confidence: number;
    quote: string | null;
    chunk_ids: string[];
    evidence: Evidence[];
    unsupported: boolean;
  }[];
  report_json: Record<string, unknown>;
  metrics: Record<string, number>;
};

export type Conflict = {
  id: string;
  entity_type: string;
  entity_key: string;
  attribute: string;
  claim_ids: string[];
  selected_claim_id: string | null;
  status: string;
  resolution_notes: string | null;
};

export type AssessmentAnswers = {
  question_set: string;
  answers: {
    id: string;
    origin?: "standard" | "uploaded" | "ad_hoc";
    question: string;
    answer: string;
    answer_source?: string;
    confidence: number;
    supported: boolean;
    needs_human_review: boolean;
    evidence_refs: string[];
    evidence: Evidence[];
    assumptions: string[];
  }[];
  complete: boolean;
  review_required: boolean;
};

export type InfrastructureRecommendation = {
  id: string;
  server_key: string;
  provider: string;
  region: string;
  recommended_sku: string;
  result: {
    server: string;
    sku_decision?: string;
    catalog_version?: string;
    measured_fields?: string[];
    assumed_fields?: string[];
    recommended_sku: string;
    explanation: string;
    explanation_source: string;
    confidence: number;
    needs_human_review: boolean;
    assumptions: string[];
    required: Record<string, number>;
    vm: Record<string, unknown>;
    disk: Record<string, unknown>;
    pricing: {
      monthly_compute: number;
      monthly_disk: number;
      monthly_total: number;
      currency: string;
      source: string;
      estimate: boolean;
    };
    alternatives: Record<string, unknown>[];
    compatibility_checks: {
      check: string;
      status: string;
      detail: string;
    }[];
    cost_optimization: {
      type: string;
      applicable: boolean;
      detail: string;
    }[];
  };
  confidence: number;
  needs_human_review: boolean;
};

export type FollowUpEntry = {
  at?: string;
  event?: string;
  detail?: { note?: string };
};

export function followUpLog(
  metrics: Record<string, unknown> | null | undefined
): FollowUpEntry[] {
  const log = metrics?.follow_up_log;
  return Array.isArray(log) ? (log as FollowUpEntry[]) : [];
}

export function llmLabel(health: Health): string {
  if (health.azure_openai) return "Azure OpenAI";
  if (health.mock_llm) return "mock";
  return "none";
}

export type Health = {
  status: string;
  mock_llm: boolean;
  azure_openai: boolean;
  azure_search: boolean;
  database: string;
  rag: boolean;
  embeddings: string;
  vector_index?: string;
  guardrails?: boolean;
  content_safety?: boolean;
  app_profile?: string;
  identity_mode?: string;
};

export const api = {
  health: () => request<Health>("/health"),
  listAssessments: () => request<AssessmentListItem[]>("/assessments"),
  getAssessment: (id: string) => request<Assessment>(`/assessments/${id}`),
  updateAssessment: (id: string, name: string) =>
    requestJson<Assessment>(`/assessments/${id}`, "PATCH", { name }),
  deleteAssessment: (id: string) =>
    request<void>(`/assessments/${id}`, { method: "DELETE" }),
  createAssessment: async (name: string, files: File[]) => {
    const form = new FormData();
    form.append("name", name);
    files.forEach((f) => form.append("files", f));
    return request<Assessment>("/assessments", { method: "POST", body: form });
  },
  uploadDocuments: async (id: string, files: File[]) => {
    const form = new FormData();
    files.forEach((f) => form.append("files", f));
    return request<Assessment>(`/assessments/${id}/documents`, {
      method: "POST",
      body: form,
    });
  },
  deleteDocument: (assessmentId: string, documentId: string) =>
    request<Assessment>(
      `/assessments/${assessmentId}/documents/${documentId}`,
      { method: "DELETE" }
    ),
  run: (id: string) =>
    request<Assessment>(`/assessments/${id}/run`, { method: "POST" }),
  claims: (id: string, reviewOnly = false) =>
    request<Claim[]>(
      `/assessments/${id}/claims${reviewOnly ? "?review_only=true" : ""}`
    ),
  reviewClaim: (
    assessmentId: string,
    claimId: string,
    action: string,
    override_value?: string,
    notes?: string
  ) =>
    requestJson<Claim>(`/assessments/${assessmentId}/claims/${claimId}/review`, "POST", {
      action,
      override_value,
      notes,
    }),
  entities: (id: string) => request<Entity[]>(`/assessments/${id}/entities`),
  graph: (id: string) => request<GraphOut>(`/assessments/${id}/graph`),
  blastRadius: (id: string, node: string, depth = 2) =>
    request<BlastRadius>(
      `/assessments/${id}/graph/blast-radius?node=${encodeURIComponent(node)}&depth=${depth}`
    ),
  conflicts: (id: string) => request<Conflict[]>(`/assessments/${id}/conflicts`),
  assessmentQuestions: (id: string) =>
    request<AssessmentAnswers>(`/assessments/${id}/assessment-questions`),
  askQuestion: (id: string, question: string) =>
    requestJson<AssessmentAnswers>(`/assessments/${id}/assessment-questions/ask`, "POST", {
      question,
    }),
  recommendations: (id: string) =>
    request<InfrastructureRecommendation[]>(`/assessments/${id}/recommendations`),
  report: (id: string) => request<Report>(`/assessments/${id}/report`),
  completeReview: (id: string) =>
    request<Assessment>(`/assessments/${id}/complete-review`, {
      method: "POST",
    }),
  addFollowUp: (id: string, note: string, tags?: string[]) =>
    requestJson<Assessment>(`/assessments/${id}/follow-up`, "POST", { note, tags }),
};
