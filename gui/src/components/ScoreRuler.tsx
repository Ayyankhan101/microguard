/**
 * One instrument, used in all three tabs.
 *
 * A single blended number tells an operator that a visitor was blocked but not
 * whether the rules or the model drove it — which is exactly what you need to
 * tune a threshold or explain a block. The ruler puts all four values on the
 * same 0-1 scale: what the rules said, what the model said, where the blend
 * landed, and where the bar was.
 */

export interface ScoreRulerProps {
  blended: number;
  modelScore?: number | undefined;
  heuristicConfidence?: number | undefined;
  threshold?: number | null | undefined;
  compact?: boolean;
}

const clamp = (value: number) => Math.max(0, Math.min(1, value));
const percent = (value: number) => `${clamp(value) * 100}%`;

export function ScoreRuler({
  blended,
  modelScore,
  heuristicConfidence,
  threshold,
  compact = false,
}: ScoreRulerProps) {
  // Strict `>` — a score sitting exactly on the bar has not cleared it
  // (live/scorer.py:147).
  const over = threshold != null && blended > threshold;
  const label =
    threshold == null
      ? `Blended score ${blended.toFixed(2)}, no threshold applied`
      : `Blended score ${blended.toFixed(2)}, ${over ? 'above' : 'below'} the ${threshold} block threshold`;

  return (
    <div>
      <div
        className={compact ? 'ruler ruler--compact' : 'ruler'}
        role="img"
        aria-label={label}
      >
        {threshold != null && (
          <div
            className="ruler__gate"
            data-testid="gate"
            // The printed value is dropped in compact form: down a feed of
            // rows that all share one threshold, it repeats forty times and
            // says nothing. The accessible label still carries it.
            {...(compact ? {} : { 'data-label': threshold.toFixed(2) })}
            style={{ left: percent(threshold) }}
          />
        )}
        {modelScore !== undefined && (
          <div
            className="ruler__mark ruler__mark--model"
            data-testid="mark-model"
            title={`model ${modelScore.toFixed(2)}`}
            style={{ left: percent(modelScore) }}
          />
        )}
        {heuristicConfidence !== undefined && (
          <div
            className="ruler__mark ruler__mark--heuristic"
            data-testid="mark-heuristic"
            title={`rules ${heuristicConfidence.toFixed(2)}`}
            style={{ left: percent(heuristicConfidence) }}
          />
        )}
        <div
          className="ruler__mark ruler__mark--blended"
          data-testid="mark-blended"
          title={`blended ${blended.toFixed(2)}`}
          style={{ left: percent(blended) }}
        />
      </div>
      {!compact && (
        <div className="ruler__scale">
          <span>0.00</span>
          <span>0.50</span>
          <span>1.00</span>
        </div>
      )}
    </div>
  );
}

export function ScoreRulerLegend() {
  return (
    <div className="ruler-legend">
      <span className="ruler-legend__key">
        <span className="ruler-legend__swatch" style={{ background: 'var(--blue)' }} />
        rules
      </span>
      <span className="ruler-legend__key">
        <span className="ruler-legend__swatch" style={{ background: 'var(--purple)' }} />
        model
      </span>
      <span className="ruler-legend__key">
        <span
          className="ruler-legend__swatch"
          style={{ background: 'var(--text)', transform: 'rotate(45deg)' }}
        />
        blended
      </span>
      <span className="ruler-legend__key">
        <span className="ruler-legend__swatch" style={{ background: 'var(--text)', width: 2 }} />
        block threshold
      </span>
    </div>
  );
}
