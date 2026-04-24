// pattern: type-only — zod schemas + type aliases for nudge/finding domain.
import { z } from 'zod';

// ─── Decision enum ─────────────────────────────────────────────────────────────

export const decisionEnum = z.enum(['accept', 'override', 'auto_fix', 'dismiss']);
export type Decision = z.infer<typeof decisionEnum>;

// ─── AutoFixAction ─────────────────────────────────────────────────────────────

export const autoFixActionSchema = z.object({
  kind: z.enum(['replace_line', 'add_line', 'remove_line', 'apply_patch']),
  filePath: z.string(),
  payload: z.string(),
});
export type AutoFixAction = z.infer<typeof autoFixActionSchema>;

// ─── FindingDecision ────────────────────────────────────────────────────────────

export const findingDecisionSchema = z.object({
  id: z.string(),
  detectionFindingId: z.string(),
  decision: decisionEnum,
  decisionActorPrincipalId: z.string(),
  decisionNotes: z.string().nullable(),
  createdAt: z.string(),
});
export type FindingDecision = z.infer<typeof findingDecisionSchema>;

// ─── Finding ───────────────────────────────────────────────────────────────────

export const findingSchema = z.object({
  id: z.string(),
  submissionId: z.string(),
  ruleId: z.string(),
  ruleVersion: z.number().int(),
  severity: z.enum(['info', 'warn', 'error']),
  title: z.string(),
  detail: z.string(),
  lineRanges: z.array(z.tuple([z.number(), z.number()])).nullable(),
  filePath: z.string().nullable(),
  suggestedAction: decisionEnum.nullable(),
  autoFix: autoFixActionSchema.nullable(),
  createdAt: z.string(),
});
export type Finding = z.infer<typeof findingSchema>;

// ─── FindingWithDecision ───────────────────────────────────────────────────────

export const findingWithDecisionSchema = findingSchema.extend({
  /** Latest decision for this finding, or null if undecided. */
  decision: findingDecisionSchema.nullable(),
});
export type FindingWithDecision = z.infer<typeof findingWithDecisionSchema>;

// ─── FindingsList ──────────────────────────────────────────────────────────────

export const findingsListSchema = z.array(findingWithDecisionSchema);
export type FindingsList = z.infer<typeof findingsListSchema>;
