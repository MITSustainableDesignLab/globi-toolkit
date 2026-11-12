<script lang="ts">
  /**
   * Willingness to pay (prototype): how this region decides to retrofit. The user picks the
   * area unit (any GIS column), gives each area its data as a CSV, says where each model input
   * comes from and how it is encoded, and enters the coefficients. Saved to use-cases/wtp.yml;
   * the check runs the mapping on the saved buildings and lists what still stops a run.
   */
  import { onMount } from 'svelte';
  import Icon from '../components/Icon.svelte';
  import Select from '../components/Select.svelte';
  import { fetchWtp, loadWtpExample, saveWtp, uploadAreaTable, type WtpStatus } from '../api/wtp';
  import { fromConfig, newArea, newInput, toConfig, type Source, type WtpForm } from '../utils/wtp';
  import { navigateTo } from '../stores/projectStore';

  let status: WtpStatus | null = null;
  let form: WtpForm = fromConfig(null);
  let error: string | null = null;
  let saving = false;

  const SOURCES: Array<{ value: Source; label: string }> = [
    { value: 'building', label: 'Building (GIS column)' },
    { value: 'results', label: 'GloBI result' },
    { value: 'area', label: 'Area table' },
    { value: 'value', label: 'Fixed value' },
  ];

  function show(next: WtpStatus) {
    status = next;
    form = fromConfig(next.config);
    error = null;
  }

  async function run(step: () => Promise<WtpStatus>, failure: string) {
    try {
      show(await step());
    } catch (e) {
      error = e instanceof Error ? e.message : failure;
    }
  }

  onMount(() => run(fetchWtp, 'Failed to read the project.'));

  async function save() {
    saving = true;
    await run(() => saveWtp(toConfig(form)), 'Saving failed.');
    saving = false;
  }

  async function loadExample() {
    if (status?.config && !confirm('Replace this model with the worked example (Massachusetts, Berzolla et al. 2025)?')) return;
    await run(loadWtpExample, 'Loading the example failed.');
  }

  async function upload(event: Event) {
    const file = (event.currentTarget as HTMLInputElement).files?.[0];
    if (!file) return;
    const draft = toConfig(form); // keep unsaved edits across the reload
    await run(async () => ({ ...(await uploadAreaTable(file)), config: draft }), 'Upload failed.');
  }

  const options = (values: string[]) => values.map((v) => ({ value: v, label: v }));
  $: buildingColumns = options((status?.buildingColumns ?? []).map((c) => c.name));
  $: resultColumns = (status?.resultColumns ?? []).map((c) => ({ value: c.name, label: `${c.name}: ${c.description}` }));
  $: tableFiles = options((status?.tables ?? []).map((t) => t.file));
  $: areaNames = options(form.areas.map((a) => a.name).filter(Boolean));
  const tableColumns = (file: string) => options(status?.tables.find((t) => t.file === file)?.columns ?? []);
  const areaFile = (name: string) => form.areas.find((a) => a.name === name)?.file ?? '';
  const distributions = (name: string) => options((form.areas.find((a) => a.name === name)?.distributions ?? []).map((d) => d.name).filter(Boolean));
  $: typologies = status?.buildingColumns.find((c) => c.name === form.typologyColumn)?.values ?? [];
  /** A building column's categories, as a map to fill in. */
  function mapTemplate(row: { column: string; map: string }) {
    const values = status?.buildingColumns.find((c) => c.name === row.column)?.values ?? [];
    row.map = values.map((v) => `${v}: `).join('\n');
    form = form;
  }
</script>

