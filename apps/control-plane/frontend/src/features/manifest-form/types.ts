// pattern: Functional Core — Zod schemas and inferred types only; no I/O.
import { z } from 'zod';

export const uploadAssetSchema = z.object({
  kind: z.literal('upload'),
  name: z.string().min(1, 'Name is required'),
  mountPath: z.string().startsWith('/', 'Mount path must be absolute'),
});

export const externalUrlAssetSchema = z.object({
  kind: z.literal('external_url'),
  name: z.string().min(1, 'Name is required'),
  mountPath: z.string().startsWith('/', 'Mount path must be absolute'),
  url: z.string().url('Must be a valid URL'),
  sha256: z
    .string()
    .regex(/^[0-9a-fA-F]{64}$/, 'sha256 must be exactly 64 hex characters'),
});

export const sharedReferenceAssetSchema = z.object({
  kind: z.literal('shared_reference'),
  name: z.string().min(1, 'Name is required'),
  mountPath: z.string().startsWith('/', 'Mount path must be absolute'),
  catalogId: z.string().min(1, 'Catalog ID is required'),
});

export const assetSchema = z.discriminatedUnion('kind', [
  uploadAssetSchema,
  externalUrlAssetSchema,
  sharedReferenceAssetSchema,
]);

export const formManifestSchema = z.object({
  targetPort: z.number().int().min(1).max(65535),
  cpuCores: z.number().min(0.25).max(2.0),
  memoryGb: z.number().min(0.5).max(8.0),
  envVars: z.record(z.string(), z.string()).default({}),
  assets: z.array(assetSchema),
});

export type FormManifest = z.infer<typeof formManifestSchema>;
export type Asset = z.infer<typeof assetSchema>;
export type UploadAssetInput = z.infer<typeof uploadAssetSchema>;
export type ExternalUrlAssetInput = z.infer<typeof externalUrlAssetSchema>;
export type SharedReferenceAssetInput = z.infer<typeof sharedReferenceAssetSchema>;
