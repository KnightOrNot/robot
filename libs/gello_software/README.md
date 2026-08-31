> 本仓库基于官方 GELLO 软件扩展，当前主要用于读取七自由度 GELLO 主手，并通过 ZMQ 控制 AgileX PiPER-X 六轴机械臂和 AGX 夹爪。
>
> 官方通用说明保存在 [`README_OFFICIAL.md`](README_OFFICIAL.md)。本文只说明当前工作区实际使用的安装、命令、PiPER-X 跟随流程及本地修改。

## 0. 项目基本信息

当前数据流：

```text
GELLO J1～J6 + gripper
        ↓ FTDI / Dynamixel Protocol 2.0
GelloAgent / DynamixelDriver
        ↓ 七维目标 [J1..J6, gripper]
piper_x_follow.py
        ↓ ZMQ tcp://127.0.0.1:6001
ag-gello-server
        ↓ SocketCAN / move_js
PiPER-X J1～J6 + AGX gripper
```

当前关键配置：

```text
GELLO 串口：57600 bit/s
关节单位：rad
PiPER-X 服务端 gripper：0=全闭，1=全开
PiPER-X 方向系数：1 1 -1 -1 1 1
跟随目标循环：100 Hz
```

100 Hz 是 Python 控制循环的目标频率，不代表 GELLO 串口每秒一定产生 100 组不同的硬件反馈。

### 0.1 当前新增和调整的功能

| 内容 | 文件 | 功能 |
| --- | --- | --- |
| GELLO 状态读取 | `experiments/read_gello_joints.py` | 打印映射后的 J1～J6 和归一化夹爪值 |
| PiPER-X 跟随客户端 | `experiments/piper_x_follow.py` | 完成七维映射、启动对齐、ZMQ 命令发送和跟随 |
| PiPER-X 跟随记录客户端 | `experiments/piper_x_follow_record.py` | 保持相同跟随逻辑，并将最终 action 和实际 observation 异步写入原始 JSONL episode |
| PiPER-X JS 定位 | `experiments/piper_x_movejs.py` | 通过现有 ZMQ/JS 会话分步移动到零位或指定目标 |
| 原始 episode 记录器 | `gello/data_utils/raw_episode_recorder.py` | 管理 session、manifest、异步队列、保存、丢弃和异常 `.partial` 文件 |
| 串口通信保护 | `gello/dynamixel/driver.py` | 检查初始化失败、连续超时、反馈过期和串口占用 |
| 串口释放路径 | `gello/agents/gello_agent.py`、`gello/robots/dynamixel.py` | 退出时停止后台线程并释放 FTDI 串口 |
| 完整自动流程 | `../start_gello_follow.sh` | 配置 CAN、检查设备、读取 GELLO、JS 校准、跟随和安全回零 |
| 跟随与数据记录流程 | `../start_data_record.sh` | 保留同一安全流程，并调用独立记录客户端；不修改普通跟随入口 |

### 0.2 主要文件结构

```text
gello_software/
├── README.md
├── README_OFFICIAL.md
├── requirements.txt
├── setup.py
├── configs/
│   ├── templates/
│   ├── yam_auto_generated.yaml
│   └── yam_auto_generated_sim.yaml
├── experiments/
│   ├── read_gello_joints.py
│   ├── piper_x_follow.py
│   ├── piper_x_follow_record.py
│   ├── piper_x_movejs.py
│   ├── launch_yaml.py
│   ├── run_env.py
│   └── quick_run.py
├── scripts/
│   ├── generate_yam_config.py
│   └── gello_get_offset.py
├── gello/
│   ├── agents/gello_agent.py
│   ├── dynamixel/driver.py
│   ├── robots/dynamixel.py
│   ├── data_utils/raw_episode_recorder.py
│   ├── env.py
│   └── zmq_core/robot_node.py
└── third_party/DynamixelSDK/
```

### 0.3 文件功能

