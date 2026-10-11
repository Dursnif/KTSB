// Typed access to the error a failed API call threw (axios), instead of `catch (err: any)`.
import { isAxiosError } from "axios";

/** FastAPI's `detail` message when it is a plain string, else undefined. */
export function apiErrorDetail(err: unknown): string | undefined {
  if (!isAxiosError(err)) return undefined;
  const detail: unknown = err.response?.data?.detail;
  return typeof detail === "string" ? detail : undefined;
}

/** HTTP status of a failed API call, else undefined. */
export function apiErrorStatus(err: unknown): number | undefined {
  return isAxiosError(err) ? err.response?.status : undefined;
}
