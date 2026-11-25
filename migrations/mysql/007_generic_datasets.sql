ALTER TABLE datasets DROP CHECK datasets_family_ck;
ALTER TABLE datasets ADD CONSTRAINT datasets_family_ck
    CHECK (dataset_family IN ('vla', 'wam', 'ego'));

ALTER TABLE datasets DROP CHECK datasets_availability_ck;
ALTER TABLE datasets ADD CONSTRAINT datasets_availability_ck
    CHECK (availability IN ('available', 'unavailable'));

ALTER TABLE datasets MODIFY COLUMN info_sha256 CHAR(64) NULL;
ALTER TABLE datasets ADD COLUMN manifest_sha256 CHAR(64) NULL AFTER info_sha256;
