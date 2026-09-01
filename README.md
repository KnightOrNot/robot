# Robot：PiPER-X 与 GELLO 数据采集系统

## （1）项目总览

### 1. 项目架构

本项目用于完成 PiPER-X 机械臂控制、GELLO 遥操作示教、原始数据采集，以及 LeRobot 标准数据集生成。各子项目保持独立职责，并通过明确的进程边界和数据接口协同工作。

```text
projects/
├── agilexrobotics/          # PiPER-X CAN 驱动、状态反馈和 ZMQ 控制服务
├── gello_software/          # GELLO Dynamixel 读取、目标映射和实时跟随
├── lerobot_converter/       # 原始数据校验与 LeRobot Dataset v3 离线转换
├── data/                    # 采集数据根目录，不属于任何代码子项目
│   ├── raw/                 # 实时采集的原始数据，是不可变数据源
│   └── lerobot/             # 从原始数据转换得到的 LeRobot 数据集
├── docs/                    # 机械臂用户手册和 CAN 协议资料
├── start_gello_follow.sh    # GELLO 跟随、校准和安全退出入口
├── start_data_record.sh     # GELLO 跟随、原始数据记录和安全退出入口
└── README.md                # 整个项目的架构、流程和联调说明
```

项目包含三个相互独立的工作域：

- `agilexrobotics` 负责直接访问 PiPER-X CAN 总线，是机械臂状态和控制的唯一所有者。
- `gello_software` 负责读取 GELLO，并将示教器状态转换为 PiPER-X 实际执行的七维控制目标。
- `lerobot_converter` 负责校验轻量原始记录格式，并在控制结束后将原始数据离线转换为 LeRobot Dataset v3；实时记录由 `gello_software` 完成。

#### 1.1 数据目录

代码与采集数据保持分离，所有数据统一放在 `projects/data/`：

```text
projects/data/
├── raw/
│   └── session_YYYYMMDD_HHMMSS/
│       ├── manifest.json
│       └── episodes/
│           ├── episode_000000.jsonl
│           ├── episode_000001.jsonl
│           └── episode_000002.jsonl.partial
└── lerobot/
    └── DATASET_NAME/
        ├── meta/
        └── data/
```

两个目录的生命周期不同：

- `data/raw/` 保存实时控制阶段产生的原始记录，是离线转换的权威数据源。转换程序只读这些数据，不得修改或删除原始 session。
- `data/lerobot/` 保存转换生成的 LeRobot Dataset v3，属于可重复生成的派生数据。转换失败时可以删除对应输出，并从原始数据重新生成。

默认数据根目录由总 shell 脚本所在位置计算，不依赖启动命令时的当前工作目录：

```bash
projects_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
raw_data_root="$projects_dir/data/raw"
lerobot_data_root="$projects_dir/data/lerobot"
```

具体程序仍应允许通过参数覆盖默认路径，以便以后将大量数据写入独立磁盘。代码中不得写死 `/home/right_or_not` 等机器相关的绝对路径。

`projects/data/` 当前位于各个子项目 Git 仓库之外，不由 `lerobot_converter/.gitignore` 或 `gello_software/.gitignore` 管理。如果以后将整个 `projects/` 初始化为一个总 Git 仓库，应在 `projects/.gitignore` 中加入：

```gitignore
/data/
```

真实采集数据不应提交到普通 Git 仓库。

实时跟随链路如下：

```text
GELLO Dynamixel
      │ FTDI 串口
      ▼
gello_software / piper_x_follow.py
      │ 方向映射、绝对对齐、单步限幅
      │ 7 维目标：[J1..J6(rad), gripper(0..1)]
      ▼
ZMQ tcp://127.0.0.1:6001
      ▼
agilexrobotics / ag-gello-server
      │ JS 流式控制
      ▼
PiPER-X / CAN
```

### 2. 数据采集总体设计

数据采集采用“实时原始记录 + 离线格式转换”的双阶段架构。实时控制阶段不直接创建 LeRobot 数据集，也不在 GELLO 环境中引入PyTorch、PyArrow、Pandas 等依赖。

```text
阶段一：实时跟随与原始记录

GELLO 输入
    ↓
生成并发送最终 action
    ↓
读取 PiPER-X 实际 observation
    ↓
写入轻量原始 episode
```

```
阶段二：离线转换

原始 episode
    ↓ 校验、清洗、按时间戳降采样
LeRobotDataset.add_frame()
    ↓
LeRobot Dataset v3
```

采用双阶段架构的原因：

- LeRobot 数据写入、Parquet 分片和统计计算不会干扰机械臂实时控制。
- 原始 action 在真正发送时记录，不需要由状态反馈反向推测。
- 不增加第二个高频 ZMQ 状态轮询客户端，避免影响当前控制服务。
- 原始数据可以使用不同 FPS、字段组合或 LeRobot 版本重复转换。
- GELLO、PiPER-X 驱动和 LeRobot 的依赖相互隔离，升级数据工具不会改变已验证的硬件控制环境。

### 3. Python 环境边界

项目允许并推荐使用多个虚拟环境。虚拟环境只影响 Python 解释器和包依赖，不会阻止进程通过 ZMQ、CAN 或串口通信。

```text
agilexrobotics/.venv
    └── CAN 驱动和 ag-gello-server

gello_software/.venv
    └── GELLO 串口、实时跟随和轻量原始记录

lerobot_converter/.venv
    └── 离线校验与 LeRobot Dataset v3 转换
```

总 shell 脚本必须显式调用每个环境中的解释器或命令，不依赖当前终端是否激活了某个虚拟环境。例如：

