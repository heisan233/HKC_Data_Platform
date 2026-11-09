ALTER TABLE datasets DROP CHECK datasets_area_ck;
ALTER TABLE datasets ADD CONSTRAINT datasets_area_ck
    CHECK (storage_area IN ('raw', 'derived', 'data'));
ALTER TABLE datasets ADD COLUMN availability VARCHAR(16) NOT NULL DEFAULT 'unavailable';
ALTER TABLE datasets ADD CONSTRAINT datasets_availability_ck
    CHECK (availability IN ('available', 'unavailable'));
CREATE INDEX datasets_availability_idx ON datasets (availability, seq);

CREATE TABLE IF NOT EXISTS dataset_status_labels (
    dataset_id VARCHAR(32) NOT NULL,
    label VARCHAR(32) NOT NULL,
    created_at VARCHAR(40) NOT NULL,
    PRIMARY KEY (dataset_id, label),
    CONSTRAINT dataset_status_labels_dataset_fk FOREIGN KEY (dataset_id)
        REFERENCES datasets(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

UPDATE datasets d
JOIN qc_tasks q ON q.dataset_id = d.id
SET d.availability = 'available'
WHERE q.seq = (SELECT MAX(latest.seq) FROM qc_tasks latest WHERE latest.dataset_id = d.id)
  AND q.status = 'COMPLETED' AND q.decision = 'PASS';

INSERT INTO dataset_status_labels (dataset_id, label, created_at)
SELECT d.id, '待质检', d.registered_at FROM datasets d
WHERE NOT EXISTS (SELECT 1 FROM qc_tasks q WHERE q.dataset_id = d.id);
