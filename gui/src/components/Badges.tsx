import { riskBand } from '../api/schemas';

const BAND_CLASS: Record<string, string> = {
  SAFE: 'badge--safe',
  LOW: 'badge--low',
  WARNING: 'badge--warning',
  DANGER: 'badge--danger',
};

/** The SAFE/LOW/WARNING/DANGER band, same thresholds as the CLI report. */
export function RiskBadge({ score }: { score: number }) {
  const band = riskBand(score);
  return <span className={`badge ${BAND_CLASS[band]}`}>{band}</span>;
}

const LABEL_CLASS: Record<string, string> = {
  bot: 'badge--danger',
  human: 'badge--safe',
  // Automated, but allowed by design: webhooks and integrations are never
  // blocked (live/scorer.py:122). Colouring them red would read as a miss.
  'automated-integration': 'badge--purple',
};

export function LabelBadge({ label }: { label: string }) {
  return <span className={`badge ${LABEL_CLASS[label] ?? 'badge--muted'}`}>{label}</span>;
}
