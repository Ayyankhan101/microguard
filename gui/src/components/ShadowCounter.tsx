import { thresholdImpact } from '../tabs/logic';

/**
 * What a threshold would do, before it does it.
 *
 * Observe-only mode (threshold 1.00) is the documented way to start a
 * deployment, but on its own it tells you nothing: every request is allowed
 * and no number says what enforcing would have cost. This is that number.
 *
 * It also carries its own sample size, because "would block 12" means
 * something very different over 40 decisions than over 1000, and the ring is
 * capped.
 */
export function ShadowCounter({
  decisions,
  threshold,
}: {
  decisions: { score: number; label: string }[];
  threshold: number | null;
}) {
  if (threshold === null || decisions.length === 0) {
    return (
      <p className="hint">
        No decisions yet — the projection needs traffic to project from.
      </p>
    );
  }

  const { actual, projected, delta, sampled } = thresholdImpact(decisions, threshold);

  return (
    <p className="hint">
      At {threshold.toFixed(2)}: <strong>{projected}</strong> of the last {sampled}{' '}
      would be blocked
      {delta === 0 ? (
        <> — same as the {actual} actually blocked.</>
      ) : (
        <>
          , <strong style={{ color: delta > 0 ? 'var(--red)' : 'var(--green)' }}>
            {delta > 0 ? `${delta} more` : `${-delta} fewer`}
          </strong>{' '}
          than the {actual} actually blocked.
        </>
      )}
    </p>
  );
}
