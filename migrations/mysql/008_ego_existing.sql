-- Databases that already ran the previous 007 may still contain generic records.
-- Keep their files and DS identifiers while moving them to the EGO category.
ALTER TABLE datasets DROP CHECK datasets_family_ck;
ALTER TABLE datasets DROP CHECK datasets_availability_ck;
UPDATE datasets SET dataset_family = 'ego', availability = 'unavailable'
    WHERE dataset_family = 'generic';
INSERT INTO dataset_status_labels (dataset_id, label, created_at)
    SELECT d.id, '待质检', d.updated_at FROM datasets AS d
    WHERE d.dataset_family = 'ego' AND NOT EXISTS (
        SELECT 1 FROM dataset_status_labels AS s WHERE s.dataset_id = d.id
    );
ALTER TABLE datasets ADD CONSTRAINT datasets_family_ck
    CHECK (dataset_family IN ('vla', 'wam', 'ego'));
ALTER TABLE datasets ADD CONSTRAINT datasets_availability_ck
    CHECK (availability IN ('available', 'unavailable'));
