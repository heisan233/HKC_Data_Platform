document.addEventListener('DOMContentLoaded', function () {
    const searchInput = document.getElementById('datasetSearch');
    const datasetRows = Array.from(document.querySelectorAll('.dataset-row'));
    const emptySearch = document.getElementById('emptySearch');

    if (searchInput) {
        searchInput.addEventListener('input', function () {
            const query = this.value.trim().toLocaleLowerCase();
            let visibleCount = 0;

            datasetRows.forEach(function (row) {
                const matches = row.dataset.search.toLocaleLowerCase().includes(query);
                row.hidden = !matches;
                if (matches) visibleCount += 1;
            });

            if (emptySearch) emptySearch.hidden = visibleCount !== 0;
        });
    }

    document.querySelectorAll('.video-select').forEach(function (select) {
        select.addEventListener('change', function () {
            const card = this.closest('.camera-card');
            const video = card ? card.querySelector('.dataset-video') : null;
            if (!video) return;

            video.pause();
            video.src = this.value;
            video.load();
        });
    });
});
