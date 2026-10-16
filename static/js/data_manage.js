(() => {
  const message = document.getElementById('dm-message');
  const show = (text, error = false) => {
    if (!message) return;
    message.hidden = false;
    message.textContent = text;
    message.classList.toggle('dm-feedback-error', error);
    message.scrollIntoView({ block: 'nearest' });
  };
  const call = async (url, options = {}) => {
    const response = await fetch(url, options);
    let body;
    try { body = await response.json(); } catch { body = {}; }
    if (!response.ok) throw new Error(body.detail || `请求失败（${response.status}）`);
    return body;
  };

  const detail = document.getElementById('dm-manage-detail');
  if (detail) {
    const id = encodeURIComponent(detail.dataset.id);
    const base = `/data/manage/raw/${id}`;
    const metadata = document.getElementById('dm-metadata-form');
    metadata.addEventListener('submit', async event => {
      event.preventDefault();
      const form = new FormData(metadata);
      const button = metadata.querySelector('button');
      button.disabled = true;
      try {
        await call(`${base}/metadata`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: form.get('name'), description: form.get('description'),
            tags: String(form.get('tags') || '').split(/[,，]/).map(tag => tag.trim()).filter(Boolean) })
        });
        location.reload();
      } catch (error) { show(error.message, true); button.disabled = false; }
    });

    document.getElementById('dm-scan').addEventListener('click', async event => {
      event.currentTarget.disabled = true;
      try { await call(`${base}/scan`, { method: 'POST' }); location.reload(); }
      catch (error) { show(error.message, true); event.currentTarget.disabled = false; }
    });

    const upload = document.getElementById('dm-update-form');
    upload.addEventListener('submit', async event => {
      event.preventDefault();
      const button = upload.querySelector('button');
      button.disabled = true;
      show('正在上传并创建质检任务，请稍候…');
      try {
        await call(`${base}/updates`, { method: 'POST', body: new FormData(upload) });
        location.reload();
      } catch (error) { show(error.message, true); button.disabled = false; }
    });

    document.querySelectorAll('[data-action][data-update]').forEach(button => {
      button.addEventListener('click', async () => {
        const action = button.dataset.action;
        if (!confirm(action === 'apply' ? '应用已通过质检的新版本？当前数据目录将被替换。' : '取消并删除此更新候选目录？')) return;
        button.disabled = true;
        try {
          await call(`${base}/updates/${encodeURIComponent(button.dataset.update)}/${action}`, { method: 'POST' });
          location.reload();
        } catch (error) { show(error.message, true); button.disabled = false; }
      });
    });

    const deleteDialog = document.getElementById('dm-delete-dialog');
    const deleteError = document.getElementById('dm-delete-error');
    document.getElementById('dm-delete').addEventListener('click', () => {
      deleteError.hidden = true;
      deleteDialog.showModal();
    });
    deleteDialog.querySelector('form').addEventListener('submit', async event => {
      if (event.submitter?.value === 'cancel') return;
      event.preventDefault();
      const code = detail.dataset.code;
      const button = document.getElementById('dm-delete-confirm');
      button.disabled = true;
      deleteError.hidden = true;
      try {
        await call(base, { method: 'DELETE' });
        location.replace(`/data/manage/?deleted=${encodeURIComponent(code)}`);
      }
      catch (error) {
        deleteError.textContent = error.message;
        deleteError.hidden = false;
        button.disabled = false;
      }
    });
  }

  const records = document.getElementById('dm-records');
  if (records) {
    const dialog = document.getElementById('dm-purge-dialog');
    const purgeError = document.getElementById('dm-purge-error');
    let selected = null;
    records.querySelectorAll('[data-purge]').forEach(button => button.addEventListener('click', () => {
      selected = button.dataset;
      document.getElementById('dm-purge-description').textContent =
        `${selected.code} · 关联 ${selected.jobs} 条质检任务、${selected.reports} 份报告。`;
      purgeError.hidden = true;
      document.getElementById('dm-purge-submit').disabled = false;
      document.getElementById('dm-purge-reports').checked = false;
      dialog.showModal();
    }));
    dialog.querySelector('form').addEventListener('submit', async event => {
      if (event.submitter?.value === 'cancel') return;
      event.preventDefault();
      if (!selected) return;
      const button = document.getElementById('dm-purge-submit');
      button.disabled = true;
      purgeError.hidden = true;
      try {
        await call(`/data/records/${encodeURIComponent(selected.purge)}`, {
          method: 'DELETE', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ confirmed: true,
            purge_reports: document.getElementById('dm-purge-reports').checked })
        });
        location.reload();
      } catch (error) {
        purgeError.textContent = error.message;
        purgeError.hidden = false;
        button.disabled = false;
      }
    });
  }
})();
