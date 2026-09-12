import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import {
  getLiveConfig,
  getLiveEvents,
  getLiveStats,
  setBlockThreshold,
  setPromotedSignals,
} from '../api/client';
import { SignalPromotion } from '../components/SignalPromotion';
import { DecisionSchema, LiveStatsSchema, type Decision, type LiveStats } from '../api/schemas';
import { Histogram } from '../components/Histogram';
import { LabelBadge, RiskBadge } from '../components/Badges';
import { ScoreRuler, ScoreRulerLegend } from '../components/ScoreRuler';
import { StatTile } from '../components/StatTile';
import { failOpenCount } from './logic';

const FEED_LIMIT = 200;

/**
 * Subscribe to the decision stream.
 *
 * Seeded from /api/live/events so the feed is not empty on arrival, then kept
 * current by SSE. Falls back silently if the stream drops — the counters query
 * keeps polling, so the page degrades to slower rather than blank.
 */
function useLiveFeed() {
  const [decisions, setDecisions] = useState<Decision[]>([]);
  const [streamStats, setStreamStats] = useState<LiveStats | null>(null);
  const seeded = useRef(false);

  const polled = useQuery({
    queryKey: ['live-stats'],
    queryFn: getLiveStats,
    refetchInterval: 10_000,
  });

  useEffect(() => {
    if (seeded.current) return;
    seeded.current = true;
    void getLiveEvents(FEED_LIMIT).then(({ events }) => setDecisions(events));
  }, []);

  useEffect(() => {
    const source = new EventSource('/api/live/stream');

    source.addEventListener('decision', (event) => {
      const decision = DecisionSchema.safeParse(JSON.parse((event as MessageEvent).data));
      if (!decision.success) return;
      setDecisions((current) => {
        if (current.some((seen) => seen.ts === decision.data.ts && seen.ip === decision.data.ip)) {
          return current;
        }
        return [decision.data, ...current].slice(0, FEED_LIMIT);
      });
    });

    source.addEventListener('stats', (event) => {
      const stats = LiveStatsSchema.safeParse(JSON.parse((event as MessageEvent).data));
      if (stats.success) setStreamStats(stats.data);
    });

    return () => source.close();
  }, []);

  return { decisions, stats: streamStats ?? polled.data ?? null };
}

