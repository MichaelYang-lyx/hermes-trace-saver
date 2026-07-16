# trace-saver — Hermes 插件：一键保存 trace 到排行榜

把 Hermes 的 session trace 打包成 `.zip`，一键上传到 **Trace Leaderboard**
（`http://10.9.66.12:8848`）。每上传一个文件 **+1 分**。

## 一键安装

**远程一行装(不用 clone):**

```bash
curl -fsSL https://raw.githubusercontent.com/MichaelYang-lyx/hermes-trace-saver/main/install.sh | bash
```

**或 clone 后本地装:**

```bash
git clone https://github.com/MichaelYang-lyx/hermes-trace-saver.git
bash hermes-trace-saver/install.sh
```

脚本做的事（**无需 root**）：
1. 把插件拷到 `~/.hermes/plugins/trace-saver/`；
2. 用 `hermes plugins enable trace-saver` 启用（CLI 不可用时自动改 `~/.hermes/config.yaml` 的 `plugins.enabled`）。

> 标准插件是 opt-in 的，必须启用才会加载。**下次开新的 Hermes 会话生效。**

卸载：

```bash
bash uninstall.sh
```

## 用法

会话里用斜杠命令：

```
/save-trace                 # 上传最近一次 session（默认）
/save-trace all             # 所有 session 打进一个 zip
/save-trace <session-id>    # 指定某个 session（id 或文件名片段）
/save-trace latest 张三      # 顺便指定榜上名字
/save-trace help            # 帮助
```

或者让 agent 直接调用工具 **`save_trace`**（参数 `session` / `name`，都可选）。

成功后返回榜上用户页，例如 `http://10.9.66.12:8848/u/<你的名字>`。

## 配置（可选，环境变量）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `TRACE_LEADERBOARD_NAME` | 系统用户名 | 榜上显示名 |
| `TRACE_LEADERBOARD_URL`  | `http://10.9.66.12:8848` | 排行榜地址 |
| `HERMES_HOME` | `~/.hermes` | Hermes 主目录（trace 在 `sessions/` 下） |

设置示例：

```bash
export TRACE_LEADERBOARD_NAME="your-name"
```

（写进 `~/.hermes/.env` 或 shell rc 即可长期生效。）

## trace 是什么

Hermes 每个会话存成 `~/.hermes/sessions/session_*.json`（含 model / tools /
完整 messages 轨迹）。本插件把选中的 session 文件加一个 `manifest.json`
一起压成 zip 上传。

## 依赖

优先用 `requests`（Hermes venv 里一般有）；没有则自动退回标准库 `urllib`，
**零额外依赖**。仅接受 `.zip`，单文件 ≤ 500MB（服务端限制）。

## 文件

```
trace-saver/
├── plugin.yaml          # 插件清单
├── __init__.py          # register(ctx)：注册 save_trace 工具 + /save-trace 命令
├── uploader.py          # 纯逻辑：找 session → 打 zip → 上传
├── install.sh           # 一键安装
├── uninstall.sh         # 一键卸载
├── config.example.env   # 环境变量样例
└── README.md
```
