/**
 * The 19 features behind one session's score.
 *
 * Features are not on a shared scale — requests_per_minute_1m can be in the
 * thousands while has_accept_language is 0 or 1 — so the bar is drawn relative
 * to the largest value in this session and the raw number is always shown.
 * A bar that implied a normalized 0-1 reading would be a lie.
 */
export function FeatureBars({ features }: { features: Record<string, number> }) {
  const entries = Object.entries(features).sort(
    ([, a], [, b]) => Math.abs(b) - Math.abs(a),
  );
  const largest = Math.max(...entries.map(([, value]) => Math.abs(value)), 0);

  return (
    <div className="grid" style={{ gap: 6 }}>
      {entries.map(([name, value]) => (
        <div
          key={name}
          style={{ display: 'grid', gridTemplateColumns: '220px 1fr 90px', gap: 10 }}
        >
          <span className="mono" style={{ fontSize: 12 }} data-testid="feature-name">
            {name}
          </span>
          <span
            style={{
              alignSelf: 'center',
              height: 6,
              borderRadius: 3,
              background: 'var(--card-border)',
            }}
          >
            <span
              style={{
                display: 'block',
                height: '100%',
                borderRadius: 3,
                background: 'var(--blue)',
                width: largest > 0 ? `${(Math.abs(value) / largest) * 100}%` : '0%',
              }}
            />
          </span>
          <span
            className="mono"
            style={{ fontSize: 12, textAlign: 'right', color: 'var(--text-muted)' }}
          >
            {Number.isInteger(value) ? value : value.toFixed(3)}
          </span>
        </div>
      ))}
    </div>
  );
}