export function LiveTab() {
  const { decisions, stats } = useLiveFeed();
  const [selected, setSelected] = useState<Decision | null>(null);
  const queryClient = useQueryClient();

  const config = useQuery({ queryKey: ['live-config'], queryFn: getLiveConfig });
  const savePromotion = useMutation({
    mutationFn: (next: string[]) =>
      setPromotedSignals(next, config.data?.block_threshold ?? null),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['live-config'] }),
  });

  const saveThreshold = useMutation({
    mutationFn: setBlockThreshold,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['live-config'] }),
  });

  const failingOpen = useMemo(() => failOpenCount(decisions), [decisions]);
  const modelDead = decisions.length > 0 && decisions.every((d) => !d.model_loaded);

  return (
    <>
      <div className="main__head">
        <div>
          <h1 className="main__title">Live traffic</h1>
          <p className="main__hint">
            Every decision the check server and the middleware made, as they made it.
            Read a row left to right: what the rules saw, what the model said, where
            the blend landed, and where the bar was.
          </p>
        </div>
      </div>

      {failingOpen > 0 && (
        <div className="notice notice--danger" style={{ marginBottom: 16 }}>
          <div className="notice__title">Failing open</div>
          <div className="notice__body">
            {failingOpen} of the last 20 decisions could not be scored and were allowed
            through unchecked. Check that Redis is reachable from the check server.
          </div>
        </div>
      )}

      {modelDead && (
        <div className="notice notice--warning" style={{ marginBottom: 16 }}>
          <div className="notice__title">Heuristics only</div>
          <div className="notice__body">
            No model is loaded on the scoring process, so model_score is 0.00 on every
            decision and the blend collapses to the rules alone.
          </div>
        </div>
      )}

      <div className="grid tiles" style={{ marginBottom: 16 }}>
        <StatTile value={(stats?.total ?? 0).toLocaleString()} label="requests scored" />
        <StatTile
          value={(stats?.blocked ?? 0).toLocaleString()}
          label="blocked"
          tone={stats && stats.blocked > 0 ? 'danger' : 'default'}
        />
        <StatTile
          value={`${((stats?.bot_rate ?? 0) * 100).toFixed(1)}%`}
          label="bot rate"
        />
        <StatTile value={(stats?.avg_score ?? 0).toFixed(2)} label="mean score" />
      </div>

      <div className="grid split-2">
        <div className="card">
          <div className="card__title">score distribution</div>
          <Histogram buckets={stats?.histogram ?? new Array(20).fill(0)} />
        </div>

        <div className="card">
          <div className="card__title">block threshold</div>
          <ThresholdControl
            value={config.data?.block_threshold ?? null}
            writable={config.data?.writable ?? false}
            saving={saveThreshold.isPending}
            error={saveThreshold.error?.message ?? null}
            onSave={(value) => saveThreshold.mutate(value)}
          />
          <SignalPromotion
            known={config.data?.known_signals ?? []}
            promoted={config.data?.promoted_signals ?? []}
            writable={config.data?.writable ?? false}
            saving={savePromotion.isPending}
            error={savePromotion.error?.message ?? null}
            onToggle={(next) => savePromotion.mutate(next)}
          />
        </div>
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <div className="card__title">decisions</div>
        {decisions.length > 0 && <ScoreRulerLegend />}
        {decisions.length === 0 ? (
          <p className="empty">
            Nothing scored yet. Start <span className="mono">microguard serve</span> or put
            the middleware in front of your app, then send it traffic.
          </p>
        ) : (
          <div className="feed">
            {decisions.map((decision, index) => (
              <button
                key={`${decision.ts}-${decision.ip}`}
                type="button"
                className={`feed__row feed__row--${decision.label === 'bot' ? 'bot' : 'human'}${
                  index === 0 ? ' feed__row--new' : ''
                }`}
                onClick={() => setSelected(decision)}
              >
                <span style={{ color: 'var(--text-muted)' }}>
                  {new Date(decision.ts * 1000).toLocaleTimeString()}
                </span>
                <span>{decision.ip}</span>
                <span>{decision.score.toFixed(2)}</span>
                <ScoreRuler
                  compact
                  blended={decision.score}
                  modelScore={decision.model_score}
                  heuristicConfidence={decision.heuristic_confidence}
                  threshold={decision.block_threshold}
                />
                <span className="truncate" style={{ color: 'var(--text-muted)' }}>
                  {decision.heuristic_reason}
                </span>
              </button>
            ))}
          </div>
        )}
      </div>

      {selected && (
        <div className="card" style={{ marginTop: 16 }}>
          <div className="card__title">
            decision for {selected.ip} — {selected.request_count}{' '}
            {selected.request_count === 1 ? 'request' : 'requests'} in the retained window
          </div>
          <div className="row" style={{ marginBottom: 12 }}>
            <LabelBadge label={selected.label} />
            <RiskBadge score={selected.score} />
            {!selected.model_loaded && <span className="badge badge--warning">no model</span>}
            <span className="mono" style={{ color: 'var(--text-muted)' }}>
              {selected.heuristic_reason}
            </span>
          </div>
          <ScoreRuler
            blended={selected.score}
            modelScore={selected.model_score}
            heuristicConfidence={selected.heuristic_confidence}
            threshold={selected.block_threshold}
          />
          <ScoreRulerLegend />
        </div>
      )}
    </>
  );
}

interface ThresholdControlProps {
  value: number | null;
  writable: boolean;
  saving: boolean;
  error: string | null;
  onSave: (value: number | null) => void;
}

export function ThresholdControl({
  value,
  writable,
  saving,
  error,
  onSave,
}: ThresholdControlProps) {
  const [draft, setDraft] = useState(value ?? 0.85);

  useEffect(() => {
    if (value != null) setDraft(value);
  }, [value]);

  if (!writable) {
    return (
      <>
        <div className="tile__value">{value == null ? 'default' : value.toFixed(2)}</div>
        <p className="tile__note">
          Read-only. Start the dashboard with{' '}
          <span className="mono">--allow-config-writes</span> to change the bar from here.
        </p>
      </>
    );
  }

  return (
    <>
      <div className="tile__value">{draft.toFixed(2)}</div>
      <input
        type="range"
        min={0}
        max={1}
        step={0.01}
        value={draft}
        aria-label="block threshold"
        onChange={(event) => setDraft(Number(event.target.value))}
      />
      <p className="tile__note">
        {draft === 1
          ? 'At 1.00 nothing is ever blocked — observe-only mode.'
          : draft === 0
            ? 'At 0.00 every visitor is blocked.'
            : 'Applies to every scoring process sharing this Redis.'}
      </p>
      <div className="row" style={{ marginTop: 10 }}>
        <button
          type="button"
          className="button button--primary"
          disabled={saving}
          onClick={() => onSave(draft)}
        >
          {saving ? 'applying…' : 'apply'}
        </button>
        <button type="button" className="button" disabled={saving} onClick={() => onSave(null)}>
          clear override
        </button>
      </div>
      {error && <p className="tile__note" style={{ color: 'var(--red)' }}>{error}</p>}
    </>
  );
}
