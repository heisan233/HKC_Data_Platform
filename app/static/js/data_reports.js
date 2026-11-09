(() => {
  const page = document.getElementById('dm-reports-page');
  if (!page) return;
  const feedback = document.getElementById('dm-report-feedback');
  page.addEventListener('click', async event => {
    const button = event.target.closest('.dm-delete-report');
    if (!button || button.disabled) return;
    const filename = button.dataset.filename;
    button.disabled = true;
    feedback.hidden = true;
    try {
      const url = `/data/reports/${encodeURIComponent(button.dataset.datasetId)}/${encodeURIComponent(filename)}`;
      const response = await fetch(url, { method: 'DELETE' });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || `移除失败（${response.status}）`);
      location.reload();
    } catch (error) {
      feedback.textContent = error.message;
      feedback.classList.add('dm-feedback-error');
      feedback.hidden = false;
      button.disabled = false;
    }
  });
})();
