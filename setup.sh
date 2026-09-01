#!/usr/bin/env bash

set -Eeuo pipefail

root_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
install_system=true
update_submodules=true
check_only=false
gello_extra=""
install_simulation_assets=false
relogin_required=false

usage() {
    cat <<'EOF'
用法：./setup.sh [选项]

初始化 robot 的 submodule、pyenv Python、三个 uv 环境和系统依赖。
脚本不会配置 CAN、访问串口或向机械臂发送命令，可安全重复执行。

选项：
  --skip-system          不使用 sudo 安装 Ubuntu 包，也不修改 dialout 用户组
  --skip-submodules      不同步或初始化 Git submodule
  --gello-extra NAME     安装 GELLO 可选依赖：camera、simulation、robots 或 full
  --with-simulation-assets
                         下载可选的 MuJoCo Menagerie 仿真模型
  --check-only           只检查现有配置，不执行安装或修改
  -h, --help             显示帮助
EOF
}

log() {
    printf '\n========== %s ==========\n' "$1"
}

fail() {
    echo "错误：$*" >&2
    exit 1
}

while (( $# > 0 )); do
    case "$1" in
        --skip-system)
            install_system=false
            shift
            ;;
        --skip-submodules)
            update_submodules=false
            shift
            ;;
        --gello-extra)
            gello_extra="${2:?--gello-extra 缺少名称}"
            shift 2
            ;;
        --with-simulation-assets)
            install_simulation_assets=true
            shift
            ;;
        --check-only)
            check_only=true
            install_system=false
            update_submodules=false
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "错误：未知参数 $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

case "$gello_extra" in
    ""|camera|simulation|robots|full) ;;
    *) fail "--gello-extra 只支持 camera、simulation、robots 或 full" ;;
esac

cd "$root_dir"
[[ -f .gitmodules ]] || fail "请从 robot 仓库根目录运行本脚本"

if [[ "$install_system" == true ]]; then
    log "[1/7] 安装 Ubuntu 系统依赖"
    command -v sudo >/dev/null 2>&1 || fail "找不到 sudo；可用 --skip-system 跳过"
    command -v apt-get >/dev/null 2>&1 \
        || fail "默认系统安装仅支持 apt-get；请手工安装依赖后使用 --skip-system"
    sudo apt-get update
    sudo apt-get install -y \
        build-essential curl ffmpeg git iproute2 libbz2-dev libffi-dev \
        liblzma-dev libncursesw5-dev libreadline-dev libsqlite3-dev \
        libssl-dev libxml2-dev libxmlsec1-dev lsof make tk-dev xz-utils \
        zlib1g-dev
else
    log "[1/7] 检查基础命令"
    for command_name in git curl; do
        command -v "$command_name" >/dev/null 2>&1 \
            || fail "找不到 $command_name；请安装系统依赖或取消 --skip-system"
    done
fi

log "[2/7] 准备 pyenv"
export PYENV_ROOT="${PYENV_ROOT:-$HOME/.pyenv}"
export PATH="$PYENV_ROOT/bin:$PATH"
if ! command -v pyenv >/dev/null 2>&1; then
    if [[ "$check_only" == true ]]; then
        fail "未安装 pyenv"
    fi
    [[ ! -e "$PYENV_ROOT" ]] \
        || fail "$PYENV_ROOT 已存在但 pyenv 不可用，请先检查现有安装"
    git clone https://github.com/pyenv/pyenv.git "$PYENV_ROOT"
fi

if [[ "$check_only" != true ]] && [[ -f "$HOME/.bashrc" ]] \
    && ! grep -Fq '# robot setup: pyenv' "$HOME/.bashrc"; then
    {
        echo
        echo '# robot setup: pyenv'
        echo 'export PYENV_ROOT="$HOME/.pyenv"'
        echo '[[ -d "$PYENV_ROOT/bin" ]] && export PATH="$PYENV_ROOT/bin:$PATH"'
        echo 'eval "$(pyenv init - bash)"'
    } >> "$HOME/.bashrc"
fi
pyenv --version

log "[3/7] 准备 uv"
if ! command -v uv >/dev/null 2>&1; then
    if [[ "$check_only" == true ]]; then
        fail "未安装 uv"
    fi
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
fi
command -v uv >/dev/null 2>&1 || fail "uv 安装后仍不可用，请重新打开终端后重试"
uv --version

