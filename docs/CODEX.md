# Codex 本地预览版

本版本为 macOS 上的 **Codex CLI** 增加监控，复用原有 SwiftUI 灵动岛。
Claude 安装入口仍然是 `./install.sh`；Codex 使用独立入口。
事件格式按 2026-09-29 的官方文档实现；文末列出验证范围与实机验收步骤。

## 安装

需要 macOS 13+、Python 3.11+、Xcode Command Line Tools，以及支持 `/hooks`
和下列事件的 Codex CLI。旧版系统 Python 3.9 无法运行此适配器。
不同客户端及旧版本的 Hook 能力可能不同，注册成功不等于运行时已接入。

在包含本次改动的源码目录内执行：

```bash
./install-codex.sh
```

安装器从源码编译 `dist/SkillMonitor.app`，将独立采集脚本复制到
`${CODEX_HOME:-~/.codex}/skill-monitor/bin/codex.py`，备份并更新该 Codex
目录中的 `hooks.json`，设置灵动岛在 Terminal／iTerm2 前台时显示。
完成后重新启动共用灵动岛，已有 Claude 采集不受影响。

然后在 Codex CLI 输入 `/hooks`，审阅并信任七个 SkillMonitor Hook，再开启
新对话。安装器不会写入信任记录或绕过审阅。更新 Hook 定义后，Codex 可能
再次要求审阅。请检查被调用的脚本，而不只检查命令行字符串。

旧版 release 的 App 不具备 Codex 显示能力。CI 的
`SkillMonitor-codex-preview` artifact 包含测试 App 和离屏预览图。
若使用该 App，需要另行安装采集器：

```bash
./install-codex.sh --hooks-only
```

仅采集器可在 Linux 上运行；灵动岛只能在 macOS 运行。`--hooks-only` 用于
测试或预配置，不要求此时有 Codex，也不会自动完成运行时连接。

自定义配置目录：

```bash
./install-codex.sh --codex-home /absolute/path/to/codex-profile
```

此选项只指定监控安装位置；Codex 自身也必须使用同一目录。安装器将绝对
路径写入 Hook 命令，不依赖 GUI 启动时继承环境变量。

## 看板怎么读

右键灵动岛可选“全部来源（最近活动）”“只看 Claude”“只看 Codex”。
过滤只影响显示，不会停止采集。默认展示最近有事件的会话，不会自动识别
你当前正在看的 Terminal 窗口或标签页。

| 内容 | 含义 |
| --- | --- |
| `Codex · 本次任务` | 当前展示的是 Codex 会话／任务 |
| 技能旁“指定” | 提示词包含 `$名称`，只找到一个本地匹配文件；尚未观察到读取 |
| 技能旁“读请求” | 观察到可识别的 `SKILL.md` 文件读取命令 |
| 技能旁 `?` | 无法确认调用来源，不计入“自动技能”数量 |
| MCP `server / tool` | Hook 观察到的工具调用，重复调用累加 |
| 耗时 | 同一 `tool_use_id` 的前后 Hook 间隔，包含调度和可能的审批等待 |
| “候选文件”／“AGENTS 候选” | 磁盘扫描的候选指令文件，不保证已加载或完整生效 |

“读取请求”不等于技能成功执行；失败或被拦截的读取也可能留下请求记录。
耗时缺失表示尚未观察到结束事件。重复工具行内显示最近完成的一次耗时，
历史按调用 ID 保存每次开始／结束。灰点表示结束、中断或等待首次输入。

## 已实现范围

- `SessionStart`、`UserPromptSubmit`、`Stop`、`Interrupt`、`SessionEnd`。
- `PreToolUse` / `PostToolUse` 的 `mcp__server__tool`，按调用 ID 配对。
- `Bash`（含 Codex 映射的 unified exec）、兼容名称 `exec_command` /
  `shell_command` 中的简单字面量读取：`cat`、`head`、`tail`、
  `sed -n '1,200p'`。支持引号路径、相对路径、简单 `bash/sh/zsh -c/-lc`
  包装，只解析，不执行命令。
- `$名称` 的本地技能发现：`~/.agents/skills`、项目根到当前目录的
  `.agents/skills`、`CODEX_HOME/skills`（兼容目录）、`/etc/codex/skills`。
  同名不同路径不强行合并，不把插件缓存当作“已启用技能”列表。
