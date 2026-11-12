// The Willingness to pay form round-trips wtp.yml, and leaves blanks for the check to name.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { fromConfig, num, parseLines, toConfig } from '../src/lib/utils/wtp.ts';

const config = {
  typology_column: 'Typology',
  seed: 42,
  areas: {
    tracts: {
      file: 'census.csv',
      join: { building: 'tract_id', table: 'geoid' },
      distributions: { income_k: { '5': ['income_less_than_10k'], '12.5': ['a', 'b'] } },
    },
  },
  inputs: {
    year_built: { source: 'building', column: 'Age_bracket', map: { post_2003: 1, pre_1975: 3.5 } },
    income: { source: 'area', table: 'tracts', sample: 'income_k', bins: [25, 50] },
    concern: { source: 'area', table: 'tracts', column: 'concern' },
    upfront_cost: { source: 'results', column: 'net_cost_usd', scale: 0.001 },
    neighbor: { source: 'value', value: 1 },
  },
  residential: {
    typologies: ['Residential'],
    monte_carlo_samples: 100,
    model: { type: 'logit', intercept: 0, coefficients: { year_built: -0.051, income: 0.046, concern: 0.271, upfront_cost: -0.058, neighbor: -0.029 } },
  },
  commercial: { typologies: ['Office'], years: 15, discount_rate: 0.07, price_growth: 0.02, threshold_usd: 0 },
};

test('wtp.yml survives a trip through the form', () => {
  assert.deepEqual(toConfig(fromConfig(config)), config);
});

test('blanks stay blank, typos stay as typed, unnamed rows are dropped', () => {
  const form = fromConfig(null);
  form.inputs.push({ ...fromConfig(config).inputs[0], coefficient: '' }, { ...fromConfig(config).inputs[0], name: ' ' });
  const raw = toConfig(form);
  assert.equal(raw.residential.model.intercept, null);
  assert.deepEqual(raw.residential.model.coefficients, { year_built: null });
  assert.equal(num('0.o7'), '0.o7');
});

test('lines are split on their last colon', () => {
  assert.deepEqual(parseLines('a: 1\nbad line\nb:c: 2', num), { a: 1, 'b:c': 2 });
});
