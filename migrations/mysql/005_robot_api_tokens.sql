CREATE TABLE IF NOT EXISTS robot_api_tokens (
    robot_id VARCHAR(32) NOT NULL PRIMARY KEY,
    token_value VARCHAR(160) NOT NULL,
    token_sha256 CHAR(64) NOT NULL UNIQUE,
    token_prefix VARCHAR(32) NOT NULL,
    created_at VARCHAR(40) NOT NULL,
    last_used_at VARCHAR(40),
    CONSTRAINT robot_api_tokens_robot_fk FOREIGN KEY (robot_id)
        REFERENCES robots(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;
