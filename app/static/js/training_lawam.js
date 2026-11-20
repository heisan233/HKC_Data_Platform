(() => {
  'use strict';

  const api = '/training/lawam/api';
  const tabs = ['tasks', 'detail', 'create', 'evaluation', 'environment'];
  const byId = (id) => document.getElementById(id);
  const num = (id) => Number(byId(id).value);
  const episodeText = () => byId('lawam-episodes').value.trim().replace(/[，、]/g, ',');
  const state = { online: false, jobs: [], deletedJobs: [], showDeleted: false,
    detailId: '', evaluations: [], bases: [], datasets: [], refreshing: false,
    taskLog: '', evalLog: '', metrics: [] };
  const metricLabels = {
    loss_total: '总损失', loss_flow: '动作流损失', loss_perceptual: '感知损失',
    loss_distill: '蒸馏损失', loss_vlm: 'VLM 损失', lr: '学习率',
    grdn: '梯度范数', step_s: '每步耗时（秒）', mem_gb: '显存（GB）',
  };
  const svgNS = 'http://www.w3.org/2000/svg';

  function show(tab) {
    if (!tabs.includes(tab)) return;
    for (const name of tabs) byId('lawam-' + name).hidden = name !== tab;
    document.querySelectorAll('[data-lawam-tab]').forEach((button) => {
      const active = button.dataset.lawamTab === tab;
      button.classList.toggle('active', active);
      button.setAttribute('aria-selected', String(active));
    });
  }

  function message(value, error = false) {
    const box = byId('lawam-message');
    box.textContent = value;
    box.classList.toggle('error', error);
    box.hidden = !value;
  }

  async function request(path, method = 'GET', payload, extraHeaders = {}) {
    const response = await fetch(api + path, {
      method, headers: { ...(payload ? { 'Content-Type': 'application/json' } : {}), ...extraHeaders },
      body: payload ? JSON.stringify(payload) : undefined,
    });
    let data;
    try { data = await response.json(); } catch (_) { throw new Error('服务器返回格式无效'); }
    if (!response.ok) {
      const detail = data.detail || data.error;
      throw new Error(typeof detail === 'string' ? detail : `请求失败（HTTP ${response.status}）`);
    }
    return data;
  }

  function setOptions(id, rows, placeholder) {
    const select = byId(id);
    const selected = select.value;
    select.replaceChildren();
    for (const [value, label] of [['', placeholder], ...rows.map((row) => [row.id, row.label])]) {
      const option = document.createElement('option');
      option.value = value;
      option.textContent = label;
      select.appendChild(option);
    }
    if (rows.some((row) => row.id === selected)) select.value = selected;
    else if (id === 'lawam-base' && rows.length) select.value = rows[0].id;
  }

  function cell(row, value) {
    const td = row.insertCell();
    td.textContent = value == null ? '—' : String(value);
    return td;
  }

  function action(parent, label, callback) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'btn btn-sm';
    button.textContent = label;
    button.addEventListener('click', callback);
    parent.appendChild(button);
    return button;
  }

  function renderJobs() {
    const body = byId('lawam-task-rows');
    body.replaceChildren();
    const filter = byId('lawam-status-filter').value;
    const query = byId('lawam-search').value.trim().toLowerCase();
    const jobs = (state.showDeleted ? state.deletedJobs : state.jobs).filter((job) => (!filter || job.status === filter) &&
      (!query || `${job.id} ${job.name}`.toLowerCase().includes(query)));
    if (!jobs.length) {
      cell(body.insertRow(), state.showDeleted ? '回收站为空或没有符合条件的任务' : '暂无符合条件的训练任务').colSpan = 8;
      return;
    }
    for (const job of jobs) {
      const row = body.insertRow();
      const p = job.parameters || {};
      cell(row, `${job.name || job.id} · ${job.id}`);
      cell(row, state.bases.find((item) => item.id === p.base)?.label || p.base);
      cell(row, `${state.datasets.find((item) => item.id === p.dataset_id)?.name || p.dataset_id || '—'} · ${p.dataset_mode || '—'}`);
      cell(row, `${job.status}${job.reason ? ' · ' + job.reason : ''}`);
      cell(row, job.status === 'RUNNING' ? `${job.progress || 0}% · ${job.step || 0}/${p.steps}` :
        job.status === 'COMPLETED' ? '100%' : '—');
      cell(row, job.loss);
      cell(row, job.started_at || job.created_at);
      const controls = cell(row, '');
      controls.textContent = '';
      action(controls, '查看详情', () => openDetail(job.id));
      if (state.showDeleted) {
        action(controls, '恢复任务', () => restoreJob(job.id));
        const purge = action(controls, '永久删除', () => purgeJob(job.id, purge));
        purge.classList.add('train-delete-link');
      } else {
        action(controls, '删除', () => deleteJob(job.id));
      }
      if (!state.showDeleted && ['QUEUED', 'RUNNING'].includes(job.status)) {
        action(controls, '取消', () => cancel('jobs', job.id));
      }
    }
  }

  function renderEvaluations() {
    const body = byId('lawam-evaluation-rows');
    body.replaceChildren();
    if (!state.evaluations.length) {
      cell(body.insertRow(), '暂无评测任务').colSpan = 7;
      return;
    }
    for (const evaluation of state.evaluations) {
      const row = body.insertRow();
      const p = evaluation.parameters || {};
      cell(row, evaluation.id);
      cell(row, p.train_id);
      cell(row, `${p.task} · ${p.task_id}`);
      cell(row, `${evaluation.status}${evaluation.reason ? ' · ' + evaluation.reason : ''}`);
      cell(row, evaluation.result ?
        `${evaluation.result.passed}/${evaluation.result.total} · ${evaluation.result.success_percent}%` : '—');
      cell(row, evaluation.created_at);
      const controls = cell(row, '');
      controls.textContent = '';
      action(controls, '日志', () => openLog('eval', evaluation.id));
      if (['QUEUED', 'RUNNING'].includes(evaluation.status)) {
        action(controls, '取消', () => cancel('evaluations', evaluation.id));
      }
    }
  }

  function renderEnvironment(status) {
    byId('lawam-env-overview').dataset.online = String(status.online === true);
    byId('lawam-env-state').textContent = status.online ? '在线' : '不可用';
    byId('lawam-env-image').textContent = status.image || 'LaWAM 独立 Docker';
    byId('lawam-env-gpu').textContent = status.gpu?.name || '未检测到 GPU';
    byId('lawam-env-memory').textContent = status.gpu ?
      `空闲 ${status.gpu.free_mb.toLocaleString('zh-CN')} / ${status.gpu.total_mb.toLocaleString('zh-CN')} MB` : '等待资源信息';
    const percent = status.gpu?.total_mb > 0 ? Math.min(100, Math.max(0,
      Math.round((status.gpu.used_mb / status.gpu.total_mb) * 100))) : 0;
    byId('lawam-env-meter-fill').style.width = `${percent}%`;
    byId('lawam-env-meter-fill').parentElement.setAttribute('aria-valuenow', String(percent));
    byId('lawam-env-queue').textContent = Number.isInteger(status.queue_count) ? status.queue_count : '—';
    byId('lawam-env-output').textContent = status.output || 'storage/training-runs/lawam/';
    byId('lawam-env-reason').textContent = status.reason || '执行器已连接，可提交通过训前检查的训练任务。';
  }

  function review() {
    const custom = byId('lawam-mode').value === 'custom';
    byId('lawam-episodes-field').hidden = !custom;
    const steps = num('lawam-steps');
    const batch = num('lawam-batch');
    const warmup = num('lawam-warmup');
    const episodes = episodeText();
    const problems = [];
    if (!byId('lawam-name').value.trim()) problems.push('填写任务名称');
    if (!byId('lawam-base').value) problems.push('选择起点权重');
    if (!byId('lawam-dataset').value) problems.push('选择已审核可用的 WAM 数据集');
    if (!Number.isInteger(steps) || steps < 1) problems.push('训练步数必须是正整数');
    if (!Number.isInteger(batch) || batch < 1) problems.push('Batch size 必须是正整数');
    if (!Number.isInteger(num('lawam-horizon')) || num('lawam-horizon') < 1) problems.push('Action horizon 必须是正整数');
    if (!Number.isInteger(warmup) || warmup < 0 || warmup >= steps) problems.push('Warmup 应小于训练步数');
    if (!Number.isInteger(num('lawam-save')) || num('lawam-save') < 1) problems.push('保存间隔必须是正整数');
    if (!Number.isFinite(num('lawam-lr')) || num('lawam-lr') <= 0) problems.push('学习率必须大于 0');
    if (!Number.isInteger(num('lawam-unfreeze')) || num('lawam-unfreeze') < 0) problems.push('解冻层数不能为负数');
    if (custom && !/^\d+(\s*,\s*\d+)*$/.test(episodes)) problems.push('自定义 Episode 请填写非负整数，例如 0,1,2,3');
    byId('lawam-estimate').textContent = Number.isInteger(steps) && steps > 0 &&
      Number.isInteger(batch) && batch > 0 ?
      `数据范围：${byId('lawam-mode').selectedOptions[0].textContent} · ${(steps * batch).toLocaleString('zh-CN')} 次样本读取 · 输出：storage/training-runs/lawam/` :
      '填写有效的步数和 Batch size 后显示训练量。';
    byId('lawam-validation').textContent = problems.join('；');
    byId('lawam-submit').disabled = !state.online || problems.length > 0;
    byId('lawam-eval-submit').disabled = !state.online || !byId('lawam-eval-train').value ||
      !byId('lawam-eval-checkpoint').value;
    return problems.length === 0;
  }

  async function refreshLog() {
    for (const [kind, id, route] of [
      ['task', state.taskLog, 'jobs'], ['eval', state.evalLog, 'evaluations'],
    ]) {
      if (!id) continue;
      try {
        const log = await request(`/${route}/${encodeURIComponent(id)}/logs`);
        byId(`lawam-${kind}-log`).textContent = log.text || '暂无日志';
      } catch (error) { message(error.message, true); }
    }
  }

  async function openLog(kind, id) {
    state[kind === 'task' ? 'taskLog' : 'evalLog'] = id;
    byId(`lawam-${kind}-log-panel`).hidden = false;
    byId(`lawam-${kind}-log-title`).textContent = `${kind === 'task' ? '训练' : '评测'}日志 · ${id}`;
    await refreshLog();
  }

  function detailField(grid, label, value) {
    const item = document.createElement('div');
    const title = document.createElement('span');
    const content = document.createElement('strong');
    title.textContent = label;
    content.textContent = value == null || value === '' ? '—' : String(value);
    item.append(title, content);
    grid.appendChild(item);
  }

  function metricNumber(value) {
    if (!Number.isFinite(value)) return '—';
    return value !== 0 && Math.abs(value) < 0.001 ? value.toExponential(2) :
      Number(value.toPrecision(4)).toLocaleString('zh-CN');
  }

  function svgElement(name, attrs = {}, label = '') {
    const element = document.createElementNS(svgNS, name);
    for (const [key, value] of Object.entries(attrs)) element.setAttribute(key, String(value));
    if (label) element.textContent = label;
    return element;
  }

  function renderMetrics() {
    const chart = byId('lawam-metric-chart');
    const select = byId('lawam-metric-select');
    const key = select.value;
    const points = state.metrics.filter((point) => Number.isFinite(point.step) && Number.isFinite(point[key]));
    chart.replaceChildren();
    const empty = byId('lawam-metric-empty');
    empty.hidden = points.length > 0;
    chart.hidden = !points.length;
    byId('lawam-metric-latest').textContent = points.length ?
      `${metricLabels[key]}：${metricNumber(points.at(-1)[key])} · ${points.length} 个记录点` : '';
    if (!points.length) {
      empty.textContent = state.metrics.length ? '所选指标尚无记录。' :
        '暂无指标记录。训练器只在日志写入指标时才会显示曲线。';
      return;
    }
    const values = points.map((point) => point[key]);
    const xMin = points[0].step;
    const xMax = points.at(-1).step;
    const rawMin = Math.min(...values);
    const rawMax = Math.max(...values);
    const padding = Math.max((rawMax - rawMin) * 0.1, Math.abs(rawMax) * 0.02, 1e-9);
    const yMin = rawMin - padding;
    const yMax = rawMax + padding;
    const x = (step) => 65 + (xMax === xMin ? 0.5 : (step - xMin) / (xMax - xMin)) * 710;
    const y = (value) => 215 - (value - yMin) / (yMax - yMin) * 185;
    for (let index = 0; index < 4; index += 1) {
      const value = yMin + (yMax - yMin) * (3 - index) / 3;
      const position = y(value);
      chart.append(svgElement('line', { x1: 65, x2: 775, y1: position, y2: position }));
      chart.append(svgElement('text', { x: 4, y: position + 4 }, metricNumber(value)));
    }
    chart.append(svgElement('text', { x: 65, y: 245 }, String(xMin)));
    chart.append(svgElement('text', { x: 775, y: 245, 'text-anchor': 'end' }, String(xMax)));
    if (points.length > 1) {
      chart.append(svgElement('polyline', {
        points: points.map((point) => `${x(point.step)},${y(point[key])}`).join(' '),
      }));
    }
    for (const point of points.length <= 150 ? points : [points[0], points.at(-1)]) {
      const dot = svgElement('circle', { cx: x(point.step), cy: y(point[key]), r: 4 });
      dot.append(svgElement('title', {}, `第 ${point.step} 步：${metricNumber(point[key])}`));
      chart.append(dot);
    }
  }

  function updateMetricOptions(points) {
    state.metrics = points;
    const available = Object.keys(metricLabels).filter((key) => points.some((point) => Number.isFinite(point[key])));
    setOptions('lawam-metric-select', available.map((key) => ({ id: key, label: metricLabels[key] })), '暂无指标');
    if (!byId('lawam-metric-select').value && available.length) byId('lawam-metric-select').value = available[0];
    renderMetrics();
  }

  async function refreshCheckpoints() {
    const trainId = byId('lawam-eval-train').value;
    setOptions('lawam-eval-checkpoint', [], trainId ? '正在读取检查点…' : '先选择训练任务');
    if (!trainId) { review(); return; }
    try {
      const rows = await request(`/jobs/${encodeURIComponent(trainId)}/checkpoints`);
      if (byId('lawam-eval-train').value !== trainId) return;
      setOptions('lawam-eval-checkpoint', rows, rows.length ? '选择检查点' : '该任务暂无检查点');
    } catch (error) {
      setOptions('lawam-eval-checkpoint', [], '检查点读取失败');
      message(error.message, true);
    }
    review();
  }

  async function openDetail(id) {
    state.detailId = id;
    show('detail');
    await refreshDetail();
  }

  async function refreshDetail() {
    const id = state.detailId;
    if (!id) return;
    try {
      const [job, log, metrics] = await Promise.all([
        request(`/jobs/${encodeURIComponent(id)}`),
        request(`/jobs/${encodeURIComponent(id)}/logs`),
        request(`/jobs/${encodeURIComponent(id)}/metrics`),
      ]);
      if (state.detailId !== id) return;
      const p = job.parameters || {};
      byId('lawam-detail-title').textContent = `${job.id} · ${job.name}`;
      byId('lawam-detail-updated').textContent = `状态：${job.status} · 更新于 ${new Date().toLocaleTimeString('zh-CN')}`;
      const summary = byId('lawam-detail-summary');
      summary.replaceChildren();
      for (const [label, value] of [
        ['状态', job.status], ['训练进度', `${job.step || 0} / ${p.steps || '—'} 步`],
        ['最新总损失', metricNumber(metrics.at(-1)?.loss_total ?? job.loss)],
        ['指标记录点', metrics.length],
      ]) detailField(summary, label, value);
      updateMetricOptions(metrics);
      const body = byId('lawam-detail-body');
      body.replaceChildren();
      const card = document.createElement('div');
      card.className = 'train-card';
      const heading = document.createElement('h3');
      heading.textContent = '任务概览与参数';
      const grid = document.createElement('div');
      grid.className = 'lawam-detail-grid';
      for (const [label, value] of [
        ['任务编号', job.id], ['任务名称', job.name], ['状态', job.status],
        ['失败或等待原因', job.reason], ['起点权重', state.bases.find((base) => base.id === p.base)?.label || p.base],
        ['训练数据', `${p.dataset_id || '—'} · ${state.datasets.find((item) => item.id === p.dataset_id)?.name || '已登记数据集'}`], ['数据范围', p.dataset_mode],
        ['Episode', p.episodes?.length ? p.episodes.join(', ') : '全部'],
        ['训练步数', p.steps], ['Batch size', p.batch], ['Action horizon', p.action_horizon],
        ['Warmup 步数', p.warmup], ['学习率', p.lr], ['保存间隔', p.save_freq],
        ['解冻 LLM 末层数', p.unfreeze_llm_layers],
        ['冻结视觉主干', p.freeze_vision ? '是' : '否'], ['冻结 LLM 主干', p.freeze_llm ? '是' : '否'],
        ['最新步数', job.step], ['最新 loss', job.loss], ['退出码', job.exit_code],
        ['创建时间', job.created_at], ['开始时间', job.started_at], ['结束时间', job.completed_at],
        ['删除时间', job.deleted_at], ['产物目录', `storage/training-runs/lawam/${job.id}/`],
      ]) detailField(grid, label, value);
      card.append(heading, grid);
      body.appendChild(card);
      byId('lawam-detail-log').textContent = log.text || '暂无日志';
    } catch (error) { message(error.message, true); }
  }

  async function deleteJob(id) {
    if (!window.confirm(`确定删除训练任务 ${id}？任务会移入回收站，记录与产物保留；运行中的任务会先取消。`)) return;
    try {
      await request(`/jobs/${encodeURIComponent(id)}`, 'DELETE');
      message(`任务 ${id} 已移入回收站`);
      if (state.detailId === id) { state.detailId = ''; show('tasks'); }
      await refresh();
    } catch (error) { message(error.message, true); }
  }

  async function restoreJob(id) {
    try {
      await request(`/jobs/${encodeURIComponent(id)}/restore`, 'POST', {});
      message(`任务 ${id} 已恢复`);
      await refresh();
    } catch (error) { message(error.message, true); }
  }

  async function purgeJob(id, button) {
    if (!window.confirm(`永久删除训练任务 ${id}？其日志、检查点及关联评测记录和产物都会清除，无法恢复。`)) return;
    button.disabled = true;
    button.textContent = '删除中…';
    try {
      const result = await request(`/jobs/${encodeURIComponent(id)}/purge`, 'POST', {},
        { 'X-HKC-LaWAM-Purge': id });
      message(`任务 ${id} 及 ${result.evaluation_count || 0} 条关联评测已永久删除`);
      if (state.detailId === id) { state.detailId = ''; show('tasks'); }
      await refresh();
    } catch (error) {
      button.disabled = false;
      button.textContent = '永久删除';
      message(error.message, true);
    }
  }

  async function cancel(route, id) {
    if (!window.confirm(`确认取消任务 ${id}？`)) return;
    try {
      await request(`/${route}/${encodeURIComponent(id)}/cancel`, 'POST', {});
      message(`已请求取消 ${id}`);
      await refresh();
    } catch (error) { message(error.message, true); }
  }

  async function refresh() {
    if (state.refreshing) return;
    state.refreshing = true;
    try {
      const [status, bases, datasets, jobs, deletedJobs, evaluations] = await Promise.all([
        request('/status'), request('/bases'), request('/datasets'), request('/jobs'),
        request('/deleted-jobs'), request('/evaluations'),
      ]);
      state.online = status.online === true;
      state.bases = bases;
      state.datasets = datasets;
      state.jobs = jobs;
      state.deletedJobs = deletedJobs;
      state.evaluations = evaluations;
      renderEnvironment(status);
      setOptions('lawam-base', bases, '选择起点权重');
      setOptions('lawam-dataset', datasets.map((item) => ({ id: item.id,
        label: `${item.id} · ${item.name} · ${item.episodes} Episodes` })), '选择 WAM 数据集');
      setOptions('lawam-eval-train', jobs.map((job) => ({ id: job.id,
        label: `${job.name || job.id} · ${job.id}` })), '选择训练任务');
      await refreshCheckpoints();
      renderJobs();
      renderEvaluations();
      review();
      await refreshLog();
      if (state.detailId) await refreshDetail();
    } catch (error) {
      state.online = false;
      byId('lawam-env-overview').dataset.online = 'false';
      byId('lawam-env-state').textContent = '连接失败';
      byId('lawam-env-reason').textContent = error.message;
      message(error.message, true);
      review();
    } finally { state.refreshing = false; }
  }

  document.querySelectorAll('[data-lawam-tab]').forEach((button) => {
    button.addEventListener('click', () => show(button.dataset.lawamTab));
  });
  document.querySelectorAll('[data-lawam-show]').forEach((button) => {
    button.addEventListener('click', () => show(button.dataset.lawamShow));
  });
  document.querySelectorAll('[data-lawam-close-log]').forEach((button) => {
    button.addEventListener('click', () => {
      const kind = button.dataset.lawamCloseLog;
      state[kind === 'task' ? 'taskLog' : 'evalLog'] = '';
      byId(`lawam-${kind}-log-panel`).hidden = true;
    });
  });
  byId('lawam-create-form').addEventListener('input', review);
  byId('lawam-create-form').addEventListener('change', review);
  byId('lawam-evaluation-form').addEventListener('change', review);
  byId('lawam-eval-train').addEventListener('change', refreshCheckpoints);
  byId('lawam-metric-select').addEventListener('change', renderMetrics);
  byId('lawam-status-filter').addEventListener('change', renderJobs);
  byId('lawam-search').addEventListener('input', renderJobs);
  byId('lawam-refresh').addEventListener('click', refresh);
  byId('lawam-env-refresh').addEventListener('click', refresh);
  byId('lawam-detail-refresh').addEventListener('click', refreshDetail);
  byId('lawam-trash-toggle').addEventListener('click', () => {
    state.showDeleted = !state.showDeleted;
    const button = byId('lawam-trash-toggle');
    button.textContent = state.showDeleted ? '返回任务列表' : '回收站';
    button.setAttribute('aria-pressed', String(state.showDeleted));
    renderJobs();
  });
  byId('lawam-create-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    if (!review()) return;
    byId('lawam-submit').disabled = true;
    const payload = {
      name: byId('lawam-name').value.trim(), base: byId('lawam-base').value,
      dataset_id: byId('lawam-dataset').value,
      dataset_mode: byId('lawam-mode').value, episodes: episodeText(),
      steps: num('lawam-steps'), batch: num('lawam-batch'),
      action_horizon: num('lawam-horizon'), warmup: num('lawam-warmup'),
      lr: num('lawam-lr'), unfreeze_llm_layers: num('lawam-unfreeze'),
      save_freq: num('lawam-save'), freeze_vision: byId('lawam-freeze-vision').checked,
      freeze_llm: byId('lawam-freeze-llm').checked, stop_vllm: byId('lawam-stop-vllm').checked,
    };
    try {
      const job = await request('/jobs', 'POST', payload);
      message(`训练任务 ${job.id} 已创建`);
      show('tasks');
      await refresh();
    } catch (error) { message(error.message, true); }
    review();
  });
  byId('lawam-evaluation-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    if (byId('lawam-eval-submit').disabled) return;
    const payload = {
      train_id: byId('lawam-eval-train').value,
      checkpoint: byId('lawam-eval-checkpoint').value,
      task: byId('lawam-eval-task').value, task_id: num('lawam-eval-task-id'),
      episodes: num('lawam-eval-episodes'), replan: num('lawam-eval-replan'),
      task_text: byId('lawam-eval-text').value,
    };
    byId('lawam-eval-submit').disabled = true;
    try {
      const evaluation = await request('/evaluations', 'POST', payload);
      message(`评测任务 ${evaluation.id} 已创建`);
      await refresh();
    } catch (error) { message(error.message, true); }
    review();
  });
  review();
  refresh();
  window.setInterval(refresh, 5000);
})();