```bash
gello_python="$projects_dir/gello_software/.venv/bin/python"
converter_python="$projects_dir/lerobot_converter/.venv/bin/python"
```

LeRobot 数据集依赖应安装在 `lerobot_converter` 的独立环境中：

```bash
uv sync --extra dataset
```

Python 虚拟环境不会自动提供 FFmpeg 系统共享库。TorchCodec 虽然由 `uv sync` 安装在 `.venv` 中，其原生扩展仍需要 Ubuntu 中的 `libavutil.so`、`libavcodec.so`、`libavformat.so` 等动态库。为后续视频数据集提前安装：

```bash
sudo apt update
sudo apt install -y ffmpeg
sudo ldconfig
```

安装后执行 `ffmpeg -version`，并用 `lerobot_converter/.venv/bin/python -c "from torchcodec.decoders import VideoDecoder; print('TorchCodec 加载正常')"` 验证。不要用 `pip install ffmpeg` 或 `uv add ffmpeg` 替代系统安装；详细的依赖分层、故障判断和验证命令见 `lerobot_converter/README.md`。

不应仅为数据转换而修改已经通过硬件验证的 GELLO 或 AgileX 环境。实际实现时应提交 `uv.lock`，由锁文件固定 LeRobot 及其传递依赖版本。这里使用的`0.6.1` 是当前可从 PyPI 安装的发行版；此前本地 LeRobot 源码中声明的`0.6.2` 不代表该版本已经发布到 PyPI。

#### 3.1 频率与 FPS 配置

本项目中的“频率”分为 GELLO Dynamixel 读取频率、跟随控制/原始记录频率、PiPER-X CAN 反馈频率和 LeRobot 数据集 FPS。它们含义不同，不应将 `ag status` 的 `receive_fps`、raw manifest 的 `control_hz` 和 LeRobot `info.json` 的 `fps` 当成同一个参数。

| 环节                   | 当前设置                                 | 代码位置                                                                                                         | 如何修改                                                                                    |
| -------------------- | ------------------------------------ | ------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------- |
| GELLO Dynamixel 后台读取 | 每轮读取前 `sleep(0.01)`，理论上限低于 100 Hz    | `gello_software/gello/dynamixel/driver.py` 的 `_read_joint_states()`                                          | 修改 `time.sleep(0.01)`；但实际频率还受 FTDI、Dynamixel 串口通信时间和七个舅机返回时间限制                          |
| 普通 GELLO 跟随          | 默认 50 Hz                             | `gello_software/experiments/piper_x_follow.py` 的 `--hz`                                                      | 运行 `./start_gello_follow.sh --hz 目标值`                                                   |
| 带记录的 GELLO 跟随        | 默认 50 Hz                             | `gello_software/experiments/piper_x_follow_record.py` 的 `--hz`                                               | 运行 `./start_data_record.sh --hz 目标值`                                                    |
| 跟随循环限速器              | 由上述 `--hz` 传入                        | `gello_software/gello/env.py` 的 `RobotEnv(..., control_rate_hz=...)` 和 `Rate.sleep()`                        | 通常不直接修改 `RobotEnv` 的 100 Hz 通用默认值，因为 PiPER-X 两个客户端已显式传入 `args.hz`                       |
| AgileX JS 命令上限       | 默认 50 Hz                             | `agilexrobotics/src/agilexrobotics/gello_server.py` 的 `--hz` 和 `gello_robot.py` 的 `_wait_for_command_slot()` | 两个总 Shell 会将同一个 `--hz` 传给 AgileX 服务端，限制 `move_js` 的最高下发频率                               |
| PiPER-X CAN 反馈       | 由机械臂固件和 pyAgxArm SDK 的 CAN 广播/接收线程决定 | `agilexrobotics/src/agilexrobotics/driver.py` 的 `get_receive_fps()` 仅返回 `self._arm.get_fps()`                | 当前封装没有修改 CAN 反馈 FPS 的接口；`uv run ag fps` 或 `uv run ag status` 只用于观测，不会设置频率               |
| LeRobot Dataset v3   | 默认 30 FPS                            | `start_data_record.sh` 的 `dataset_fps=30`，以及 `lerobot_converter` CLI 的 `--fps`                               | 自动转换使用 `./start_data_record.sh --dataset-fps 30`；手工转换使用 `lerobot-converter ... --fps 30` |

普通跟随和记录跟随都通过总 Shell 的 `--hz` 显式设置控制频率。Shell 会将同一个值同时传给 GELLO 客户端和 AgileX 服务端，不需要再分别编辑两个子项目的默认值。raw manifest 的 `control_hz` 会由记录客户端自动写入，不要手工编辑 manifest。

设置值是目标频率，不代表硬件一定能达到。`RobotEnv.step()` 在一个周期内依次发送 ZMQ 命令、等待服务端返回、限速休眠，再请求 observation；如果串口、ZMQ、CAN 或机械臂处理总耗时超过目标周期，实际频率会低于 `--hz`。应以转换后 `quality_report.json` 中的 `actual_sample_hz`、`average_interval_ms` 和 `max_interval_ms` 判断实际采集质量。

### 4. 阶段一：实时原始数据记录

原始记录应位于 GELLO 跟随控制循环中，因为该位置同时知道“最终发送的action”和“执行后的机械臂反馈”。记录对象必须是经过方向变换、绝对对齐和单步限幅后，真正传给 `command_joint_state()` 的目标，不能使用未经处理的GELLO 原始角度代替 action。

单个控制周期的语义定义为：

