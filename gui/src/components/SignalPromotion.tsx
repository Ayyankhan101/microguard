/**
 * Which signals are allowed to decide a verdict.
 *
 * Every new signal ships observe-only: resolved, recorded on each decision,
 * and unable to change any of them. That makes the rollout a measurement
 * instead of a bet — you can see how many extra blocks a source would cause on
 * your own traffic before it causes any. This is where you act on that.
 *
 * It sits beside the threshold slider because the two are the same kind of
 * control: both change who gets a 403 on a live site, and both are gated
 * behind --allow-config-writes for that reason.
 */
export function SignalPromotion({
  known,
  promoted,
  writable,
  saving,
  error,
  onToggle,
}: {
  known: string[];
  promoted: string[];
  writable: boolean;
  saving: boolean;
  error: string | null;
  onToggle: (next: string[]) => void;
}) {
  const active = new Set(promoted);

  return (
    <div className="panel">
      <div className="panel__head">
        <span>Signal enforcement</span>
        {saving && <span className="badge">saving…</span>}
      </div>

      <p className="hint">
        {writable
          ? 'Unpromoted signals are recorded but never block. Promote one to enforce it.'
          : 'Enforcement is read-only here, same as the threshold above.'}
      </p>

      <div className="signal-promotion">
        {known.map((name) => {
          const on = active.has(name);
          return (
            <label key={name} className="signal-promotion__row">
              <input
                type="checkbox"
                checked={on}
                disabled={!writable || saving}
                onChange={() =>
                  onToggle(
                    on ? promoted.filter((s) => s !== name) : [...promoted, name],
                  )
                }
              />
              <span>{name}</span>
              <span
                className="badge"
                style={{ color: on ? 'var(--red)' : 'var(--text-muted)' }}
              >
                {on ? 'enforced' : 'observe-only'}
              </span>
            </label>
          );
        })}
      </div>

      {error && <p className="error">{error}</p>}
    </div>
  );
}
