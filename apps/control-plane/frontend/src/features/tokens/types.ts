// type-only — Zod schemas + type aliases for reviewer token domain.
import { z } from 'zod';

export const tokenListItemSchema = z.object({
  jti: z.string(),
  reviewerLabel: z.string().nullable(),
  issuedAt: z.string(),
  expiresAt: z.string(),
  revokedAt: z.string().nullable(),
  scope: z.string(),
  issuedByPrincipalId: z.string().nullable(),
});
export type TokenListItem = z.infer<typeof tokenListItemSchema>;

export const tokenListResponseSchema = z.object({
  items: z.array(tokenListItemSchema),
});
export type TokenListResponse = z.infer<typeof tokenListResponseSchema>;

export const tokenCreateResponseSchema = z.object({
  jwt: z.string(),
  jti: z.string(),
  expiresAt: z.string(),
  reviewerLabel: z.string(),
  visitUrl: z.string(),
});
export type TokenCreateResponse = z.infer<typeof tokenCreateResponseSchema>;
