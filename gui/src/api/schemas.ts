/**
 * The wire contract with the Python side.
 *
 * Every response is parsed through one of these before it reaches a component,
 * so a shape change surfaces here rather than as `undefined` in a table.
 * Fixtures captured from the real API keep them honest — see schemas.test.ts.
 */
import { z } from 'zod';

export const LabelSchema = z.enum(['bot', 'human', 'automated-integration', 'unknown']);
export type Label = z.infer<typeof LabelSchema>;

export const SignalSourceSchema = z.object({
  name: z.string(),
  ok: z.boolean(),
  entries: z.number(),
  fetched_at: z.number().optional(),
  error: z.string().optional(),
});

/**
 * `running: false` carries a reason because the three ways the slow tier can
 * be absent need different fixes: no redis, redis unreachable, or a refresher
 * nobody ever started.
 */
export const SignalHealthSchema = z.object({
  running: z.boolean(),
  reason: z.string().optional(),
  age_seconds: z.number().optional(),
  resolved: z.number().optional(),
  sources: z.array(SignalSourceSchema),
});
export type SignalHealth = z.infer<typeof SignalHealthSchema>;

export const HealthSchema = z.object({
  version: z.string(),
  model_loaded: z.boolean(),
  redis_connected: z.boolean(),
  signals: SignalHealthSchema,
  /** False when no --deployment-id was set: corrections have nothing to
   *  attribute to, so the control explains itself instead of failing. */
  feedback_enabled: z.boolean(),
});
export type Health = z.infer<typeof HealthSchema>;

export const ScanSampleSchema = z.object({
  name: z.string(),
  bytes: z.number(),
  lines: z.number(),
});

export const ScanSamplesSchema = z.object({
  samples: z.array(ScanSampleSchema),
});
export type ScanSample = z.infer<typeof ScanSampleSchema>;

export const ScanSessionSchema = z.object({
  ip: z.string(),
  score: z.number(),
  model_score: z.number(),
  heuristic_label: z.string(),
  heuristic_confidence: z.number(),
  heuristic_reason: z.string(),
  label: z.string(),
  request_count: z.number(),
  duration: z.number(),
  top_endpoint: z.string(),
  user_agent: z.string(),
  features: z.record(z.string(), z.number()),
});
export type ScanSession = z.infer<typeof ScanSessionSchema>;

/**
 * A scan that failed drops threshold, model_used, integration_count and
 * summary, and adds `error` (cli.py:62). Optional rather than a separate
 * schema, because every consumer has to handle both anyway.
 */
export const ScanResultSchema = z.object({
  total_sessions: z.number(),
  bot_count: z.number(),
  human_count: z.number(),
  integration_count: z.number().optional(),
  bot_rate: z.number(),
  threshold: z.number().optional(),
  model_used: z.boolean().optional(),
  sessions: z.array(ScanSessionSchema),
  error: z.string().optional(),
  summary: z
    .object({
      total_entries: z.number(),
      total_sessions: z.number(),
      bot_sessions: z.number(),
      human_sessions: z.number(),
      integration_sessions: z.number(),
      bot_rate: z.number(),
    })
    .optional(),
});
export type ScanResult = z.infer<typeof ScanResultSchema>;

export const ModelSchema = z.object({
  num_features: z.number(),
  architecture: z.array(z.number()),
  feature_names: z.array(z.string()),
  weights: z.array(z.number()),
  normalization: z
    .object({ mins: z.array(z.number()), maxs: z.array(z.number()) })
    .nullable(),
});
export type Model = z.infer<typeof ModelSchema>;

export const ModelEvaluationSchema = z.object({
  dataset: z.string(),
  threshold: z.number(),
  n_samples: z.number(),
  confusion: z.object({
    tp: z.number(),
    fp: z.number(),
    fn: z.number(),
    tn: z.number(),
  }),
  precision: z.number(),
  recall: z.number(),
  f1: z.number(),
  accuracy: z.number(),
  roc: z.array(z.object({ fpr: z.number(), tpr: z.number() })),
  score_distribution: z.array(z.number()),
});
export type ModelEvaluation = z.infer<typeof ModelEvaluationSchema>;

/**
 * One decision from the live path (live/scorer.py:172).
 *
 * `block_threshold` is null on the fail-open payload, where scoring never ran
 * and no threshold was consulted. `ts` is added by the recorder.
 */
export const DecisionSchema = z.object({
  ip: z.string(),
  label: z.string(),
  score: z.number(),
  model_score: z.number(),
  heuristic_label: z.string(),
  heuristic_confidence: z.number(),
  heuristic_reason: z.string(),
  reason: z.string(),
  request_count: z.number(),
  duration: z.number(),
  model_loaded: z.boolean(),
  /**
   * Set when a retrained model would not load and the previous one was kept.
   * It rides the decision because that is the only channel this app reads —
   * the failure it replaces was completely silent, and a dead model is
   * indistinguishable from a working one in every score it produces.
   */
  model_refused: z.string().nullable().optional(),
  block_threshold: z.number().nullable(),
  /** What a correction points at. Absent on rows recorded before M3. */
  id: z.string().optional(),
  ts: z.number(),
});
export type Decision = z.infer<typeof DecisionSchema>;

export const LiveEventsSchema = z.object({ events: z.array(DecisionSchema) });

export const LiveStatsSchema = z.object({
  total: z.number(),
  blocked: z.number(),
  allowed: z.number(),
  bot_rate: z.number(),
  avg_score: z.number(),
  histogram: z.array(z.number()),
  top_blocked_ips: z.array(z.object({ ip: z.string(), count: z.number() })),
  started_at: z.number().nullable(),
});
export type LiveStats = z.infer<typeof LiveStatsSchema>;

/**
 * `promoted_signals` is the list of sources allowed to decide a verdict. A
 * signal that is resolved but not promoted is recorded on every decision and
 * changes none of them — that is how a new signal gets measured against real
 * traffic before it starts blocking anyone.
 */
export const LiveConfigSchema = z.object({
  block_threshold: z.number().nullable(),
  promoted_signals: z.array(z.string()),
  known_signals: z.array(z.string()),
  writable: z.boolean(),
});
export type LiveConfig = z.infer<typeof LiveConfigSchema>;

export const RISK_BANDS = ['SAFE', 'LOW', 'WARNING', 'DANGER'] as const;
export type RiskBand = (typeof RISK_BANDS)[number];

/** Mirrors report.score_label (report.py:38) so the UI and the CLI agree. */
export function riskBand(score: number): RiskBand {
  if (score <= 0.3) return 'SAFE';
  if (score <= 0.59) return 'LOW';
  if (score <= 0.79) return 'WARNING';
  return 'DANGER';
}
