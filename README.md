# 一、项目总览

## 1. 项目架构

本项目用于完成 PiPER-X 机械臂控制、GELLO 遥操作示教、原始数据采集，以及 LeRobot 标准数据集生成。各子项目保持独立职责，并通过明确的进程边界和数据接口协同工作。

```text
projects/
├── agilexrobotics/          # PiPER-X CAN 驱动、状态反馈和 ZMQ 控制服务
├── gello_software/          # GELLO Dynamixel 读取、目标映射和实时跟随
├── lerobot_recorder/        # 原始数据规范与 LeRobot 离线转换工具（待实现）
├── data/                    # 采集数据根目录，不属于任何代码子项目
│   ├── raw/                 # 实时采集的原始数据，是不可变数据源
│   └── lerobot/             # 从原始数据转换得到的 LeRobot 数据集
├── docs/                    # 机械臂用户手册和 CAN 协议资料
├── start_gello_follow.sh    # GELLO 跟随、校准和安全退出入口
└── README.md                # 整个项目的架构、流程和联调说明
```

项目包含三个相互独立的工作域：

- `agilexrobotics` 负责直接访问 PiPER-X CAN 总线，是机械臂状态和控制的唯一所有者。
- `gello_software` 负责读取 GELLO，并将示教器状态转换为 PiPER-X 实际执行的七维控制目标。
- `lerobot_recorder` 负责定义轻量原始记录格式，并在控制结束后将原始数据离线转换为 LeRobot Dataset v3。

### 1.1 数据目录

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

`projects/data/` 当前位于各个子项目 Git 仓库之外，不由 `lerobot_recorder/.gitignore` 或 `gello_software/.gitignore` 管理。如果以后将整个 `projects/` 初始化为一个总 Git 仓库，应在 `projects/.gitignore` 中加入：

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

## 2. 数据采集总体设计

数据采集采用“实时原始记录 + 离线格式转换”的双阶段架构。实时控制阶段不直接创建 LeRobot 数据集，也不在 GELLO 环境中引入PyTorch、PyArrow、Pandas 等依赖。

````text
阶段一：实时跟随与原始记录

GELLO 输入
    ↓
生成并发送最终 action
    ↓
读取 PiPER-X 实际 observation
    ↓
写入轻量原始 episode
````

````
阶段二：离线转换

原始 episode
    ↓ 校验、清洗、按时间戳降采样
LeRobotDataset.add_frame()
    ↓
LeRobot Dataset v3
````

采用双阶段架构的原因：

- LeRobot 数据写入、Parquet 分片和统计计算不会干扰机械臂实时控制。
- 原始 action 在真正发送时记录，不需要由状态反馈反向推测。
- 不增加第二个高频 ZMQ 状态轮询客户端，避免影响当前控制服务。
- 原始数据可以使用不同 FPS、字段组合或 LeRobot 版本重复转换。
- GELLO、PiPER-X 驱动和 LeRobot 的依赖相互隔离，升级数据工具不会改变已验证的硬件控制环境。

## 3. Python 环境边界

项目允许并推荐使用多个虚拟环境。虚拟环境只影响 Python 解释器和包依赖，不会阻止进程通过 ZMQ、CAN 或串口通信。

```text
agilexrobotics/.venv
    └── CAN 驱动和 ag-gello-server

gello_software/.venv
    └── GELLO 串口、实时跟随和轻量原始记录

lerobot_recorder/.venv
    └── 离线校验与 LeRobot Dataset v3 转换
```

总 shell 脚本必须显式调用每个环境中的解释器或命令，不依赖当前终端是否激活了某个虚拟环境。例如：

```bash
gello_python="$projects_dir/gello_software/.venv/bin/python"
recorder_python="$projects_dir/lerobot_recorder/.venv/bin/python"
```

LeRobot 数据集依赖应安装在 `lerobot_recorder` 的独立环境中：

```bash
uv add "lerobot[dataset]==0.6.1"
```

不应仅为数据转换而修改已经通过硬件验证的 GELLO 或 AgileX 环境。实际实现时应提交 `uv.lock`，由锁文件固定 LeRobot 及其传递依赖版本。这里使用的`0.6.1` 是当前可从 PyPI 安装的发行版；此前本地 LeRobot 源码中声明的`0.6.2` 不代表该版本已经发布到 PyPI。

## 4. 阶段一：实时原始数据记录

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
  "control_hz": 100,
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

实时记录不得把完整 episode 长期保存在内存中。推荐由控制线程将样本放入有界队列，后台线程负责 JSON 序列化和缓冲写盘。队列溢出必须报告错误并将 episode 标记为不完整，不能静默丢帧；也不应每帧调用 `fsync()`，以免磁盘延迟破坏控制周期。

## 5. Episode 操作约定

跟随和记录是两个不同的状态，停止记录不应停止机械臂跟随。计划支持以下操作：

