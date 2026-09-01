# 项目说明

本项目用于 PiPER-X 机械臂控制、GELLO 遥操作示教、原始数据采集，以及 LeRobot 标准数据集生成。三个工作域相互独立并通过明确的进程边界协作：

- `agilexrobotics/` — PiPER-X CAN 驱动、状态反馈和 ZMQ 控制服务
- `gello_software/` — GELLO Dynamixel 读取、目标映射和实时跟随
- `lerobot_converter/` — 原始数据校验与 LeRobot 离线转换

采集数据统一放在 `data/`（`raw/` 为不可变原始数据源，`lerobot/` 为转换结果），代码与数据分离。详细架构见 `README.md`。

# gstack

gstack 已在此项目配置（个人技能套件）。使用约定如下。

## Web browsing

- 所有网页浏览使用 gstack 的 **/browse** 技能。
- 绝不使用 `mcp__claude-in-chrome__*` 工具。

## Skill routing

When the user's request matches an available skill, invoke it via the Skill tool. When in doubt, invoke the skill.

Key routing rules:
- Product ideas/brainstorming → invoke /office-hours
- Strategy/scope → invoke /plan-ceo-review
- Architecture → invoke /plan-eng-review
- Design system/plan review → invoke /design-consultation or /plan-design-review
- Full review pipeline → invoke /autoplan
- Bugs/errors → invoke /investigate
- QA/testing site behavior → invoke /qa or /qa-only
- Code review/diff check → invoke /review
- Visual polish → invoke /design-review
- Ship/deploy/PR → invoke /ship or /land-and-deploy
- Save progress → invoke /context-save
- Resume context → invoke /context-restore
- Author a backlog-ready spec/issue → invoke /spec
