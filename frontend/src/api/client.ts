const API_URL = (import.meta.env.VITE_API_URL ?? "http://127.0.0.1:8000").trim();
const API_KEY = (import.meta.env.VITE_API_KEY ?? "").trim();

type RequestOptions = RequestInit & { errorContext?: string; timeoutMs?: number };

function formatApiError(status: number, body: Record<string, unknown>): string {
  const detail = body.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && detail.length > 0) {
    const first = detail[0] as { msg?: string };
    if (first?.msg) return first.msg.replace(/^Value error,?\s*/i, "");
  }
  if (typeof body.message === "string") return body.message;
  if (status === 401) return "مفتاح API غير صحيح";
  if (status === 503) return "الخادم لا يستطيع الاتصال بقاعدة البيانات";
  return `خطأ من الخادم (${status})`;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { errorContext, timeoutMs, ...fetchOptions } = options;
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(fetchOptions.headers as Record<string, string> | undefined),
  };
  if (API_KEY) {
    headers["X-API-Key"] = API_KEY;
  }

  const controller = timeoutMs ? new AbortController() : undefined;
  const timer =
    controller && timeoutMs
      ? window.setTimeout(() => controller.abort(), timeoutMs)
      : undefined;

  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, {
      ...fetchOptions,
      headers,
      signal: controller?.signal,
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new Error("انتهت مهلة توليد الصورة — حاول مجدداً");
    }
    throw new Error(
      errorContext ?? `تعذر الاتصال بالخادم على ${API_URL} — تحقق أن backend يعمل`,
    );
  } finally {
    if (timer) window.clearTimeout(timer);
  }

  if (!res.ok) {
    const body = (await res.json().catch(() => ({}))) as Record<string, unknown>;
    throw new Error(formatApiError(res.status, body));
  }
  return res.json() as Promise<T>;
}

export interface EntrySummary {
  id: string;
  word: string;
  definition?: string;
  category: string;
  status: string;
  prompt_family?: string;
  has_image: boolean;
  image_count?: number;
  updated_at: string;
}

export interface ImageSummary {
  id: string;
  public_url: string;
  drive_file_id?: string;
  prompt?: string;
  generated_by?: string;
  is_current: boolean;
  is_selected: boolean;
  created_at: string;
  generation_attempt?: number;
  generation_label?: string;
  image_role?: string;
  source?: string;
}

export interface EntryDetail {
  id: string;
  word: string;
  definition?: string;
  category: string;
  status: string;
  prompt_family?: string;
  rejection_reason?: string;
  reviewer_vision?: string;
  object_description?: string;
  base_prompt?: string;
  current_image_id?: string;
  selected_image_id?: string;
  current_image?: ImageSummary;
  notes?: string;
  image_count: number;
  created_at: string;
  updated_at: string;
}

export interface PaginatedEntries {
  items: EntrySummary[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

export interface Stats {
  total_entries: number;
  total_images: number;
  by_status: Record<string, number>;
}

export interface RejectBody {
  rejection_reason: string;
  reviewer_vision?: string;
  notes?: string;
  regenerate?: boolean;
}

export const api = {
  stats: () => request<Stats>("/stats", { errorContext: "تعذر تحميل الإحصائيات" }),
  entries: (params: URLSearchParams) =>
    request<PaginatedEntries>(`/entries?${params.toString()}`, {
      errorContext: "تعذر تحميل قائمة الكلمات",
    }),
  entry: (id: string) =>
    request<EntryDetail>(`/entries/${id}`, { errorContext: "تعذر تحميل تفاصيل الكلمة" }),
  queueNext: (status: string, currentId?: string, direction: "next" | "prev" = "next") => {
    const params = new URLSearchParams({ status, direction });
    if (currentId) params.set("current_id", currentId);
    return request<EntryDetail>(`/entries/queue/next?${params.toString()}`, {
      errorContext: "تعذر جلب الكلمة التالية",
    });
  },
  entryImages: (id: string) =>
    request<ImageSummary[]>(`/entries/${id}/images`, {
      errorContext: "تعذر تحميل صور الكلمة",
    }),
  reject: (id: string, body: RejectBody) =>
    request(`/entries/${id}/reject`, {
      method: "POST",
      body: JSON.stringify(body),
      errorContext: "تعذر حفظ الرفض",
    }),
  regenerate: (id: string, body: RejectBody) =>
    request<{ data?: { job_id?: string; image_id?: string } }>(`/entries/${id}/regenerate`, {
      method: "POST",
      body: JSON.stringify(body),
      timeoutMs: 180_000,
      errorContext: "تعذر إعادة توليد الصورة",
    }),
  selectImage: (entryId: string, imageId: string) =>
    request(`/entries/${entryId}/select-image/${imageId}`, { method: "POST" }),
  approve: (id: string) =>
    request(`/entries/${id}/approve`, { method: "POST" }),
};
