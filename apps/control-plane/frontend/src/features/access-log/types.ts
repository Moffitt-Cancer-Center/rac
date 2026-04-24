// type-only — Zod schemas + type aliases for access log viewer domain.
import { z } from 'zod';

export const accessLogItemSchema = z.object({
  id: z.string(),
  createdAt: z.string(),
  reviewerTokenJti: z.string().nullable(),
  reviewerLabel: z.string().nullable(),
  accessMode: z.string().nullable(),
  method: z.string().nullable(),
  path: z.string().nullable(),
  upstreamStatus: z.number().nullable(),
  latencyMs: z.number().nullable(),
  sourceIp: z.string().nullable(),
});
export type AccessLogItem = z.infer<typeof accessLogItemSchema>;

export const accessLogListResponseSchema = z.object({
  items: z.array(accessLogItemSchema),
  nextCursor: z.string().nullable(),
});
export type AccessLogListResponse = z.infer<typeof accessLogListResponseSchema>;
