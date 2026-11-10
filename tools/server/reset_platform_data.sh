#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
用途：永久清空 HKC 平台 storage 与 MySQL 数据，并重新创建空白平台。

本机示例：
  tools/server/reset_platform_data.sh \
    --env-file .env \
    --project-name hkc-v31-mysql \
    --compose-file compose.yaml \
    --compose-file compose.dev.yaml \
    --compose-file deploy/compose.local-mysql.yaml \
    --confirm DELETE-ALL-HKC-DATA

参数：
  --env-file PATH       Compose 环境文件（必填）
  --project-name NAME   Compose 项目名（必填）
  --compose-file PATH   Compose 文件，可重复指定（至少一个）
  --confirm TEXT        必须精确为 DELETE-ALL-HKC-DATA
EOF
}

env_file=""
project_name=""
confirmation=""
compose_files=()

while (($#)); do
  case "$1" in
    --env-file)
      env_file=${2:-}
      shift 2
      ;;
    --project-name)
      project_name=${2:-}
      shift 2
      ;;
    --compose-file)
      compose_files+=("${2:-}")
      shift 2
      ;;
    --confirm)
      confirmation=${2:-}
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "未知参数：$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "$env_file" || -z "$project_name" || ${#compose_files[@]} -eq 0 ]]; then
  echo "必须提供 --env-file、--project-name 和至少一个 --compose-file。" >&2
  exit 2
fi
if [[ "$confirmation" != "DELETE-ALL-HKC-DATA" ]]; then
  echo "操作已取消：必须明确传入 --confirm DELETE-ALL-HKC-DATA。" >&2
  exit 2
fi

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
project_root=$(cd -- "$script_dir/../.." && pwd -P)
if [[ "$env_file" = /* ]]; then
  env_path=$(realpath -m -- "$env_file")
else
  env_path=$(realpath -m -- "$project_root/$env_file")
fi
if [[ ! -f "$env_path" ]]; then
  echo "环境文件不存在：$env_path" >&2
  exit 2
fi

set -a
# shellcheck disable=SC1090
. "$env_path"
set +a

resolve_data_path() {
  local value=$1
  if [[ -z "$value" ]]; then
    return 1
  elif [[ "$value" = /* ]]; then
    realpath -m -- "$value"
  else
    realpath -m -- "$project_root/$value"
  fi
}

storage_path=$(resolve_data_path "${HKC_STORAGE_PATH:-}") || {
  echo "HKC_STORAGE_PATH 不能为空。" >&2
  exit 2
}
mysql_path=$(resolve_data_path "${HKC_MYSQL_DATA_PATH:-}") || {
  echo "HKC_MYSQL_DATA_PATH 不能为空。" >&2
  exit 2
}

validate_target() {
  local label=$1
  local target=$2
  if [[ "$target" == / || "$target" == "$HOME" || "$target" == "$project_root" ]]; then
    echo "拒绝清空危险的 $label 路径：$target" >&2
    exit 3
  fi
  if [[ "$target" != "$HOME"/* && "$target" != /srv/* && "$target" != /media/* && "$target" != /mnt/* ]]; then
    echo "拒绝清空不在允许根目录下的 $label 路径：$target" >&2
    exit 3
  fi
}

validate_target "storage" "$storage_path"
validate_target "MySQL" "$mysql_path"
if [[ "$storage_path" == "$mysql_path" ]]; then
  echo "storage 与 MySQL 路径不能相同。" >&2
  exit 3
fi

compose_args=(--env-file "$env_path" -p "$project_name")
for file in "${compose_files[@]}"; do
  if [[ "$file" = /* ]]; then
    compose_path=$(realpath -m -- "$file")
  else
    compose_path=$(realpath -m -- "$project_root/$file")
  fi
  if [[ ! -f "$compose_path" ]]; then
    echo "Compose 文件不存在：$compose_path" >&2
    exit 2
  fi
  compose_args+=(-f "$compose_path")
done

mkdir -p -- "$storage_path" "$mysql_path"
echo "即将永久清空："
echo "  storage: $storage_path"
echo "  MySQL:   $mysql_path"
echo "  Compose: $project_name"

docker compose "${compose_args[@]}" down
docker run --rm --user 0 \
  --volume "$storage_path:/reset-storage" \
  --volume "$mysql_path:/reset-mysql" \
  mysql:8.4 \
  sh -c 'find /reset-storage -mindepth 1 -delete && find /reset-mysql -mindepth 1 -delete'
docker compose "${compose_args[@]}" up -d --no-build --force-recreate --wait

echo "重置完成。平台已恢复为空数据库；当前没有用户、机器人或数据集。"
