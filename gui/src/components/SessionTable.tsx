import { useMemo, useState } from 'react';

import type { ScanSession } from '../api/schemas';
import { LabelBadge, RiskBadge } from './Badges';
import { ScoreRuler } from './ScoreRuler';

type SortKey = 'score' | 'request_count' | 'duration' | 'ip';

export interface SessionTableProps {
  sessions: ScanSession[];
  filter?: string;
  /**
   * Re-label rows against a threshold chosen after the scan. Every row carries
   * its raw score, so moving the bar is a client-side question and does not
   * need the log parsed again.
   */
  threshold?: number | undefined;
  selected?: ScanSession | null;
  onSelect?: (session: ScanSession) => void;
}

const COLUMNS: { key: SortKey; title: string }[] = [
  { key: 'ip', title: 'session' },
  { key: 'score', title: 'score' },
  { key: 'request_count', title: 'requests' },
  { key: 'duration', title: 'duration' },
];

function matches(session: ScanSession, filter: string): boolean {
  const needle = filter.toLowerCase();
  return [session.ip, session.top_endpoint, session.user_agent, session.heuristic_reason].some(
    (field) => field.toLowerCase().includes(needle),
  );
}

function labelFor(session: ScanSession, threshold: number | undefined): string {
  // Recognized integrations are never scored against the bar (live/scorer.py:122),
  // so dragging the threshold must not turn one into a bot.
  if (threshold === undefined || session.label === 'automated-integration') return session.label;
  return session.score >= threshold ? 'bot' : 'human';
}

export function SessionTable({
  sessions,
  filter = '',
  threshold,
  selected,
  onSelect,
}: SessionTableProps) {
  const [sortKey, setSortKey] = useState<SortKey>('score');
  const [ascending, setAscending] = useState(false);

  const rows = useMemo(() => {
    const visible = filter ? sessions.filter((s) => matches(s, filter)) : sessions;
    const direction = ascending ? 1 : -1;
    return [...visible].sort((a, b) => {
      const left = a[sortKey];
      const right = b[sortKey];
      if (typeof left === 'string' && typeof right === 'string') {
        return left.localeCompare(right) * direction;
      }
      return ((left as number) - (right as number)) * direction;
    });
  }, [sessions, filter, sortKey, ascending]);

  function sortBy(key: SortKey) {
    if (key === sortKey) {
      setAscending((value) => !value);
      return;
    }
    setSortKey(key);
    setAscending(false);
  }

  if (sessions.length === 0) {
    return <p className="empty">No sessions yet. Scan a log file to see traffic here.</p>;
  }

  if (rows.length === 0) {
    return <p className="empty">No sessions match that filter.</p>;
  }

  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            {COLUMNS.map((column) => (
              <th
                key={column.key}
                aria-sort={
                  sortKey === column.key ? (ascending ? 'ascending' : 'descending') : 'none'
                }
              >
                <button type="button" onClick={() => sortBy(column.key)}>
                  {column.title}
                  {sortKey === column.key ? (ascending ? ' ↑' : ' ↓') : ''}
                </button>
              </th>
            ))}
            <th>verdict</th>
            <th>endpoint</th>
            <th>why</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((session) => (
            <tr
              key={`${session.ip}-${session.request_count}-${session.duration}`}
              aria-selected={selected?.ip === session.ip}
              onClick={() => onSelect?.(session)}
              style={{ cursor: onSelect ? 'pointer' : 'default' }}
            >
              <td>
                <div className="mono" data-testid="row-ip">
                  {session.ip}
                </div>
                <div className="truncate" style={{ color: 'var(--text-muted)', fontSize: 12 }}>
                  {session.user_agent}
                </div>
              </td>
              <td style={{ minWidth: 170 }}>
                <div className="mono">{session.score.toFixed(2)}</div>
                <ScoreRuler
                  compact
                  blended={session.score}
                  modelScore={session.model_score}
                  heuristicConfidence={session.heuristic_confidence}
                  threshold={threshold ?? null}
                />
              </td>
              <td className="mono">{session.request_count}</td>
              <td className="mono">{session.duration.toFixed(1)}s</td>
              <td>
                <LabelBadge label={labelFor(session, threshold)} />{' '}
                <RiskBadge score={session.score} />
              </td>
              <td className="mono truncate">{session.top_endpoint}</td>
              <td className="truncate" style={{ color: 'var(--text-muted)' }}>
                {session.heuristic_reason}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