```text
action[i]      = 本周期实际发送给 PiPER-X 的七维目标
observation[i] = 发送目标并等待一个控制周期后读取到的实际反馈
```

发送命令和读取反馈不是同一时刻，因此必须分别保存单调时钟时间戳：

- `command_time_ns`：最终 action 发送前的 `time.monotonic_ns()`。
- `observation_time_ns`：机械臂反馈返回后的 `time.monotonic_ns()`。
- `wall_time_ns`：可选的真实日期时间，只用于审计和定位采集会话。

每个样本至少包含：

```text
sequence
command_time_ns
observation_time_ns
action                 # 7 维：J1..J6(rad) + gripper(0..1)
joint_positions        # 7 维实际反馈
```

推荐同时记录：

```text
joint_velocities       # 7 维，J1..J6 为 rad/s
ee_pos_quat            # [x, y, z, qx, qy, qz, qw]，位置单位为 m
gripper_position       # 标量校验字段，0=全闭、1=全开
control_period_ns
```

原始数据第一版采用 JSON Lines。每行是一个完整样本，便于检查、恢复和离线处理：

```text
projects/data/raw/
└── session_YYYYMMDD_HHMMSS/
    ├── manifest.json
    └── episodes/
        ├── episode_000000.jsonl
        ├── episode_000001.jsonl
        └── episode_000002.jsonl.partial
```

`manifest.json` 保存整个会话不随帧变化的信息，例如：

```json
{
  "format": "piper_x_gello_raw",
  "format_version": 1,
  "robot_type": "piper_x",
  "control_hz": 50.0,
  "joint_units": "rad",
  "gripper_range": [0.0, 1.0],
  "quaternion_order": "xyzw",
  "joint_names": [
    "joint_1", "joint_2", "joint_3",
    "joint_4", "joint_5", "joint_6", "gripper"
  ],
  "joint_signs": [1, 1, -1, -1, 1, 1]
}
```

正在写入的 episode 使用 `.partial` 后缀。正常结束并完成刷新后再原子重命名为 `.jsonl`。转换器默认忽略 `.partial`，防止把异常中断的数据作为正式演示使用。

实时记录不把完整 episode 长期保存在内存中。控制线程将样本放入有界队列，后台线程负责 JSON 序列化和缓冲写盘。默认队列容量为 500 条，约每个控制频率周期数刷新一次用户态缓冲，并在 episode 结束时执行 `fsync()`。队列溢出会终止本次跟随并保留不完整 episode，不会静默丢帧。

### 5. Episode 操作约定

跟随和记录是两个不同的状态，停止记录不会停止机械臂跟随。`start_data_record.sh` 启动后支持以下单键操作，无需按 Enter：

```text
R       开始记录一个新 episode
S       停止并保存当前 episode
D       停止并丢弃当前 episode
P       显示当前记录状态
H       显示按键帮助
Ctrl+C  结束跟随，随后执行既有的 PiPER-X JS 安全回零流程
```

默认启动方式：

```bash
cd ~/projects
./start_data_record.sh --task "pick up the object"
```

默认仅启动跟随并等待按 `R`，不会自动记录。如果希望完成对齐后立即开始 episode 0，可以使用：

```bash
./start_data_record.sh --task "pick up the object" --start-recording
```

常用路径和性能参数：

```bash
./start_data_record.sh \
  --raw-data-root ./data/raw \
  --record-queue-size 500 \
  --task "pick up the object"
```

一次正式 episode 应对应一次完整任务演示，而不是简单对应固定大小的文件：

```text
启动跟随
    ↓
调整机械臂和场景
    ↓
R：开始记录
    ↓
完成一次任务
    ↓
S：保存 episode
    ↓
重置场景并录制下一次任务
```

如果 Ctrl+C 或通信异常发生时仍在录制，应先停止接收新帧、排空写入队列并保留 `.partial` 文件，然后继续执行原有安全回零流程。中断数据只有经过显式检查后才能恢复或转换。

### 6. 阶段二：转换为 LeRobot Dataset v3

离线转换器运行在 `lerobot_converter` 环境中，负责：

1. 读取并验证 `manifest.json` 和所有正式 episode。
2. 检查字段、维度、单位、非有限值和 `sequence` 连续性。
3. 根据真实单调时间戳检查控制频率、丢帧和采样抖动。
4. 按目标 FPS 选择时间最接近的原始样本。
5. 将字段映射为 LeRobot feature。
6. 每个原始 episode 调用一次 `save_episode()`。
7. 完成全部 episode 后调用 `finalize()` 生成元数据和统计信息。

推荐的最小 LeRobot 映射为：

```text
原始 joint_positions   → observation.state
原始 action            → action
任务描述               → task / task_index
```

可选映射为：

```text
原始 joint_velocities  → observation.velocity
原始 ee_pos_quat       → observation.ee_pose
```

其中：

- `observation.state` 表示 PiPER-X 的实际反馈。
- `action` 表示 GELLO 控制链路最终实际下发的目标。
- 不得使用同一份实际关节反馈同时代替 `observation.state` 和 `action`。
- 六个关节统一使用弧度，夹爪统一使用 `0=全闭、1=全开`。
- 末端姿态统一使用 `xyzw` 四元数顺序。

LeRobot 会自动生成以下字段，转换程序不应手工传入：

```text
index
episode_index
frame_index
timestamp
task_index
```

当前控制循环默认为 50 Hz，目标数据集默认为 30 FPS。降采样不能简单使用固定步长，因为 `50 / 30` 不是整数，且实际控制周期存在抖动。转换器依据 `observation_time_ns` 建立目标时间轴，并为每个目标时间选择最近的有效样本。

