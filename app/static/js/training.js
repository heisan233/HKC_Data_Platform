(() => {
  'use strict';
  const api = '/training/api';
  const state = {tab: 'tasks', step: 0, catalog: null, checked: null, selectedDS: '', selectedModel: 'xvla', preset: 'standard', executor: 'primary', runtimeId: '', name: '', taskId: null, detailData: null, showDeleted: false, purgingTaskId: null, custom: false, customParams: {}, detailTab: 'metrics', lossLog: true, logsPinned: false, logBefore: 0, logSize: 0, detailLoading: false, tasksLoading: false, detailRequest: 0};
  const $ = (id) => document.getElementById(id);
  const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const label = {QUEUED:'排队',PREPARING:'准备环境',RUNNING:'训练',EVALUATING:'评估',PACKAGING:'打包',COMPLETED:'已完成',FAILED:'失败',CANCELED:'已取消',INTERRUPTED:'中断'};
  async function request(path, options = {}) {
    const res = await fetch(api + path, {credentials:'same-origin', cache:'no-store', headers:{'Content-Type':'application/json', ...(options.headers || {})}, ...options});
    if (!res.ok) {let detail = `请求失败（${res.status}）`; try {detail = (await res.json()).detail || detail;} catch (_) {} throw new Error(detail);}
    return res.json();
  }
  function message(text) {const node = $('train-message'); node.textContent = text || ''; node.hidden = !text;}
  function metricNumber(value) {
    if (value === null || value === undefined || value === '') return null;
    const number = Number(value);
    return Number.isFinite(number) ? number.toFixed(3) : null;
  }
  function trainingMetric(task) {
    const metric = task.latest_metric || {};
    const loss = metricNumber(metric.training_loss);
    const step = Number(metric.step);
    const total = Number(metric.total_steps);
    return {
      loss,
      step: Number.isFinite(step) && step >= 0 ? step : null,
      total: Number.isFinite(total) && total > 0 ? total : null,
      learningRate: metric.learning_rate !== null && metric.learning_rate !== undefined && Number.isFinite(Number(metric.learning_rate)) ? Number(metric.learning_rate) : null,
      gradientNorm: metricNumber(metric.gradient_norm),
      epoch: metricNumber(metric.epoch),
      samples: metric.samples_label || null,
    };
  }
  function validationLoss(evaluation) {
    return metricNumber(evaluation?.metrics?.validation_forward_loss);
  }
  function metricSummary(task) {
    const training = trainingMetric(task);
    const validation = validationLoss(task.evaluation);
    if (task.status === 'COMPLETED' && validation !== null) {
      return `<span class="train-summary-label">离线验证损失</span><strong class="train-summary-value">${validation}</strong>${training.loss !== null ? `<small>最终训练损失 ${training.loss}</small>` : ''}`;
    }
    if (training.loss !== null) {
      const progress = training.step !== null ? `第 ${training.step}${training.total !== null ? ` / ${training.total}` : ''} 步` : '最新记录';
      return `<span class="train-summary-label">${task.status === 'COMPLETED' ? '最终训练损失' : '训练损失'}</span><strong class="train-summary-value">${training.loss}</strong><small>${esc(progress)}</small>`;
    }
    return '<span class="train-muted">暂无指标</span>';
  }
  function evaluationView(evaluation) {
    if (!evaluation) return '<p class="train-muted">训练尚未完成评估。</p>';
    const loss = validationLoss(evaluation);
    const ids = Array.isArray(evaluation.validation_episode_ids) ? evaluation.validation_episode_ids : [];
    const frames = Number(evaluation.validation_frames);
    const frameText = Number.isFinite(frames) && frames >= 0 ? `${frames} 帧` : '帧数未记录';
    const episodes = ids.length ? `${ids.length} 个 Episode` : 'Episode 未记录';
    const smokePassed = evaluation.checkpoint_smoke_test === true;
    const cameraMap = evaluation.camera_rename_map && typeof evaluation.camera_rename_map === 'object'
      ? Object.entries(evaluation.camera_rename_map) : [];
    return `<div class="train-eval-summary">
      <div class="train-eval-primary"><span>离线验证前向损失</span><strong>${loss === null ? '—' : loss}</strong><small>在未参与训练的 Episode 上计算</small></div>
      <div class="train-eval-check"><span>模型加载与推理检查</span><strong class="${smokePassed ? 'train-ok' : 'train-error'}">${smokePassed ? '已通过' : '未通过'}</strong><small>checkpoint 冒烟测试</small></div>
      <div class="train-eval-check"><span>验证数据</span><strong>${esc(episodes)}</strong><small>${esc(frameText)}</small></div>
    </div>
    <p class="train-eval-caution">当前只评估最终 checkpoint，因此这里是一次最终结果，不是训练期间的验证曲线。离线前向损失不是动作 MAE，也不代表真实机器人任务成功率。</p>
    <details class="train-eval-details"><summary>查看评估说明与技术信息</summary>
      <dl><div><dt>验证 Episode</dt><dd>${ids.length ? esc(ids.join('、')) : '未记录'}</dd></div>
      <div><dt>指标定义</dt><dd>${esc(evaluation.metric_definition || '留出 Episode 的平均前向损失')}</dd></div>
      <div><dt>相机映射</dt><dd>${cameraMap.length ? cameraMap.map(([source, target]) => `${esc(source)} → ${esc(target)}`).join('；') : '未记录'}</dd></div></dl>
    </details>`;
  }
  function compactNumber(value) {
    if (!Number.isFinite(value)) return '—';
    if (value !== 0 && Math.abs(value) < 0.001) return value.toExponential(2);
    if (Math.abs(value) >= 1000) return value.toLocaleString('zh-CN', {maximumFractionDigits: 1});
    return value.toLocaleString('zh-CN', {maximumFractionDigits: 3});
  }
  function chartSvg(raw, key, title, color, options = {}) {
    const points = (raw || []).filter((point) => point && point[key] !== null && point[key] !== undefined && point[key] !== '').map((point, index) => ({x: options.time ? index : Number(point.step), y: Number(point[key]), source: point})).filter((point) => Number.isFinite(point.x) && Number.isFinite(point.y));
    if (points.length < 2) return `<div class="train-chart-empty">${esc(options.empty || '尚未采集到足够的真实指标点')}</div>`;
    points.sort((a, b) => a.x - b.x);
    const minX = points[0].x, maxX = points[points.length - 1].x;
    const values = points.map((point) => point.y);
    const logarithmic = options.log && values.every((value) => value > 0);
    const scaled = (value) => logarithmic ? Math.log10(value) : value;
    const minY = Math.min(...values), maxY = Math.max(...values);
    const low = scaled(minY), high = scaled(maxY);
    const x = (value) => 52 + (value - minX) / Math.max(1, maxX - minX) * 558;
    const y = (value) => 185 - (scaled(value) - low) / Math.max(0.0000001, high - low) * 162;
    const ticks = [0, 1, 2, 3, 4].map((index) => {
      const fraction = index / 4;
      const tickValue = logarithmic ? 10 ** (low + (high - low) * (1 - fraction)) : maxY - (maxY - minY) * fraction;
      const position = 23 + fraction * 162;
      return `<line x1="52" y1="${position}" x2="610" y2="${position}" class="train-chart-gridline"/><text x="45" y="${position + 4}" text-anchor="end" class="train-chart-axis">${esc(compactNumber(tickValue))}</text>`;
    }).join('');
    const line = points.map((point) => `${x(point.x).toFixed(1)},${y(point.y).toFixed(1)}`).join(' ');
    let smooth = '';
    if (options.smooth) {
      smooth = points.map((point, index) => {
        const window = points.slice(Math.max(0, index - 4), index + 1);
        const mean = window.reduce((sum, item) => sum + item.y, 0) / window.length;
        return `${x(point.x).toFixed(1)},${y(mean).toFixed(1)}`;
      }).join(' ');
    }
    const sampleEvery = Math.max(1, Math.ceil(points.length / 80));
    const dots = points.filter((_, index) => index % sampleEvery === 0 || index === points.length - 1).map((point) => {
      const place = options.time ? `采样 ${point.x + 1}` : `第 ${point.x} 步`;
      const extra = options.time && point.source.used_gb !== undefined ? `，显存 ${compactNumber(Number(point.source.used_gb))} GB` : '';
      return `<circle cx="${x(point.x).toFixed(1)}" cy="${y(point.y).toFixed(1)}" r="3" fill="${color}" opacity="0.7"><title>${esc(place)}：${esc(compactNumber(point.y))}${esc(options.unit || '')}${esc(extra)}</title></circle>`;
    }).join('');
    const footer = options.time ? `采样 ${points.length} 次 · 整卡使用率，不代表此任务独占` : `Step ${minX}–${maxX} · 范围 ${compactNumber(minY)}–${compactNumber(maxY)}`;
    return `<svg class="train-metric-chart" viewBox="0 0 630 220" role="img" aria-label="${esc(title)}曲线">${ticks}<line x1="52" y1="185" x2="610" y2="185" class="train-chart-axis-line"/><polyline points="${line}" fill="none" stroke="${color}" stroke-width="${options.smooth ? 1.5 : 2.4}" opacity="${options.smooth ? 0.38 : 1}" stroke-linecap="round" stroke-linejoin="round"/>${smooth ? `<polyline points="${smooth}" fill="none" stroke="${color}" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>` : ''}${dots}<text x="52" y="210" class="train-chart-axis">${esc(options.time ? '开始' : String(minX))}</text><text x="610" y="210" text-anchor="end" class="train-chart-axis">${esc(options.time ? '最近' : String(maxX))}</text></svg><p class="train-chart-caption">${esc(footer)}${logarithmic ? ' · 对数纵轴' : ''}${options.smooth ? ' · 浅线为原始值，深线为 5 点平滑' : ''}</p>`;
  }
  function chartCard(title, raw, key, color, options = {}) {
    return `<article class="train-chart-card"><div class="train-chart-heading"><h3>${esc(title)}</h3><span>${esc(options.subtitle || '真实采集')}</span></div>${chartSvg(raw, key, title, color, options)}</article>`;
  }
  function show(tab) {
    state.tab = tab; message('');
    for (const name of ['tasks','wizard','detail','models','environment']) $('train-' + name).hidden = tab !== name;
    document.querySelectorAll('.train-tabs button').forEach((button) => button.classList.toggle('active', button.dataset.tab === tab));
    if (tab === 'tasks') loadTasks(); if (tab === 'models') loadVersions(); if (tab === 'environment') loadEnvironment();
  }
  async function loadCatalog() {
    state.catalog = await request('/catalog');
    const options = state.catalog.datasets.map((ds) => `<option value="${esc(ds.id)}">${esc(ds.id)} · ${esc(ds.name)}</option>`).join('');
    $('train-dataset-filter').innerHTML = '<option value="">全部数据集</option>' + options;
  }
  async function loadTasks() {
    if (state.tasksLoading) return;
    state.tasksLoading = true;
    const params = new URLSearchParams();
    for (const [key, id] of [['status','train-status'],['model_id','train-model-filter'],['dataset_id','train-dataset-filter']]) if ($(id).value) params.set(key, $(id).value);
    if (state.showDeleted) params.set('deleted', 'true');
    $('train-trash-toggle').textContent = state.showDeleted ? '返回任务列表' : '回收站';
    $('train-trash-toggle').setAttribute('aria-pressed', String(state.showDeleted));
    $('train-task-subtitle').textContent = state.showDeleted ? '回收站任务可恢复；永久删除会一并清除该任务日志、模型包及残留训练文件，且无法撤销。' : '查看训练进度、失败原因和指标摘要。';
    try {
      const rows = await request('/tasks?' + params);
      $('train-task-rows').innerHTML = rows.length ? rows.map((task) => {
        const status = label[task.status] || task.status;
        const actions = state.showDeleted
          ? `<button data-restore-task="${esc(task.id)}" ${state.purgingTaskId===task.id?'disabled':''}>恢复任务</button><button class="train-delete-link" data-purge-task="${esc(task.id)}" title="永久删除任务及其专属产物，无法恢复" ${state.purgingTaskId===task.id?'disabled':''}>${state.purgingTaskId===task.id?'删除中…':'永久删除'}</button>`
          : `<button data-open-task="${esc(task.id)}">查看详情</button><button class="train-delete-link" data-delete-task="${esc(task.id)}" ${task.delete_requested_at?'disabled':''}>${task.delete_requested_at?'删除中…':'删除'}</button>`;
        return `<tr><td><strong>${esc(task.id)}</strong><small>${esc(task.name)}</small></td><td>${esc(task.dataset_id)}</td><td>X-VLA · 基座微调</td><td><span class="train-badge ${task.status==='FAILED'?'failed':task.status==='COMPLETED'?'done':''}">${esc(status)}</span>${task.delete_requested_at ? '<small>正在取消任务，结束后移入回收站</small>' : task.error ? `<small class="train-error">${esc(task.error)}</small>` : ''}</td><td>${esc(task.progress)}%</td><td>${metricSummary(task)}</td><td>${esc(task.started_at || task.created_at || '—')}</td><td><div class="train-row-actions">${actions}</div></td></tr>`;
      }).join('') : `<tr><td colspan="8" class="train-muted">${state.showDeleted ? '回收站为空。' : '暂无训练任务。只有真实提交的任务会显示在此处。'}</td></tr>`;
      $('train-list-updated').textContent = `自动更新 · ${new Date().toLocaleTimeString('zh-CN')}`;
    } catch (err) { $('train-list-updated').textContent = '自动更新失败'; message(err.message); }
    finally { state.tasksLoading = false; }
  }
  function selectedPreset() {
    const model = state.catalog?.models?.find((item) => item.id === 'xvla');
    return model?.presets?.find((item) => item.id === state.preset) || null;
  }
  function effectiveParams() {
    const base = selectedPreset()?.parameters || {};
    if (!state.custom) return {...base};
    const params = {...base};
    for (const key of ['steps', 'batch_size', 'log_freq']) {
      const value = state.customParams[key];
      if (value === '' || value === null || value === undefined || !Number.isInteger(Number(value))) throw new Error(`${key} 必须为整数`);
      params[key] = Number(value);
    }
    const schema = state.catalog.models.find((item) => item.id === 'xvla')?.parameter_schema || {};
    for (const key of ['steps', 'batch_size', 'log_freq']) {
      const rule = schema[key]; const value = params[key];
      if (!rule || (rule.choices && !rule.choices.includes(value)) || (rule.min !== undefined && value < rule.min) || (rule.max !== undefined && value > rule.max)) throw new Error(`${key} 超出执行器允许范围`);
    }
    if (params.log_freq > params.steps) throw new Error('指标记录间隔不能大于训练步数');
    return params;
  }
  function renderWizard() {
    document.querySelectorAll('.train-steps li').forEach((node, index) => node.classList.toggle('active', index === state.step));
    $('train-prev').hidden = state.step === 0; $('train-next').textContent = state.step === 3 ? '确认提交' : '下一步';
    const body = $('train-wizard-body'); const catalog = state.catalog;
    if (state.step === 0) {
      body.innerHTML = `<h3>选择已登记的数据集</h3><p class="train-note">只对最终选中的 DS 做轻量训前校验；来源 DS 的质检结论不能替代当前 DS。</p>${catalog.datasets.length ? catalog.datasets.map((ds) => `<label class="train-list-option"><input type="radio" name="dataset" value="${esc(ds.id)}" ${state.selectedDS===ds.id?'checked':''}> <strong>${esc(ds.id)}</strong> ${esc(ds.name)} <small> · ${esc(ds.storage_area)} · Episode ${esc(ds.episodes ?? '—')} · 帧 ${esc(ds.frames ?? '—')} · ${esc(ds.annotation_status)} · 质检 ${esc(ds.qc_label || ds.qc_state || '未知')} · 来源链 ${esc((ds.lineage || [ds.id]).join(' → '))}</small></label>`).join('') : '<p class="train-error">没有可选择的已登记数据集。</p>'}<div id="train-preflight"></div>`;
      body.querySelectorAll('input[name=dataset]').forEach((radio) => radio.addEventListener('change', () => {state.selectedDS = radio.value; state.checked = null; $('train-preflight').textContent='点击下一步运行轻量校验。';}));
      if (state.checked) renderPreflight();
    } else if (state.step === 1) {
      const model = catalog.models.find((m) => m.id === 'xvla');
      const presets = model?.presets || []; const available = !!model?.available;
      const base = selectedPreset()?.parameters || {};
      const rules = model?.parameter_schema || {};
      if (!Object.keys(state.customParams).length) state.customParams = {...base};
      const params = state.custom ? state.customParams : base;
      const editable = !!(rules.steps && rules.batch_size && rules.log_freq);
      body.innerHTML = `<h3>选择模型与训练参数</h3><div class="train-list-option"><strong>X-VLA · 基座微调</strong> <span class="train-badge ${available?'done':'failed'}">${available?'可用':'不可用'}</span><p class="train-note">基座版本：${esc(model?.version || '未登记')}。从零训练和 checkpoint 续训不在本期开放。</p>${available?'':`<p class="train-error">${esc(model?.reason || '执行器未配置')}</p>`}</div>
        <div class="train-param-toolbar"><label><span>训练预设</span><select id="train-preset">${presets.map((p) => `<option value="${esc(p.id)}" ${state.preset===p.id?'selected':''}>${esc(p.name || p.id)}</option>`).join('')}</select></label><label class="train-custom-toggle"><input id="train-custom-toggle" type="checkbox" ${state.custom?'checked':''} ${editable?'':'disabled'}><span>自定义基础参数</span></label></div>
        <div class="train-param-grid"><label><span>训练步数 steps</span><input id="train-param-steps" type="number" min="${esc(rules.steps?.min ?? 1)}" max="${esc(rules.steps?.max ?? 10000)}" step="1" value="${esc(params.steps ?? '')}" ${state.custom?'':'disabled'}><small>本执行器允许 ${esc(rules.steps?.min ?? '—')}–${esc(rules.steps?.max ?? '—')} 步；增加步数会延长训练时间。</small></label>
        <label><span>批大小 batch_size</span><select id="train-param-batch" ${state.custom?'':'disabled'}>${(rules.batch_size?.choices || [base.batch_size]).map((value) => `<option value="${esc(value)}" ${Number(params.batch_size)===value?'selected':''}>${esc(value)}</option>`).join('')}</select><small>当前已验证的任务使用 1；选择 2 可能显存不足，执行器会在资源不足时排队。</small></label>
        <label><span>指标记录间隔</span><select id="train-param-log" ${state.custom?'':'disabled'}>${(rules.log_freq?.choices || [base.log_freq]).map((value) => `<option value="${esc(value)}" ${Number(params.log_freq)===value?'selected':''}>每 ${esc(value)} 步</option>`).join('')}</select><small>间隔越小，图表点越密集；不会改变训练目标。</small></label></div>
        <p class="train-note">其余参数由已验证的执行器配置固定，不接受任意命令或路径。最终生效值将在提交前再次确认。</p>`;
      $('train-preset')?.addEventListener('change', (event) => {state.preset = event.target.value; state.customParams = {...selectedPreset()?.parameters}; renderWizard();});
      $('train-custom-toggle')?.addEventListener('change', (event) => {state.custom = event.target.checked; state.customParams = {...selectedPreset()?.parameters}; renderWizard();});
      for (const [id, key] of [['train-param-steps','steps'],['train-param-batch','batch_size'],['train-param-log','log_freq']]) $(id)?.addEventListener('change', (event) => {state.customParams[key] = event.target.value;});
    } else if (state.step === 2) {
      const env = catalog.environment; state.executor = env.id;
      const runtimes = Array.isArray(env.runtimes) ? env.runtimes : [];
      const compatible = runtimes.filter((runtime) => runtime.available && (runtime.model_ids || []).includes(state.selectedModel));
      if (!runtimes.some((runtime) => runtime.id === state.runtimeId && compatible.includes(runtime))) state.runtimeId = compatible.length === 1 ? compatible[0].id : '';
      body.innerHTML = `<h3>选择 Conda 训练环境</h3><p class="train-note">只显示已登记的训练环境。当前只有一个；今后登记新环境后，可在这里选择，不需要填写路径或激活命令。</p><div class="train-runtime-options" role="radiogroup" aria-label="Conda 训练环境">${runtimes.length ? runtimes.map((runtime) => {
        const supported = (runtime.model_ids || []).includes(state.selectedModel);
        const selectable = env.online && runtime.available && supported;
        const reason = !env.online ? env.reason : !supported ? '不支持当前模型' : runtime.reason || '可用于当前模型';
        return `<label class="train-list-option train-runtime-option ${selectable?'':'unavailable'}"><input type="radio" name="training-runtime" value="${esc(runtime.id)}" ${state.runtimeId===runtime.id?'checked':''} ${selectable?'':'disabled'}><span class="train-runtime-content"><strong>${esc(runtime.name)}</strong><small>${esc(runtime.kind === 'conda' ? 'Conda 虚拟环境' : '训练运行环境')} · 支持 ${(runtime.model_ids || []).map(esc).join('、') || '未登记模型'}</small><small>${esc(reason)}</small></span><span class="train-badge ${selectable?'done':'failed'}">${selectable?'可选':'不可选'}</span></label>`;
      }).join('') : '<p class="train-error">尚未登记训练环境。</p>'}</div><div class="train-runtime-resource"><strong>共享执行资源</strong><span>${esc(env.name)} · ${esc(env.gpu?.name || 'GPU 未提供')} · 空闲 ${esc(env.gpu?.free_gb ?? '—')} GB · 排队 ${esc(env.queue_count)} 个任务</span></div><p class="train-note">同一服务器上的 Conda 环境共用 GPU。执行器会在提交时重新检查资源；若 VLM 正在占用 GPU，请由授权操作员在“执行环境”页确认关闭。</p>`;
      body.querySelectorAll('input[name="training-runtime"]').forEach((radio) => radio.addEventListener('change', () => {state.runtimeId = radio.value;}));
    } else {
      const model = catalog.models.find((m) => m.id === 'xvla');
      const params = effectiveParams();
      const runtime = catalog.environment.runtimes?.find((item) => item.id === state.runtimeId);
      body.innerHTML = `<h3>确认提交</h3><label><span>任务名称</span><input id="train-name" maxlength="100" value="${esc(state.name)}" placeholder="例如：抓取任务微调"></label><div class="train-grid"><div>最终 DS<br><strong>${esc(state.selectedDS)}</strong><small>信息摘要 ${esc(state.checked?.snapshot?.info_sha256?.slice(0,16) || '—')}…</small></div><div>模型与方式<br><strong>X-VLA · 基座微调</strong><small>${esc(model?.version || '')}</small></div><div>数据划分<br><strong>${esc(state.checked?.split?.train_episode_ids?.length || 0)} 训练 / ${esc(state.checked?.split?.validation_episode_ids?.length || 0)} 验证 Episode</strong></div><div>Conda 环境 / 预设<br><strong>${esc(runtime?.name || state.runtimeId)} / ${esc(state.preset)}${state.custom?'（已调整）':''}</strong><small>执行器 ${esc(state.executor)}</small></div></div><div class="train-confirm-params"><strong>最终生效参数</strong><span>steps ${esc(params.steps)}</span><span>batch_size ${esc(params.batch_size)}</span><span>每 ${esc(params.log_freq)} 步记录指标</span><span>num_workers ${esc(params.num_workers)}</span></div><p class="train-note">提交后生成稳定 TR 编号；数据、基座、Conda 环境和参数随任务固定。训练完成并通过冒烟测试和离线评估后，才可下载模型包。</p>`;
      $('train-name').addEventListener('input', (event) => {state.name = event.target.value;});
    }
  }
  function renderPreflight() {
    const checked = state.checked; if (!checked) return;
    $('train-preflight').innerHTML = checked.passed ? `<p class="train-ok">训前校验通过：${esc(checked.dataset.episodes)} 个 Episode，${esc(checked.dataset.frames)} 帧；训练/验证按 Episode 划分。</p>` : `<p class="train-error">训前校验未通过：</p><ul class="train-error">${checked.issues.map((issue) => `<li>${esc(issue)}</li>`).join('')}</ul>`;
  }
  async function nextWizard() {
    try {
      if (state.step === 0) {if (!state.selectedDS) throw new Error('请先选择数据集'); state.checked = await request('/preflight', {method:'POST', body:JSON.stringify({dataset_id:state.selectedDS, model_id:'xvla'})}); renderPreflight(); if (!state.checked.passed) return;}
      if (state.step === 1) {const model = state.catalog.models.find((m) => m.id === 'xvla'); if (!model?.available || !state.preset) throw new Error(model?.reason || '基座模型未就绪'); effectiveParams();}
      if (state.step === 2) {
        const env = state.catalog.environment;
        const runtime = env.runtimes?.find((item) => item.id === state.runtimeId);
        if (!env.online || !runtime?.available || !(runtime.model_ids || []).includes(state.selectedModel)) throw new Error('请选择可用且支持当前模型的 Conda 训练环境');
      }
      if (state.step === 3) {if (!state.name.trim()) throw new Error('请填写任务名称'); const task = await request('/tasks', {method:'POST', body:JSON.stringify({name:state.name.trim(),dataset_id:state.selectedDS,model_id:'xvla',preset:state.preset,executor_id:state.executor,runtime_id:state.runtimeId,parameters:state.custom?effectiveParams():null})}); await openTask(task.id); return;}
      state.step += 1; renderWizard();
    } catch (err) {message(err.message);}
  }
  function selectDetailTab(tab) {
    state.detailTab = tab;
    for (const name of ['overview','metrics','params','evaluation','logs']) $('train-detail-' + name).hidden = name !== tab;
    document.querySelectorAll('#train-detail-tabs button').forEach((button) => {
      const active = button.dataset.detailTab === tab;
      button.classList.toggle('active', active);
      button.setAttribute('aria-selected', String(active));
    });
  }
  function renderTaskDetail(task) {
    state.detailData = task;
    $('train-detail-title').textContent = `${task.id} · ${task.name}`;
    $('train-detail-updated').textContent = `${task.telemetry_online === false ? '执行器暂不可达 · 显示已保存信息' : '自动更新'} · ${new Date().toLocaleTimeString('zh-CN')}`;
    const stages = ['QUEUED','PREPARING','RUNNING','EVALUATING','PACKAGING','COMPLETED'];
    const active = ['QUEUED','PREPARING','RUNNING','EVALUATING','PACKAGING'].includes(task.status);
    const metric = trainingMetric(task);
    const gpu = task.gpu || {};
    const validation = validationLoss(task.evaluation);
    const stepText = metric.step !== null ? `${metric.step} / ${metric.total ?? task.parameters?.steps ?? '—'}` : `0 / ${task.parameters?.steps ?? '—'}`;
    const cards = [
      ['训练步数', stepText, `进度 ${task.progress}%`],
      [task.status === 'COMPLETED' ? '最终训练损失' : '最近训练损失', metric.loss ?? '—', '模型前向损失'],
      ['学习率', metric.learningRate === null ? '—' : compactNumber(metric.learningRate), '来自训练日志'],
      ['梯度范数', metric.gradientNorm ?? '—', '来自训练日志'],
      ['训练 Epoch', metric.epoch ?? '—', metric.samples ? `日志样本 ${metric.samples}` : '来自训练日志'],
      ['GPU 当前使用率', gpu.utilization_percent === undefined ? '—' : `${esc(gpu.utilization_percent)}%`, `${esc(gpu.used_gb ?? '—')} / ${esc(gpu.total_gb ?? '—')} GB · 整卡`],
      ['离线验证损失', validation ?? '—', task.evaluation ? '仅最终 checkpoint' : '训练结束后计算'],
    ];
    $('train-detail-summary').innerHTML = `<div class="train-stage">${stages.map((stage) => `<span class="${task.status===stage?'active':''}">${esc(label[stage])}</span>`).join('')}</div><div class="train-detail-status"><span class="train-badge ${task.status==='FAILED'?'failed':task.status==='COMPLETED'?'done':''}">${esc(label[task.status] || task.status)}</span><span>阶段 ${esc(task.phase)} · ${esc(task.progress)}%</span></div><div class="train-progress"><i style="width:${Math.max(0,Math.min(100,Number(task.progress)||0))}%"></i></div>${task.error?`<p class="train-error train-detail-error">${esc(task.error)}</p>`:''}<div class="train-kpi-grid">${cards.map(([title,value,note])=>`<div class="train-kpi"><span>${esc(title)}</span><strong>${esc(value)}</strong><small>${esc(note)}</small></div>`).join('')}</div><div class="train-task-actions"><button id="train-cancel" class="btn" ${active?'':'hidden'}>取消任务</button><button id="train-retry" class="btn" ${['FAILED','CANCELED','INTERRUPTED'].includes(task.status)?'':'hidden'}>按原参数重试为新任务</button></div>`;
    $('train-cancel')?.addEventListener('click', async () => {if (!confirm('确定取消此训练任务？')) return; try {await request('/tasks/'+encodeURIComponent(task.id)+'/cancel',{method:'POST',body:'{}'}); await refreshTaskDetail();} catch(err){message(err.message);}});
    $('train-retry')?.addEventListener('click', async () => {try {const created=await request('/tasks/'+encodeURIComponent(task.id)+'/retry',{method:'POST',body:'{}'}); await openTask(created.id);} catch(err){message(err.message);}});
    $('train-detail-overview').innerHTML = `<div class="train-card"><h3>任务概览</h3><div class="train-grid"><div>来源 DS<br><strong>${esc(task.dataset_id)}</strong><small>快照 ${esc(task.snapshot?.info_sha256?.slice(0,16) || '—')}…</small></div><div>模型 / 方式<br><strong>X-VLA · 基座微调</strong><small>${esc(task.model_version)}</small></div><div>训练环境<br><strong>${esc(task.runtime_id || 'lerobot')}</strong><small>执行器 ${esc(task.executor_id)}</small></div><div>数据划分<br><strong>${esc(task.split?.train_episode_ids?.length ?? '—')} / ${esc(task.split?.validation_episode_ids?.length ?? '—')} Episode</strong><small>训练 / 验证，按 Episode 分开</small></div><div>任务时间<br><strong>${esc(task.started_at || task.created_at || '—')}</strong><small>${task.completed_at ? `结束 ${esc(task.completed_at)}` : '等待结束'}</small></div></div><p class="train-note">这里展示任务的固定数据快照与当前执行状态。详细曲线来自实际训练日志；页面每 5 秒自动请求一次状态。</p></div>`;
    const history = task.metric_history || [];
    const gpuHistory = task.gpu_history || [];
    $('train-detail-metrics').innerHTML = `<div class="train-metric-toolbar"><p>横轴为训练 Step；曲线只绘制执行器实际采集的指标。GPU 趋势代表整张显卡。</p><button id="train-loss-scale" class="btn">${state.lossLog ? '切换为线性纵轴' : '切换为对数纵轴'}</button></div><div class="train-chart-grid"><div class="train-chart-main">${chartCard('训练损失 Loss', history, 'loss', '#e16b68', {log:state.lossLog,smooth:true,empty:'尚无训练损失记录；开始训练后自动显示'})}</div>${chartCard('学习率 Learning Rate', history, 'learning_rate', '#2a9db6', {empty:'此任务未采集学习率'})}${chartCard('梯度范数 Gradient Norm', history, 'gradient_norm', '#d69638', {empty:'此任务未采集梯度范数'})}${chartCard('GPU 使用率', gpuHistory, 'utilization_percent', '#6b83bf', {time:true,unit:'%',empty:'此任务未采集 GPU 历史趋势'})}</div><p class="train-note">悬停曲线圆点可查看原始数值。早期任务若没有某项记录，将明确显示“未采集”，不会补造数据。</p>`;
    $('train-loss-scale')?.addEventListener('click', () => {state.lossLog = !state.lossLog; renderTaskDetail(state.detailData);});
    const params = task.parameters || {};
    $('train-detail-params').innerHTML = `<div class="train-card"><h3>训练参数快照</h3><p class="train-note">提交时固定；重试会生成新 TR 任务，并沿用原任务的环境与有效参数。</p><div class="train-grid"><div>Conda 环境<br><strong>${esc(task.runtime_id || 'lerobot')}</strong></div><div>训练预设<br><strong>${esc(task.preset)}</strong></div><div>训练步数 steps<br><strong>${esc(params.steps ?? '—')}</strong></div><div>批大小 batch_size<br><strong>${esc(params.batch_size ?? '—')}</strong></div><div>指标记录间隔<br><strong>${esc(params.log_freq ?? '—')} 步</strong></div><div>数据加载 workers<br><strong>${esc(params.num_workers ?? '—')}</strong></div><div>基座版本<br><strong>${esc(task.model_version)}</strong></div></div></div>`;
    const evaluationOpen = $('train-detail-evaluation').querySelector('details')?.open || false;
    $('train-detail-evaluation').innerHTML = `<div class="train-card"><h3>离线评估</h3>${evaluationView(task.evaluation)}<div class="train-grid"><div>训练 Episode<br><strong>${esc(task.split?.train_episode_ids?.length ?? '—')}</strong></div><div>验证 Episode<br><strong>${esc(task.split?.validation_episode_ids?.length ?? '—')}</strong><small>不参与训练</small></div><div>所用 checkpoint<br><strong>最终 checkpoint</strong><small>当前没有训练过程中的多点验证</small></div></div></div>`;
    if (evaluationOpen && $('train-detail-evaluation').querySelector('details')) $('train-detail-evaluation').querySelector('details').open = true;
    if (!state.logsPinned) {
      $('train-log-text').textContent = task.logs || '暂无日志';
      state.logBefore = task.log_start || 0;
      $('train-older-logs').hidden = state.logBefore === 0;
      $('train-latest-logs').hidden = true;
    } else if ((task.log_size || 0) > state.logSize) {
      $('train-latest-logs').hidden = false;
    }
    state.logSize = task.log_size || 0;
  }
  async function refreshTaskDetail() {
    if (!state.taskId || state.detailLoading) return;
    const id = state.taskId;
    const requestId = ++state.detailRequest;
    state.detailLoading = true;
    try {
      const task = await request('/tasks/' + encodeURIComponent(id));
      if (state.taskId === id && state.tab === 'detail' && requestId === state.detailRequest) renderTaskDetail(task);
    } catch (err) {
      if (requestId === state.detailRequest) {
        $('train-detail-updated').textContent = '自动更新失败 · 可手动刷新';
        message(err.message);
      }
    } finally {if (requestId === state.detailRequest) state.detailLoading = false;}
  }
  async function openTask(id) {
    const changed = state.taskId !== id;
    state.taskId = id;
    if (changed) {state.detailRequest++; state.detailLoading = false; state.detailTab = 'metrics'; state.logsPinned = false; state.logBefore = 0; state.logSize = 0;}
    show('detail');
    $('train-detail-body').innerHTML = `<div id="train-detail-summary" class="train-card"></div><nav id="train-detail-tabs" class="train-detail-tabs" aria-label="任务详情"><button data-detail-tab="overview">任务概览</button><button data-detail-tab="metrics">训练指标</button><button data-detail-tab="params">训练参数</button><button data-detail-tab="evaluation">离线评估</button><button data-detail-tab="logs">执行日志</button></nav><section id="train-detail-overview"></section><section id="train-detail-metrics"></section><section id="train-detail-params"></section><section id="train-detail-evaluation"></section><section id="train-detail-logs"><div class="train-card"><div class="train-log-head"><h3>执行日志</h3><div><button id="train-older-logs" class="btn" hidden>查看更早日志</button><button id="train-latest-logs" class="btn" hidden>回到最新日志</button></div></div><pre id="train-log-text" class="train-log">读取中…</pre></div></section>`;
    $('train-detail-tabs').addEventListener('click', (event) => {const button=event.target.closest('[data-detail-tab]');if(button)selectDetailTab(button.dataset.detailTab);});
    $('train-older-logs').addEventListener('click', async () => {try {const older=await request('/tasks/'+encodeURIComponent(state.taskId)+'/logs?before='+state.logBefore); $('train-log-text').textContent=older.text+$('train-log-text').textContent;state.logBefore=older.start;state.logsPinned=true;$('train-older-logs').hidden=state.logBefore===0;} catch(err){message(err.message);}});
    $('train-latest-logs').addEventListener('click', () => {state.logsPinned=false;refreshTaskDetail();});
    selectDetailTab(state.detailTab);
    await refreshTaskDetail();
  }
  async function loadVersions() {
    try {const versions = await request('/models'); $('train-model-rows').innerHTML = versions.length ? versions.map((row) => `<div class="train-card"><h3>${esc(row.version_id)} · X-VLA</h3><div class="train-grid"><div>任务<br>${esc(row.id)}</div><div>来源 DS<br>${esc(row.dataset_id)}</div><div>基座版本<br>${esc(row.model_version)}</div><div>包大小<br>${esc((row.artifact.size_bytes/1073741824).toFixed(2))} GB</div></div><div class="train-version-result"><span>离线验证前向损失</span><strong>${validationLoss(row.evaluation) ?? '—'}</strong><small>非机器人任务成功率</small></div><p class="train-note">仅显示通过文件校验、加载和推理冒烟测试的模型包。</p><a class="btn btn-primary" href="${api}/models/${encodeURIComponent(row.id)}/download">下载完整模型包</a></div>`).join('') : '<div class="train-card train-muted">暂无通过验证的模型版本。</div>';} catch(err){message(err.message);}
  }
  async function loadEnvironment() {
    try {const data = await request('/catalog'); state.catalog = data; const env=data.environment; const vlm=env.vlm || {};
      $('train-environment-body').innerHTML=`<div class="train-card"><div class="train-grid"><div>执行器<br><strong>${esc(env.name)}</strong><small>${env.online?'在线':'离线：'+esc(env.reason)}</small></div><div>最近心跳<br><strong>${esc(env.last_heartbeat || '—')}</strong></div><div>GPU<br><strong>${esc(env.gpu?.name || '未提供')}</strong><small>使用率 ${esc(env.gpu?.utilization_percent ?? '—')}%，已用 ${esc(env.gpu?.used_gb ?? '—')} / ${esc(env.gpu?.total_gb ?? '—')} GB</small></div><div>排队<br><strong>${esc(env.queue_count)} 个任务</strong></div></div><h3>模型能力</h3><p>${env.models.length?env.models.map((m)=>`${esc(m.name || m.id)}：${m.available?'可用':esc(m.reason || '不可用')}`).join('<br>'):'尚未登记模型'}</p><div class="train-vlm"><div class="train-vlm-head"><div><h3>VLM 服务与 GPU 释放</h3><p>需要训练显存时，可由授权操作员暂时关闭 VLM 质检服务。</p></div><span class="train-badge ${vlm.state==='running'?'done':'failed'}">${esc(vlm.state || '未知')}</span></div><p>质检状态：${vlm.qc_paused?'VLM 质检排队暂停':'VLM 质检可执行'} ${vlm.reason?`· ${esc(vlm.reason)}`:''}</p><p class="train-note">关闭前会检查正在运行的 VLM 质检；已有任务未结束时会拒绝关闭。关闭期间，新 VLM 质检任务保持排队，恢复服务后继续执行。</p><div class="train-vlm-actions"><button id="train-stop-vlm" class="btn" ${data.can_control_vlm&&vlm.controllable&&vlm.state==='running'?'':'disabled'}>关闭占用 GPU 的 VLM</button><button id="train-start-vlm" class="btn" ${data.can_control_vlm&&vlm.controllable&&(vlm.state==='stopped'||vlm.qc_paused)?'':'disabled'}>恢复 VLM</button></div>${!vlm.controllable?'<p class="train-muted">服务器尚未授权受控启停，按钮不可用。</p>':''}</div></div>`;
      for (const [op,id] of [['stop','train-stop-vlm'],['start','train-start-vlm']]) $(id)?.addEventListener('click', async () => {if (!confirm(op==='stop'?'确认停止 VLM 服务并暂停依赖它的质检？':'确认恢复 VLM 服务？')) return; try {await request('/vlm/'+op,{method:'POST',body:'{}',headers:{'X-HKC-Training-Action':'confirm'}});await loadEnvironment();}catch(err){message(err.message);}});
    } catch(err){message(err.message);}
  }
  document.querySelectorAll('.train-tabs button').forEach((button) => button.addEventListener('click', () => show(button.dataset.tab)));
  $('train-new').addEventListener('click', async () => {try {await loadCatalog(); state.step=0; state.checked=null; state.selectedDS=''; state.name=''; state.custom=false; state.customParams={}; state.runtimeId=''; state.preset=state.catalog.models.find((m)=>m.id==='xvla')?.presets?.[0]?.id || 'standard'; show('wizard'); renderWizard();} catch(err){message(err.message);}});
  $('train-back-list').addEventListener('click',()=>show('tasks')); $('train-detail-back').addEventListener('click',()=>show('tasks')); $('train-detail-refresh').addEventListener('click',refreshTaskDetail);
  $('train-prev').addEventListener('click',()=>{if(state.step>0){state.step--;renderWizard();}}); $('train-next').addEventListener('click',nextWizard);
  for(const id of ['train-status','train-model-filter','train-dataset-filter']) $(id).addEventListener('change',loadTasks);
  $('train-refresh').addEventListener('click',loadTasks);
  $('train-trash-toggle').addEventListener('click',()=>{state.showDeleted=!state.showDeleted;message('');loadTasks();});
  $('train-task-rows').addEventListener('click',async (event)=>{
    const button=event.target.closest('button'); if(!button) return;
    if(button.dataset.openTask){openTask(button.dataset.openTask);return;}
    if(button.dataset.deleteTask){
      const id=button.dataset.deleteTask;
      if(!confirm(`确定删除训练任务 ${id}？如果任务仍在排队或运行，会先取消，结束后移入回收站。训练记录、日志和模型包不会被清除。`)) return;
      try{button.disabled=true;const result=await request('/tasks/'+encodeURIComponent(id),{method:'DELETE'});await loadTasks();message(result.deleted_at ? `任务 ${id} 已移入回收站，可恢复。` : `任务 ${id} 正在取消，结束后会移入回收站。`);}catch(err){button.disabled=false;message(err.message);}
    } else if(button.dataset.restoreTask){
      const id=button.dataset.restoreTask;
      try{button.disabled=true;await request('/tasks/'+encodeURIComponent(id)+'/restore',{method:'POST',body:'{}'});await loadTasks();message(`任务 ${id} 已恢复。`);}catch(err){button.disabled=false;message(err.message);}
    } else if(button.dataset.purgeTask){
      const id=button.dataset.purgeTask;
      if(state.purgingTaskId) return;
      state.purgingTaskId=id;
      button.disabled=true;
      button.textContent='删除中…';
      message(`正在永久删除任务 ${id}，请稍候…`);
      try{
        await request('/tasks/'+encodeURIComponent(id)+'/purge',{method:'POST',body:'{}',headers:{'X-HKC-Training-Purge':id}});
        message(`任务 ${id} 及其专属产物已永久删除。`);
      }catch(err){message(err.message);}
      finally{state.purgingTaskId=null;await loadTasks();}
    }
  });
  function refreshVisible() {
    if (document.hidden) return;
    if (state.tab === 'detail') refreshTaskDetail();
    else if (state.tab === 'tasks') loadTasks();
  }
  window.setInterval(refreshVisible, 5000);
  document.addEventListener('visibilitychange', () => {if (!document.hidden) refreshVisible();});
  loadCatalog().then(loadTasks).catch((err)=>message(err.message));
})();
