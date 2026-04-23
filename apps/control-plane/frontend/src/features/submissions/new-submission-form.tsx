// pattern: Functional Core — render function and schema validation are pure;
// the submit handler is the imperative shell boundary.

import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { submissionCreateSchema } from './schemas';
import type { SubmissionCreateRequest, ApiError } from './schemas';

export interface NewSubmissionFormProps {
  onSubmit: (data: SubmissionCreateRequest) => Promise<void>;
  isLoading?: boolean;
}

interface FieldError {
  field: string;
  message: string;
}

/**
 * Pure form render function.
 * Handles the UI layout and error display based on form state.
 */
export function NewSubmissionForm({
  onSubmit,
  isLoading = false,
}: NewSubmissionFormProps) {
  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
    setError,
  } = useForm<SubmissionCreateRequest>({
    resolver: zodResolver(submissionCreateSchema),
    defaultValues: {
      gitRef: 'main',
      dockerfilePath: 'Dockerfile',
    },
  });

  // Shell boundary: async submit handler
  const onSubmitHandler = async (data: SubmissionCreateRequest) => {
    try {
      await onSubmit(data);
    } catch (error) {
      // Parse API error and set field-level errors
      if (error instanceof Error && 'apiError' in error) {
        const apiError = (error as any).apiError as ApiError;
        if (apiError.details) {
          for (const detail of apiError.details) {
            setError(detail.field as any, {
              type: 'manual',
              message: detail.message,
            });
          }
        } else {
          setError('root', {
            type: 'manual',
            message: apiError.message || 'Submission failed',
          });
        }
      } else {
        setError('root', {
          type: 'manual',
          message: error instanceof Error ? error.message : 'Unknown error',
        });
      }
    }
  };

  const isProcessing = isSubmitting || isLoading;

  return (
    <form onSubmit={handleSubmit(onSubmitHandler)} className="space-y-6">
      {errors.root && (
        <div className="rounded-md bg-red-50 p-4">
          <p className="text-sm text-red-800">{errors.root.message}</p>
        </div>
      )}

      <div>
        <label htmlFor="githubRepoUrl" className="block text-sm font-medium">
          GitHub Repository URL *
        </label>
        <input
          {...register('githubRepoUrl')}
          type="url"
          id="githubRepoUrl"
          placeholder="https://github.com/owner/repo"
          className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2"
          disabled={isProcessing}
        />
        {errors.githubRepoUrl && (
          <p className="mt-1 text-sm text-red-600">{errors.githubRepoUrl.message}</p>
        )}
      </div>

      <div className="grid grid-cols-2 gap-4">
        <div>
          <label htmlFor="gitRef" className="block text-sm font-medium">
            Git Ref
          </label>
          <input
            {...register('gitRef')}
            type="text"
            id="gitRef"
            placeholder="main"
            className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2"
            disabled={isProcessing}
          />
          {errors.gitRef && (
            <p className="mt-1 text-sm text-red-600">{errors.gitRef.message}</p>
          )}
        </div>

        <div>
          <label htmlFor="dockerfilePath" className="block text-sm font-medium">
            Dockerfile Path
          </label>
          <input
            {...register('dockerfilePath')}
            type="text"
            id="dockerfilePath"
            placeholder="Dockerfile"
            className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2"
            disabled={isProcessing}
          />
          {errors.dockerfilePath && (
            <p className="mt-1 text-sm text-red-600">{errors.dockerfilePath.message}</p>
          )}
        </div>
      </div>

      <div>
        <label htmlFor="paperTitle" className="block text-sm font-medium">
          Paper Title (optional)
        </label>
        <input
          {...register('paperTitle')}
          type="text"
          id="paperTitle"
          placeholder="My Research Paper"
          className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2"
          disabled={isProcessing}
        />
        {errors.paperTitle && (
          <p className="mt-1 text-sm text-red-600">{errors.paperTitle.message}</p>
        )}
      </div>

      <div className="grid grid-cols-2 gap-4">
        <div>
          <label htmlFor="piPrincipalId" className="block text-sm font-medium">
            PI Principal ID *
          </label>
          <input
            {...register('piPrincipalId')}
            type="text"
            id="piPrincipalId"
            placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
            className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2"
            disabled={isProcessing}
          />
          {errors.piPrincipalId && (
            <p className="mt-1 text-sm text-red-600">{errors.piPrincipalId.message}</p>
          )}
        </div>

        <div>
          <label htmlFor="deptFallback" className="block text-sm font-medium">
            Department (Fallback) *
          </label>
          <input
            {...register('deptFallback')}
            type="text"
            id="deptFallback"
            placeholder="Medical Oncology"
            className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2"
            disabled={isProcessing}
          />
          {errors.deptFallback && (
            <p className="mt-1 text-sm text-red-600">{errors.deptFallback.message}</p>
          )}
        </div>
      </div>

      <button
        type="submit"
        disabled={isProcessing}
        className="w-full rounded-md bg-blue-600 px-4 py-2 text-white hover:bg-blue-700 disabled:bg-gray-400"
      >
        {isProcessing ? 'Submitting...' : 'Submit Application'}
      </button>
    </form>
  );
}