每次转换还应输出质量报告，至少包括：

```text
原始样本数和转换帧数
平均与实际采样频率
最大采样间隔
丢失或重复的 sequence
NaN/Inf 和维度错误数量
最大时间匹配误差
保留、忽略和失败的 episode 数量
```

#### 6.1 自动转换

`start_data_record.sh` 默认在记录客户端退出后自动转换。脚本会先完成 PiPER-X JS 安全回零并关闭 CAN/ZMQ 服务，再将本次 `data/raw/session_YYYYMMDD_HHMMSS` 转换到同名的 `data/lerobot/session_YYYYMMDD_HHMMSS`。只有按 `S` 保存的 `.jsonl` 会成为正式 episode；`.jsonl.partial` 会被统计但忽略；如果 session 中没有正式 episode，脚本会跳过转换。

```bash
./start_data_record.sh \
  --task "pick up the object" \
  --dataset-fps 30 \
  --lerobot-data-root ./data/lerobot
```

如果当次只需保留 raw session，不要进行自动转换：

```bash
./start_data_record.sh --task "pick up the object" --skip-conversion
```

#### 6.2 手工转换

手工转换适用于历史 raw session、更换目标 FPS，或者使用不同 feature 组合重新生成数据集。先初始化独立环境：

```bash
cd ~/projects/robot/lerobot_converter
uv sync --extra dataset
```

然后转换一个已录制 session：

```bash
cd ~/projects/robot/lerobot_converter
uv run --extra dataset lerobot-converter \
  ../data/raw/session_YYYYMMDD_HHMMSS \
  ../data/lerobot/session_YYYYMMDD_HHMMSS \
  --repo-id local/piper_x_gello_session_YYYYMMDD_HHMMSS \
  --fps 30
```

默认保留 `observation.velocity` 和 `observation.ee_pose`。如果某个下游任务不需要它们，可以分别追加 `--without-velocity` 或 `--without-ee-pose`。输出路径必须尚不存在，转换器不会覆盖已有数据集；需要用不同参数重新转换时，应使用新的输出目录名。

#### 6.3 转换内部流程

```text
manifest.json + episodes/*.jsonl
              │
              ├── 校验格式版本、单位、字段、7 维形状、NaN/Inf
              ├── 校验 sequence 连续和 observation_time_ns 严格递增
              ├── 忽略 episodes/*.jsonl.partial
              ├── 按 observation_time_ns 建立目标 FPS 时间轴
              ├── 为每个目标时刻选择最近的 raw 样本
              ├── LeRobotDataset.add_frame() / save_episode() / finalize()
              └── meta/ + data/ + quality_report.json
```

转换器先完成全部 raw 校验，再创建输出数据集。任何正式 episode 存在 manifest 不匹配、sequence 缺口、时间戳倒退、维度错误或 NaN/Inf 时，整个转换立即失败，不会静默跳过损坏的正式 episode。

输出中 `meta/info.json` 记录 `codebase_version: v3.0`、目标 `fps`、feature 定义和 episode/frame 总数；`data/chunk-*/file-*.parquet` 保存帧数据；`meta/stats.json` 保存统计量；`quality_report.json` 保存 raw 采集频率、采样间隔、时间匹配误差和 episode 处理数量。

#### 6.4 转换结果检查

先检查质量报告和 v3 元数据：

```bash
python -m json.tool data/lerobot/session_YYYYMMDD_HHMMSS/quality_report.json
python -m json.tool data/lerobot/session_YYYYMMDD_HHMMSS/meta/info.json
```

重点确认 `failed_episodes` 和各项错误数量为 0，`kept_episodes`、`total_episodes` 与预期一致，`actual_sample_hz` 接近原始跟随频率，`max_interval_ms` 和 `max_time_match_error_ms` 没有异常尖峰，并且 `meta/info.json` 中的 `codebase_version` 为 `v3.0`、`fps` 等于转换时的目标值。

### 7. 当前实现状态

当前已经完成并通过硬件联调的是：

- PiPER-X CAN 驱动和状态读取。
- `ag-gello-server` ZMQ 控制服务。
- GELLO 七维目标映射和 PiPER-X JS 实时跟随。
- 启动校准、通信异常处理和 Ctrl+C 安全回零。

当前已经完成并通过实际硬件验证的是：

- `start_data_record.sh` 独立数据记录入口；没有修改 `start_gello_follow.sh`。
- `piper_x_follow_record.py` 带记录的 PiPER-X 专用跟随客户端。
- `RawEpisodeRecorder` 异步 JSONL episode 记录器。
- `R/S/D/P/H` 单键记录操作和 `--start-recording` 自动开始选项。
- `manifest.json`、正式 `.jsonl`、异常 `.jsonl.partial` 和 session 重名保护。
- 最终 action、实际 observation、单调时间戳、墙上时间和控制周期记录。
- 原始数据到 LeRobot Dataset v3 的离线转换器。
- 转换后的数据集加载验证和质量报告。
- 第一阶段记录功能的真实 PiPER-X/GELLO 硬件验收和频率质量测试。

记录脚本退出时先通过已建立的 JS 会话安全回零并关闭 CAN/ZMQ 服务，然后再启动离线转换，避免 PyTorch/Arrow 初始化影响机械臂退出。

## （2）PiPER-X 与 GELLO 跟随模式联调记录

本工作区通过 GELLO 示教器控制 AgileX PiPER-X 机械臂，主要包含以下内容：

