(() => {
  const feedback = document.getElementById('qc-feedback');
  const show = (message, error = false) => {
    if (!feedback) return;
    feedback.textContent = message;
    feedback.classList.toggle('dm-feedback-error', error);
    feedback.hidden = false;
    feedback.scrollIntoView({ block: 'nearest' });
  };
  const request = async (url, options = {}) => {
    const response = await fetch(url, options);
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.detail || `请求失败（${response.status}）`);
    return body;
  };
  const json = data => ({ headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[char]);
  const readJson = id => JSON.parse(document.getElementById(id).textContent);
  const openDialog = dialog => {
    if (typeof dialog.showModal === 'function') dialog.showModal();
    else dialog.setAttribute('open', '');
  };
  const closeDialog = dialog => {
    if (typeof dialog.close === 'function') dialog.close();
    else dialog.removeAttribute('open');
  };

  const taskPage = document.getElementById('qc-task-page');
  if (taskPage) {
    const plans = readJson('qc-plans-json');
    const planSelect = document.getElementById('qc-plan');
    const choices = [...document.querySelectorAll('.qc-dataset-choice')];
    const preview = document.getElementById('qc-task-rule-preview');
    const checks = document.getElementById('qc-task-checks');
    const uploadFiles = document.getElementById('qc-upload-files');
    const selectAll = document.getElementById('qc-select-all');
    const form = document.getElementById('qc-task-form');
    let selected = null;
    let tab = 'doctor';
    let enabled = { doctor: [], robot_rules: [], vlm: [] };
    const flash = sessionStorage.getItem('qc-task-flash');
    if (flash) {
      sessionStorage.removeItem('qc-task-flash');
      const item = JSON.parse(flash);
      show(item.message, item.error);
    }
    function updateCount() {
      const count = choices.filter(item => item.querySelector('input').checked).length;
      const files = uploadFiles.files.length;
      document.getElementById('qc-selection-count').textContent = `已选 ${count} 个数据集 · ${files} 个 ZIP`;
      document.getElementById('qc-upload-count').textContent = files ? `${files} 个 ZIP 已选择` : '未选择文件；单批最多 20 个 ZIP。';
      const visible = choices.filter(item => !item.hidden);
      selectAll.checked = visible.length > 0 && visible.every(item => item.querySelector('input').checked);
      selectAll.indeterminate = visible.some(item => item.querySelector('input').checked) && !selectAll.checked;
    }
    function renderRules() {
      if (!selected) { preview.hidden = true; return; }
      preview.hidden = false;
      document.getElementById('qc-selected-plan').textContent = `${selected.name} · v${selected.version}`;
      document.getElementById('qc-edit-plan').href = `/qc/plans/${encodeURIComponent(selected.id)}`;
      document.querySelectorAll('[data-task-tab]').forEach(button => button.classList.toggle('active', button.dataset.taskTab === tab));
      const rows = tab === 'doctor' ? selected.config.doctor.checks :
        tab === 'robot_rules' ? selected.config.robot_rules : selected.config.vlm.rules;
      const names = Object.fromEntries([
        ['metadata', '元数据与格式'], ['temporal', '时序一致性'], ['actions', '动作质量'],
        ['videos', '视频完整性'], ['statistics', '统计分布'], ['episodes', 'Episode 健康度'],
        ['consistency', '字段一致性'], ['training', '训练就绪性'], ['anomalies', '异常检测'],
        ['portability', '可移植性'], ['per_episode', '逐 Episode 诊断']
      ]);
      checks.innerHTML = rows.length ? rows.map(row => {
        const detail = tab === 'doctor' ? row.id : tab === 'robot_rules' ?
          Object.entries(row.params).map(([key, value]) => `${key}: ${value}`).join(' · ') || row.kind :
          `${row.camera_key || '未选相机'} · ${row.prompt.slice(0, 70)}`;
        return `<label class="qc-check-row"><span><strong>${esc(row.name || names[row.id] || row.id)}</strong><small>${esc(detail)}</small></span><input type="checkbox" data-group="${tab}" value="${esc(row.id)}" ${enabled[tab].includes(row.id) ? 'checked' : ''}></label>`;
      }).join('') : '<p class="dm-empty">该部分尚未配置规则。</p>';
    }
    function setPlan() {
      selected = plans.find(item => item.id === planSelect.value) || null;
      enabled = selected ? {
        doctor: selected.config.doctor.checks.filter(row => row.enabled).map(row => row.id),
        robot_rules: selected.config.robot_rules.filter(row => row.enabled).map(row => row.id),
        vlm: selected.config.vlm.rules.filter(row => row.enabled).map(row => row.id)
      } : { doctor: [], robot_rules: [], vlm: [] };
      let available = 0;
      choices.forEach(item => {
        item.hidden = !selected || item.dataset.robot !== selected.robot_type || item.dataset.state !== 'ready';
        if (item.hidden) item.querySelector('input').checked = false;
        else available++;
      });
      selectAll.disabled = !available;
      document.getElementById('qc-dataset-empty').hidden = available > 0;
      document.getElementById('qc-dataset-empty').textContent = selected ?
        `当前没有可用的 ${selected.robot_type} 数据集；可上传 ZIP 或先到数据管理登记。` :
        '先选择方案，再选择匹配的数据集。';
      document.getElementById('qc-plan-context').textContent = selected ?
        `${selected.robot_type} · ${available} 个可用数据集 · 同一方案可批量复用` :
        '选择方案后显示机器人类型、可用数据集和检查规则。';
      renderRules(); updateCount();
    }
    planSelect.addEventListener('change', setPlan);
    choices.forEach(item => item.querySelector('input').addEventListener('change', updateCount));
    selectAll.addEventListener('change', () => {
      choices.filter(item => !item.hidden).forEach(item => { item.querySelector('input').checked = selectAll.checked; });
      updateCount();
    });
    uploadFiles.addEventListener('change', updateCount);
    document.querySelectorAll('[data-task-tab]').forEach(button => button.addEventListener('click', () => {
      tab = button.dataset.taskTab; renderRules();
    }));
    checks.addEventListener('change', event => {
      const box = event.target.closest('input[data-group]');
      if (!box) return;
      const list = enabled[box.dataset.group];
      enabled[box.dataset.group] = box.checked ? [...list, box.value] : list.filter(id => id !== box.value);
    });
    form.addEventListener('submit', async event => {
      event.preventDefault();
      if (!selected) { show('请先选择质检方案', true); return; }
      const registered = choices.filter(item => item.querySelector('input').checked).map(item => item.querySelector('input').value);
      const files = [...uploadFiles.files];
      if (!registered.length && !files.length) { show('请选择已登记数据集或上传 ZIP', true); return; }
      if (registered.length + files.length > 20) { show('每批最多创建 20 个质检任务', true); return; }
      const button = form.querySelector('button[type=submit]');
      button.disabled = true;
      const errors = [];
      try {
        let ids = [...registered];
        if (files.length) {
          show(`正在上传并登记 ${files.length} 个 ZIP…`);
          const data = new FormData();
          files.forEach(file => data.append('files', file));
          const uploaded = await request('/ingestion/api/uploads/batch', { method: 'POST', body: data });
          ids = ids.concat(uploaded.uploaded.map(item => item.dataset_id));
          errors.push(...uploaded.errors.map(item => `${item.filename}：${item.message}`));
        }
        if (!ids.length) { show(`没有可质检的数据集。${errors.join('；')}`, true); return; }
        show(`正在为 ${ids.length} 个数据集创建质检任务…`);
        const result = await request('/qc/api/tasks/batch', { method: 'POST', ...json({
          dataset_ids: ids, plan_id: selected.id, plan_version: selected.version, selections: enabled
        }) });
        errors.push(...result.errors.map(item => `${item.dataset_id}：${item.message}`));
        const message = `已创建 ${result.created.length} 个质检任务${errors.length ? `；未创建：${errors.join('；')}` : '。可在下方查看进度和报告。'}`;
        if (result.created.length) {
          sessionStorage.setItem('qc-task-flash', JSON.stringify({ message, error: errors.length > 0 }));
          location.href = '/qc/?submitted=1#qc-tasks';
        } else show(message, true);
      } catch (error) { show(error.message, true); }
      finally { button.disabled = false; }
    });
    const queryDataset = new URLSearchParams(location.search).get('dataset');
    if (queryDataset) {
      const choice = choices.find(item => item.querySelector('input').value === queryDataset);
      const match = choice && plans.find(item => item.robot_type === choice.dataset.robot);
      if (match) planSelect.value = match.id;
      setPlan();
      if (choice && !choice.hidden) choice.querySelector('input').checked = true;
      updateCount();
    } else setPlan();
    document.querySelectorAll('[data-hide-task], [data-show-task]').forEach(button => button.addEventListener('click', async () => {
      const hidden = button.hasAttribute('data-hide-task');
      const taskId = hidden ? button.dataset.hideTask : button.dataset.showTask;
      if (hidden && !window.confirm('仅将此记录移出当前列表？任务、报告和人工判定均会保留，可在“已移出记录”中恢复。')) return;
      button.disabled = true;
      try {
        await request(`/qc/api/tasks/${encodeURIComponent(taskId)}/${hidden ? 'hide' : 'show'}`, { method: 'POST' });
        location.reload();
      } catch (error) { show(error.message, true); button.disabled = false; }
    }));
    const pending = [...document.querySelectorAll('[data-task-id]')].some(row => ['排队中', '运行中'].includes(row.querySelector('[data-task-status]').textContent.trim()));
    if (pending) setInterval(async () => {
      try {
        const tasks = await request(taskPage.dataset.showHidden === 'true' ? '/qc/api/tasks?hidden=true' : '/qc/api/tasks');
        const byId = new Map(tasks.map(item => [item.id, item]));
        const states = { QUEUED: '排队中', RUNNING: '运行中', COMPLETED: '已完成', FAILED: '执行失败', INTERRUPTED: '已中断' };
        const phases = { pending: '等待执行', doctor: 'Doctor', robot_rules: '机器人专项', vlm: 'VLM 视频', done: '全部完成', error: '执行错误', interrupted: '已中断' };
        const outcomes = { PASS: '通过', REVIEW: '待复核', FAIL: '发现失败项', PARTIAL: '部分检查' };
        document.querySelectorAll('[data-task-id]').forEach(row => {
          const task = byId.get(row.dataset.taskId);
          if (!task) return;
          const badge = row.querySelector('[data-task-status]');
          badge.textContent = states[task.status] || task.status;
          badge.className = `dm-badge ${task.status === 'COMPLETED' ? 'dm-ok' : ['FAILED', 'INTERRUPTED'].includes(task.status) ? 'qc-fail' : 'dm-warn'}`;
          row.querySelector('[data-task-phase]').textContent = phases[task.phase] || task.phase;
          row.querySelector('[data-task-outcome]').textContent = outcomes[task.outcome] || '—';
        });
      } catch { /* Keep the current task table if polling fails. */ }
    }, 4000);
  }

  const plansPage = document.getElementById('qc-plans-page');
  if (plansPage) {
    const plans = readJson('qc-plans-json');
    const dialog = document.getElementById('qc-create-dialog');
    const dataset = document.getElementById('qc-plan-dataset');
    document.getElementById('qc-open-create').addEventListener('click', () => openDialog(dialog));
    document.getElementById('qc-empty-create')?.addEventListener('click', () => openDialog(dialog));
    document.getElementById('qc-create-cancel').addEventListener('click', () => closeDialog(dialog));
    dataset.addEventListener('change', () => {
      const type = dataset.selectedOptions[0]?.dataset.robot;
      const matching = plans.filter(item => item.robot_type === type);
      document.getElementById('qc-existing-plans').textContent = !type ?
        '先选择数据集；若已有同类型方案，可直接复用。' : matching.length ?
        `已有 ${matching.length} 个 ${type} 方案可直接复用：${matching.map(item => item.name).join('、')}。仍可创建独立方案。` :
        `目前没有 ${type} 方案，将以此数据集的元数据创建初始配置。`;
    });
    document.getElementById('qc-create-form').addEventListener('submit', async event => {
      event.preventDefault();
      if (!dataset.value) { show('请选择已登记数据集', true); return; }
      const button = document.getElementById('qc-plan-create');
      button.disabled = true;
      try {
        const plan = await request(`/qc/api/plans/from-dataset/${encodeURIComponent(dataset.value)}`, { method: 'POST' });
        location.href = `/qc/plans/${encodeURIComponent(plan.id)}`;
      } catch (error) { closeDialog(dialog); show(error.message, true); button.disabled = false; }
    });
    document.querySelectorAll('[data-delete-plan]').forEach(button => button.addEventListener('click', async () => {
      if (!window.confirm(`删除方案「${button.dataset.planName}」？已有任务与报告会保留原方案版本。`)) return;
      button.disabled = true;
      try {
        await request(`/qc/api/plans/${encodeURIComponent(button.dataset.deletePlan)}`, { method: 'DELETE' });
        location.reload();
      } catch (error) { show(error.message, true); button.disabled = false; }
    }));
  }

  const editor = document.getElementById('qc-plan-editor');
  if (editor) {
    const plan = readJson('qc-plan-json');
    const types = readJson('qc-rule-types-json');
    const config = structuredClone(plan.config);
    const value = (id, content) => { document.getElementById(id).value = content ?? ''; };
    value('qc-plan-name', plan.name);
    value('qc-expected-fps', config.robot.expected_fps);
    value('qc-state-key', config.robot.state_key);
    value('qc-state-dim', config.robot.state_dim);
    value('qc-action-key', config.robot.action_key);
    value('qc-action-dim', config.robot.action_dim);
    value('qc-video-keys', config.robot.video_keys.join('\n'));
    value('qc-joint-limits', Object.keys(config.robot.joint_limits || {}).length ? JSON.stringify(config.robot.joint_limits, null, 2) : '');
    value('qc-doctor-max', config.doctor.max_episodes);
    value('qc-vlm-model', config.vlm.model);
    value('qc-vlm-timeout', config.vlm.timeout_seconds);
    value('qc-vlm-tokens', config.vlm.max_tokens);
    value('qc-vlm-max', config.vlm.max_episodes);
    const doctorList = document.getElementById('qc-doctor-list');
    const doctorNames = Object.fromEntries(types.doctor);
    doctorList.innerHTML = config.doctor.checks.map(row =>
      `<label><input type="checkbox" data-doctor="${esc(row.id)}" ${row.enabled ? 'checked' : ''}><span>${esc(doctorNames[row.id] || row.id)}<small>${esc(row.id)}</small></span></label>`).join('');
    const editorTabs = document.querySelectorAll('[data-editor-tab]');
    editorTabs.forEach(button => button.addEventListener('click', () => {
      editorTabs.forEach(item => item.classList.toggle('active', item === button));
      document.querySelectorAll('[data-editor-panel]').forEach(panel => { panel.hidden = panel.dataset.editorPanel !== button.dataset.editorTab; });
    }));
    const options = (catalog, selected) => Object.entries(catalog).map(([key, item]) =>
      `<option value="${esc(key)}" ${key === selected ? 'selected' : ''}>${esc(item.label)}</option>`).join('');
    function renderRobot() {
      document.getElementById('qc-robot-rule-list').innerHTML = config.robot_rules.map((rule, index) => {
        const params = Object.entries(rule.params).map(([key, item]) => `<label>${esc(key)}${key === 'field' ?
          `<select data-index="${index}" data-param="${esc(key)}"><option value="action" ${item === 'action' ? 'selected' : ''}>action</option><option value="state" ${item === 'state' ? 'selected' : ''}>state</option></select>` :
          `<input data-index="${index}" data-param="${esc(key)}" type="number" min="0" step="any" value="${esc(item)}">`}</label>`).join('');
        return `<article class="qc-rule-edit"><div class="qc-rule-title"><label class="qc-rule-heading">规则名称<input data-index="${index}" data-robot-field="name" maxlength="100" value="${esc(rule.name)}"><small>系统标识：${esc(rule.id)}</small></label><button type="button" data-remove-robot="${index}">删除规则</button></div><label>规则类型<select data-index="${index}" data-robot-field="kind">${options(types.robot, rule.kind)}</select></label><label>严重度<select data-index="${index}" data-robot-field="severity"><option value="WARN" ${rule.severity === 'WARN' ? 'selected' : ''}>警告</option><option value="FAIL" ${rule.severity === 'FAIL' ? 'selected' : ''}>失败</option></select></label>${params}<label class="check"><input data-index="${index}" data-robot-field="enabled" type="checkbox" ${rule.enabled ? 'checked' : ''}>默认启用</label></article>`;
      }).join('') || '<p class="dm-empty">尚未配置机器人专项规则。</p>';
    }
    function renderVlm() {
      document.getElementById('qc-vlm-rule-list').innerHTML = config.vlm.rules.map((rule, index) =>
        `<article class="qc-rule-edit"><div class="qc-rule-title"><strong>${esc(rule.id)}</strong><button type="button" data-remove-vlm="${index}">删除规则</button></div><label>规则类型<select data-index="${index}" data-vlm-field="kind">${options(types.vlm, rule.kind)}</select></label><label>显示名称<input data-index="${index}" data-vlm-field="name" maxlength="100" value="${esc(rule.name)}"></label><label>相机视角<select data-index="${index}" data-vlm-field="camera_key"><option value="">默认主视角</option>${config.robot.video_keys.map(key => `<option value="${esc(key)}" ${key === rule.camera_key ? 'selected' : ''}>${esc(key)}</option>`).join('')}</select></label><label class="full">判断要求<textarea data-index="${index}" data-vlm-field="prompt" rows="3" maxlength="1500">${esc(rule.prompt)}</textarea></label><label class="check"><input data-index="${index}" data-vlm-field="enabled" type="checkbox" ${rule.enabled ? 'checked' : ''}>默认启用</label></article>`
      ).join('') || '<p class="dm-empty">尚未配置 VLM 视频规则。</p>';
    }
    renderRobot(); renderVlm();
    document.getElementById('qc-add-robot-rule').addEventListener('click', () => {
      const kind = 'feature_schema';
      config.robot_rules.push({ id: `robot_${crypto.randomUUID().slice(0, 8)}`, kind,
        name: types.robot[kind].label, enabled: true, severity: 'WARN',
        params: structuredClone(types.robot[kind].params) });
      renderRobot();
    });
    document.getElementById('qc-add-vlm-rule').addEventListener('click', () => {
      const kind = 'custom';
      config.vlm.rules.push({ id: `vlm_${crypto.randomUUID().slice(0, 8)}`, kind,
        name: types.vlm[kind].label, enabled: false,
        camera_key: config.robot.video_keys[0] || '', prompt: types.vlm[kind].prompt });
      renderVlm();
    });
    editor.addEventListener('click', event => {
      const removeRobot = event.target.closest('[data-remove-robot]');
      const removeVlm = event.target.closest('[data-remove-vlm]');
      if (removeRobot) { config.robot_rules.splice(Number(removeRobot.dataset.removeRobot), 1); renderRobot(); }
      if (removeVlm) { config.vlm.rules.splice(Number(removeVlm.dataset.removeVlm), 1); renderVlm(); }
    });
    editor.addEventListener('change', event => {
      const target = event.target;
      if (target.dataset.doctor) {
        const row = config.doctor.checks.find(item => item.id === target.dataset.doctor);
        if (row) row.enabled = target.checked;
      }
      const robotField = target.dataset.robotField;
      if (robotField) {
        const row = config.robot_rules[Number(target.dataset.index)];
        const previousDefaultName = types.robot[row.kind]?.label;
        const hasCustomName = row.name !== previousDefaultName;
        row[robotField] = robotField === 'enabled' ? target.checked : target.value;
        if (robotField === 'kind') {
          if (!hasCustomName) row.name = types.robot[row.kind].label;
          row.params = structuredClone(types.robot[row.kind].params);
          renderRobot();
        }
      }
      const vlmField = target.dataset.vlmField;
      if (vlmField) {
        const row = config.vlm.rules[Number(target.dataset.index)];
        row[vlmField] = vlmField === 'enabled' ? target.checked : target.value;
        if (vlmField === 'kind') {
          row.name = types.vlm[row.kind].label;
          row.prompt = types.vlm[row.kind].prompt;
          renderVlm();
        }
      }
      if (target.dataset.param) config.robot_rules[Number(target.dataset.index)].params[target.dataset.param] =
        target.dataset.param === 'field' ? target.value : Number(target.value);
    });
    editor.addEventListener('input', event => {
      const target = event.target;
      if (target.dataset.robotField === 'name') config.robot_rules[Number(target.dataset.index)].name = target.value;
      if (target.dataset.vlmField === 'name' || target.dataset.vlmField === 'prompt')
        config.vlm.rules[Number(target.dataset.index)][target.dataset.vlmField] = target.value;
      if (target.dataset.param && target.dataset.param !== 'field')
        config.robot_rules[Number(target.dataset.index)].params[target.dataset.param] = Number(target.value);
    });
    document.getElementById('qc-plan-form').addEventListener('submit', async event => {
      event.preventDefault();
      const text = id => document.getElementById(id).value.trim();
      try {
        config.robot.expected_fps = text('qc-expected-fps') ? Number(text('qc-expected-fps')) : null;
        config.robot.state_key = text('qc-state-key');
        config.robot.state_dim = text('qc-state-dim') ? Number(text('qc-state-dim')) : null;
        config.robot.action_key = text('qc-action-key');
        config.robot.action_dim = text('qc-action-dim') ? Number(text('qc-action-dim')) : null;
        config.robot.video_keys = text('qc-video-keys').split(/\r?\n/).map(item => item.trim()).filter(Boolean);
        config.robot.joint_limits = text('qc-joint-limits') ? JSON.parse(text('qc-joint-limits')) : {};
        config.doctor.max_episodes = text('qc-doctor-max') ? Number(text('qc-doctor-max')) : null;
        config.vlm.model = text('qc-vlm-model');
        config.vlm.timeout_seconds = Number(text('qc-vlm-timeout'));
        config.vlm.max_tokens = Number(text('qc-vlm-tokens'));
        config.vlm.max_episodes = text('qc-vlm-max') ? Number(text('qc-vlm-max')) : null;
        const result = await request(`/qc/api/plans/${encodeURIComponent(plan.id)}`, {
          method: 'PUT', ...json({ name: text('qc-plan-name'), robot_type: plan.robot_type,
            expected_version: plan.version, config })
        });
        show(`方案已保存为 v${result.version}`);
        setTimeout(() => location.reload(), 900);
      } catch (error) { show(error.message, true); }
    });
  }

  const reportPage = document.getElementById('qc-report-page');
  if (reportPage) {
    const taskId = reportPage.dataset.taskId;
    const sectionButtons = [...document.querySelectorAll('[data-report-tab]')];
    const sectionPanels = [...document.querySelectorAll('[data-report-panel]')];
    const openSection = section => {
      sectionButtons.forEach(button => {
        const active = button.dataset.reportTab === section;
        button.classList.toggle('active', active);
        button.setAttribute('aria-expanded', String(active));
      });
      sectionPanels.forEach(panel => { panel.hidden = panel.dataset.reportPanel !== section; });
      document.getElementById('qc-report-prompt').hidden = Boolean(section);
    };
    sectionButtons.forEach(button => button.addEventListener('click', () => {
      const section = button.dataset.reportTab;
      openSection(section);
      history.replaceState(null, '', `#${section}`);
    }));
    const initialSection = location.hash.slice(1);
    if (sectionButtons.some(button => button.dataset.reportTab === initialSection)) openSection(initialSection);
    const dialog = document.getElementById('qc-review-dialog');
    const reason = document.getElementById('qc-review-reason');
    let decision = null;
    const names = { PASS: '通过', REVIEW: '待复核', RECAPTURE: '打回重采' };
    document.querySelectorAll('[data-qc-decision]').forEach(button => button.addEventListener('click', () => {
      decision = button.dataset.qcDecision;
      document.getElementById('qc-review-title').textContent = `人工判定 · ${names[decision]}`;
      reason.required = decision === 'RECAPTURE' || (decision === 'PASS' && reportPage.dataset.outcome !== 'PASS');
      reason.value = '';
      openDialog(dialog);
    }));
    document.getElementById('qc-review-cancel').addEventListener('click', () => closeDialog(dialog));
    document.getElementById('qc-review-form').addEventListener('submit', async event => {
      event.preventDefault();
      try {
        await request(`/qc/api/tasks/${encodeURIComponent(taskId)}/review`, {
          method: 'POST', ...json({ decision, reason: reason.value })
        });
        location.reload();
      } catch (error) { closeDialog(dialog); show(error.message, true); }
    });
    document.getElementById('qc-rerun').addEventListener('click', async () => {
      try {
        const task = await request(`/qc/api/tasks/${encodeURIComponent(taskId)}/rerun`, { method: 'POST' });
        location.href = `/qc/reports/${encodeURIComponent(task.id)}`;
      } catch (error) { show(error.message, true); }
    });
    if (['QUEUED', 'RUNNING'].includes(reportPage.dataset.status)) {
      const timer = setInterval(async () => {
        try {
          const result = await request(`/qc/api/tasks/${encodeURIComponent(taskId)}`);
          if (!['QUEUED', 'RUNNING'].includes(result.task.status) || result.task.phase !== reportPage.dataset.phase) {
            clearInterval(timer); location.reload();
          }
        } catch { clearInterval(timer); }
      }, 4000);
    }
  }
})();
