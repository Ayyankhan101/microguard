/**
 * Every call to the Python dashboard goes through here.
 *
 * Two jobs: parse responses through their schema so bad shapes fail at the
 * boundary, and turn the API's `detail` message into something the UI can put
 * in front of a person.
 */
import type { z } from 'zod';

import {
  HealthSchema,
  LiveConfigSchema,
  LiveEventsSchema,
  LiveStatsSchema,
  ModelEvaluationSchema,
  ModelSchema,
  ScanResultSchema,
  ScanSamplesSchema,
} from './schemas';

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

async function request<T extends z.ZodTypeAny>(
  path: string,
  schema: T,
  init?: RequestInit,
): Promise<z.infer<T>> {
  const response = await fetch(path, init);

  if (!response.ok) {
    throw new ApiError(await errorMessage(response), response.status);
  }

  return schema.parse(await response.json());
}

async function errorMessage(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === 'string') return body.detail;
    // A validation error's detail is a list of field problems, not a sentence.
    if (Array.isArray(body.detail)) return JSON.stringify(body.detail);
  } catch {
    // Not JSON. The status line is all we have.
  }
  return `Request failed with status ${response.status}`;
}

function postJson(body: unknown): RequestInit {
  return {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  };
}

export const getHealth = () => request('/api/health', HealthSchema);

export const getScanSamples = () => request('/api/scan/samples', ScanSamplesSchema);

export interface ScanRequest {
  sample: string;
  threshold?: number;
  format?: string;
  timeout_minutes?: number;
}

export const runScan = (body: ScanRequest) =>
  request('/api/scan', ScanResultSchema, postJson(body));

export function uploadScan(file: File, threshold?: number) {
  const form = new FormData();
  form.append('file', file);
  if (threshold !== undefined) form.append('threshold', String(threshold));
  return request('/api/scan/upload', ScanResultSchema, { method: 'POST', body: form });
}

export type ExportFormat = 'json' | 'html' | 'nginx' | 'cloudflare';

export async function exportScan(results: unknown, format: ExportFormat): Promise<string> {
  const response = await fetch('/api/scan/export', postJson({ results, format }));
  if (!response.ok) throw new ApiError(await errorMessage(response), response.status);
  return response.text();
}

export const getModel = () => request('/api/model', ModelSchema);

export const evaluateModel = (dataset: string, threshold: number) =>
  request('/api/model/evaluate', ModelEvaluationSchema, postJson({ dataset, threshold }));

export const getLiveStats = () => request('/api/live/stats', LiveStatsSchema);

export const getLiveEvents = (limit = 100) =>
  request(`/api/live/events?limit=${limit}`, LiveEventsSchema);

export const getLiveConfig = () => request('/api/live/config', LiveConfigSchema);

export const setBlockThreshold = (blockThreshold: number | null) =>
  request('/api/live/config', LiveConfigSchema, {
    method: 'PUT',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ block_threshold: blockThreshold }),
  });
