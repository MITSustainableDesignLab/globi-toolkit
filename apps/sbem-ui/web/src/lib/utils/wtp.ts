/**
 * The Willingness to pay form <-> use-cases/wtp.yml (see globi_toolkit.use_cases.wtp).
 * The form keeps numbers as typed text, so a blank stays blank (saved as null) and the
 * server's check names it, instead of the form inventing a value.
 */

export type Source = 'building' | 'results' | 'area' | 'value';

export interface InputRow {
  name: string;
  source: Source;
  /** building / results / area column */
  column: string;
  table: string;
  /** area inputs: one value per area, or drawn from one of its distributions */
  areaKind: 'column' | 'sample';
  sample: string;
  value: string;
  coefficient: string;
  scale: string;
  /** "25, 50, 75" */
  bins: string;
  /** one "category: number" per line */
  map: string;
}

export interface AreaRow {
  name: string;
  file: string;
  joinBuilding: string;
  joinTable: string;
  /** one "value: count column, count column" per line */
  distributions: Array<{ name: string; text: string }>;
}

export interface WtpForm {
  typologyColumn: string;
  seed: string;
  intercept: string;
  residentialTypologies: string[];
  samples: string;
  commercialTypologies: string[];
  years: string;
  discountRate: string;
  priceGrowth: string;
  threshold: string;
  areas: AreaRow[];
  inputs: InputRow[];
}

type Raw = Record<string, any>;

export const newInput = (): InputRow => ({
  name: '', source: 'building', column: '', table: '', areaKind: 'column', sample: '', value: '',
  coefficient: '', scale: '', bins: '', map: '',
});

export const newArea = (): AreaRow => ({ name: '', file: '', joinBuilding: '', joinTable: '', distributions: [] });

const text = (v: unknown) => (v === null || v === undefined ? '' : String(v));

/** '' -> null; otherwise the number, or the text as typed so the check can say it isn't one. */
export function num(value: string): number | string | null {
  const trimmed = value.trim();
  if (trimmed === '') return null;
  const n = Number(trimmed);
  return Number.isNaN(n) ? trimmed : n;
}

export function parseLines(value: string, parse: (right: string) => unknown): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const line of value.split('\n')) {
    const at = line.lastIndexOf(':');
    if (at <= 0) continue;
    out[line.slice(0, at).trim()] = parse(line.slice(at + 1).trim());
  }
  return out;
}

const columnsOf = (right: string) => right.split(',').map((c) => c.trim()).filter(Boolean);

export function formatLines(value: Record<string, unknown> | null | undefined): string {
  return Object.entries(value ?? {})
    .map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(', ') : v}`)
    .join('\n');
}

export function fromConfig(raw: Raw | null): WtpForm {
  const residential = raw?.residential ?? {};
  const commercial = raw?.commercial ?? {};
  const coefficients = residential.model?.coefficients ?? {};
  return {
    typologyColumn: text(raw?.typology_column ?? 'Typology'),
    seed: text(raw?.seed ?? 42),
    intercept: text(residential.model?.intercept),
    residentialTypologies: residential.typologies ?? [],
    samples: text(residential.monte_carlo_samples ?? 100),
    commercialTypologies: commercial.typologies ?? [],
    years: text(commercial.years),
    discountRate: text(commercial.discount_rate),
    priceGrowth: text(commercial.price_growth ?? 0),
    threshold: text(commercial.threshold_usd ?? 0),
    areas: Object.entries(raw?.areas ?? {}).map(([name, a]: [string, any]) => ({
      name,
      file: text(a.file),
      joinBuilding: text(a.join?.building),
      joinTable: text(a.join?.table),
      distributions: Object.entries(a.distributions ?? {}).map(([d, spec]) => ({ name: d, text: formatLines(spec as Raw) })),
    })),
    inputs: Object.entries(raw?.inputs ?? {}).map(([name, i]: [string, any]) => ({
      ...newInput(),
      name,
      source: i.source,
      column: text(i.column),
      table: text(i.table),
      areaKind: i.sample ? 'sample' : 'column',
      sample: text(i.sample),
      value: text(i.value),
      coefficient: text(coefficients[name]),
      scale: i.scale === undefined || i.scale === 1 ? '' : text(i.scale),
      bins: (i.bins ?? []).join(', '),
      map: formatLines(i.map),
    })),
  };
}

function inputToConfig(row: InputRow): Raw {
  const out: Raw = { source: row.source };
  if (row.source === 'value') out.value = num(row.value);
  else if (row.source === 'area') {
    out.table = row.table;
    if (row.areaKind === 'sample') out.sample = row.sample;
    else out.column = row.column;
  } else out.column = row.column;
  if (row.map.trim()) out.map = parseLines(row.map, num);
  if (row.scale.trim()) out.scale = num(row.scale);
  if (row.bins.trim()) out.bins = row.bins.split(',').map(num);
  return out;
}

/** The form as wtp.yml; unnamed rows are left out. */
export function toConfig(form: WtpForm): Raw {
  const inputs = form.inputs.filter((i) => i.name.trim());
  return {
    typology_column: form.typologyColumn,
    seed: num(form.seed),
    areas: Object.fromEntries(
      form.areas.filter((a) => a.name.trim()).map((a) => [
        a.name.trim(),
        {
          file: a.file,
          join: { building: a.joinBuilding, table: a.joinTable },
          distributions: Object.fromEntries(
            a.distributions.filter((d) => d.name.trim()).map((d) => [d.name.trim(), parseLines(d.text, columnsOf)])
          ),
        },
      ])
    ),
    inputs: Object.fromEntries(inputs.map((i) => [i.name.trim(), inputToConfig(i)])),
    residential: {
      typologies: form.residentialTypologies,
      monte_carlo_samples: num(form.samples),
      model: {
        type: 'logit',
        intercept: num(form.intercept),
        coefficients: Object.fromEntries(inputs.map((i) => [i.name.trim(), num(i.coefficient)])),
      },
    },
    commercial: {
      typologies: form.commercialTypologies,
      years: num(form.years),
      discount_rate: num(form.discountRate),
      price_growth: num(form.priceGrowth),
      threshold_usd: num(form.threshold),
    },
  };
}