- 候选 `AGENTS.override.md` 优先于 `AGENTS.md`，支持候选 `config.toml`
  的 `project_doc_fallback_filenames`。项目根通过 `.git` 目录或文件识别。
  指纹变化写入 `instructions.jsonl`。
- 原子写入、进程锁、分会话／分任务账本、去重和日志轮转。旧任务延迟返回
  的结果只更新旧账本，不会覆盖同会话的新任务。

## 能力边界

不是所有技能和工具的完整追踪器。不会解析任意 Python/JavaScript、复杂
Shell 管道、变量替换或会话 transcript，这类读取可能漏记。尚未接入专用
技能加载工具、插件内置展开和完整子代理调用树。普通 hosted WebSearch 等
不经过本地工具 Hook 的路径不会出现。

自然语言指定技能无法只靠 `$名称` 判断，所以未匹配显式语法的读取标为
来源未知。显式引用本身也是请求证据，不是运行时完成加载的确认。

指令扫描不计算可信配置层、管理策略、启动参数、字节截断和运行时注入后的
最终配置；不复制 Claude 的 `InstructionsLoaded` 行为。候选文件不能证明
某项规则实际影响了模型结果。

本版没有验证桌面端、IDE 或云端接入。云端任务不能直接写 Mac 的本地文件。
多个配置目录可接入同一灵动岛，但需要分别安装采集器。

## 配置、诊断与卸载

显示配置：`~/.config/skill-monitor/config.json`。

```json
{
  "stateFiles": [
    "~/.claude/skill-monitor/state.json",
    "~/.codex/skill-monitor/state.json"
  ],
  "showWhenFrontmost": [
    "com.anthropic.claudefordesktop",
    "com.apple.Terminal",
    "com.googlecode.iterm2"
  ]
}
```

可添加其他终端的 bundle ID，修改后重启灵动岛。未配置新文件时，旧 Claude
显示配置仍可使用。终端在前台时显示，不代表该终端一定正在运行 Codex。

```bash
./install-codex.sh --hooks-only --status
./uninstall-codex.sh
```

`--status` 区分“已注册”和“已观察到事件”，不猜测 Hook 信任状态。
卸载只移除自己的 handler，保留同组其他 handler、Claude、共用 App 和历史。
自定义目录需同样传入 `--codex-home`。不再需要旧数据时，从 `stateFiles`
移除相应来源并重启灵动岛。

Codex 数据位于 `${CODEX_HOME:-~/.codex}/skill-monitor/`：

- `state.json`：最近活动会话快照。
- `sessions/`：各会话最新任务，最多 40 个账本。
- `turns/`：独立任务账本，最多 200 个。
- `history.jsonl`：技能引用、读取请求、MCP 调用开始／结束。
- `fingerprints.json`、`instructions.jsonl`：候选指令指纹和变更。

仅本地保存。快照保存最多 160 字符的提示词摘要、工作目录、技能路径。
**不保存完整工具参数、工具响应或完整对话**。每个历史日志超过 2 MiB 时
保留一份轮转文件。Hook 只返回空 JSON，不向模型插入指令。

## 验证

```bash
python3 tests/test_hook.py
python3 -m unittest discover -s tests -p 'test_codex*.py' -v
UNIVERSAL=1 ./build.sh
```

Python 测试使用文档格式构造事件，在临时 HOME 中验证并发、重复、跨任务
延迟、来源未知、指令候选、路径引用、安装／卸载和数据保存范围。这不是
来自用户机器的真实运行录制。GitHub Actions 编译双架构 App、校验 bundle，
生成 Claude / Codex 离屏预览。编译与渲染不能替代真实会话验收。

Mac 实机验收：

1. 安装后在 `/hooks` 审阅并信任七个事件，再开启新会话。
2. 调用一个已配置 MCP，核对名称、次数与结束后的耗时。
3. 用 `$已安装技能名` 发起任务，核对“指定”与可观察读取是否合并。
4. 另一会话发任务，核对摘要与列表不串线；中断后核对灰点。
5. 同时运行 Claude，右键切换来源，核对原有行为。
6. 卸载 Codex Hook，确认其他 Hook 和 Claude 配置保留。

依据：[Hooks](https://learn.chatgpt.com/docs/hooks)、
[Build skills](https://learn.chatgpt.com/docs/build-skills)、
[AGENTS.md](https://learn.chatgpt.com/docs/agent-configuration/agents-md)。
