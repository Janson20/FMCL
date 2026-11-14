#!/usr/bin/env bash
# ============================================================
# FMCL Linux 安装脚本（QML / onedir）
#
# 默认：从源码构建 PyInstaller 目录产物 dist/FMCL，再安装到 INSTALL_DIR/runtime。
# 可选：--tarball <本地路径|URL> 直接安装 CI/Release 产出的 FMCL-*-linux-x86_64.tar.gz。
#
# 用法:
#   bash scripts/install.sh [安装目录]
#   bash scripts/install.sh /opt/fmcl --tarball ./FMCL-2.12.6-linux-x86_64.tar.gz
#   bash scripts/install.sh ~/.local/share/fmcl --tarball https://...
#
# 默认安装目录: /opt/fmcl
# 需要构建时：git、python3、venv、C++ 编译器、cmake、ninja、Qt 6.7.3 SDK。
# 脚本会通过 aqtinstall 把 Qt 装到 ~/Qt，不需要系统预装 Qt 开发包。
# ============================================================
set -euo pipefail

RED="" GREEN="" YELLOW="" CYAN="" NC=""
if [ -t 1 ]; then
    RED="\033[0;31m"; GREEN="\033[0;32m"; YELLOW="\033[1;33m"; CYAN="\033[0;36m"; NC="\033[0m"
fi
info()  { printf "%s[INFO]%s  %s\n" "$GREEN" "$NC" "$*"; }
warn()  { printf "%s[WARN]%s  %s\n" "$YELLOW" "$NC" "$*"; }
error() { printf "%s[ERROR]%s %s\n" "$RED" "$NC" "$*" >&2; exit 1; }
step()  { printf "%s[STEP]%s  %s\n" "$CYAN" "$NC" "$*"; }

usage() {
    cat <<'USAGE'
FMCL Linux 安装脚本（QML / onedir）

用法:
  bash scripts/install.sh [安装目录]
  bash scripts/install.sh /opt/fmcl --tarball ./FMCL-2.12.6-linux-x86_64.tar.gz
  bash scripts/install.sh ~/.local/share/fmcl --version 2.12.6

选项:
  --tarball <路径|URL>  安装预构建的 Linux 目录产物，不编译
  --version <版本>      从 GitHub Release 下载对应 Linux tarball
  --no-desktop          不创建桌面入口

默认安装目录: /opt/fmcl
默认从源码构建；构建需要 cmake、ninja、C++ 编译器与 Qt 6.7.3 SDK。
USAGE
}

INSTALL_DIR="/opt/fmcl"
TARBALL=""
VERSION=""
NO_DESKTOP=0
SUDO=""
INSTALL_SUDO=""
BIN_SUDO=""
while [ "$#" -gt 0 ]; do
    case "$1" in
        --tarball) TARBALL="${2:-}"; [ -n "$TARBALL" ] || error "--tarball 需要参数"; shift 2 ;;
        --version) VERSION="${2:-}"; [ -n "$VERSION" ] || error "--version 需要参数"; shift 2 ;;
        --no-desktop) NO_DESKTOP=1; shift ;;
        -h|--help) usage; exit 0 ;;
        --) shift; [ "$#" -gt 0 ] && INSTALL_DIR="$1"; break ;;
        -*) error "未知参数: $1" ;;
        *) INSTALL_DIR="$1"; shift ;;
    esac
done

if [ -z "$TARBALL" ] && [ -n "$VERSION" ]; then
    VERSION="${VERSION#v}"
    TARBALL="https://github.com/Janson20/FMCL/releases/download/v${VERSION}/FMCL-${VERSION}-linux-x86_64.tar.gz"
fi

REPO_URL="https://github.com/Janson20/FMCL.git"
BIN_PATH="/usr/local/bin/fmcl"
DESKTOP_FILE="${HOME}/.local/share/applications/fmcl.desktop"
APP_DIR="${INSTALL_DIR}/runtime"
TMP_DIR=""

cleanup() { [ -n "$TMP_DIR" ] && rm -rf "$TMP_DIR"; }
trap cleanup EXIT

need_sudo() {
    if [ "$(id -u)" -eq 0 ]; then
        SUDO=""
    elif command -v sudo >/dev/null 2>&1; then
        SUDO="sudo"
    else
        error "需要 root 权限（或安装 sudo）来写 ${INSTALL_DIR} / ${BIN_PATH}"
    fi
}

determine_privileges() {
    need_sudo
    if [ -w "${INSTALL_DIR}" ] || [ -w "$(dirname "${INSTALL_DIR}")" ]; then
        INSTALL_SUDO=""
    else
        INSTALL_SUDO="${SUDO}"
    fi
    if [ -w "$(dirname "${BIN_PATH}")" ]; then
        BIN_SUDO=""
    else
        BIN_SUDO="${SUDO}"
    fi
}

