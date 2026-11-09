ALTER TABLE datasets ADD COLUMN deleted_at VARCHAR(40) NULL;
ALTER TABLE datasets ADD COLUMN deleted_by VARCHAR(100) NULL;
ALTER TABLE datasets ADD COLUMN purge_requested_at VARCHAR(40) NULL;
CREATE INDEX datasets_deleted_idx ON datasets (deleted_at, seq);
