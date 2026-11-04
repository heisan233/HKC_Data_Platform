CREATE TABLE IF NOT EXISTS dataset_numbers (
    seq BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

CREATE TABLE IF NOT EXISTS datasets (
    id VARCHAR(32) NOT NULL PRIMARY KEY,
    seq BIGINT UNSIGNED NOT NULL UNIQUE,
    storage_area VARCHAR(16) NOT NULL,
    storage_relpath VARCHAR(255) NOT NULL,
    name VARCHAR(255) NOT NULL,
    robot_type VARCHAR(255) NOT NULL,
    description TEXT NOT NULL,
    tags_json LONGTEXT NOT NULL,
    source_dataset_ids_json LONGTEXT NOT NULL,
    info_sha256 CHAR(64) NOT NULL,
    registered_at VARCHAR(40) NOT NULL,
    updated_at VARCHAR(40) NOT NULL,
    CONSTRAINT datasets_area_ck CHECK (storage_area IN ('raw', 'derived')),
    UNIQUE KEY datasets_location_idx (storage_area, storage_relpath),
    KEY datasets_registered_idx (registered_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

CREATE TABLE IF NOT EXISTS users (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(64) COLLATE utf8mb4_0900_as_ci NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    is_active TINYINT NOT NULL DEFAULT 1,
    created_at VARCHAR(40) NOT NULL,
    updated_at VARCHAR(40) NOT NULL,
    last_login_at VARCHAR(40),
    CONSTRAINT users_active_ck CHECK (is_active IN (0, 1))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

CREATE TABLE IF NOT EXISTS user_sessions (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    token_hash CHAR(64) NOT NULL UNIQUE,
    user_id BIGINT UNSIGNED NOT NULL,
    created_at VARCHAR(40) NOT NULL,
    expires_at VARCHAR(40) NOT NULL,
    KEY user_sessions_user_idx (user_id),
    KEY user_sessions_expiry_idx (expires_at),
    CONSTRAINT user_sessions_user_fk FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

CREATE TABLE IF NOT EXISTS robot_numbers (
    seq BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

CREATE TABLE IF NOT EXISTS robots (
    id VARCHAR(32) NOT NULL PRIMARY KEY,
    seq BIGINT UNSIGNED NOT NULL UNIQUE,
    name VARCHAR(100) COLLATE utf8mb4_0900_as_ci NOT NULL UNIQUE,
    robot_type VARCHAR(100) NOT NULL,
    model VARCHAR(100) NOT NULL DEFAULT '',
    serial_number VARCHAR(100) COLLATE utf8mb4_0900_as_ci UNIQUE,
    status VARCHAR(16) NOT NULL DEFAULT 'active',
    network_address VARCHAR(255) NOT NULL DEFAULT '',
    sensors_json LONGTEXT NOT NULL,
    notes TEXT NOT NULL,
    created_at VARCHAR(40) NOT NULL,
    updated_at VARCHAR(40) NOT NULL,
    CONSTRAINT robots_status_ck CHECK (status IN ('active', 'maintenance', 'inactive')),
    KEY robots_status_idx (status),
    KEY robots_type_idx (robot_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

CREATE TABLE IF NOT EXISTS dataset_robot_links (
    dataset_id VARCHAR(32) NOT NULL PRIMARY KEY,
    robot_id VARCHAR(32) NOT NULL,
    linked_at VARCHAR(40) NOT NULL,
    updated_at VARCHAR(40) NOT NULL,
    updated_by VARCHAR(100) NOT NULL DEFAULT '',
    KEY dataset_robot_links_robot_idx (robot_id, dataset_id),
    CONSTRAINT dataset_robot_links_dataset_fk FOREIGN KEY (dataset_id)
        REFERENCES datasets(id) ON DELETE CASCADE,
    CONSTRAINT dataset_robot_links_robot_fk FOREIGN KEY (robot_id)
        REFERENCES robots(id) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

CREATE TABLE IF NOT EXISTS qc_plans (
    seq BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    id VARCHAR(32) UNIQUE,
    name VARCHAR(255) NOT NULL,
    robot_type VARCHAR(255) NOT NULL,
    current_version INTEGER NOT NULL DEFAULT 1,
    created_at VARCHAR(40) NOT NULL,
    updated_at VARCHAR(40) NOT NULL,
    archived_at VARCHAR(40)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

CREATE TABLE IF NOT EXISTS qc_plan_versions (
    plan_id VARCHAR(32) NOT NULL,
    version INTEGER NOT NULL,
    config_json LONGTEXT NOT NULL,
    created_at VARCHAR(40) NOT NULL,
    PRIMARY KEY (plan_id, version),
    CONSTRAINT qc_plan_versions_plan_fk FOREIGN KEY (plan_id) REFERENCES qc_plans(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

CREATE TABLE IF NOT EXISTS qc_tasks (
    seq BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    id VARCHAR(32) NOT NULL UNIQUE,
    dataset_id VARCHAR(32) NOT NULL,
    plan_id VARCHAR(32) NOT NULL,
    plan_version INTEGER NOT NULL,
    snapshot_json LONGTEXT NOT NULL,
    raw_fingerprint VARCHAR(255) NOT NULL,
    status VARCHAR(32) NOT NULL,
    phase VARCHAR(64) NOT NULL,
    outcome VARCHAR(32),
    decision VARCHAR(32),
    error TEXT,
    created_at VARCHAR(40) NOT NULL,
    started_at VARCHAR(40),
    completed_at VARCHAR(40),
    updated_at VARCHAR(40) NOT NULL,
    archived_at VARCHAR(40),
    hidden_at VARCHAR(40),
    KEY qc_tasks_dataset_idx (dataset_id, created_at),
    CONSTRAINT qc_tasks_dataset_fk FOREIGN KEY (dataset_id) REFERENCES datasets(id),
    CONSTRAINT qc_tasks_plan_fk FOREIGN KEY (plan_id) REFERENCES qc_plans(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

CREATE TABLE IF NOT EXISTS qc_reviews (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    task_id VARCHAR(32) NOT NULL,
    decision VARCHAR(32) NOT NULL,
    reason TEXT NOT NULL,
    created_at VARCHAR(40) NOT NULL,
    CONSTRAINT qc_reviews_task_fk FOREIGN KEY (task_id) REFERENCES qc_tasks(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

CREATE TABLE IF NOT EXISTS qc_runtime_control (
    id TINYINT NOT NULL PRIMARY KEY,
    vlm_paused TINYINT NOT NULL DEFAULT 0,
    CONSTRAINT qc_runtime_control_id_ck CHECK (id = 1)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

INSERT INTO qc_runtime_control (id, vlm_paused) VALUES (1, 0)
    ON DUPLICATE KEY UPDATE id = id;

CREATE TABLE IF NOT EXISTS conversion_jobs (
    id VARCHAR(40) NOT NULL PRIMARY KEY,
    profile_id VARCHAR(64) NOT NULL,
    source_dataset_id VARCHAR(32) NOT NULL,
    source_name VARCHAR(255) NOT NULL,
    source_fingerprint VARCHAR(255) NOT NULL,
    status VARCHAR(32) NOT NULL,
    completed_episodes BIGINT NOT NULL,
    total_episodes BIGINT NOT NULL,
    files_json LONGTEXT NOT NULL,
    error TEXT,
    created_at VARCHAR(40) NOT NULL,
    started_at VARCHAR(40),
    finished_at VARCHAR(40),
    KEY conversion_jobs_created_idx (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

CREATE TABLE IF NOT EXISTS training_jobs (
    seq BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    id VARCHAR(32) UNIQUE,
    name VARCHAR(255) NOT NULL,
    dataset_id VARCHAR(32) NOT NULL,
    model_id VARCHAR(64) NOT NULL,
    model_version VARCHAR(64) NOT NULL,
    preset VARCHAR(64) NOT NULL,
    executor_id VARCHAR(64) NOT NULL,
    runtime_id VARCHAR(64) NOT NULL DEFAULT 'lerobot',
    status VARCHAR(32) NOT NULL,
    phase VARCHAR(64) NOT NULL,
    progress INTEGER NOT NULL DEFAULT 0,
    snapshot_json LONGTEXT NOT NULL,
    split_json LONGTEXT NOT NULL,
    parameters_json LONGTEXT NOT NULL,
    latest_metric_json LONGTEXT,
    evaluation_json LONGTEXT,
    artifact_json LONGTEXT,
    error TEXT,
    retry_of VARCHAR(32),
    created_at VARCHAR(40) NOT NULL,
    started_at VARCHAR(40),
    completed_at VARCHAR(40),
    updated_at VARCHAR(40) NOT NULL,
    deleted_at VARCHAR(40),
    deleted_by VARCHAR(100),
    delete_requested_at VARCHAR(40),
    delete_requested_by VARCHAR(100),
    KEY training_jobs_dataset_idx (dataset_id, created_at),
    KEY training_jobs_deleted_idx (deleted_at, seq),
    CONSTRAINT training_jobs_dataset_fk FOREIGN KEY (dataset_id) REFERENCES datasets(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

CREATE TABLE IF NOT EXISTS semantic_definition_numbers (
    seq BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;

CREATE TABLE IF NOT EXISTS semantic_definitions (
    id VARCHAR(32) NOT NULL PRIMARY KEY,
    seq BIGINT UNSIGNED NOT NULL UNIQUE,
    category VARCHAR(16) NOT NULL,
    code VARCHAR(64) COLLATE utf8mb4_0900_as_ci NOT NULL,
    name VARCHAR(100) COLLATE utf8mb4_0900_as_ci NOT NULL,
    description TEXT NOT NULL,
    examples_json LONGTEXT NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'active',
    sort_order INTEGER NOT NULL DEFAULT 100,
    created_at VARCHAR(40) NOT NULL,
    updated_at VARCHAR(40) NOT NULL,
    UNIQUE KEY semantic_definitions_code_idx (category, code),
    UNIQUE KEY semantic_definitions_name_idx (category, name),
    KEY semantic_definitions_category_idx (category, status, sort_order, seq),
    CONSTRAINT semantic_definitions_category_ck CHECK (category IN ('scene', 'task', 'action', 'object')),
    CONSTRAINT semantic_definitions_status_ck CHECK (status IN ('active', 'inactive'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;
