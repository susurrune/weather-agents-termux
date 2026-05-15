#!/usr/bin/env bash
# Weather Agents — Termux Ubuntu 普通用户一键安装脚本
# 用法：bash install.sh
# 或（从 GitHub 直接运行）：
#   bash <(curl -fsSL https://raw.githubusercontent.com/susurrune/weather-agents/main/install.sh)

set -e

REPO="https://github.com/susurrune/weather-agents.git"
LOCAL_BIN="$HOME/.local/bin"

# ── 颜色 ──────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()    { printf "${CYAN}[info]${NC}  %s\n" "$*"; }
ok()      { printf "${GREEN}[ ok ]${NC}  %s\n" "$*"; }
warn()    { printf "${YELLOW}[warn]${NC}  %s\n" "$*"; }
die()     { printf "${RED}[fail]${NC}  %s\n" "$*" >&2; exit 1; }

# ── Python 3.11+ ──────────────────────────────────────────────────────────────
info "检查 Python 版本..."
PYTHON=$(command -v python3 || command -v python || die "找不到 Python，请先安装 Python 3.11+")
PY_VER=$("$PYTHON" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_MAJOR=${PY_VER%%.*}
PY_MINOR=${PY_VER#*.}
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 11 ]; }; then
    die "需要 Python 3.11+，当前版本 $PY_VER"
fi
ok "Python $PY_VER"

# ── uv ────────────────────────────────────────────────────────────────────────
if ! command -v uv &>/dev/null && [ ! -x "$LOCAL_BIN/uv" ]; then
    info "安装 uv 包管理器..."
    if command -v curl &>/dev/null; then
        curl -LsSf https://astral.sh/uv/install.sh | sh
    elif command -v wget &>/dev/null; then
        wget -qO- https://astral.sh/uv/install.sh | sh
    else
        die "需要 curl 或 wget 来下载 uv，请先安装其中之一"
    fi
fi

UV="$LOCAL_BIN/uv"
[ -x "$UV" ] || UV=$(command -v uv 2>/dev/null) || die "uv 安装失败"
ok "uv $(\"$UV\" --version)"

# ── ensurepip（Termux Ubuntu 特殊处理）────────────────────────────────────────
if ! "$PYTHON" -c "import ensurepip" &>/dev/null; then
    info "修复 ensurepip（Termux Ubuntu 需要此步骤）..."
    VENV_DEB="/var/cache/apt/archives/python3.${PY_MINOR}-venv_*.deb"
    TMPDIR_PKG=$(mktemp -d)

    # 尝试从 apt 缓存提取
    if ls $VENV_DEB &>/dev/null 2>&1; then
        dpkg -x "$(ls $VENV_DEB | head -1)" "$TMPDIR_PKG"
    else
        # 下载（无需 root，只是下载包文件）
        info "下载 python3.${PY_MINOR}-venv 包..."
        apt-get download "python3.${PY_MINOR}-venv" 2>/dev/null && \
        dpkg -x python3.${PY_MINOR}-venv_*.deb "$TMPDIR_PKG" && \
        rm -f python3.${PY_MINOR}-venv_*.deb || warn "ensurepip 修复跳过（uv 通常无需此步骤）"
    fi

    EP_SRC="$TMPDIR_PKG/usr/lib/python${PY_VER}/ensurepip"
    EP_DST="/usr/lib/python${PY_VER}/ensurepip"
    if [ -d "$EP_SRC" ] && [ ! -d "$EP_DST" ]; then
        cp -r "$EP_SRC" "$EP_DST" && ok "ensurepip 已安装" || warn "无权写入系统目录，跳过（uv 不需要 ensurepip）"
    fi
    rm -rf "$TMPDIR_PKG"
fi

# ── 安装 / 升级 weather-agents ────────────────────────────────────────────────
info "安装 weather-agents..."
"$UV" tool install --reinstall "$REPO"
ok "weather-agents 安装成功"

# ── 检查已安装的命令 ──────────────────────────────────────────────────────────
WA_BIN="$LOCAL_BIN/wa"
WACODE_BIN="$LOCAL_BIN/wacode"

# ── PATH 配置 ─────────────────────────────────────────────────────────────────
PATH_LINE='export PATH="$HOME/.local/bin:$PATH"'

add_to_rc() {
    local rc="$1"
    if [ -f "$rc" ]; then
        if ! grep -qF '.local/bin' "$rc"; then
            printf '\n# Weather Agents — 工具目录\n%s\n' "$PATH_LINE" >> "$rc"
            ok "已将 \$HOME/.local/bin 添加到 $rc"
        fi
    fi
}

# 在 Termux Ubuntu 中 .bashrc 不默认 source .profile，都加一下
add_to_rc "$HOME/.bashrc"
add_to_rc "$HOME/.profile"

# uv 生成的 env 文件
UV_ENV="$HOME/.local/bin/env"
if [ -f "$UV_ENV" ]; then
    for rc in "$HOME/.bashrc" "$HOME/.profile"; do
        if [ -f "$rc" ] && ! grep -qF 'uv/env\|.local/bin/env' "$rc"; then
            printf '\n[ -f "%s" ] && . "%s"\n' "$UV_ENV" "$UV_ENV" >> "$rc"
        fi
    done
fi

# ── 完成 ──────────────────────────────────────────────────────────────────────
printf "\n${GREEN}══════════════════════════════════════════════════════${NC}\n"
printf "${GREEN}  Weather Agents 安装完成！${NC}\n"
printf "${GREEN}══════════════════════════════════════════════════════${NC}\n\n"

printf "  命令路径：\n"
[ -x "$WA_BIN" ]     && printf "    ${CYAN}wa${NC}      → $WA_BIN\n"
[ -x "$WACODE_BIN" ] && printf "    ${CYAN}wacode${NC}  → $WACODE_BIN\n"

printf "\n  快速上手：\n"
printf "    ${YELLOW}source ~/.bashrc${NC}        # 让 PATH 生效（或重新打开终端）\n"
printf "    ${YELLOW}wa init${NC}                 # 配置向导（选择模型 + API Key）\n"
printf "    ${YELLOW}wa chat${NC}                 # 开始对话（默认 Fog Agent）\n"
printf "    ${YELLOW}wa task \"帮我写一个 API\"${NC}  # 多 Agent 协作\n"
printf "    ${YELLOW}wa --help${NC}               # 查看所有命令\n\n"

printf "  升级：${YELLOW}uv tool upgrade weather-agents${NC}\n"
printf "  卸载：${YELLOW}uv tool uninstall weather-agents${NC}\n\n"
