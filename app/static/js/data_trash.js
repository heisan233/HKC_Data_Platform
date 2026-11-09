(() => {
  const page = document.getElementById('dm-trash-page');
  if (!page) return;
  const feedback = document.getElementById('dm-trash-feedback');
  page.addEventListener('click', async event => {
    const button = event.target.closest('.dm-restore-dataset, .dm-purge-dataset, .dm-restore-report, .dm-purge-report');
    if (!button || button.disabled) return;
    const datasetId = encodeURIComponent(button.dataset.datasetId);
    const filename = button.dataset.filename && encodeURIComponent(button.dataset.filename);
    let url;
    if (button.classList.contains('dm-restore-dataset')) url = `/data/api/datasets/${datasetId}/restore`;
    else if (button.classList.contains('dm-purge-dataset')) url = `/data/api/datasets/${datasetId}/purge`;
    else if (button.classList.contains('dm-restore-report')) url = `/data/reports/trash/${datasetId}/${filename}/restore`;
    else url = `/data/reports/trash/${datasetId}/${filename}/purge`;
    button.disabled = true;
    feedback.hidden = false;
    feedback.classList.remove('dm-feedback-error');
    feedback.textContent = '正在处理…';
    try {
      const response = await fetch(url, { method: 'POST' });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || `操作失败（${response.status}）`);
      location.reload();
    } catch (error) {
      feedback.textContent = error.message;
      feedback.classList.add('dm-feedback-error');
      button.disabled = false;
    }
  });
})();