| 文件 | 功能 |
| --- | --- |
| `experiments/read_gello_joints.py` | 独占 GELLO 串口读取一次七维状态，支持文本和 JSON 输出 |
| `experiments/piper_x_follow.py` | 连接 GELLO 和 PiPER-X ZMQ 服务，执行方向映射、启动检查和连续跟随 |
| `experiments/piper_x_follow_record.py` | 使用独立入口执行相同跟随，并在最终 action 下发和 observation 返回位置生成原始样本 |
| `experiments/piper_x_movejs.py` | 在服务端已运行时，通过同一 JS 会话分步定位并验收误差 |
| `gello/data_utils/raw_episode_recorder.py` | 使用后台线程将有界队列中的样本写入 JSONL，正常保存时原子改名，异常退出时保留 `.partial` |
| `experiments/launch_yaml.py` | 按 YAML 创建官方 GELLO agent/robot，支持单臂、双臂和保存接口 |
| `scripts/generate_yam_config.py` | 检测 YAM GELLO 偏移并生成硬件、仿真 YAML |
| `scripts/gello_get_offset.py` | 根据已知姿态计算 GELLO 关节 offset |
| `gello/agents/gello_agent.py` | 保存串口对应的 ID、offset、sign 和 gripper 配置 |
| `gello/robots/dynamixel.py` | 将原始电机角度转换为 GELLO 状态并归一化夹爪 |
| `gello/dynamixel/driver.py` | 管理 FTDI、Group Sync Read、通信超时、读取线程和资源释放 |
| `gello/env.py` | 按目标频率执行 robot command 并组合 observation |
| `gello/zmq_core/robot_node.py` | 实现 GELLO Robot 的 ZMQ 客户端和服务端协议 |

## 1. 安装与设备检查

### 1.1 安装 Python 环境

```bash
cd ~/projects/gello_software
uv venv --python 3.11
source .venv/bin/activate
git submodule update --init --recursive
uv pip install -r requirements.txt
uv pip install -e .
uv pip install -e third_party/DynamixelSDK/python
```

后续文档统一使用 `.venv/bin/python`，因此不要求提前激活虚拟环境。

### 1.2 查看 GELLO 串口

```bash
ls -l /dev/serial/by-id/
```

当前使用的串口：

```text
/dev/serial/by-id/usb-FTDI_USB__-__Serial_Converter_FTBM4Z46-if00-port0
```

检查占用进程：

```bash
lsof /dev/serial/by-id/usb-FTDI_USB__-__Serial_Converter_FTBM4Z46-if00-port0
```

同一时间只能有一个 DynamixelDriver 占用该串口。不要同时运行状态读取命令和跟随客户端。

## 2. 常用命令

| 命令 | 功能 | 是否控制 PiPER-X |
| --- | --- | --- |
| `../start_gello_follow.sh` | 自动完成设备检查、JS 校准、跟随和 Ctrl+C 安全回零 | 是，推荐入口 |
| `../start_data_record.sh` | 完成相同安全跟随流程并记录第一阶段原始数据 | 是，数据采集入口 |
| `experiments/read_gello_joints.py` | 读取 GELLO 当前 J1～J6 和 gripper | 否 |
| `experiments/piper_x_follow.py` | 手动启动 GELLO 到 PiPER-X 的跟随客户端 | 是，需要服务端 |
| `experiments/piper_x_follow_record.py` | 手动启动带原始记录的 PiPER-X 跟随客户端 | 是，需要服务端 |
| `experiments/piper_x_movejs.py` | 通过已运行的服务端让 PiPER-X JS 定位 | 是，需要服务端 |
| `experiments/launch_yaml.py` | 运行官方 YAML 工作流 | 取决于 YAML，不用于当前 PiPER-X 跟随 |

### 2.1 一键启动 PiPER-X 跟随

推荐命令：

```bash
cd ~/projects
./start_gello_follow.sh
```

脚本依次执行：

1. 配置 PiPER-X CAN。
2. 检查 GELLO 串口和 PiPER-X CAN。
3. 读取 GELLO 当前 J1～J6 和 gripper。
4. 启动 `ag-gello-server`，通过 JS 分步回零。
5. 通过同一 JS 会话对齐 PiPER-X 与 GELLO。
6. 启动 `piper_x_follow.py` 进入跟随。

跟随过程中按 Ctrl+C，脚本会保持服务端运行，通过 JS 将 PiPER-X 移回零位，再释放 GELLO 串口并关闭服务端。当前固件不执行 JS 到普通 J 的模式切换，因为硬件实验确认该转换可能导致六轴失能。

参数：

| 参数 | 含义 | 默认值 |
| --- | --- | --- |
| `--gello-port` | GELLO FTDI 串口 | 当前 FTBM4Z46 by-id 路径 |
| `--can-interface` | PiPER-X CAN 接口 | `can0` |
| `--can-bitrate` | CAN 波特率 | `1000000 bit/s` |
| `--host` | PiPER-X ZMQ 地址 | `127.0.0.1` |
| `--port` | PiPER-X ZMQ 端口 | `6001` |
| `--yes` | 跳过运动前人工输入 `yes` | 关闭 |

