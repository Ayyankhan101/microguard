import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';

import { evaluateModel, getModel } from '../api/client';
import { Histogram } from '../components/Histogram';
import { StatTile } from '../components/StatTile';
import { featureInfluence, suspiciouslyPerfect } from './logic';

const DATASETS = [
  { id: 'holdout', title: 'held-out real traffic' },
  { id: 'adversarial', title: 'adversarial' },
] as const;

export function ModelTab() {
  const [dataset, setDataset] = useState<string>('holdout');
  const [threshold, setThreshold] = useState(0.5);

  const model = useQuery({ queryKey: ['model'], queryFn: getModel });
  const evaluation = useQuery({
    queryKey: ['model-evaluation', dataset, threshold],
    queryFn: () => evaluateModel(dataset, threshold),
  });

  if (model.isError) {
    return (
      <div className="notice notice--warning">
        <div className="notice__title">No trained model</div>
        <div className="notice__body">{model.error.message}</div>
      </div>
    );
  }

  return (
    <>
      <div className="main__head">
        <div>
          <h1 className="main__title">The model</h1>
          <p className="main__hint">
            An 85-parameter network built on micrograd. It contributes 60% of every
            blended score; the rules contribute the rest. Move the bar to see what that
            costs in missed bots and blocked customers.
          </p>
        </div>
      </div>

      {model.data && (
        <>
          <div className="grid tiles" style={{ marginBottom: 16 }}>
            <StatTile
              value={[model.data.num_features, ...model.data.architecture].join(' → ')}
              label="architecture"
              note="inputs → hidden → output"
            />
            <StatTile value={String(model.data.weights.length)} label="parameters" />
            <StatTile
              value={model.data.normalization ? 'min-max' : 'none'}
              label="input scaling"
              note={
                model.data.normalization
                  ? 'applied inside predict()'
                  : 'scores are unreliable without it'
              }
            />
          </div>

          <div className="card" style={{ marginBottom: 16 }}>
            <div className="card__title">what each input contributes</div>
            <p className="main__hint" style={{ marginBottom: 12 }}>
              Summed absolute weight on each feature's connections into the hidden layer.
              It says how much the network can react to a feature, not which way.
            </p>
            <FeatureInfluence
              names={model.data.feature_names}
              weights={model.data.weights}
              hidden={model.data.architecture[0] ?? 0}
            />
          </div>
        </>
      )}

      <div className="card" style={{ marginBottom: 16 }}>
        <div className="card__title">evaluation</div>
        <div className="row">
          <label className="field">
            dataset
            <select
              aria-label="dataset"
              value={dataset}
              onChange={(event) => setDataset(event.target.value)}
            >
              {DATASETS.map((option) => (
                <option key={option.id} value={option.id}>
                  {option.title}
                </option>
              ))}
            </select>
          </label>
          <label className="field" style={{ flex: 1, minWidth: 240 }}>
            threshold {threshold.toFixed(2)}
            <input
              type="range"
              min={0}
              max={1}
              step={0.01}
              value={threshold}
              aria-label="threshold"
              onChange={(event) => setThreshold(Number(event.target.value))}
            />
          </label>
        </div>
      </div>

      {evaluation.isPending && <p className="empty">Scoring the set…</p>}

      {evaluation.data && (
        <>
          {suspiciouslyPerfect(evaluation.data.confusion) && (
            <div className="notice notice--warning" style={{ marginBottom: 16 }}>
              <div className="notice__title">Perfect separation — read it as a caution</div>
              <div className="notice__body">
                Nothing was missed and nothing misfired on this set. That usually says
                the set is easy rather than the model is: only 9 of the 19 features vary
                much in the human baseline this was trained against. Try the adversarial
                set.
              </div>
            </div>
          )}
          <div className="grid tiles" style={{ marginBottom: 16 }}>
            <StatTile value={evaluation.data.precision.toFixed(3)} label="precision" note="of those blocked, how many were bots" />
            <StatTile value={evaluation.data.recall.toFixed(3)} label="recall" note="of the bots, how many were caught" />
            <StatTile value={evaluation.data.f1.toFixed(3)} label="f1" />
            <StatTile value={evaluation.data.accuracy.toFixed(3)} label="accuracy" />
          </div>

          <div className="grid split-3">
            <div className="card">
              <div className="card__title">confusion at {threshold.toFixed(2)}</div>
              <Confusion counts={evaluation.data.confusion} />
            </div>
            <div className="card">
              <div className="card__title">score distribution</div>
              <Histogram buckets={evaluation.data.score_distribution} />
            </div>
            <div className="card">
              <div className="card__title">roc</div>
              <RocCurve points={evaluation.data.roc} />
            </div>
          </div>
        </>
      )}
    </>
  );
}

function FeatureInfluence({
  names,
  weights,
  hidden,
}: {
  names: string[];
  weights: number[];
  hidden: number;
}) {
  const ranked = featureInfluence(names, weights, hidden);
  const largest = Math.max(...ranked.map((item) => item.total), 0);

  return (
    <div className="grid" style={{ gap: 6 }}>
      {ranked.map((item) => (
        <div
          key={item.name}
          style={{ display: 'grid', gridTemplateColumns: '220px 1fr 60px', gap: 10 }}
        >
          <span className="mono" style={{ fontSize: 12 }}>
            {item.name}
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
                background: 'var(--purple)',
                width: largest > 0 ? `${(item.total / largest) * 100}%` : '0%',
              }}
            />
          </span>
          <span
            className="mono"
            style={{ fontSize: 12, textAlign: 'right', color: 'var(--text-muted)' }}
          >
            {item.total.toFixed(2)}
          </span>
        </div>
      ))}
    </div>
  );
}

function Confusion({
  counts,
}: {
  counts: { tp: number; fp: number; fn: number; tn: number };
}) {
  const cells = [
    { key: 'tp', title: 'bots caught', value: counts.tp, color: 'var(--green)' },
    { key: 'fp', title: 'humans blocked', value: counts.fp, color: 'var(--red)' },
    { key: 'fn', title: 'bots missed', value: counts.fn, color: 'var(--yellow)' },
    { key: 'tn', title: 'humans allowed', value: counts.tn, color: 'var(--text-muted)' },
  ];

  return (
    <div
      className="grid"
      style={{ gridTemplateColumns: '1fr 1fr', gap: 10, alignContent: 'space-between', height: '100%' }}
    >
      {cells.map((cell) => (
        <div key={cell.key}>
          <div className="tile__value" style={{ color: cell.color, fontSize: 24 }}>
            {cell.value}
          </div>
          <div className="tile__label">{cell.title}</div>
        </div>
      ))}
    </div>
  );
}

function RocCurve({ points }: { points: { fpr: number; tpr: number }[] }) {
  const path = points
    .map((point, index) => `${index === 0 ? 'M' : 'L'} ${point.fpr * 100} ${100 - point.tpr * 100}`)
    .join(' ');

  return (
    <svg viewBox="0 0 100 100" role="img" aria-label="ROC curve" style={{ width: '100%' }}>
      <line x1="0" y1="100" x2="100" y2="0" stroke="var(--card-border)" strokeWidth="1" strokeDasharray="3 3" />
      <path d={path} fill="none" stroke="var(--purple)" strokeWidth="2" />
    </svg>
  );
}