```text
projects/
├── agilexrobotics/          # PiPER-X CAN 通信、控制驱动和命令行工具
├── gello_software/          # GELLO Dynamixel 读取和跟随客户端
└── start_gello_follow.sh    # 自动检查、校准、跟随和安全退出脚本
```

本文记录实际硬件联调中确认的重要行为和故障处理原则。以下结论对应当前测试机械臂固件：

```text
hardware: H-V1.2-1
software: S-V1.8-2
```

不同固件版本的模式切换行为可能不同，升级固件后需要重新验证。

### 1. 普通 J 模式与 JS 模式

#### 1.1 普通 J 模式

普通 J 模式用于执行常规关节运动，例如：

```bash
uv run ag zero
uv run ag move_j ...
```

它适合单次目标位置控制。驱动发送目标后，等待机械臂到达目标，并根据关节误差和超时时间判断运动是否完成。普通命令默认采用较保守的速度限制。

普通 J 模式的特点：

- 适合回零、单次关节定位等非实时控制。
- 命令通常包含目标到达检查。
- 如果关节未在规定时间内到达目标，会报告 `did not reach its target`。
- 不适合作为 GELLO 高频连续跟随的主要控制通道。

#### 1.2 JS 模式

JS 模式用于高频、连续的关节流式控制。GELLO 跟随时，服务端不断接收客户端的 J1～J6 和 gripper 目标，并通过 `move_js` 更新机械臂目标。

JS 模式的特点：

- 适合 GELLO 实时跟随。
- 不对每一帧命令执行普通 J 模式的目标到达等待。
- 可以连续更新目标，响应速度明显快于逐条执行 `move_j`。
- 当前 GELLO 跟随、启动校准和退出回零全部使用同一个 JS 会话。

#### 1.3 状态反馈的限制

当前 SDK 使用 J 类型的运动模式字段配合额外的 JS 标志进入 JS 控制，但机械臂状态反馈没有完整返回这个额外标志。因此：

```text
mode_feedback = 1
```

只能说明反馈属于 J 类控制，不能仅凭这个字段可靠地区分普通 J 模式和 JS 模式。判断当前是否处于 JS 工作流时，还需要结合服务端生命周期和最近执行的控制操作。

### 2. JS 切换回普通 J 模式的失能现象

#### 2.1 已通过硬件实验确认的现象

在当前 `S-V1.8-2` 固件上进行过不带运动目标的纯模式切换实验：

1. 机械臂在普通 J 模式下保持使能。
2. 切换到 JS 模式后，六个关节仍保持使能。
3. 不发送关节位置和速度命令，直接从 JS 切换回普通 J 模式。
4. 大约 1 秒后，六个关节全部失能。

因此，当前环境中“从已使能的 JS 模式直接切换到普通 J 模式”本身就可能触发失能。该现象与以下因素无直接关系：

- 是否发送了非零关节目标。
- 是否调用了 `move_j`。
- 是否写入 `speed-percent`。
- 是否断开了 Python 或 CAN 连接。
- 机械臂是否已经处于零位。

这也是曾经出现“退出跟随后再次执行 `ag zero`，机械臂立即失能”的主要原因：机械臂控制器实际仍处于 JS 状态，新进程在执行普通 J 命令前进行了 JS 到 J 的转换。

#### 2.2 当前采用的处理方式

自动校准、实时跟随和退出回零统一保持在 JS 模式下完成：

```text
启动 ag-gello-server
        ↓
进入一次 JS 模式
        ↓
通过 JS 小步移动到 zero
        ↓
通过 JS 对齐 GELLO 当前姿态
        ↓
通过 JS 实时跟随
        ↓
Ctrl+C 后通过同一 JS 通道回到 zero
        ↓
关闭客户端和服务端，不主动切换到普通 J 模式
```

安全退出时不得额外执行以下操作：

- 不执行 `reset`。
- 不执行普通 J 模式的 `zero` 或 `move_j`。
- 不主动发送 JS 到普通 J 的模式切换。
- 不在退出过程中重复执行使能或速度设置。

当前 `end_fast_response_mode()` 只恢复 SDK 内部的自动模式设置，不向机械臂发送普通 J 模式切换命令。

### 3. 自动校准与跟随流程

在 `projects` 目录执行：

```bash
./start_gello_follow.sh
```

脚本当前执行以下流程：

1. 配置 PiPER-X 的 CAN 接口。
2. 检查 GELLO 串口和 PiPER-X CAN 通信。
3. 读取 GELLO 当前 J1～J6 和 gripper。
4. 启动 `ag-gello-server`，通过 JS 分步将 PiPER-X 移动到 zero。
5. 按已确认的方向系数，通过同一 JS 会话对齐六轴和夹爪。
6. 启动 GELLO 客户端并进入实时跟随。

当前六轴方向映射为：

```text
J1  J2  J3  J4  J5  J6
 1   1  -1  -1   1   1
```

这些系数已经结合实际机械臂方向验证，不应在排查通信或模式问题时随意修改。

脚本使用文件锁保证同一时间只有一个自动跟随流程读取 GELLO 串口。服务端运行在独立 session 中，使终端收到 Ctrl+C 时，服务端能够暂时保持存活并完成 JS 安全回零。

### 4. Ctrl+C 安全退出

正常跟随过程中按下 Ctrl+C 后：

1. 停止 GELLO 跟随客户端。
2. 暂时保持 `ag-gello-server` 运行。
3. 使用 `piper_x_movejs.py` 通过现有 JS 通道分步回零。
4. 回零完成后关闭服务端。
5. 释放 GELLO 串口和后台 Dynamixel 读取线程。

