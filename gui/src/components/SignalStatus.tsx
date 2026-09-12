import type { SignalHealth } from '../api/schemas';

/**
 * A dead threat-intel feed produces no signal and no error. Nothing throws,
 * nothing logs at request time, and an actor with an unresolved signal reads
 * exactly like a clean one. This line is the only place that difference is
 * visible, so it names the source and the reason rather than saying
 * "degraded".
 */
export function SignalStatus({ health }: { health: SignalHealth }) {
  if (!health.running) {
    return (
      <span style={{ color: 'var(--yellow)' }} title={`signal refresher: ${health.reason}`}>
        signals {health.reason ?? 'not running'}
      </span>
    );
  }

  const failed = health.sources.filter((s) => !s.ok);
  if (failed.length > 0) {
    const detail = failed.map((s) => `${s.name}: ${s.error || 'failed'}`).join(', ');
    return (
      <span style={{ color: 'var(--yellow)' }} title={detail}>
        signals {failed.length} source{failed.length > 1 ? 's' : ''} failing
      </span>
    );
  }

  return (
    <span
      style={{ color: 'var(--text-muted)' }}
      title={health.sources.map((s) => `${s.name}: ${s.entries} entries`).join(', ')}
    >
      signals ok ({formatAge(health.age_seconds ?? 0)})
    </span>
  );
}

/** Age at a glance. Precision past the leading unit is noise here. */
export function formatAge(seconds: number): string {
  if (seconds < 90) return `${Math.round(seconds)}s ago`;
  if (seconds < 5400) return `${Math.round(seconds / 60)}m ago`;
  return `${Math.round(seconds / 3600)}h ago`;
}
