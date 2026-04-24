// pattern: Functional Core — renders the one-shot reviewer URL with a copy button.
// The URL is cleared from the DOM 5 minutes after mount to mitigate shoulder-surfing.
import { useEffect, useRef, useState } from 'react';

type OneShotUrlDisplayProps = {
  visitUrl: string;
};

const CLEAR_AFTER_MS = 5 * 60 * 1000; // 5 minutes

export function OneShotUrlDisplay({ visitUrl }: OneShotUrlDisplayProps) {
  const [displayUrl, setDisplayUrl] = useState(visitUrl);
  const [copied, setCopied] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    timerRef.current = setTimeout(() => {
      setDisplayUrl('URL cleared for security');
    }, CLEAR_AFTER_MS);
    return () => {
      if (timerRef.current !== null) {
        clearTimeout(timerRef.current);
      }
    };
  }, [visitUrl]);

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(displayUrl);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard write failed — no-op, user can select manually
    }
  }

  const isCleared = displayUrl === 'URL cleared for security';

  return (
    <div className="mt-4 space-y-3">
      <div
        className="bg-amber-50 border border-amber-200 rounded p-3 text-sm text-amber-800"
        role="alert"
      >
        <p className="font-semibold">This reviewer URL contains the token.</p>
        <p>
          It is shown once — after you close this dialog, the token remains valid,
          but the raw URL cannot be retrieved again.
        </p>
      </div>

      <div className="bg-gray-100 rounded p-3 break-all font-mono text-sm text-gray-900">
        {displayUrl}
      </div>

      <button
        type="button"
        onClick={() => void handleCopy()}
        disabled={isCleared}
        className="w-full bg-blue-600 text-white py-2 px-4 rounded font-semibold
                   hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
      >
        {copied ? 'Copied!' : 'Copy URL'}
      </button>
    </div>
  );
}