```text
R       开始记录一个新 episode
S       停止并保存当前 episode
D       停止并丢弃当前 episode
Ctrl+C  结束跟随，随后执行既有的 PiPER-X JS 安全回零流程
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

## 6. 阶段二：转换为 LeRobot Dataset v3

离线转换器运行在 `lerobot_recorder` 环境中，负责：

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

当前控制循环默认为 100 Hz，目标数据集可以采用 30 FPS。降采样不能简单使用 `frames[::3]`，因为 `100 / 3` 并不等于 30，且实际控制周期存在抖动。转换器应依据 `observation_time_ns` 建立 30 FPS 目标时间轴，并为每个目标时间选择最近的有效样本。

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

## 7. 当前实现状态

当前已经完成并通过硬件联调的是：

- PiPER-X CAN 驱动和状态读取。
- `ag-gello-server` ZMQ 控制服务。
- GELLO 七维目标映射和 PiPER-X JS 实时跟随。
- 启动校准、通信异常处理和 Ctrl+C 安全回零。

当前尚待实现的是：

- 原始数据 schema 和 `manifest.json` 的程序化校验。
- 跟随循环中的异步 episode 记录器。
- 开始、停止和丢弃 episode 的操作界面。
- 原始数据到 LeRobot Dataset v3 的离线转换器。
- 转换后的数据集加载验证和质量报告。

在这些功能落地前，本文中的原始记录目录、按键和转换命令均属于设计约定，不应视为当前已经可执行的功能。

# 二、PiPER-X 与 GELLO 联调记录

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

## 1. 普通 J 模式与 JS 模式

### 1.1 普通 J 模式

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

### 1.2 JS 模式

JS 模式用于高频、连续的关节流式控制。GELLO 跟随时，服务端不断接收客户端的 J1～J6 和 gripper 目标，并通过 `move_js` 更新机械臂目标。

JS 模式的特点：

- 适合 GELLO 实时跟随。
- 不对每一帧命令执行普通 J 模式的目标到达等待。
- 可以连续更新目标，响应速度明显快于逐条执行 `move_j`。
- 当前 GELLO 跟随、启动校准和退出回零全部使用同一个 JS 会话。

### 1.3 状态反馈的限制

当前 SDK 使用 J 类型的运动模式字段配合额外的 JS 标志进入 JS 控制，但机械臂状态反馈没有完整返回这个额外标志。因此：

```text
mode_feedback = 1
```

只能说明反馈属于 J 类控制，不能仅凭这个字段可靠地区分普通 J 模式和 JS 模式。判断当前是否处于 JS 工作流时，还需要结合服务端生命周期和最近执行的控制操作。

## 2. JS 切换回普通 J 模式的失能现象

### 2.1 已通过硬件实验确认的现象

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

### 2.2 当前采用的处理方式

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

## 3. 自动校准与跟随流程

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

## 4. Ctrl+C 安全退出

正常跟随过程中按下 Ctrl+C 后：

1. 停止 GELLO 跟随客户端。
2. 暂时保持 `ag-gello-server` 运行。
3. 使用 `piper_x_movejs.py` 通过现有 JS 通道分步回零。
4. 回零完成后关闭服务端。
5. 释放 GELLO 串口和后台 Dynamixel 读取线程。

如果在服务端启动前中断，例如脚本停在第 3 步读取 GELLO，则不会执行 PiPER-X JS 回零，因为本次脚本还没有建立 JS 控制通道，也没有让机械臂产生新的运动。

不建议使用 `kill -9` 结束脚本。`kill -9` 无法执行 shell 的退出处理，应优先使用 Ctrl+C。

## 5. GELLO `-3001` 通信错误

### 5.1 错误含义

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

### 5.2 常见原因

`-3001` 可能由以下原因引起：

- GELLO Dynamixel 总线供电不稳定或断电。
- FTDI USB 松动、USB 端口瞬断或转接器异常。
- Dynamixel 串联线缆接触不良。
- 某个舵机或其上游连接故障，导致后续设备无法返回状态包。
- 另一个进程同时占用同一个 GELLO 串口。
- Dynamixel ID、波特率或状态返回配置与代码不一致。

如果系统能够看到 `/dev/serial/by-id/...`，只能证明 FTDI 设备已被 Linux 枚举，不能证明 Dynamixel 总线供电和状态包通信正常。

### 5.3 单独验证 GELLO

恢复供电和线缆后，先执行只读命令：

```bash
cd ~/projects/gello_software
.venv/bin/python experiments/read_gello_joints.py \
  --gello-port /dev/serial/by-id/usb-FTDI_USB__-__Serial_Converter_FTBM4Z46-if00-port0
```

只有该命令能够正常输出 J1～J6 和 gripper 后，才应重新运行自动跟随脚本。

## 6. PiPER-X CAN 与 GELLO 串口需要分开判断

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

## 7. 联调安全原则

- 启动、校准和退出时确保机械臂周围无人且无障碍物。
- JS 校准采用小步插值，不直接发送跨度较大的单帧目标。
- 不在当前固件上直接执行已使能 JS 到普通 J 的模式切换。
- 通信异常时不得继续使用缓存关节值控制机械臂。
- 不使用自动杀进程的方式抢占串口。
- 修改关节方向、单位换算或限制参数后必须逐轴、小幅验证。
- 不能仅根据命令行状态断言机械臂已使能，应同时确认实际机械臂状态。
