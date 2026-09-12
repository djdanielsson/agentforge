/**
 * Errors raised by the AgentForge client.
 *
 * FastAPI answers failures with a `{"detail": ...}` envelope, so the client
 * lifts that into a typed error once: callers switch on `status` and show
 * `detail` without re-parsing the body themselves.
 */

/** Shapes `detail` can take in a FastAPI error body. */
interface ValidationIssue {
  msg?: unknown;
  [key: string]: unknown;
}

/** Best-effort human-readable text for one entry of a validation-error list. */
function describeIssue(issue: unknown): string {
  if (issue !== null && typeof issue === "object" && "msg" in issue) {
    return String((issue as ValidationIssue).msg);
  }
  return JSON.stringify(issue) ?? String(issue);
}

/**
 * Pull a useful message out of a response body.
 *
 * Falls back to the raw body because a 502 from a reverse proxy is HTML, not
 * JSON, and that text is the only clue the caller has.
 */
function extractDetail(body: string, fallback: string): string {
  if (body.length === 0) return fallback.length > 0 ? fallback : "request failed";
  try {
    const parsed: unknown = JSON.parse(body);
    if (parsed !== null && typeof parsed === "object" && "detail" in parsed) {
      const detail = (parsed as { detail: unknown }).detail;
      if (typeof detail === "string") return detail;
      // Validation errors arrive as a list of per-field issues.
      if (Array.isArray(detail)) return detail.map(describeIssue).join("; ");
      if (detail !== undefined) return JSON.stringify(detail) ?? String(detail);
    }
  } catch {
    // Not JSON — the raw body is the best available detail.
  }
  const trimmed = body.trim();
  if (trimmed.length > 0) return trimmed;
  return fallback.length > 0 ? fallback : "request failed";
}

/** Options accepted by {@link ApiError}. */
export interface ApiErrorOptions {
  /** Raw response body, kept for logging. */
  body?: string | null;
  /** URL that produced the failure. */
  url?: string | null;
  /** Underlying cause, when the error wraps another. */
  cause?: unknown;
}

/**
 * A non-2xx response from the AgentForge API.
 *
 * `status` is the HTTP status and `detail` is the server's `detail` field when
 * the body carried one. Transport failures (offline, DNS, a fetch `AbortError`)
 * are deliberately not wrapped: they are not HTTP responses and callers detect
 * aborts by error name.
 */
export class ApiError extends Error {
  /** HTTP status code, e.g. 404. */
  readonly status: number;

  /** Server detail, or the raw body when it was not the `{detail}` envelope. */
  readonly detail: string;

  /** Raw response body, when it was read. */
  readonly body: string | null;

  /** Request URL that failed. */
  readonly url: string | null;

  constructor(status: number, detail: string, options: ApiErrorOptions = {}) {
    super(`HTTP ${status}: ${detail}`, options.cause === undefined ? undefined : { cause: options.cause });
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    this.body = options.body ?? null;
    this.url = options.url ?? null;
  }

  /** 404 — the object does not exist, or a project-pinned key cannot see it. */
  get isNotFound(): boolean {
    return this.status === 404;
  }

  /** 401 — missing or invalid API key. */
  get isUnauthorized(): boolean {
    return this.status === 401;
  }

  /** 403 — the key authenticated but lacks the `write` scope. */
  get isForbidden(): boolean {
    return this.status === 403;
  }

  /** Build an ApiError by reading a failed `Response`. */
  static async fromResponse(response: Response, url?: string): Promise<ApiError> {
    let body = "";
    try {
      body = await response.text();
    } catch {
      // Body already consumed or the stream broke; the status still stands.
      body = "";
    }
    return new ApiError(response.status, extractDetail(body, response.statusText), {
      body,
      url: url ?? null,
    });
  }
}

/** Narrow an unknown caught value to an {@link ApiError}. */
export function isApiError(value: unknown): value is ApiError {
  return value instanceof ApiError;
}
