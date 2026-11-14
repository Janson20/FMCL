#!/usr/bin/env bash
# ============================================================
# 在 Linux/macOS 上从 third_party/FluentUI 的 pin 源码编译 QML 插件。
#
# 与 scripts/build_fluentui.ps1 的目标一致：产物只落在项目内，
# 不污染共享 Qt SDK；QML 模块与 .so 最终都放进 third_party/_install/qml/FluentUI。
#
# 前置: cmake、ninja、C++ 编译器、Qt 6.7.3 SDK（含 Qt5Compat / ShaderTools）。
# 用法:
#   bash scripts/build_fluentui_linux.sh --qt-dir "$HOME/Qt/6.7.3/gcc_64"
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
THIRD_PARTY="${REPO_ROOT}/third_party"
SRC_DIR="${THIRD_PARTY}/FluentUI"
BUILD_DIR="${THIRD_PARTY}/_build_linux"
INSTALL_DIR="${THIRD_PARTY}/_install"
QML_DIR="${INSTALL_DIR}/qml/FluentUI"
QT_DIR="${QTDIR:-}"
JOBS="$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 2)"

usage() {
    echo "usage: $0 [--qt-dir DIR] [--jobs N] [--clean]"
}

CLEAN=0
while [ "$#" -gt 0 ]; do
    case "$1" in
        --qt-dir) QT_DIR="${2:-}"; shift 2 ;;
        --jobs) JOBS="${2:-}"; shift 2 ;;
        --clean) CLEAN=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "unknown argument: $1" >&2; usage; exit 2 ;;
    esac
done

log()  { printf "%s\n" "[fluentui] $*"; }
fail() { printf "%s\n" "[fluentui] ERROR: $*" >&2; exit 1; }

command -v cmake >/dev/null 2>&1 || fail "缺少 cmake"
command -v ninja >/dev/null 2>&1 || fail "缺少 ninja（Debian/Ubuntu: apt install ninja-build）"
command -v c++ >/dev/null 2>&1 || fail "缺少 C++ 编译器"
[ -f "${SRC_DIR}/CMakeLists.txt" ] || fail "缺少源码树：${SRC_DIR}（先运行 scripts/fetch_sources_linux.sh）"

if [ -z "${QT_DIR}" ]; then
    fail "未指定 Qt SDK。设置 QTDIR 或传 --qt-dir（例如 $HOME/Qt/6.7.3/gcc_64）"
fi
[ -d "${QT_DIR}" ] || fail "Qt 目录不存在：${QT_DIR}"
[ -n "$(find "${QT_DIR}" -name Qt6Config.cmake -print -quit)" ] || fail "在 ${QT_DIR} 下找不到 Qt6Config.cmake"

if [ "${CLEAN}" -eq 1 ]; then
    log "清理 ${BUILD_DIR}"
    rm -rf "${BUILD_DIR}"
fi
mkdir -p "${BUILD_DIR}" "${QML_DIR}"

log "Qt: ${QT_DIR}"
log "生成器: Ninja，并行: ${JOBS}"
cmake -S "${SRC_DIR}" -B "${BUILD_DIR}" -G Ninja \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_PREFIX_PATH="${QT_DIR}" \
    -DCMAKE_INSTALL_PREFIX="${INSTALL_DIR}" \
    -DFLUENTUI_QML_PLUGIN_DIRECTORY="${QML_DIR}" \
    -DFLUENTUI_BUILD_EXAMPLES=OFF \
    -DFLUENTUI_BUILD_STATIC_LIB=OFF

cmake --build "${BUILD_DIR}" --target fluentuiplugin --parallel "${JOBS}"

PLUGIN="$(find "${BUILD_DIR}" -type f \( -name "libfluentuiplugin.so" -o -name "fluentuiplugin.so" -o -name "libfluentuiplugin.dylib" -o -name "fluentuiplugin.dylib" \) -print -quit)"
[ -n "${PLUGIN}" ] || fail "编译结束但找不到 fluentuiplugin 动态库"
log "插件: ${PLUGIN}"
cp -f "${PLUGIN}" "${QML_DIR}/"

[ -f "${QML_DIR}/qmldir" ] || fail "缺少 ${QML_DIR}/qmldir；上游 CMake 没有把 QML 模块写进 FLUENTUI_QML_PLUGIN_DIRECTORY"
[ -f "${QML_DIR}/$(basename "${PLUGIN}")" ] || fail "插件没有落到 QML 目录"

if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "${QML_DIR}/$(basename "${PLUGIN}")" "${QML_DIR}/qmldir"
else
    shasum -a 256 "${QML_DIR}/$(basename "${PLUGIN}")" "${QML_DIR}/qmldir"
fi

printf "%s\n" "构建时间: $(date -u +%Y-%m-%dT%H:%M:%SZ)" "Qt: ${QT_DIR}" > "${INSTALL_DIR}/BUILD_PROVENANCE-linux.txt"
log "产物已安装到 ${QML_DIR}"