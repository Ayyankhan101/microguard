import { useMemo, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';

import {
  exportScan,
  getScanSamples,
  runScan,
  uploadScan,
  type ExportFormat,
} from '../api/client';
import type { ScanResult, ScanSession } from '../api/schemas';
import { FeatureBars } from '../components/FeatureBars';
import { LabelBadge, RiskBadge } from '../components/Badges';
import { ScoreRuler, ScoreRulerLegend } from '../components/ScoreRuler';
import { SessionTable } from '../components/SessionTable';
import { StatTile } from '../components/StatTile';
import { countAtThreshold } from './logic';

const EXPORTS: { format: ExportFormat; title: string; filename: string }[] = [
  { format: 'json', title: 'JSON', filename: 'microguard-report.json' },
  { format: 'html', title: 'HTML report', filename: 'microguard-report.html' },
  { format: 'nginx', title: 'nginx deny list', filename: 'microguard-deny.conf' },
  { format: 'cloudflare', title: 'Cloudflare rule', filename: 'microguard-rule.txt' },
];

export function ScanTab() {
  const [result, setResult] = useState<ScanResult | null>(null);
  const [selected, setSelected] = useState<ScanSession | null>(null);
  const [filter, setFilter] = useState('');
  const [threshold, setThreshold] = useState(0.7);
  const [dragging, setDragging] = useState(false);

  const samples = useQuery({ queryKey: ['scan-samples'], queryFn: getScanSamples });

  const scan = useMutation({
    mutationFn: (sample: string) => runScan({ sample, threshold }),
    onSuccess: (data) => {
      setResult(data);
      setSelected(null);
    },
  });

  const upload = useMutation({
    mutationFn: (file: File) => uploadScan(file, threshold),
    onSuccess: (data) => {
      setResult(data);
      setSelected(null);
    },
  });

  const busy = scan.isPending || upload.isPending;
  const error = scan.error ?? upload.error;

  // Counts follow the threshold slider, so moving the bar re-answers "how much
  // of this traffic would I block?" without parsing the log again.
  const counts = useMemo(
    () => (result ? countAtThreshold(result.sessions, threshold) : null),
    [result, threshold],
  );

  function download(format: ExportFormat, filename: string) {
    if (!result) return;
    void exportScan(result, format).then((text) => {
      const url = URL.createObjectURL(new Blob([text], { type: 'text/plain' }));
      const link = document.createElement('a');
      link.href = url;
      link.download = filename;
      link.click();
      URL.revokeObjectURL(url);
    });
  }

  return (
    <>
      <div className="main__head">
        <div>
          <h1 className="main__title">Scan a log</h1>
          <p className="main__hint">
            Group requests into sessions, score each one, and see which rule or feature
            drove the verdict. Nothing is blocked here — this is the audit view.
          </p>
        </div>
      </div>

      <div className="grid" style={{ gridTemplateColumns: '1fr', marginBottom: 16 }}>
        <div className="card">
          <div className="card__title">source</div>
          <div className="row">
            <label className="field">
              bundled sample
              <select
                aria-label="bundled sample"
                defaultValue=""
                onChange={(event) => {
                  if (event.target.value) scan.mutate(event.target.value);
                }}
                disabled={busy}
              >
                <option value="" disabled>
                  choose a log…
                </option>
                {samples.data?.samples.map((sample) => (
                  <option key={sample.name} value={sample.name}>
                    {sample.name} ({sample.lines.toLocaleString()} lines)
                  </option>
                ))}
              </select>
            </label>

            <label className="field">
              bot threshold {threshold.toFixed(2)}
              <input
                type="range"
                min={0}
                max={1}
                step={0.01}
                value={threshold}
                aria-label="bot threshold"
                onChange={(event) => setThreshold(Number(event.target.value))}
                style={{ width: 220 }}
              />
            </label>

            <label className="field" style={{ flex: 1, minWidth: 200 }}>
              filter
              <input
                type="text"
                placeholder="ip, endpoint, user agent, reason"
                value={filter}
                aria-label="filter"
                onChange={(event) => setFilter(event.target.value)}
              />
            </label>
          </div>

          <label
            className={dragging ? 'dropzone dropzone--over' : 'dropzone'}
            style={{ display: 'block', marginTop: 12 }}
            onDragOver={(event) => {
              event.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(event) => {
              event.preventDefault();
              setDragging(false);
              const file = event.dataTransfer.files[0];
              if (file) upload.mutate(file);
            }}
          >
            drop an access log here, or
            <input
              type="file"
              aria-label="upload a log file"
              style={{ marginLeft: 8 }}
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) upload.mutate(file);
              }}
            />
          </label>
        </div>
      </div>

      {busy && <p className="empty">Scanning…</p>}

      {error && (
        <div className="notice notice--danger" style={{ marginBottom: 16 }}>
          <div className="notice__title">Scan failed</div>
          <div className="notice__body">{error.message}</div>
        </div>
      )}

      {result?.error && (
        <div className="notice notice--danger" style={{ marginBottom: 16 }}>
          <div className="notice__title">Nothing to scan</div>
          <div className="notice__body">{result.error}</div>
        </div>
      )}

      {result && !result.error && counts && (
        <>
          <div className="grid tiles" style={{ marginBottom: 16 }}>
            <StatTile
              value={`${(counts.botRate * 100).toFixed(1)}%`}
              label="bot traffic"
              tone={counts.botRate > 0.1 ? 'danger' : 'default'}
              note={`at threshold ${threshold.toFixed(2)}`}
            />
            <StatTile value={String(counts.bots)} label="bot sessions" />
            <StatTile value={String(counts.humans)} label="human sessions" />
            <StatTile
              value={String(counts.integrations)}
              label="integrations"
              note="never blocked"
            />
          </div>

          {result.model_used === false && (
            <div className="notice notice--warning" style={{ marginBottom: 16 }}>
              <div className="notice__title">Heuristics only</div>
              <div className="notice__body">
                No trained model was loaded, so every score below came from the rules
                alone and model_score is 0.00 throughout.
              </div>
            </div>
          )}

          <div className="card" style={{ marginBottom: 16 }}>
            <div className="card__title">sessions</div>
            <SessionTable
              sessions={result.sessions}
              filter={filter}
              threshold={threshold}
              selected={selected}
              onSelect={setSelected}
            />
            <ScoreRulerLegend />
          </div>

          {selected && (
            <div className="card">
              <div className="card__title">
                session {selected.ip} — {selected.request_count}{' '}
                {selected.request_count === 1 ? 'request' : 'requests'} over{' '}
                {selected.duration.toFixed(1)}s
              </div>
              <div className="row" style={{ marginBottom: 12 }}>
                <LabelBadge label={selected.label} />
                <RiskBadge score={selected.score} />
                <span className="mono" style={{ color: 'var(--text-muted)' }}>
                  {selected.heuristic_reason}
                </span>
              </div>
              <ScoreRuler
                blended={selected.score}
                modelScore={selected.model_score}
                heuristicConfidence={selected.heuristic_confidence}
                threshold={threshold}
              />
              <ScoreRulerLegend />
              <div className="card__title" style={{ marginTop: 20 }}>
                features
              </div>
              <FeatureBars features={selected.features} />
            </div>
          )}

          <div className="row" style={{ marginTop: 16 }}>
            {EXPORTS.map((option) => (
              <button
                key={option.format}
                type="button"
                className="button"
                onClick={() => download(option.format, option.filename)}
              >
                {option.title}
              </button>
            ))}
          </div>
        </>
      )}
    </>
  );
}
