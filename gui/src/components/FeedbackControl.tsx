import { useState } from 'react';

/**
 * "This verdict was wrong."
 *
 * It lives on the decision row rather than behind a detail panel because an
 * operator notices a wrong verdict while looking at it, and a click between
 * noticing and correcting is where corrections stop happening. Retraining
 * needs fifty of them before it will run at all, so collection has to be
 * cheap.
 *
 * The two buttons say what the session ACTUALLY was, not "wrong" — the
 * server stores a label, and asking for it directly removes the chance to
 * record the opposite of what the operator meant.
 */
export function FeedbackControl({
  decisionId,
  currentLabel,
  available,
  onSubmit,
}: {
  decisionId: string | undefined;
  currentLabel: string;
  available: boolean;
  onSubmit: (decisionId: string, label: 'bot' | 'human') => Promise<unknown>;
}) {
  const [state, setState] = useState<'idle' | 'sending' | 'done' | 'failed'>('idle');
  const [message, setMessage] = useState<string | null>(null);

  // Rows recorded before decisions carried ids cannot be corrected: there is
  // nothing to point at. Saying so beats a button that always fails.
  if (!decisionId) {
    return <span className="hint">no id — recorded before corrections existed</span>;
  }
  if (!available) {
    return <span className="hint">start with --deployment-id to correct verdicts</span>;
  }
  if (state === 'done') {
    return <span className="badge">correction recorded</span>;
  }

  const send = async (label: 'bot' | 'human') => {
    setState('sending');
    setMessage(null);
    try {
      await onSubmit(decisionId, label);
      setState('done');
    } catch (error) {
      setState('failed');
      setMessage(error instanceof Error ? error.message : 'could not record');
    }
  };

  const opposite = currentLabel === 'bot' ? 'human' : 'bot';

  return (
    <span className="feedback">
      <span className="hint">Actually a</span>
      <button
        type="button"
        className="feedback__btn"
        disabled={state === 'sending'}
        onClick={() => send(opposite)}
      >
        {opposite}
      </button>
      <button
        type="button"
        className="feedback__btn feedback__btn--muted"
        disabled={state === 'sending'}
        onClick={() => send(currentLabel === 'bot' ? 'bot' : 'human')}
      >
        {currentLabel} (confirm)
      </button>
      {state === 'failed' && <span className="error">{message}</span>}
    </span>
  );
}
