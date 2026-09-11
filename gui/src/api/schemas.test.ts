/**
 * The contract between two languages.
 *
 * Fixtures under __fixtures__ are real payloads captured from the running
 * Python API (gui/scripts/capture_fixtures.py). Parsing each one through the
 * schema the app actually uses means a shape change on the Python side fails
 * here, loudly, instead of reaching a table as `undefined`.
 */
import { describe, expect, it } from 'vitest';

import health from './__fixtures__/health.json';
import liveConfig from './__fixtures__/live-config.json';
import liveEvents from './__fixtures__/live-events.json';
import liveStats from './__fixtures__/live-stats.json';
import modelEvaluation from './__fixtures__/model-evaluation.json';
import model from './__fixtures__/model.json';
import scanError from './__fixtures__/scan-error.json';
import scanSamples from './__fixtures__/scan-samples.json';
import scan from './__fixtures__/scan.json';
import {
  DecisionSchema,
  HealthSchema,
  LiveConfigSchema,
  LiveEventsSchema,
  LiveStatsSchema,
  ModelEvaluationSchema,
  ModelSchema,
  ScanResultSchema,
  ScanSamplesSchema,
  riskBand,
} from './schemas';

describe('captured payloads parse', () => {
  it('health', () => {
    expect(() => HealthSchema.parse(health)).not.toThrow();
  });

  it('scan samples', () => {
    expect(() => ScanSamplesSchema.parse(scanSamples)).not.toThrow();
  });

  it('a successful scan', () => {
    const parsed = ScanResultSchema.parse(scan);
    expect(parsed.sessions.length).toBeGreaterThan(0);
  });

  it('a scan that failed, which drops half the keys and adds `error`', () => {
    const parsed = ScanResultSchema.parse(scanError);
    expect(parsed.error).toBeTruthy();
    expect(parsed.summary).toBeUndefined();
  });

  it('the model', () => {
    const parsed = ModelSchema.parse(model);
    expect(parsed.feature_names).toHaveLength(19);
  });

  it('a model evaluation', () => {
    expect(() => ModelEvaluationSchema.parse(modelEvaluation)).not.toThrow();
  });

  it('live stats', () => {
    const parsed = LiveStatsSchema.parse(liveStats);
    expect(parsed.histogram).toHaveLength(20);
  });

  it('live events', () => {
    const parsed = LiveEventsSchema.parse(liveEvents);
    expect(parsed.events.length).toBeGreaterThan(0);
  });

  it('live config', () => {
    expect(() => LiveConfigSchema.parse(liveConfig)).not.toThrow();
  });
});

describe('every scan session carries all 19 features', () => {
  it('keyed by the names the model was trained on', () => {
    const parsed = ScanResultSchema.parse(scan);
    const featureNames = ModelSchema.parse(model).feature_names;

    for (const session of parsed.sessions) {
      expect(Object.keys(session.features).sort()).toEqual([...featureNames].sort());
    }
  });
});

describe('a decision from the live path', () => {
  it('reports the threshold it was judged against', () => {
    const decision = DecisionSchema.parse(LiveEventsSchema.parse(liveEvents).events[0]);
    expect(typeof decision.block_threshold).toBe('number');
  });

  it('tolerates the fail-open payload, where no threshold was consulted', () => {
    const failOpen = {
      ip: '1.2.3.4',
      label: 'human',
      score: 0,
      model_score: 0,
      heuristic_label: 'unknown',
      heuristic_confidence: 0,
      heuristic_reason: 'scoring unavailable',
      reason: 'scoring unavailable',
      request_count: 0,
      duration: 0,
      model_loaded: false,
      block_threshold: null,
      ts: 1,
    };

    expect(DecisionSchema.parse(failOpen).block_threshold).toBeNull();
  });
});

describe('riskBand mirrors report.score_label', () => {
  it.each([
    [0.0, 'SAFE'],
    [0.3, 'SAFE'],
    [0.31, 'LOW'],
    [0.59, 'LOW'],
    [0.6, 'WARNING'],
    [0.79, 'WARNING'],
    [0.8, 'DANGER'],
    [1.0, 'DANGER'],
  ])('%s is %s', (score, band) => {
    expect(riskBand(score as number)).toBe(band);
  });
});