如果在服务端启动前中断，例如脚本停在第 3 步读取 GELLO，则不会执行 PiPER-X JS 回零，因为本次脚本还没有建立 JS 控制通道，也没有让机械臂产生新的运动。

不建议使用 `kill -9` 结束脚本。`kill -9` 无法执行 shell 的退出处理，应优先使用 Ctrl+C。

### 5. GELLO `-3001` 通信错误

#### 5.1 错误含义

Dynamixel SDK 中的 `-3001` 是：

```text
COMM_RX_TIMEOUT: There is no status packet
```

它表示 FTDI 已尝试发送指令，但没有在规定时间内收到 Dynamixel 状态包。该错误属于 GELLO 串口总线，不是 PiPER-X CAN 错误。

连续出现以下输出时，GELLO 关节值已经不能再作为实时控制输入：

```text
warning, comm failed: -3001
```

旧驱动会无限等待第一帧反馈，或者在运行中继续使用最后一次缓存的旧角度，因此会表现为“程序还在运行，但是机械臂不再跟随”。当前驱动已经修改为：

- 初始化时任一 Dynamixel 无响应即判定本次初始化失败。
- 初始化失败后先关闭串口，再执行下一次重试。
- 连续 10 次读取超时后向上层报告异常。
- 第一帧反馈最多等待 3 秒。
- 不允许使用超过 1 秒的旧反馈继续控制。
- 客户端异常退出后主动释放串口。
- 不再通过 `fuser -k` 自动杀死串口占用进程。

跟随期间发生持续通信超时后，客户端会退出，shell 随后通过仍在运行的 PiPER-X 服务端执行 JS 安全回零。

#### 5.2 常见原因

`-3001` 可能由以下原因引起：

- GELLO Dynamixel 总线供电不稳定或断电。
- FTDI USB 松动、USB 端口瞬断或转接器异常。
- Dynamixel 串联线缆接触不良。
- 某个舵机或其上游连接故障，导致后续设备无法返回状态包。
- 另一个进程同时占用同一个 GELLO 串口。
- Dynamixel ID、波特率或状态返回配置与代码不一致。

如果系统能够看到 `/dev/serial/by-id/...`，只能证明 FTDI 设备已被 Linux 枚举，不能证明 Dynamixel 总线供电和状态包通信正常。

#### 5.3 单独验证 GELLO

恢复供电和线缆后，先执行只读命令：

```bash
cd ~/projects/gello_software
.venv/bin/python experiments/read_gello_joints.py \
  --gello-port /dev/serial/by-id/usb-FTDI_USB__-__Serial_Converter_FTBM4Z46-if00-port0
```

只有该命令能够正常输出 J1～J6 和 gripper 后，才应重新运行自动跟随脚本。

### 6. PiPER-X CAN 与 GELLO 串口需要分开判断

以下两条通信链路彼此独立：

```text
PiPER-X 机械臂 ←→ CAN 适配器 ←→ agilexrobotics
GELLO 舵机     ←→ FTDI 串口   ←→ gello_software
```

因此：

- `ag status` 正常，只能证明 PiPER-X CAN 通信正常。
- GELLO 出现 `-3001` 时，PiPER-X CAN 仍可能完全正常。
- CAN 为 `ERROR-ACTIVE` 且错误计数为零，不代表 GELLO 串口正常。
- 排障时应分别验证两条链路，不要通过其中一条推断另一条。

### 7. 联调安全原则

- 启动、校准和退出时确保机械臂周围无人且无障碍物。
- JS 校准采用小步插值，不直接发送跨度较大的单帧目标。
- 不在当前固件上直接执行已使能 JS 到普通 J 的模式切换。
- 通信异常时不得继续使用缓存关节值控制机械臂。
- 不使用自动杀进程的方式抢占串口。
- 修改关节方向、单位换算或限制参数后必须逐轴、小幅验证。
- 不能仅根据命令行状态断言机械臂已使能，应同时确认实际机械臂状态。

## （3）PiPER-X 参数记录模式调试记录

### 1. `start_data_record.sh` 跟随与原始数据记录

#### 1.1 功能和启动方式

`start_data_record.sh` 在保留原有 CAN 检查、GELLO 状态读取、PiPER-X JS 回零、姿态对齐和 Ctrl+C 安全退出流程的基础上，启动独立的 `piper_x_follow_record.py` 客户端。默认行为是先进入跟随模式但不立即记录，便于先检查机械臂方向、夹爪和场景是否正常。

推荐命令：

```bash
cd ~/projects
./start_data_record.sh --task "pick up the object"
```

脚本依次完成：

1. 配置并检查 PiPER-X CAN。
2. 检查 GELLO 串口并读取当前七维状态。
3. 启动 `ag-gello-server`，通过同一 JS 会话分步回零。
4. 将 PiPER-X 对齐到 GELLO 当前姿态。
5. 启动独立的 `piper_x_follow_record.py` 客户端。
6. 进入正常跟随状态，但默认不记录数据。

终端显示以下信息后，可以先操作 GELLO 检查跟随：

```text
GELLO 跟随已启动；R=开始，S=保存，D=丢弃，P=状态，H=帮助，Ctrl+C=退出
```

#### 1.2 记录按键

所有按键均为单键操作，不需要按 Enter：

| 按键       | 功能                  | 是否停止跟随 |
| -------- | ------------------- | ------ |
| `R`      | 开始记录一个新 episode     | 否      |
| `S`      | 停止并保存当前 episode     | 否      |
| `D`      | 停止并丢弃当前 episode     | 否      |
| `P`      | 显示当前记录状态            | 否      |
| `H`      | 显示按键帮助              | 否      |
| `Ctrl+C` | 结束跟随并通过现有 JS 服务安全回零 | 是      |

