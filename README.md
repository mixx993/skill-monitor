# SkillMonitor

一个 macOS 原生悬浮窗，显示**最新一次任务**中 Claude Code 调用了哪些 skill 和 MCP 工具。

## 组成

| 文件 | 作用 |
|---|---|
| `hook/log.py` | Claude Code hook：把调用写进 `~/.claude/skill-monitor/state.json` |
| `app/Island.swift` | 灵动岛视图 + 状态轮询（0.3 秒） |
| `app/main.swift` | 窗口层：透明、无边框、菜单栏之上、贴刘海 |
| `tools/preview/main.swift` | 离屏渲染预览图 |
| `build.sh` | 编译出 `dist/SkillMonitor.app` |
| `uninstall.sh` | 从 settings.json 摘掉 hook 并退出 app |

## 工作原理

三个 hook 写进 `~/.claude/settings.json`：

- `UserPromptSubmit` → 清空状态，开一轮新任务（同步，确保早于第一个工具调用）

  ⚠️ 这个事件**不只在你打字时触发**：后台任务完成通知、system-reminder、CI 事件等 harness 往会话里注入的内容也算一次提交。
  `log.py` 里的 `is_injected()` 把它们挡掉（识别 `<task-notification`、`<system-reminder`、
  `[SYSTEM NOTIFICATION` 等开头），否则一条后台通知就会把计数清零、并把原始 XML 当提示词显示出来。
- `PreToolUse`，matcher `Skill|mcp__.*` → 记一条调用（异步，不增加延迟）
- `Stop` → 把状态标成 done（绿点变灰）

多个 Claude Code 会话同时开着时，**每个会话写自己的 `sessions/<id>.json`**，每次写完再把自己发布到
`state.json`。所以岛上永远是最近活跃的那个任务，而且提示词和调用列表必定来自同一个会话。
（共用单文件时，A 会话的提示词会配上 B 会话的调用。）会话文件超过 24 小时或 40 个自动清理。

重复调用同一个 skill 会合并成 `×N`，不会刷屏。

## 数据

- `~/.claude/skill-monitor/sessions/<session_id>.json` — 每个会话各自的账本
- `~/.claude/skill-monitor/state.json` — **最近活跃**的那个会话的副本，岛只读它
- `~/.claude/skill-monitor/history.jsonl` — 跨会话流水（带 session_id、cwd），超过 2MB 自动轮转

用来统计「哪些 skill 装了但从没用过」：

```bash
cut -d'"' -f16 ~/.claude/skill-monitor/history.jsonl | sort | uniq -c | sort -rn
```

## 界面：灵动岛

挂在刘海正下方（无刘海的机器则嵌进菜单栏中间的空位），三个状态之间弹簧过渡：

| 状态 | 样子 | 触发 |
|---|---|---|
| collapsed | `● 5` 小胶囊 | 空闲。绿点=任务进行中，灰点=已结束 |
| flash | `docs / guide  04:53:44` | 新调用落地，2.2 秒后自动收回 |
| expanded | 完整列表 | 鼠标移上去 |

- 窗口层级在菜单栏之上、全空间跟随、永不抢焦点；透明区域点击直接穿透到下面的窗口
- 紫色实心 ◆ = skill，青色空心 ◇ = MCP（显示成 `服务器 / 工具名`）
- 退出：展开后点右上角 `×`，或在岛上右键 → 退出

### 只在 Claude 前台时显示

切到 Chrome、微信等其他 app 时岛会淡出并 `orderOut`（完全离开屏幕，不只是透明），切回来再淡入。
靠 `NSWorkspace.didActivateApplicationNotification` 监听前台 app 切换。

白名单在 `~/.claude/skill-monitor/config.json`，首次启动自动生成：

```json
{
  "showWhenFrontmost": ["com.anthropic.claudefordesktop"]
}
```

在终端里跑 CLI 版 Claude Code 的话，把终端的 bundle id 加进去（`com.apple.Terminal`、
`com.googlecode.iterm2`、`com.mitchellh.ghostty` 等），改完重启 app。

预览图在 `docs/`，由 `tools/preview/main.swift` 离屏渲染生成（不需要截屏权限）：

```bash
swiftc -O -framework AppKit -framework SwiftUI -o dist/preview-tool app/Island.swift tools/preview/main.swift
./dist/preview-tool docs
```

## 开机自启

系统设置 → 通用 → 登录项 → `+` → 选 `dist/SkillMonitor.app`

## 注意

hook 里写的是本目录的绝对路径。**移动这个文件夹后要改 `~/.claude/settings.json` 里的三条命令**，或者重跑安装。
