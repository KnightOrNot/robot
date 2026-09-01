# Robot：PiPER-X 与 GELLO 数据采集系统

本项目用于完成 AgileX PiPER-X 机械臂控制、GELLO 遥操作示教、原始数据记录，以及 LeRobot Dataset v3 离线转换。顶层仓库负责组合三个相互独立的子项目和两个安全启动脚本；三个子项目通过 Git submodule 固定版本，各自维护独立的 Python 环境和依赖。

> [!WARNING]
> 跟随和记录流程会控制真实机械臂。首次运行前必须固定机械臂底座、清空工作空间、确认急停按钮触手可及，并先完成 CAN 与 GELLO 的只读检查。PiPER-X 失能后可能因重力下坠，执行失能操作前必须托住机械臂。

## （1）项目架构

```text
robot/
├── agilexrobotics/          # submodule：PiPER-X CAN 驱动与 ZMQ 控制服务
├── gello_software/          # submodule：GELLO 读取、跟随与 raw 数据记录
├── lerobot_converter/       # submodule：raw → LeRobot Dataset v3 离线转换
├── data/
│   ├── raw/                 # 不可变的原始 session
│   └── lerobot/             # 可重新生成的 LeRobot 数据集
├── docs/
│   └── DEVELOPMENT.md       # 顶层联调与故障排查手册
├── setup.sh                 # submodule、pyenv、uv 环境和系统依赖的一键配置
├── start_gello_follow.sh    # 跟随、校准和安全退出入口
├── start_data_record.sh     # 跟随、记录、安全退出和离线转换入口
└── .gitmodules              # 三个子项目的仓库地址与挂载路径
```

三个 submodule 的职责边界：

| 子项目                 | Python | 职责                                       |
| ------------------- | ------ | ---------------------------------------- |
| `agilexrobotics`    | 3.11   | 独占 PiPER-X CAN，读取反馈并提供 `ag-gello-server` |
| `gello_software`    | 3.11   | 独占 GELLO 串口，生成跟随 action 并记录 raw session  |
| `lerobot_converter` | 3.12   | 离线校验 raw session，生成 LeRobot Dataset v3   |

完整运行链路：

```text
GELLO → gello_software → ZMQ → agilexrobotics → CAN → PiPER-X
                 │
                 └── data/raw/session_*
                              │
                              └── lerobot_converter → data/lerobot/session_*
```

## （2）环境要求

### 1. 操作系统与工具

