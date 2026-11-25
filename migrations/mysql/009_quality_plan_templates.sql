ALTER TABLE qc_plans
    ADD COLUMN dataset_family VARCHAR(8) NOT NULL DEFAULT 'vla' AFTER robot_type,
    ADD COLUMN template_id VARCHAR(32) NOT NULL DEFAULT 'vla' AFTER dataset_family,
    ADD COLUMN template_version INTEGER NOT NULL DEFAULT 1 AFTER template_id,
    ADD CONSTRAINT qc_plans_family_ck CHECK (dataset_family IN ('vla', 'wam', 'ego'));

CREATE INDEX qc_plans_family_type_idx
    ON qc_plans (dataset_family, robot_type, archived_at);
