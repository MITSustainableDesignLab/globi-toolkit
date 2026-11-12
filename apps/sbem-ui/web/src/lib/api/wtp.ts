/** Willingness to pay: the project's use-cases/wtp.yml, its area tables, and its check. */

export interface WtpStatus {
  config: Record<string, any> | null;
  /** The saved buildings' GIS columns; `values` lists them when there are few enough to map. */
  buildingColumns: Array<{ name: string; values: string[] }>;
  /** What the toolkit computes per building from GloBI results and costs. */
  resultColumns: Array<{ name: string; description: string }>;
  /** CSVs in use-cases/, one row per area. */
  tables: Array<{ file: string; columns: string[] }>;
  check: { ready: boolean; problems: string[]; residential: number; commercial: number };
}

async function request(input: string, init?: RequestInit): Promise<WtpStatus> {
  const response = await fetch(input, init);
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(typeof payload?.detail === 'string' ? payload.detail : `Request to ${input} failed (${response.status}).`);
  }
  return response.json();
}

export const fetchWtp = () => request('/api/wtp');

export const saveWtp = (config: Record<string, any>) =>
  request('/api/wtp', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(config) });

export function uploadAreaTable(file: File) {
  const body = new FormData();
  body.append('file', file);
  return request('/api/wtp/tables', { method: 'POST', body });
}

export const loadWtpExample = () => request('/api/wtp/example', { method: 'POST' });