if [[ "$update_submodules" == true ]]; then
    log "[4/7] 同步 Git submodule"
    while IFS= read -r submodule_path; do
        if [[ -e "$submodule_path/.git" ]] \
            && [[ -n "$(git -C "$submodule_path" status --porcelain)" ]]; then
            fail "submodule $submodule_path 存在未提交内容；请先提交、暂存或移走后重试"
        fi
    done < <(git config --file .gitmodules --get-regexp path | awk '{print $2}')
    git submodule sync
    git submodule update --init
    if [[ "$install_simulation_assets" == true ]]; then
        git -C gello_software submodule sync
        git -C gello_software submodule update \
            --init third_party/mujoco_menagerie
    fi
else
    log "[4/7] 检查 Git submodule"
fi

for submodule_path in agilexrobotics gello_software lerobot_converter; do
    [[ -f "$submodule_path/pyproject.toml" ]] \
        || fail "$submodule_path 尚未初始化；请取消 --skip-submodules"
done

sync_project() {
    local project_dir="$1"
    shift
    local requested_version resolved_version interpreter
    requested_version="$(tr -d '[:space:]' < "$project_dir/.python-version")"
    [[ -n "$requested_version" ]] || fail "$project_dir/.python-version 为空"
    resolved_version="$(pyenv latest -k "$requested_version")"

    if [[ "$check_only" != true ]]; then
        pyenv install -s "$resolved_version"
    fi
    pyenv prefix "$resolved_version" >/dev/null 2>&1 \
        || fail "pyenv 中没有 $resolved_version（$project_dir 需要 $requested_version）"
    interpreter="$(PYENV_VERSION="$resolved_version" pyenv which python)"

    if [[ "$check_only" != true ]]; then
        UV_NO_MANAGED_PYTHON=1 uv sync \
            --project "$project_dir" \
            --frozen \
            --python "$interpreter" \
            "$@"
    fi
    [[ -x "$project_dir/.venv/bin/python" ]] \
        || fail "$project_dir/.venv 不存在或不完整"
    printf '%-20s requested=%-6s resolved=%-8s venv=' \
        "$project_dir" "$requested_version" "$resolved_version"
    "$project_dir/.venv/bin/python" --version
}

log "[5/7] 使用 pyenv Python 同步 uv 环境"
sync_project agilexrobotics
if [[ -n "$gello_extra" ]]; then
    sync_project gello_software --extra "$gello_extra"
else
    sync_project gello_software
fi
sync_project lerobot_converter --extra dataset

log "[6/7] 创建数据目录并验证软件入口"
if [[ "$check_only" != true ]]; then
    mkdir -p data/raw data/lerobot
fi
[[ -d data/raw && -d data/lerobot ]] || fail "data/raw 或 data/lerobot 不存在"

agilexrobotics/.venv/bin/ag --help >/dev/null
agilexrobotics/.venv/bin/ag-gello-server --help >/dev/null
gello_software/.venv/bin/gello --help >/dev/null
lerobot_converter/.venv/bin/lerobot-converter --help >/dev/null
ffmpeg -version >/dev/null 2>&1 || fail "FFmpeg 不可用"
lerobot_converter/.venv/bin/python -c \
    "from torchcodec.decoders import VideoDecoder; print('TorchCodec 加载正常')"

log "[7/7] 检查 GELLO 串口用户组"
if id -nG "$USER" | tr ' ' '\n' | grep -Fxq dialout; then
    echo "当前用户已属于 dialout。"
elif [[ "$install_system" == true && "$check_only" != true ]]; then
    sudo usermod -aG dialout "$USER"
    relogin_required=true
    echo "已将 $USER 加入 dialout。"
else
    echo "警告：当前用户不属于 dialout。请执行：" >&2
    echo "  sudo usermod -aG dialout \"$USER\"" >&2
fi

echo
echo "软件环境配置完成。"
if [[ "$relogin_required" == true ]]; then
    echo "重要：必须注销并重新登录，dialout 权限才会在新会话中生效。"
fi
echo "可以连接硬件并参照 README 进行只读验证。"
