#!/usr/bin/env bash
# ============================================================
# 按 third_party/PROVENANCE.md 的 pin 拉取第三方源码（Linux/macOS）
#
# 与 third_party/fetch_sources.ps1 保持同一组 commit；Windows 继续用 ps1。
# 脚本只拉源码，不下载任何上游预编译二进制，也不改写 pin 之外的提交。
#
# 用法:
#   bash scripts/fetch_sources_linux.sh
#   bash scripts/fetch_sources_linux.sh --verify-only
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
THIRD_PARTY="${REPO_ROOT}/third_party"

FLUENTUI_COMMIT="7e33a2f672d18239ef49ab960075b1137a1d18e7"
PYSIDE_FLUENTUI_COMMIT="617986a17c9850ecb365bc02713433abab79c7aa"

VERIFY_ONLY=0
if [ "${1:-}" = "--verify-only" ]; then
    VERIFY_ONLY=1
fi

log()  { printf '%s\n' "[sources] $*"; }
fail() { printf '%s\n' "[sources] ERROR: $*" >&2; exit 1; }

ensure_repo() {
    local name="$1" url="$2" commit="$3" dir="${THIRD_PARTY}/$1"

    if [ "${VERIFY_ONLY}" -eq 1 ]; then
        [ -d "${dir}/.git" ] || fail "${name}: 源码目录不存在（${dir}）"
    elif [ -d "${dir}/.git" ]; then
        if ! git -C "${dir}" diff --quiet || ! git -C "${dir}" diff --cached --quiet; then
            fail "${name}: 工作区有本地改动，拒绝覆盖。请先提交/清理，或删除 ${dir} 后重试"
        fi
        log "${name}: 更新已存在的源码树"
        git -C "${dir}" fetch --all --tags --prune
        git -C "${dir}" checkout --detach "${commit}"
    else
        log "${name}: 克隆 ${url}"
        git clone --recursive "${url}" "${dir}"
        git -C "${dir}" checkout --detach "${commit}"
    fi

    git -C "${dir}" submodule update --init --recursive
    local actual
    actual="$(git -C "${dir}" rev-parse HEAD)"
    [ "${actual}" = "${commit}" ] || fail "${name}: HEAD=${actual}，期望 ${commit}"
    log "${name}: OK (${actual})"
}

ensure_repo "FluentUI" "https://github.com/zhuzichu520/FluentUI" "${FLUENTUI_COMMIT}"
ensure_repo "PySide6-FluentUI-QML" "https://github.com/zhuzichu520/PySide6-FluentUI-QML" "${PYSIDE_FLUENTUI_COMMIT}"

log "第三方源码已就绪"