# ── 1. 系统依赖 ──────────────────────────────────────────────
install_deps_debian() {
    ${SUDO} apt-get update -qq
    ${SUDO} apt-get install -y -qq git curl ca-certificates python3 python3-venv python3-pip \
        build-essential cmake ninja-build pkg-config \
        libgl1-mesa-dev libegl1 libxkbcommon-dev libxkbcommon-x11-0 \
        libxcb-cursor0 libxcb-xinerama0 libxcb-icccm4 libxcb-image0 \
        libxcb-keysyms1 libxcb-randr0 libxcb-render-util0 libxcb-shape0 libxcb-xfixes0 \
        libfontconfig1 libdbus-1-3 libportaudio2
}
install_deps_ubuntu()      { install_deps_debian; }
install_deps_linuxmint()   { install_deps_debian; }
install_deps_fedora() {
    ${SUDO} dnf install -y git curl python3 python3-devel gcc-c++ cmake ninja-build pkgconf \
        mesa-libGL-devel libxkbcommon-devel xcb-util-cursor xcb-util-wm xcb-util-image \
        xcb-util-keysyms xcb-util-renderutil fontconfig dbus-libs portaudio
}
install_deps_rhel()    { install_deps_fedora; }
install_deps_centos()  { install_deps_rhel; }
install_deps_arch() {
    ${SUDO} pacman -Syu --noconfirm --needed git curl python base-devel cmake ninja pkgconf \
        mesa libxkbcommon xcb-util-cursor xcb-util-wm xcb-util-image \
        xcb-util-keysyms xcb-util-renderutil fontconfig dbus portaudio
}
install_deps_manjarolinux() { install_deps_arch; }
install_deps_opensuse() {
    ${SUDO} zypper install -y git curl python3 python3-devel gcc-c++ cmake ninja pkg-config \
        Mesa-libGL-devel libxkbcommon-devel libxcb-cursor0 libxcb-icccm4 libxcb-image0 \
        libxcb-keysyms1 libxcb-randr0 libxcb-render-util0 libxcb-shape0 libxcb-xfixes0 \
        fontconfig dbus-1 libportaudio2
}
install_deps_opensuse-tumbleweed() { install_deps_opensuse; }

install_system_deps() {
    need_sudo
    local distro=""
    if [ -r /etc/os-release ]; then
        . /etc/os-release
        distro="${ID:-}"
    fi
    if [ -n "$distro" ] && declare -f "install_deps_${distro}" >/dev/null 2>&1; then
        info "检测到发行版: ${distro}"
        "install_deps_${distro}"
        info "系统依赖安装完成"
    else
        warn "未知发行版或无法识别，跳过自动安装系统依赖"
        warn "请手动安装: git curl python3 venv C++ 编译器 cmake ninja、Qt 6 运行库与 XCB/OpenGL 头文件"
    fi
}

install_uv() {
    if command -v uv >/dev/null 2>&1; then
        info "uv 已安装: $(uv --version 2>/dev/null || echo ok)"
        return
    fi
    step "安装 uv"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="${HOME}/.local/bin:${HOME}/.cargo/bin:${PATH}"
    command -v uv >/dev/null 2>&1 || error "uv 安装后仍不在 PATH 中"
    info "uv 安装完成: $(uv --version)"
}

# ── 2. 取源码 / 取预构建 tarball ─────────────────────────────
prepare_source() {
    step "克隆或更新 FMCL 源码到 ${INSTALL_DIR}"
    if [ -d "${INSTALL_DIR}/.git" ]; then
        info "已有 git 仓库，执行 git pull --ff-only"
        git -C "${INSTALL_DIR}" pull --ff-only
    else
        if [ -d "${INSTALL_DIR}" ] && [ -n "$(ls -A "${INSTALL_DIR}" 2>/dev/null)" ]; then
            error "${INSTALL_DIR} 非空且不是 git 仓库；请换目录或手动处理"
        fi
        ${INSTALL_SUDO} mkdir -p "${INSTALL_DIR}"
        if [ -n "$INSTALL_SUDO" ]; then
            ${INSTALL_SUDO} chown "$(id -u):$(id -g)" "${INSTALL_DIR}"
        fi
        git clone "${REPO_URL}" "${INSTALL_DIR}"
    fi
}

prepare_tarball() {
    local src="$1"
    TMP_DIR="$(mktemp -d)"
    if printf "%s" "$src" | grep -Eq "^https?://"; then
        step "下载 Linux 构建产物"
        curl -fL --retry 3 --connect-timeout 15 -o "${TMP_DIR}/fmcl.tar.gz" "$src"
        src="${TMP_DIR}/fmcl.tar.gz"
    fi
    [ -f "$src" ] || error "找不到 tarball: $src"
    tar -tzf "$src" | grep -Eq "(^|/)FMCL$" || error "tarball 里没有 FMCL 可执行文件"
    tar -xzf "$src" -C "$TMP_DIR"
    if [ -d "${TMP_DIR}/FMCL" ]; then
        APP_SRC="${TMP_DIR}/FMCL"
    else
        APP_SRC="$TMP_DIR"
    fi
    [ -x "${APP_SRC}/FMCL" ] || error "解压后找不到可执行的 FMCL"
}