- Ubuntu 或其他支持 SocketCAN 和 USB 串口的 Linux 系统
- Git 2.x，并支持 Git submodule
- [pyenv](https://github.com/pyenv/pyenv)：安装和选择各子项目所需的 Python
- [uv](https://docs.astral.sh/uv/getting-started/installation/)：创建 `.venv`、同步锁定依赖和运行命令
- `iproute2`：提供 CAN 配置所需的 `ip`
- `build-essential`：安装部分 GELLO Python 依赖
- `curl`：安装 uv
- `ffmpeg`：为 LeRobot/TorchCodec 后续视频处理提供系统共享库
- `lsof`：排查 GELLO 串口占用

`setup.sh` 默认自动安装这些系统包。需要手工安装时执行：

```bash
sudo apt update
sudo apt install -y git curl build-essential iproute2 ffmpeg lsof \
  libssl-dev zlib1g-dev libbz2-dev libreadline-dev libsqlite3-dev \
  libncursesw5-dev xz-utils tk-dev libxml2-dev libxmlsec1-dev \
  libffi-dev liblzma-dev
sudo ldconfig
```

### 2. 硬件

- AgileX PiPER-X 与 AGX 夹爪
- 兼容 SocketCAN 的 USB-CAN 适配器，默认接口名 `can0`
- 正确连接的 CAN-H/CAN-L 和终端电阻，默认波特率 `1,000,000 bit/s`
- 七自由度 GELLO 主手、稳定供电和 FTDI/Dynamixel 串口适配器
- 当前账户可使用 `sudo` 配置 CAN，并具有 GELLO 串口读写权限

### 3. Python 环境边界

Python 解释器由 pyenv 管理，uv 只使用 pyenv 提供的解释器创建 `.venv` 并同步依赖。三个子项目必须保留各自的 `.venv`，不要在顶层创建一个环境混装全部依赖：

```text
agilexrobotics/.venv      Python 3.11：CAN、pyAgxArm、ZMQ
gello_software/.venv      Python 3.11：Dynamixel、GELLO、实时记录
lerobot_converter/.venv   Python 3.12：LeRobot、PyTorch、Parquet、转换
```

顶层 Shell 会显式调用对应子项目中的解释器和 CLI，不依赖当前终端激活了哪个虚拟环境。

## （3）快速开始

### 1. Clone 顶层项目

使用 SSH：

```bash
git clone git@github.com:right-or-not/robot.git
cd robot
```

未配置 GitHub SSH 密钥时使用 HTTPS：

```bash
git clone https://github.com/right-or-not/robot.git
cd robot
```

`setup.sh` 会按顶层锁定的 commit 初始化三个一级子项目，不要求 clone 时添加 `--recurse-submodules`。体积较大且正常 PiPER-X 开发不需要的 MuJoCo Menagerie 默认不会下载。

### 2. 一键配置软件环境

默认配置包含 Ubuntu 系统包、submodule、pyenv、uv、三个虚拟环境、LeRobot dataset 依赖、FFmpeg/TorchCodec 验证和 `dialout` 用户组：

```bash
./setup.sh
```

脚本不会配置 CAN、访问串口或移动机械臂，可以安全重复执行。若脚本将当前用户加入了 `dialout`，完成后必须注销并重新登录。常用选项：

```bash
./setup.sh --skip-system
./setup.sh --gello-extra full
./setup.sh --with-simulation-assets
./setup.sh --check-only
```

- `--skip-system` 适用于系统包、pyenv 和 uv 已经安装且不希望使用 sudo 的机器；

- `--gello-extra full` 额外安装相机、仿真和第三方机器人的 Python 依赖；

- `--with-simulation-assets` 仅在确实运行 MuJoCo 仿真时下载 Menagerie 模型；

- `--check-only` 不修改系统，只验证现有配置。默认 PiPER-X 跟随与记录只安装 GELLO 基础依赖。

### 3. 手工配置等效流程

不使用一键脚本时，先初始化 submodule：

```bash
git submodule sync
git submodule update --init
```

只有仿真开发需要额外执行：

```bash
git -C gello_software submodule update \
  --init third_party/mujoco_menagerie
```

然后读取各子项目的 `.python-version`，由 pyenv 安装解释器并显式交给 uv。当前版本的等效命令为：

```bash
pyenv install -s "$(pyenv latest -k 3.11)"
pyenv install -s "$(pyenv latest -k 3.12)"

python311="$(PYENV_VERSION="$(pyenv latest -k 3.11)" pyenv which python)"
python312="$(PYENV_VERSION="$(pyenv latest -k 3.12)" pyenv which python)"

UV_NO_MANAGED_PYTHON=1 uv sync --project agilexrobotics --frozen --python "$python311"
UV_NO_MANAGED_PYTHON=1 uv sync --project gello_software --frozen --python "$python311"
UV_NO_MANAGED_PYTHON=1 uv sync --project lerobot_converter --frozen --extra dataset --python "$python312"
```

这里的 `UV_NO_MANAGED_PYTHON=1` 防止 uv 自行下载另一套 Python。DynamixelSDK 已由 `gello_software/uv.lock` 管理，不再手工 clone。最后验证：

```bash
ffmpeg -version
lerobot_converter/.venv/bin/python -c "from torchcodec.decoders import VideoDecoder; print('TorchCodec 加载正常')"
```

即使当前仅转换关节数据，也建议提前完成该验证，为后续视频 feature 做准备。

### 4. 配置 GELLO 串口权限

```bash
sudo usermod -aG dialout "$USER"
```

执行后注销并重新登录，再连接 GELLO 并查看稳定设备路径：

```bash
ls -l /dev/serial/by-id/
```

默认启动脚本使用以下设备；如果你的路径不同，运行时必须通过 `--gello-port` 指定，并确保该路径已经在 `gello_software/src/gello/agents/gello_agent.py` 的 `PORT_CONFIG_MAP` 中正确配置：

```text
/dev/serial/by-id/usb-FTDI_USB__-__Serial_Converter_FTBM4Z46-if00-port0
```

### 5. 进行只读硬件验证

连接 USB-CAN 和机械臂、给 PiPER-X 上电并释放急停，然后配置 CAN：

```bash
./agilexrobotics/scripts/config_can.sh can0 1000000 100
```

只读检查 PiPER-X：

```bash
cd agilexrobotics
uv run ag status --channel can0 --wait 1.0
cd ..
```

正常通信时 `connected` 和 `communication_ok` 应为 `true`，并且 `ip -statistics link show can0` 中的 RX 应持续增加。

只读检查 GELLO，将路径替换为当前设备的 `by-id` 路径：

```bash
uv run --project gello_software gello read \
  --gello-port /dev/serial/by-id/usb-FTDI_USB__-__Serial_Converter_FTBM4Z46-if00-port0
```

只有两个只读检查都能稳定通过后，才进入运动流程。

### 6. 启动跟随或数据记录

先查看顶层脚本参数：

```bash
./start_gello_follow.sh --help
./start_data_record.sh --help
```

启动普通跟随：

```bash
./start_gello_follow.sh \
  --gello-port /dev/serial/by-id/usb-FTDI_USB__-__Serial_Converter_FTBM4Z46-if00-port0 \
  --hz 50
```

启动跟随与数据记录，退出后自动转换为 30 FPS 的 LeRobot Dataset v3：

```bash
./start_data_record.sh \
  --gello-port /dev/serial/by-id/usb-FTDI_USB__-__Serial_Converter_FTBM4Z46-if00-port0 \
  --task "pick up the object" \
  --hz 50 \
  --dataset-fps 30
```

脚本涉及真实运动，执行前会要求人工确认。跟随时按 `Ctrl+C` 会先停止新命令，再尝试通过现有 JS 会话安全回零；不要使用 `kill -9` 跳过清理流程。

## （4）Submodule 日常维护

### 1. 拉取顶层仓库固定版本

其他开发者获取顶层最新提交后执行：

```bash
git pull --ff-only
git submodule sync
git submodule update --init
```

这会把三个子项目切换到顶层仓库记录的 commit，不会自动把它们更新到远端分支最新提交。

### 2. 更新某个子项目版本

需要升级 submodule 时，应先在对应子项目仓库完成修改、测试、提交和推送，再回到顶层记录新的 commit。例如：

```bash
cd agilexrobotics
git switch main
git pull --ff-only
cd ..
git add agilexrobotics
git commit -m "chore: update agilexrobotics submodule"
```

`gello_software` 和 `lerobot_converter` 使用相同流程。不要只在 submodule 中产生未提交改动后提交顶层 gitlink，否则其他开发者无法取得这些修改。

### 3. 查看 submodule 状态

```bash
git submodule status
git status --short
git -C agilexrobotics status --short
git -C gello_software status --short
git -C lerobot_converter status --short
```

## （5）文档导航

- [顶层开发与调试手册](docs/DEVELOPMENT.md)：完整联调顺序、频率实验、记录、转换与故障排查
- [agilexrobotics README](agilexrobotics/README.md)：PiPER-X 环境和只读 CAN 验证
- [agilexrobotics 开发手册](agilexrobotics/docs/DEVELOPMENT.md)：`ag`、硬件命令和 GELLO 服务参数
- [gello_software README](gello_software/README.md)：GELLO 环境、串口权限和只读检查
- [gello_software 开发手册](gello_software/docs/DEVELOPMENT.md)：映射、跟随、记录与 Dynamixel 排错
- [lerobot_converter README](lerobot_converter/README.md)：独立转换器的输入/输出格式和快速开始
- [lerobot_converter 开发手册](lerobot_converter/docs/DEVELOPMENT.md)：raw schema、重采样、LeRobot v3 输出和质量报告

## （6）常见初始化问题

### 1. Submodule 目录为空

```bash
git submodule sync
git submodule update --init
```

### 2. Submodule 显示 detached HEAD

这是正常状态。顶层仓库通过 commit 固定子项目版本，而不是要求 submodule 始终跟随某个分支。只有准备在子项目中开发和提交时，才进入目录执行 `git switch main`。

### 3. 找不到虚拟环境命令

不要用另一个子项目的 Python 代替，也不要让 uv 自行下载解释器。使用一键脚本保留 submodule 和系统配置，仅按 `.python-version` 重新检查 pyenv 并同步三个环境：

```bash
./setup.sh --skip-system --skip-submodules
```

单独修复某个环境时，按照快速开始“手工配置等效流程”显式传递 pyenv 解释器。

### 4. CAN 接口 UP 但没有反馈

接口 `UP` 只代表本机 SocketCAN 配置完成，不代表机械臂正在发送数据。检查机械臂供电、急停、CAN 接线、终端电阻和波特率，并观察：

```bash
ip -details -statistics link show can0
```

详细排查见[顶层开发与调试手册](docs/DEVELOPMENT.md)。
