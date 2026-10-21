(() => {
  const form = document.getElementById('conversion-form');
  const source = document.getElementById('conversion-source');
  const feedback = document.getElementById('export-feedback');
  const taskBody = document.getElementById('conversion-tasks');
  const datasetBody = document.getElementById('export-datasets');
  const empty = document.getElementById('conversion-empty');
  if (!form) return;

  const show = (message, error = false) => {
    feedback.textContent = message;
    feedback.classList.toggle('error', error);
    feedback.hidden = false;
  };
  const cell = (row, value) => {
    const element = document.createElement('td');
    element.textContent = value ?? '—';
    row.appendChild(element);
    return element;
  };
  const link = (parent, href, label) => {
    const anchor = document.createElement('a');
    anchor.href = href;
    anchor.textContent = label;
    parent.appendChild(anchor);
  };
  const request = async (url, options) => {
    const response = await fetch(url, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || `请求失败（${response.status}）`);
    return data;
  };
  const renderTasks = tasks => {
    taskBody.replaceChildren();
    if (!tasks.length) {
      const row = taskBody.insertRow();
      const item = cell(row, '暂无转换任务。');
      item.colSpan = 5;
      item.className = 'export-empty';
    }
    for (const task of tasks) {
      const row = taskBody.insertRow();
      cell(row, task.id);
      cell(row, task.source_name);
      cell(row, `${task.completed_episodes}/${task.total_episodes} episodes`);
      cell(row, ({ QUEUED: '排队中', RUNNING: '转换中', COMPLETED: '已完成', FAILED: '失败' })[task.status] || task.status);
      const result = cell(row, '');
      if (task.status === 'COMPLETED') {
        link(result, `/export/archive/converted/${encodeURIComponent(task.id)}`, '下载 HDF5 压缩包');
      } else {
        result.textContent = task.error || '—';
        if (task.error) result.className = 'export-error';
      }
    }
  };
  const renderDatasets = data => {
    const selected = source.value;
    const derived = data.derived || [];
    const converted = data.converted || [];
    source.replaceChildren();
    for (const item of derived) {
      const option = document.createElement('option');
      option.value = item.name;
      option.textContent = `${item.name} · ${item.episodes} episodes · ${item.frames} 帧`;
      source.appendChild(option);
    }
    if (derived.some(item => item.name === selected)) source.value = selected;
    source.disabled = !derived.length;
    form.querySelector('button[type=submit]').disabled = !derived.length;
    empty.hidden = !!derived.length;
    datasetBody.replaceChildren();
    const items = [...derived, ...converted];
    if (!items.length) {
      const row = datasetBody.insertRow();
      const item = cell(row, '暂无可导出的数据集。');
      item.colSpan = 5;
      item.className = 'export-empty';
    }
    for (const item of items) {
      const row = datasetBody.insertRow();
      cell(row, item.area);
      cell(row, item.name);
      cell(row, item.format);
      cell(row, `${item.episodes} episodes · ${item.frames} 帧`);
      link(cell(row, ''), `/export/archive/${item.area}/${encodeURIComponent(item.name)}`, '下载压缩包');
    }
  };
  const refresh = async () => {
    const [tasks, datasets] = await Promise.all([
      request('/export/api/conversions'), request('/export/api/datasets')
    ]);
    renderTasks(tasks);
    renderDatasets(datasets);
  };

  form.addEventListener('submit', async event => {
    event.preventDefault();
    const button = form.querySelector('button[type=submit]');
    button.disabled = true;
    try {
      const task = await request('/export/api/conversions', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ source_name: source.value })
      });
      show(`转换任务 ${task.id} 已创建。`);
      await refresh();
    } catch (error) {
      show(error.message, true);
    } finally {
      button.disabled = source.disabled;
    }
  });
  setInterval(() => refresh().catch(error => show(error.message, true)), 3000);
})();