# ── 3. 源码构建 ──────────────────────────────────────────────
build_from_source() {
    install_system_deps
    install_uv
    prepare_source
    cd "${INSTALL_DIR}"

    step "安装 Python 依赖 (uv sync)"
    uv sync

    step "拉取第三方源码（FluentUI / PySide6-FluentUI-QML）"
    bash "${INSTALL_DIR}/scripts/fetch_sources_linux.sh"

    step "准备 Qt 6.7.3 SDK"
    if [ -z "${QTDIR:-}" ]; then
        local qt_root="${HOME}/Qt"
        mkdir -p "$qt_root"
        if ! uv tool run --from aqtinstall aqt install-qt linux desktop 6.7.3 gcc_64 \
            -m qt5compat qtshadertools -O "$qt_root"; then
            warn "gcc_64 安装失败，尝试 aqt 的旧别名 linux_gcc_64"
            uv tool run --from aqtinstall aqt install-qt linux desktop 6.7.3 linux_gcc_64 \
                -m qt5compat qtshadertools -O "$qt_root"
        fi
        for candidate in "${qt_root}/6.7.3/gcc_64" "${qt_root}/6.7.3/linux_gcc_64"; do
            if [ -f "${candidate}/lib/cmake/Qt6/Qt6Config.cmake" ]; then
                QTDIR="$candidate"
                break
            fi
        done
    fi
    [ -n "${QTDIR:-}" ] || error "找不到 Qt 6.7.3 SDK；可手动设置 QTDIR 后重试"
    export QTDIR
    info "QTDIR=${QTDIR}"

    step "编译 FluentUI QML 插件"
    bash "${INSTALL_DIR}/scripts/build_fluentui_linux.sh" --qt-dir "$QTDIR"

    step "PyInstaller 构建 Linux 目录产物"
    uv run pyinstaller build.spec --noconfirm
    [ -x "${INSTALL_DIR}/dist/FMCL/FMCL" ] || error "构建结束但 dist/FMCL/FMCL 不存在"
    APP_SRC="${INSTALL_DIR}/dist/FMCL"
}

# ── 4. 安装 ──────────────────────────────────────────────────
install_app() {
    step "安装到 ${APP_DIR}"
    ${INSTALL_SUDO} rm -rf "${APP_DIR}"
    ${INSTALL_SUDO} mkdir -p "${APP_DIR}" "${INSTALL_DIR}"
    ${INSTALL_SUDO} cp -a "${APP_SRC}/." "${APP_DIR}/"
    ${INSTALL_SUDO} chmod +x "${APP_DIR}/FMCL"
    if [ ! -x "${APP_DIR}/FMCL" ]; then
        error "安装后 ${APP_DIR}/FMCL 不可执行"
    fi
    if [ -f "${APP_DIR}/_internal/icon.ico" ]; then
        ${INSTALL_SUDO} cp -f "${APP_DIR}/_internal/icon.ico" "${INSTALL_DIR}/icon.ico"
    fi

    local launcher_tmp
    launcher_tmp="$(mktemp)"
    cat > "$launcher_tmp" <<EOF
#!/bin/sh
exec "${APP_DIR}/FMCL" "\$@"
EOF
    ${BIN_SUDO} install -m 0755 "$launcher_tmp" "$BIN_PATH"
    rm -f "$launcher_tmp"

    if [ "$NO_DESKTOP" -eq 0 ]; then
        mkdir -p "$(dirname "$DESKTOP_FILE")"
        cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Name=FMCL
Comment=Fusion Minecraft Launcher
Exec=${BIN_PATH}
Icon=${INSTALL_DIR}/icon.ico
Terminal=false
Type=Application
Categories=Game;
StartupWMClass=FMCL
EOF
        info "桌面入口: ${DESKTOP_FILE}"
    fi
}

# ── 5. 收尾 ──────────────────────────────────────────────────
printf "\n============================================\n"
printf " FMCL 安装完成\n"
printf "============================================\n"
printf "  安装目录:  %s\n" "$INSTALL_DIR"
printf "  主程序:    %s\n" "$APP_DIR/FMCL"
printf "  fmcl 命令: %s\n" "$BIN_PATH"
printf "\n  使用方法:\n"
printf "    fmcl              # 启动 GUI\n"
printf "    fmcl -A           # Agent 交互模式\n"
printf "    fmcl -A 指令    # Agent 单指令模式\n"
printf "\n  更新:\n"
printf "    源码安装: cd %s && git pull && bash scripts/install.sh %s\n" "$INSTALL_DIR" "$INSTALL_DIR"
printf "    tarball:  重新运行本脚本并指向新 tarball\n"
printf "\n  故障排除: %s/docs/TROUBLESHOOTING.md\n" "$INSTALL_DIR"
printf "============================================\n"

determine_privileges

if [ -n "$TARBALL" ]; then
    prepare_tarball "$TARBALL"
else
    build_from_source
fi
install_app