完整示例：

```bash
./start_gello_follow.sh \
  --gello-port /dev/serial/by-id/usb-FTDI_USB__-__Serial_Converter_FTBM4Z46-if00-port0 \
  --can-interface can0 \
  --can-bitrate 1000000 \
  --host 127.0.0.1 \
  --port 6001 \
  --yes
```

#### 2.1.1 一键启动跟随与第一阶段原始记录

`start_data_record.sh` 复制并保持普通跟随入口的设备检查、JS 回零、姿态对齐和 Ctrl+C 安全退出顺序，但最后启动新增的 `piper_x_follow_record.py`。它不会修改或调用替换版的 `start_gello_follow.sh`、`piper_x_follow.py` 或 `piper_x_movejs.py`。

```bash
cd ~/projects
./start_data_record.sh --task "pick up the object"
```

对齐完成后，使用 `R` 开始、`S` 保存、`D` 丢弃、`P` 查看状态、`H` 查看帮助，均无需按 Enter。`Ctrl+C` 时活动 episode 会刷新并保留 `.jsonl.partial`，随后外层 Shell 继续执行原有 JS 安全回零。使用 `--start-recording` 可以在对齐完成后立即开始 episode 0。

默认模式会先进入跟随但不记录，适合在按 `R` 前检查机械臂方向、夹爪和场景。按 `S` 或 `D` 只结束当前 episode，机械臂继续跟随；可以重复按 `R`、`S` 在同一个 session 中采集多个 episode。完整操作流程和全部参数见父目录 [`README.md`](../README.md#5-episode-操作约定)。

原始数据默认写入：

```text
../data/raw/session_YYYYMMDD_HHMMSS/
├── manifest.json
└── episodes/
```

相关参数：

| 参数 | 含义 | 默认值 |
| --- | --- | --- |
| `--raw-data-root` | 原始 session 根目录 | `../data/raw` |
| `--task` | 当前 session 的任务描述 | `PiPER-X GELLO teleoperation` |
| `--record-queue-size` | 后台异步写盘队列容量 | `500` |
| `--start-recording` | 对齐完成后立即开始 episode 0 | 关闭 |

### 2.2 读取 GELLO 当前状态

推荐命令：

```bash
cd ~/projects/gello_software
.venv/bin/python experiments/read_gello_joints.py \
  --gello-port /dev/serial/by-id/usb-FTDI_USB__-__Serial_Converter_FTBM4Z46-if00-port0
```

正常输出包含 J1～J6 的 rad/deg 和 gripper 归一化值。读取工具按照 GELLO 配置中的 `open_deg/closed_deg` 映射并标记为 `0=全开、1=全闭`；该命令只读取 GELLO，不连接 PiPER-X。

参数：

| 参数 | 含义 | 默认值 | 单位/范围 |
| --- | --- | --- | --- |
| `--gello-port` | GELLO 串口；必须存在于 `PORT_CONFIG_MAP` | 无，必填 | 设备路径 |
| `--baudrate` | Dynamixel 波特率 | `57600` | `bit/s`，正整数且必须与全部舵机一致 |
| `--json` | stdout 只输出机器可读 JSON | 关闭 | 开关 |

完整示例：

```bash
.venv/bin/python experiments/read_gello_joints.py \
  --gello-port /dev/serial/by-id/usb-FTDI_USB__-__Serial_Converter_FTBM4Z46-if00-port0 \
  --baudrate 57600 \
  --json
```

### 2.3 手动启动 PiPER-X 跟随客户端

先在终端 1 启动 PiPER-X 服务端：

```bash
cd ~/projects/agilexrobotics
uv run ag-gello-server
```

再在终端 2 启动 GELLO 客户端：

```bash
cd ~/projects/gello_software
.venv/bin/python experiments/piper_x_follow.py \
  --gello-port /dev/serial/by-id/usb-FTDI_USB__-__Serial_Converter_FTBM4Z46-if00-port0 \
  --start-joints 0 0 0 0 0 0
```

看到以下输出才表示跟随已经运行：

```text
GELLO and PiPER-X aligned; teleoperation started (Ctrl-C to stop)
```

参数：

| 参数 | 含义 | 默认值 | 单位/范围 |
| --- | --- | --- | --- |
| `--gello-port` | GELLO FTDI 串口 | 无，必填 | 必须存在于 `PORT_CONFIG_MAP` |
| `--hostname` | PiPER-X ZMQ 地址 | `127.0.0.1` | IP 或主机名 |
| `--robot-port` | PiPER-X ZMQ 端口 | `6001` | 整数端口 |
| `--hz` | 跟随控制循环目标频率 | `100.0` | Hz，正数 |
| `--start-joints` | GELLO 多圈角度分支的六轴参考值 | 无，必填 | 6 个有限数，rad |
| `--joint-signs` | GELLO 到 PiPER-X 的方向系数 | `1 1 -1 -1 1 1` | 每项只能为 `1/-1` |
| `--max-start-error-rad` | 启动允许的最大六轴姿态差 | `0.35` | rad，正数 |
| `--transition-step-rad` | 启动插值最大单步角度 | `0.02` | rad，范围 `(0, 0.05]` |
| `--max-command-step-rad` | 跟随六轴命令向量最大单步变化 | `1.0` | rad，范围 `(0, 1.0]` |
| `--absolute-leader` | 使用 GELLO 绝对角度，不使用启动相对偏移 | 关闭 | 开关 |

完整示例：

```bash
.venv/bin/python experiments/piper_x_follow.py \
  --gello-port /dev/serial/by-id/usb-FTDI_USB__-__Serial_Converter_FTBM4Z46-if00-port0 \
  --hostname 127.0.0.1 \
  --robot-port 6001 \
  --hz 100 \
  --start-joints 0 0 0 0 0 0 \
  --joint-signs 1 1 -1 -1 1 1 \
  --max-start-error-rad 0.35 \
  --transition-step-rad 0.02 \
  --max-command-step-rad 1.0 \
  --absolute-leader
```

`--absolute-leader` 是一键脚本完成预对齐后使用的模式。手动启动且没有提前完成绝对对齐时，通常不要添加。

### 2.4 通过现有 JS 服务定位 PiPER-X

该工具不读取 GELLO，只连接已经运行的 `ag-gello-server`。服务端未启动时不能单独使用。

移动到零位并保持当前夹爪：

```bash
.venv/bin/python experiments/piper_x_movejs.py
```

移动到指定目标：

```bash
.venv/bin/python experiments/piper_x_movejs.py \
  --joints 0 0 0 0 0 0 \
  --gripper 0.5
```

参数：

| 参数 | 含义 | 默认值 | 单位/范围 |
| --- | --- | --- | --- |
| `--hostname` | ZMQ 服务地址 | `127.0.0.1` | IP 或主机名 |
| `--robot-port` | ZMQ 服务端口 | `6001` | 整数端口 |
| `--step-rad` | 六轴插值最大单步角度 | `0.02` | rad，正数 |
| `--period` | 相邻定位命令等待时间 | `0.02` | s，正数 |
| `--tolerance-deg` | 最终最大允许误差 | `1.0` | deg，正数 |
| `--joints` | PiPER-X J1～J6 目标 | `0 0 0 0 0 0` | 6 个有限数，rad |
| `--gripper` | 夹爪目标；省略时保持当前值 | 省略 | `0～1` |

该工具在 5 秒内重复发送最终目标并检查真实反馈；任一关节超出容差时会明确报错。

## 3. 官方/YAM 通用命令

本节不是当前 PiPER-X 的推荐入口。不要与 `start_gello_follow.sh` 同时运行，否则会争用 GELLO 串口。

### 3.1 生成 YAM 配置

```bash
.venv/bin/python scripts/generate_yam_config.py \
  --port /dev/serial/by-id/usb-FTDI_USB__-__Serial_Converter_FTBM4Z46-if00-port0
```

| 参数 | 含义 | 默认值 |
| --- | --- | --- |
| `--port` | GELLO 串口 | 自动检测 |
| `--start-joints` | 标定姿态 J1～J6 | `0 0 0 0 0 0` |
| `--joint-signs` | YAM GELLO 内部方向 | `1 -1 -1 -1 1 1` |
| `--gripper/--no-gripper` | 是否包含 ID 7 夹爪 | 开启 |
| `--channel` | YAM 从臂 CAN 通道 | `can_left` |
| `--output-path` | 硬件 YAML 输出 | `configs/yam_auto_generated.yaml` |
| `--sim-output-path` | 仿真 YAML 输出 | `configs/yam_auto_generated_sim.yaml` |

### 3.2 根据已知姿态计算 offset

```bash
.venv/bin/python scripts/gello_get_offset.py \
  --port /dev/serial/by-id/usb-FTDI_USB__-__Serial_Converter_FTBM4Z46-if00-port0 \
  --start-joints 0 0 0 0 0 0 \
  --joint-signs 1 -1 -1 -1 1 1 \
  --gripper
```

该命令只计算并打印推荐 offset，不会自动写回 `gello_agent.py`。

### 3.3 使用 YAML 启动官方工作流

单臂：

```bash
.venv/bin/python experiments/launch_yaml.py \
  --left-config-path configs/yam_auto_generated.yaml
```

双臂：

```bash
.venv/bin/python experiments/launch_yaml.py \
  --left-config-path configs/left.yaml \
  --right-config-path configs/right.yaml
```

加上 `--use-save-interface` 可以启用官方键盘保存接口。YAML 中的 `hz` 决定该工作流频率；当前 `yam_auto_generated.yaml` 为 `30 Hz`，与 PiPER-X 客户端默认的 `100 Hz` 无关。

## 4. 关键数据映射

### 4.1 两层方向系数

```text
Dynamixel 原始角度
  → GELLO joint_signs：1 -1 -1 -1 1 1
  → 映射后的 GELLO J1～J6
  → PiPER-X joint_signs：1 1 -1 -1 1 1
  → PiPER-X J1～J6 目标
```

第一层位于 `PORT_CONFIG_MAP`，用于将电机安装方向转换为 GELLO 坐标；第二层位于 `piper_x_follow.py`，用于将 GELLO 坐标转换为 PiPER-X 坐标。两层不能合并理解，也不应在排查通信问题时随意修改。

### 4.2 `--start-joints`

该参数主要用于选择 Dynamixel 多圈角度最接近哪一个 `2π` 分支，不是让 PiPER-X 自动运动到这些数值。

- 手动相对跟随常用 `0 0 0 0 0 0` 作为参考。
- 一键脚本先读取 GELLO 并完成 JS 对齐，再传入方向映射前的 GELLO J1～J6。
- 不要对传入值提前重复应用 PiPER-X `joint-signs`。

### 4.3 gripper

GELLO 读取侧根据 `gripper_config=(ID, open_deg, closed_deg)` 归一化原始角度，其中两个端点分别对应 `0` 和 `1`。PiPER-X 跟随链路将第七维独立传输，不与 J1～J6 的单步缩放耦合。主手端点方向由当前 `gripper_config` 标定决定；从臂服务端使用下面的 AGX 宽度约定。

PiPER-X 服务端最终采用：

```text
0 = 全闭
1 = 全开
AGX width_m = gripper × 0.1
```

## 5. Dynamixel 通信与 `-3001`

`warning, comm failed: -3001` 表示 SDK 没有在超时时间内收到状态包：

```text
COMM_RX_TIMEOUT = -3001
```

常见原因：

- GELLO 总线没有供电或电压不稳定。
- FTDI、TTL 数据线或公共 GND 接触不良。
- 某个串联接头或舵机异常。
- 主机与舵机的 baudrate、Protocol 或 ID 不一致。
- ID 重复导致状态包碰撞。
- `Status Return Level` 不返回写指令确认。
- 另一个进程占用同一串口。
- USB 瞬断、系统调度延迟或读取过于密集。

当前驱动会：

- 任一舵机初始化无响应时停止初始化。
- 初始化失败后先关闭串口再重试。
- 连续 10 次读取失败后向上层报告异常。
- 第一帧反馈最多等待 3 秒。
- 拒绝使用超过 1 秒的缓存反馈继续跟随。
- 退出时主动停止读取线程并释放串口。
- 串口被占用时只报错，不自动杀死其他进程。

只有 2.2 的只读命令能够稳定输出后，才应启动 PiPER-X 跟随。

## 6. 运行注意事项

- 同一时间只运行一个访问 GELLO FTDI 串口的进程。
- PiPER-X 推荐使用父目录一键脚本，不要混用官方 YAM YAML 工作流。
- `piper_x_movejs.py` 必须连接已启动的 `ag-gello-server`。
- 当前 PiPER-X 跟随、校准和退出回零全部保持在 JS 模式。
- 不要在跟随退出时额外执行普通 J 模式的 `zero` 或 `move_j`。
- 不要使用 `kill -9` 结束一键脚本，否则无法执行 JS 安全回零。
- 提高 `--hz` 只提高目标循环频率，不会自动提高硬件反馈率。
- 修改 baudrate 时必须同时修改全部 Dynamixel 和主机端配置。
- 修改 offset、sign 或 gripper 范围后，先只读验证，再逐轴小幅测试。
