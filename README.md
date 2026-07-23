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

会话里用斜杠命令。`/save-trace` 会把**本次会话的 trace + 会话里读/写过的文件**打成一个 zip 一起处理：

```
/save-trace                 # 先预览：会传哪个 trace + 附带哪些文件（不上传）
/save-trace --yes           # 确认，上传到排行榜（+1 分）
/save-trace --yes --local   # 只存本地，不上传 → ~/hermes-traces/
```

微调附带的文件（和 `--yes` 一起用）：

```
/save-trace --yes -x debug.log      # 去掉某个文件
/save-trace --yes -a extra.csv      # 再补一个文件
/save-trace --yes --only *.xlsx     # 只保留 xlsx
/save-trace --yes --no-files        # 只传 trace，不带文件
/save-trace --yes all               # 打包所有 session
/save-trace --yes --name 张三        # 临时改榜上名字
/save-trace help                    # 完整帮助
```

自动跳过 `.env` / `*.key` / SSH 密钥、大于 50MB 的文件、`.git` / `.hermes` / `node_modules` 等目录。

预览的第一行会标注 `[当前会话]` 或 `[按最近修改时间猜测]`——后者表示没拿到 `HERMES_SESSION_ID`，可能选错，用 `/save-trace --yes <session-id>` 手动指定即可。

或者让 agent 直接调用工具 **`save_trace`**（参数 `session` / `name` / `with_files` / `local`，都可选）。

成功后返回榜上用户页，例如 `http://10.9.66.12:8848/u/<你的名字>`。

## 配置（可选，环境变量）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `TRACE_LEADERBOARD_NAME` | 系统用户名 | 榜上显示名 |
| `TRACE_LEADERBOARD_URL`  | `http://10.9.66.12:8848` | 排行榜地址 |
| `TRACE_SAVE_DIR` | `~/hermes-traces` | `--local` 模式的保存目录 |
| `HERMES_HOME` | `~/.hermes` | Hermes 主目录（trace 在 `sessions/` 下） |

设置示例：

```bash
export TRACE_LEADERBOARD_NAME="your-name"
```

（写进 `~/.hermes/.env` 或 shell rc 即可长期生效。）

## trace 是什么

Hermes 每个会话存成 `~/.hermes/sessions/session_*.json`（含 model / tools /
完整 messages 轨迹）。`/save-trace` 把选中的 session 文件放进 `sessions/`，
并自动扫描本次会话读/写过的工作文件放进 `files/`，加一个 `manifest.json`
一起压成 zip（`--no-files` 可只保留 trace）。

## 依赖

优先用 `requests`（Hermes venv 里一般有）；没有则自动退回标准库 `urllib`，
**零额外依赖**。仅接受 `.zip`，单文件 ≤ 500MB（服务端限制）。

## 文件

```
trace-saver/
├── plugin.yaml          # 插件清单
├── __init__.py          # register(ctx)：注册 save_trace / upload_files 工具 + /save-trace 命令
├── uploader.py          # 找 session → 打 zip → 登录 → 上传
├── filepicker.py        # 扫描会话、过滤文件、生成预览
├── install.sh           # 一键安装
├── uninstall.sh         # 一键卸载
├── config.example.env   # 环境变量样例
└── README.md
```