<section class="workflow">
  <div class="workflow-header">
    <h1>Willingness to pay <span class="badge">prototype</span></h1>
    <p class="hint">
      How likely each building is to accept a retrofit at its cost. Homes use your region's model (a logistic
      regression); commercial buildings accept when the savings pay back the cost. Split your buildings into areas of
      your choice with a column in your GIS (any unit: tract, district, municipality…), give each area its data as a CSV,
      then say where each model input comes from.
    </p>
  </div>

  {#if status}
    <div class="panel" role="status">
      {#if status.check.ready}
        <p class="status-ok"><Icon name="check" size={14} /><span>Ready: {status.check.residential} homes and {status.check.commercial} commercial buildings can be scored once GloBI results exist.</span></p>
      {:else}
        <p class="status-warn"><Icon name="alert" size={14} /><span>Still needed before this can run:</span></p>
        <ul class="problems">{#each status.check.problems as problem}<li>{problem}</li>{/each}</ul>
      {/if}
      <div class="actions">
        <button type="button" class="btn" onclick={save} disabled={saving}>{saving ? 'Saving…' : 'Save and check'}</button>
        <button type="button" class="btn btn-secondary" onclick={loadExample}>Load the worked example</button>
        {#if status.buildingColumns.length === 0}
          <button type="button" class="btn btn-link" onclick={() => navigateTo('gis-cleaning')}>Save buildings first (GIS Cleaning)</button>
        {/if}
      </div>
      {#if error}<p class="error" role="alert">{error}</p>{/if}
    </div>

    <div class="panel">
      <h2>1. Area tables</h2>
      <p class="hint">
        One CSV per data source, one row per area. Join it to the buildings on the GIS column that holds the area code.
        A distribution lists, for each value, the count columns behind it (e.g. <code>12.5: income_10k_to_14999</code>); each
        home draws its household from its area's counts.
      </p>
      <label class="field">Add a CSV <input type="file" accept=".csv" onchange={upload} /></label>
      {#each form.areas as area, i}
        <div class="card">
          <div class="row">
            <label class="field">Name<input type="text" bind:value={area.name} placeholder="e.g. districts" /></label>
            <div class="field"><span>File</span><Select value={area.file} options={tableFiles} onChange={(v) => (area.file = v)} ariaLabel="File" /></div>
            <div class="field"><span>Building column</span><Select value={area.joinBuilding} options={buildingColumns} onChange={(v) => (area.joinBuilding = v)} ariaLabel="Building column" /></div>
            <div class="field"><span>Matches table column</span><Select value={area.joinTable} options={tableColumns(area.file)} onChange={(v) => (area.joinTable = v)} ariaLabel="Table column" /></div>
            <button type="button" class="btn btn-link" onclick={() => (form.areas = form.areas.filter((_, j) => j !== i))}>Remove</button>
          </div>
          {#each area.distributions as dist, d}
            <div class="row">
              <label class="field">Distribution<input type="text" bind:value={dist.name} placeholder="e.g. income_k" /></label>
              <label class="field grow">Value: count columns, one per line<textarea rows="3" bind:value={dist.text}></textarea></label>
              <button type="button" class="btn btn-link" onclick={() => (area.distributions = area.distributions.filter((_, k) => k !== d))}>Remove</button>
            </div>
          {/each}
          <button type="button" class="btn btn-link" onclick={() => (area.distributions = [...area.distributions, { name: '', text: '' }])}>Add a distribution</button>
        </div>
      {/each}
      <button type="button" class="btn btn-link" onclick={() => (form.areas = [...form.areas, newArea()])}>Add an area table</button>
    </div>

    <div class="panel">
      <h2>2. Model inputs</h2>
      <p class="hint">
        Each input feeds the model as <em>coefficient × value</em>. Encode it the way the model was fitted: a map turns
        categories into numbers, scale changes units (0.001 for USD → k$), bins turn numbers into ordinal classes.
      </p>
      <label class="field narrow">Intercept<input type="text" inputmode="decimal" bind:value={form.intercept} /></label>
      {#each form.inputs as input, i}
        <div class="card">
          <div class="row">
            <label class="field">Name<input type="text" bind:value={input.name} placeholder="e.g. upfront_cost" /></label>
            <div class="field"><span>Comes from</span><Select value={input.source} options={SOURCES} onChange={(v) => (input.source = v as Source)} ariaLabel="Source" /></div>
            {#if input.source === 'building'}
              <div class="field"><span>Column</span><Select value={input.column} options={buildingColumns} onChange={(v) => (input.column = v)} ariaLabel="Column" /></div>
            {:else if input.source === 'results'}
              <div class="field grow"><span>Result</span><Select value={input.column} options={resultColumns} onChange={(v) => (input.column = v)} ariaLabel="Result" /></div>
            {:else if input.source === 'area'}
              <div class="field"><span>Table</span><Select value={input.table} options={areaNames} onChange={(v) => (input.table = v)} ariaLabel="Area table" /></div>
              <div class="field"><span>Per area</span><Select value={input.areaKind} options={[{ value: 'column', label: 'One value' }, { value: 'sample', label: 'Drawn per household' }]} onChange={(v) => (input.areaKind = v as 'column' | 'sample')} ariaLabel="Kind" /></div>
              {#if input.areaKind === 'sample'}
                <div class="field"><span>Distribution</span><Select value={input.sample} options={distributions(input.table)} onChange={(v) => (input.sample = v)} ariaLabel="Distribution" /></div>
              {:else}
                <div class="field"><span>Column</span><Select value={input.column} options={tableColumns(areaFile(input.table))} onChange={(v) => (input.column = v)} ariaLabel="Area column" /></div>
              {/if}
            {:else}
              <label class="field narrow">Value<input type="text" inputmode="decimal" bind:value={input.value} /></label>
            {/if}
            <label class="field narrow">Coefficient<input type="text" inputmode="decimal" bind:value={input.coefficient} /></label>
            <button type="button" class="btn btn-link" onclick={() => (form.inputs = form.inputs.filter((_, j) => j !== i))}>Remove</button>
          </div>
          <details open={Boolean(input.map || input.scale || input.bins)}>
            <summary>Encoding</summary>
            <div class="row">
              <label class="field grow">
                Map: category: number, one per line
                <textarea rows="3" bind:value={input.map}></textarea>
                {#if input.source === 'building' && !input.map}
                  <button type="button" class="btn btn-link" onclick={() => mapTemplate(input)}>List this column's categories</button>
                {/if}
              </label>
              <label class="field narrow">Scale<input type="text" inputmode="decimal" bind:value={input.scale} placeholder="1" /></label>
              <label class="field">Bins<input type="text" bind:value={input.bins} placeholder="e.g. 25, 50, 75" /></label>
            </div>
          </details>
        </div>
      {/each}
      <button type="button" class="btn btn-link" onclick={() => (form.inputs = [...form.inputs, newInput()])}>Add an input</button>
    </div>

    <div class="panel">
      <h2>3. Who decides how</h2>
      <div class="row">
        <div class="field"><span>Typology column</span><Select value={form.typologyColumn} options={buildingColumns} onChange={(v) => (form.typologyColumn = v)} ariaLabel="Typology column" /></div>
        <label class="field narrow">Households drawn per home<input type="text" inputmode="numeric" bind:value={form.samples} /></label>
      </div>
      <fieldset class="field">
        <legend>Homes (scored with the model above)</legend>
        <div class="checks-row">{#each typologies as t}<label><input type="checkbox" value={t} bind:group={form.residentialTypologies} /> {t}</label>{/each}</div>
      </fieldset>
      <fieldset class="field">
        <legend>Commercial (accept when the savings repay the cost)</legend>
        <div class="checks-row">{#each typologies as t}<label><input type="checkbox" value={t} bind:group={form.commercialTypologies} /> {t}</label>{/each}</div>
      </fieldset>
      <div class="row">
        <label class="field narrow">Years of savings<input type="text" inputmode="numeric" bind:value={form.years} /></label>
        <label class="field narrow">Discount rate<input type="text" inputmode="decimal" bind:value={form.discountRate} placeholder="e.g. 0.07" /></label>
        <label class="field narrow">Energy price growth / yr<input type="text" inputmode="decimal" bind:value={form.priceGrowth} /></label>
        <label class="field narrow">Accept when NPV ≥ (USD)<input type="text" inputmode="decimal" bind:value={form.threshold} /></label>
      </div>
    </div>
  {:else if error}
    <p class="error" role="alert">{error}</p>
  {/if}
</section>

<style>
  .badge { font-size: var(--text-xs); font-weight: normal; padding: 0 var(--space-2); border: 1px solid var(--border); border-radius: var(--radius); vertical-align: middle; }
  .card { border: 1px solid var(--border); border-radius: var(--radius); padding: var(--space-3); margin-bottom: var(--space-3); }
  .row { display: flex; gap: var(--space-3); flex-wrap: wrap; align-items: flex-end; margin-bottom: var(--space-2); }
  .row .field { flex: 1 1 10em; min-width: 0; }
  .row .field.narrow, .field.narrow { flex: 0 1 9em; }
  .row .field.grow { flex: 2 1 16em; }
  .problems { margin: 0 0 var(--space-3); padding-left: var(--space-5); font-size: var(--text-sm); }
  .checks-row { display: flex; gap: var(--space-3); flex-wrap: wrap; font-size: var(--text-sm); }
  fieldset.field { border: none; padding: 0; margin: 0 0 var(--space-3); }
  textarea { width: 100%; font-family: var(--font-mono, monospace); }
</style>