确认跟随正常后按 `R`，程序创建 `episode_000000.jsonl.partial` 并从下一个控制周期开始记录。完成一次任务后按 `S`，程序刷新后台队列、执行落盘并将文件原子重命名为 `episode_000000.jsonl`，机械臂继续跟随。重置场景后可以再次按 `R` 录制 `episode_000001`，因此同一个 session 可以连续保存多个 episode。

如果本次演示失败，按 `D` 会停止并删除当前 `.partial`，但不会停止跟随；下一次按 `R` 会重新使用同一个 episode 编号。如果当前没有活动 episode，按 `S` 或 `D` 只会显示提示，不会改变机械臂状态。

#### 1.3 推荐操作流程

```text
运行 start_data_record.sh
    ↓
完成回零和 GELLO 对齐
    ↓
先试运行跟随，不记录
    ↓
R：开始 episode 000000
    ↓
完成一次任务演示
    ↓
S：保存 episode 000000，继续跟随
    ↓
重置场景
    ↓
R：开始 episode 000001
    ↓
S：保存，或 D：丢弃
    ↓
Ctrl+C：结束跟随并安全回零
```

#### 1.4 自动开始记录

如果希望完成对齐后立即开始 episode 0，可以使用：

```bash
./start_data_record.sh --task "pick up the object" --start-recording
```

该模式没有跟随试运行阶段，只适合已经确认硬件、方向和场景均正常的情况。

#### 1.5 参数

| 参数                    | 含义                               | 默认值                           |
| --------------------- | -------------------------------- | ----------------------------- |
| `--gello-port`        | GELLO FTDI/Dynamixel 串口          | 当前 FTBM4Z46 by-id 路径          |
| `--can-interface`     | PiPER-X CAN 接口                   | `can0`                        |
| `--can-bitrate`       | CAN 波特率                          | `1000000`                     |
| `--host`              | `ag-gello-server` 地址             | `127.0.0.1`                   |
| `--port`              | `ag-gello-server` 端口             | `6001`                        |
| `--hz`                | GELLO 跟随、raw 采样和 PiPER-X JS 命令频率 | `50`                          |
| `--raw-data-root`     | 原始 session 根目录                   | `projects/data/raw`           |
| `--lerobot-data-root` | LeRobot Dataset v3 根目录           | `projects/data/lerobot`       |
| `--dataset-fps`       | 离线数据集目标帧率                        | `30`                          |
| `--skip-conversion`   | 安全退出后跳过离线转换                      | 关闭                            |
| `--task`              | 当前 session 的任务描述                 | `PiPER-X GELLO teleoperation` |
| `--record-queue-size` | 后台异步写盘队列容量                       | `500`                         |
| `--start-recording`   | 对齐完成后立即开始 episode 0              | 关闭                            |
| `--yes`               | 跳过真实运动前的 `yes` 确认                | 关闭                            |

完整示例：

```bash
./start_data_record.sh \
  --gello-port /dev/serial/by-id/usb-FTDI_USB__-__Serial_Converter_FTBM4Z46-if00-port0 \
  --can-interface can0 \
  --can-bitrate 1000000 \
  --host 127.0.0.1 \
  --port 6001 \
  --hz 50 \
  --raw-data-root ./data/raw \
  --lerobot-data-root ./data/lerobot \
  --dataset-fps 30 \
  --record-queue-size 500 \
  --task "pick up the object"
```

相对形式的数据根目录按启动脚本的工作目录解析，随后转换为绝对路径，因此子进程切换工作目录后不会改变输出位置。

#### 1.6 输出文件

默认输出结构：

```text
projects/data/raw/
└── session_YYYYMMDD_HHMMSS/
    ├── manifest.json
    └── episodes/
        ├── episode_000000.jsonl
        ├── episode_000001.jsonl
        └── episode_000002.jsonl.partial
```

`.jsonl` 表示已经按 `S` 正常保存的 episode；`.jsonl.partial` 表示仍在记录或被 Ctrl+C、通信错误等情况中断的数据，离线转换器会统计但忽略它。

正常退出后，脚本先安全回零并关闭控制服务，再将本次 session 自动转换到 `data/lerobot/<session_name>/`。如果没有按 `S` 保存任何正式 episode，则跳过转换；如需只保留 raw 数据，使用 `--skip-conversion`。

#### 1.7 Ctrl+C 和异常退出

如果按 Ctrl+C 时存在活动 episode，记录客户端先停止接收新帧、排空后台队列并保留 `.partial`，随后外层 Shell 通过仍在运行的 `ag-gello-server` 执行 PiPER-X JS 安全回零。如果当前没有活动 episode，程序直接结束跟随并执行安全回零。

不要使用 `kill -9` 停止数据记录流程，因为它无法刷新后台队列、恢复终端按键模式或执行 PiPER-X 安全回零。

当前跟随、原始记录和离线转换都已通过实际 PiPER-X/GELLO 数据验证。

### 2. 数据记录频率 FPS 调试记录

#### 2.1 统一参数

普通跟随和数据记录入口都对外提供 `--hz`。该参数是实时控制目标频率，同时控制 GELLO 跟随循环、PiPER-X JS 命令最高下发频率，以及记录模式的 raw 目标采样频率。默认值为 50 Hz，参数必须是正的有限数值。

```bash
# 普通跟随
./start_gello_follow.sh --hz 50

# 跟随并记录，raw manifest.control_hz 会自动写为 50.0
./start_data_record.sh --hz 50 --dataset-fps 30 --task "frequency test"
```

