ALTER TABLE users
    ADD COLUMN display_name VARCHAR(64) NOT NULL DEFAULT '待设置' AFTER username,
    ADD COLUMN must_change_password TINYINT NOT NULL DEFAULT 0 AFTER password_hash,
    ADD COLUMN password_changed_at VARCHAR(40) AFTER must_change_password;

UPDATE users SET display_name = username WHERE display_name = '待设置';

ALTER TABLE users
    ALTER COLUMN display_name DROP DEFAULT,
    ADD CONSTRAINT users_must_change_password_ck
        CHECK (must_change_password IN (0, 1)),
    ADD CONSTRAINT users_display_name_ck
        CHECK (CHAR_LENGTH(TRIM(display_name)) BETWEEN 1 AND 64);
