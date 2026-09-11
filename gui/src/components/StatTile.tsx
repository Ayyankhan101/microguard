export interface StatTileProps {
  value: string;
  label: string;
  note?: string;
  tone?: 'default' | 'danger' | 'warning';
}

const TONE_COLOR: Record<string, string> = {
  danger: 'var(--red)',
  warning: 'var(--yellow)',
};

export function StatTile({ value, label, note, tone = 'default' }: StatTileProps) {
  return (
    <div className="card">
      <div className="tile__value" style={{ color: TONE_COLOR[tone] ?? 'var(--text)' }}>
        {value}
      </div>
      <div className="tile__label">{label}</div>
      {note && <div className="tile__note">{note}</div>}
    </div>
  );
}
