ALTER TABLE datasets
    ADD COLUMN dataset_family VARCHAR(8) NOT NULL DEFAULT 'vla' AFTER robot_type,
    ADD CONSTRAINT datasets_family_ck CHECK (dataset_family IN ('vla', 'wam'));

CREATE INDEX datasets_family_availability_idx
    ON datasets (dataset_family, availability, deleted_at);
