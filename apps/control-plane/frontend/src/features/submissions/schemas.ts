import { z } from 'zod';

/**
 * Case conversion utilities for API communication.
 * Backend uses snake_case, frontend uses camelCase.
 */
function snakeToCamel(str: string): string {
  return str.replace(/_([a-z])/g, (_, letter) => letter.toUpperCase());
}

function camelToSnake(str: string): string {
  return str.replace(/[A-Z]/g, (letter) => `_${letter.toLowerCase()}`);
}

export function convertKeysToSnake(
  obj: Record<string, unknown>
): Record<string, unknown> | Array<unknown> {
  if (Array.isArray(obj)) {
    return obj.map((item) =>
      typeof item === 'object' && item !== null
        ? convertKeysToSnake(item as Record<string, unknown>)
        : item
    );
  }

  if (typeof obj !== 'object' || obj === null) {
    return obj;
  }

  return Object.keys(obj).reduce(
    (result, key) => {
      const snakeKey = camelToSnake(key);
      const value = obj[key];
      result[snakeKey] =
        typeof value === 'object' && value !== null
          ? convertKeysToSnake(value as Record<string, unknown>)
          : value;
      return result;
    },
    {} as Record<string, unknown>
  );
}

export function convertKeysToCamel(
  obj: Record<string, unknown>
): Record<string, unknown> | Array<unknown> {
  if (Array.isArray(obj)) {
    return obj.map((item) =>
      typeof item === 'object' && item !== null
        ? convertKeysToCamel(item as Record<string, unknown>)
        : item
    );
  }

  if (typeof obj !== 'object' || obj === null) {
    return obj;
  }

  return Object.keys(obj).reduce(
    (result, key) => {
      const camelKey = snakeToCamel(key);
      const value = obj[key];
      result[camelKey] =
        typeof value === 'object' && value !== null
          ? convertKeysToCamel(value as Record<string, unknown>)
          : value;
      return result;
    },
    {} as Record<string, unknown>
  );
}

// Frontend submission form schema (camelCase)
export const submissionCreateSchema = z.object({
  githubRepoUrl: z.string().url('Must be a valid GitHub URL'),
  gitRef: z.string().min(1, 'Required').default('main'),
  dockerfilePath: z.string().min(1, 'Required').default('Dockerfile'),
  paperTitle: z.string().optional(),
  piPrincipalId: z.string().uuid('Must be a valid UUID'),
  deptFallback: z.string().min(1, 'Required'),
  manifest: z.record(z.unknown()).optional(),
});

export type SubmissionCreateRequest = z.infer<typeof submissionCreateSchema>;

// Response schemas (converted from backend snake_case to camelCase)
export const submissionResponseSchema = z.object({
  id: z.string().uuid(),
  slug: z.string(),
  status: z.enum([
    'awaiting_scan',
    'pipeline_error',
    'scan_rejected',
    'needs_user_action',
    'needs_assistance',
    'awaiting_research_review',
    'research_rejected',
    'awaiting_it_review',
    'it_rejected',
    'approved',
    'deployed',
  ]),
  submitterPrincipalId: z.string().uuid(),
  agentId: z.string().uuid().nullable(),
  githubRepoUrl: z.string(),
  gitRef: z.string(),
  dockerfilePath: z.string(),
  piPrincipalId: z.string().uuid(),
  deptFallback: z.string(),
  manifest: z.record(z.unknown()).optional(),
  createdAt: z.string().datetime(),
  updatedAt: z.string().datetime(),
});

export type SubmissionResponse = z.infer<typeof submissionResponseSchema>;

export const submissionListResponseSchema = z.object({
  items: z.array(submissionResponseSchema),
  total: z.number(),
  page: z.number(),
  pageSize: z.number(),
});

export type SubmissionListResponse = z.infer<typeof submissionListResponseSchema>;

/**
 * API error response with field-level validation errors.
 */
export const apiErrorSchema = z.object({
  code: z.string(),
  message: z.string(),
  correlationId: z.string().optional(),
  details: z
    .array(
      z.object({
        field: z.string(),
        message: z.string(),
      })
    )
    .optional(),
});

export type ApiError = z.infer<typeof apiErrorSchema>;