`--hz` 和 `--dataset-fps` 不是同一个参数。`--hz` 控制真实硬件跟随与 raw 采集；`--dataset-fps` 只控制退出跟随后离线转换出的 LeRobot Dataset FPS。例如 `--hz 50 --dataset-fps 30` 表示以 50 Hz 目标采集 raw，然后按时间戳最近邻采样为 30 FPS 数据集。

#### 2.2 参数传递路径

```text
start_gello_follow.sh --hz HZ
    ├── ag-gello-server --hz HZ
    │       └── GelloPiperXRobot(control_hz=HZ)
    │               └── _wait_for_command_slot() 限制 move_js 下发上限
    └── piper_x_follow.py --hz HZ
            └── RobotEnv(control_rate_hz=HZ)

start_data_record.sh --hz HZ
    ├── ag-gello-server --hz HZ
    │       └── GelloPiperXRobot(control_hz=HZ)
    └── piper_x_follow_record.py --hz HZ
            ├── RobotEnv(control_rate_hz=HZ)
            └── RawEpisodeRecorder(control_hz=HZ)
```

对应源码位置：

| 层级          | 文件                                                    | 参数或函数                                        | 作用                              |
| ----------- | ----------------------------------------------------- | -------------------------------------------- | ------------------------------- |
| 总入口         | `start_gello_follow.sh`                               | `hz="50"` 和 `--hz`                           | 普通跟随实验入口                        |
| 总入口         | `start_data_record.sh`                                | `hz="50"` 和 `--hz`                           | 记录跟随实验入口                        |
| GELLO 客户端   | `gello_software/experiments/piper_x_follow.py`        | CLI `--hz`，默认 50.0                           | 将频率传给 `RobotEnv`                |
| GELLO 记录客户端 | `gello_software/experiments/piper_x_follow_record.py` | CLI `--hz`，默认 50.0                           | 将频率传给 `RobotEnv` 和 raw recorder |
| GELLO 限速器   | `gello_software/gello/env.py`                         | `RobotEnv(control_rate_hz)` 和 `Rate.sleep()` | 保证整轮客户端循环不超过目标频率                |
| AgileX 服务端  | `agilexrobotics/src/agilexrobotics/gello_server.py`   | CLI `--hz`，默认 50.0                           | 把目标频率传给 PiPER-X GELLO 适配器       |
| AgileX 适配器  | `agilexrobotics/src/agilexrobotics/gello_robot.py`    | `control_hz` 和 `_wait_for_command_slot()`    | 防止 `move_js` 实际下发频率超过设置值        |

#### 2.3 建议实验步骤

每次只改变 `--hz`，其他硬件、动作、数据集 FPS 和场景条件保持不变。建议从已验证的 50 Hz 基线开始，先向下测试 30 Hz，再逐步向上测试 75 Hz 和 100 Hz，不要第一次就设置过高值。

```bash
./start_data_record.sh --hz 30  --dataset-fps 30 --task "hz_30"
./start_data_record.sh --hz 50  --dataset-fps 30 --task "hz_50"
./start_data_record.sh --hz 75  --dataset-fps 30 --task "hz_75"
./start_data_record.sh --hz 100 --dataset-fps 30 --task "hz_100"
```

每个频率至少录制一个持续数秒的正式 episode，然后检查对应 `quality_report.json` 中的 `actual_sample_hz`、`average_interval_ms`、`max_interval_ms`、`max_time_match_error_ms`、sequence 错误和失败 episode 数量。同时观察 GELLO 是否出现 `-3001`、PiPER-X 是否跟随抖动、迟滞或通信异常。

目标周期的理论值为 `1000 / hz` 毫秒：

| `--hz` | 理论周期     |
| ------:| --------:|
| 30     | 33.33 ms |
| 50     | 20.00 ms |
| 75     | 13.33 ms |
| 100    | 10.00 ms |

如果提高 `--hz` 后 `actual_sample_hz` 不再提高，而 `average_interval_ms` 稳定在某个更大的数值，说明 GELLO 串口、ZMQ 往返、AgileX CAN 命令、observation 获取或本地调度中的某个环节已成为瓶颈。`observation_time_ns - command_time_ns` 还包含 `RobotEnv` 为了限速而主动等待的时间，不能直接当作机械臂硬件响应延迟。

#### 2.4 不由 `--hz` 修改的频率

GELLO Dynamixel 后台线程在 `gello_software/gello/dynamixel/driver.py::_read_joint_states()` 中每轮读取前执行 `time.sleep(0.01)`。这是串口读取调度间隔，不会由总 Shell 的 `--hz` 自动修改。它的理论上限低于 100 Hz，实际还受 FTDI、Dynamixel SyncRead 和舅机返回时间限制。测试 100 Hz 或更高的跟随频率时，必须注意客户端可能多次使用同一帧 GELLO 缓存角度；除非已确认串口余量和通信稳定性，不建议同时缩短这个 `0.01 s` 间隔。

PiPER-X CAN `receive_fps` 由机械臂固件的 CAN 广播和 pyAgxArm 接收线程决定。`agilexrobotics/src/agilexrobotics/driver.py::get_receive_fps()` 只读取 SDK 统计值，`uv run ag fps` 和 `uv run ag status` 也只能观测。pyAgxArm `FPSManager` 中的 `0.1 s` 是统计窗口，修改它只会改变 FPS 数字的刷新方式，不会改变 CAN 真实反馈频率。CAN 波特率 `1000000 bit/s` 也不等于 FPS，不应在频率实验中改动。
