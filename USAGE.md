# trace-saver 使用指南

一键把 Hermes 会话的 trace(session 文件)打包上传到 **Trace Leaderboard**
（`http://10.9.66.12:8848`）。每上传一个 trace **+1 分**，分数越高排名越靠前。

- 仓库地址：<https://github.com/MichaelYang-lyx/hermes-trace-saver>
- 排行榜：<http://10.9.66.12:8848/>

---

## 一、安装（每台机器一次）

在要跑 Hermes 的机器上执行一行命令：

```bash
curl -fsSL https://raw.githubusercontent.com/MichaelYang-lyx/hermes-trace-saver/main/install.sh | bash
```

脚本做的事（**无需 root**）：

1. 把插件拷到 `~/.hermes/plugins/trace-saver/`
2. 在 `~/.hermes/config.yaml` 的 `plugins.enabled` 里加上 `trace-saver`

> ⚠️ **装完后要开一个新的 Hermes 会话才生效**（Hermes 的标准插件在新会话启动时才加载）。

看到这样的输出就是装成功了：

```
==> Done. 'trace-saver' is installed and enabled.
    Takes effect on the NEXT Hermes session (restart hermes / open a new chat).
```

---

## 二、设置榜上显示的名字（推荐）

不设的话会用系统用户名。想固定一个显示名（比如中文名 / 花名）：

```bash
export TRACE_LEADERBOARD_NAME="张三"
```

写进 `~/.hermes/.env` 或 `~/.bashrc` / `~/.zshrc` 里可以长期生效。

也可以在单次 `/save-trace` 里临时指定（见下节）。

---

## 三、在 Hermes 会话里保存 trace

**两种模式**:

| 模式 | 命令 | 是否上传 | 是否 +1 分 |
|------|------|:--------:|:----------:|
| 上传(默认) | `/save-trace ...` | ✅ | ✅ |
| 仅本地 | `/save-trace --local ...` | ❌ | ❌ |

### 方式 A:斜杠命令(你手动触发,最常用)

在 Hermes 的对话框里直接输入:

**上传到排行榜(默认,+1):**

| 命令 | 作用 |
|------|------|
| `/save-trace` | 上传**最近一次** session(默认行为,最常用) |
| `/save-trace all` | 把**所有** session 打成一个 zip 上传 |
| `/save-trace <session-id>` | 上传指定 session(支持 id 或文件名片段匹配) |
| `/save-trace latest 李四` | 顺便临时改榜上名字(不动 env) |

**只本地保存,不上传:**

| 命令 | 作用 |
|------|------|
| `/save-trace --local` | 存最近一次 session 到 `~/hermes-traces/`(默认目录) |
| `/save-trace --local all` | 存所有 session |
| `/save-trace --local <session-id>` | 存指定 session |
| `/save-trace --local -o /tmp/xxx` | 存到指定目录(`-o` 会自动开启本地模式) |
| `/save-trace --local <sess> <name> -o <dir>` | 全参数 |

| 命令 | 作用 |
|------|------|
| `/save-trace help` | 显示完整帮助 |

**成功输出**:

```
📤 Uploaded 1 trace(s) as '张三' (12.3 KB). See http://10.9.66.12:8848/u/张三
💾 Saved 1 trace(s) locally as '张三' (12.3 KB) -> /home/you/hermes-traces/hermes_trace_张三_latest_20260721_180000.zip
```

上传成功 📤,本地保存 💾。**失败输出**以 `⚠️` 开头,把原因写清楚(leaderboard 连不上、没有 session 文件、名字为空等)。

### 方式 B:让 agent 自己调用

直接跟 agent 说一句:

> 把这次 trace 存到排行榜(默认会上传)
> 把这次 trace 只存到本地,不要上传

agent 会调用 `save_trace` 工具。参数:

- `session`(可选):`latest`(默认)/ `all` / session-id
- `name`(可选):榜上/文件名
- `local`(可选,bool):`true` = 只本地保存,不上传。默认 `false`
- `out_dir`(可选):`local=true` 时的输出目录,不给则用 `$TRACE_SAVE_DIR` 或 `~/hermes-traces`

---

## 三点五、上传任意文件(input / output 等)

`/save-trace` 传的是 Hermes 的会话记录。如果你想传**具体的工作文件**(比如分析用的 `input.xlsx` 和你产出的 `output.xlsx`),用 `/upload-files`。

### 自动扫描(推荐)

自动找出**本次会话里读/写过、且现在磁盘上还在**的文件:

```
/upload-files          先扫描并列出会传哪些(预览,不上传)
/upload-files --yes    确认后直接上传(+1 分)
```

预览会列出 ✓ 保留的 和 ✗ 跳过的(带原因)。**自动跳过**:`.env` / `*.key` / `*.pem` / SSH 密钥,大于 50MB 的文件,以及 `.git` / `.hermes` / `.claude` / `node_modules` 等目录里的文件。

### 一句话微调扫出来的清单

不用先存文件再改,直接在同一句里增删(和 `--yes` 一起用就直接上传):

| 指令 | 作用 |
|------|------|
| `/upload-files --yes -x debug.log` | 扫,但去掉 `debug.log` |
| `/upload-files --yes -a extra.csv` | 扫,再补上 `extra.csv` |
| `/upload-files --yes -x *.log -a a.pdf` | 去掉所有 `*.log`,加上 `a.pdf` |
| `/upload-files --yes --only *.xlsx` | 只保留 `.xlsx` 文件 |

- `-x` = `--exclude`(排除),`-a` = `--add`(补充),`--only`(白名单)。都可重复叠加。
- 匹配规则:完整路径 / 文件名 / 通配符(`*.log`)都行。
- `--add` 的文件同样过安全检查;被挡下会以 `✗` + 原因显示。
- 微调结果用 `+` / `-` / `✗` / `!` 标记,一眼看清每步改了什么。

### 手动指定文件(跳过扫描)

```
/upload-files a.xlsx b.xlsx        直接把这两个文件打成一个 zip 上传
/upload-files --local a.xlsx       只存本地,不上传
/upload-files a.xlsx -n "本周分析"  加一段备注(写进 zip 的 manifest.json)
```

### 让 agent 自己调用

直接说:

> 把这次用到的 input 和 output 文件都传到排行榜

agent 会调用 `upload_files` 工具。参数:`paths`(数组,留空=自动扫描)、`name`、`note`、`local`、`out_dir`。

---

## 四、看榜

浏览器打开：

- **排行榜首页**：<http://10.9.66.12:8848/>
- **你自己的页面**：<http://10.9.66.12:8848/u/你的名字>
  - 可以下载/删除历史 trace
  - 显示每次上传的时间、大小

**打分规则**：上传一个文件 = 1 分。分数并列时，注册早的排前面。

---

## 五、配置（环境变量）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `TRACE_LEADERBOARD_NAME` | 系统用户名 | 榜上显示名 |
| `TRACE_LEADERBOARD_URL` | `http://10.9.66.12:8848` | 排行榜地址(换服务器时改这个) |
| `TRACE_SAVE_DIR` | `~/hermes-traces` | `--local` 模式的默认保存目录 |
| `HERMES_HOME` | `~/.hermes` | Hermes 主目录,trace 存在其下的 `sessions/` |

**只对当前 shell 生效**：直接 `export` 即可。
**永久生效**：写进 `~/.hermes/.env` 或 shell rc 文件。

---

## 六、trace 是什么，装到哪里了

- Hermes 每个会话存成 `~/.hermes/sessions/session_<时间>_<id>.json`，包含 model / tools / 完整 messages 轨迹。
- 插件会把选中的 session 文件 + 一个 `manifest.json`（描述打包内容）压成 zip 上传。
- 服务端仅接受 `.zip`，单文件 ≤ 500MB。

装完后插件本体在这里：

```
~/.hermes/plugins/trace-saver/
├── plugin.yaml
├── __init__.py       # 注册 save_trace / upload_files 工具 + /save-trace /upload-files 命令
├── uploader.py       # 打包 + 登录 + 上传的核心逻辑
├── filepicker.py     # 扫描会话、过滤文件、生成预览
├── README.md
├── config.example.env
└── uninstall.sh
```

---

## 七、常见问题

| 现象 | 排查/解决 |
|------|-----------|
| 输了 `/save-trace` 没反应 / 提示未知命令 | 你还在**旧会话**里，关掉重开一个 Hermes 会话再试 |
| `⚠️ Leaderboard unreachable: ...` | 当前网络到不了 `10.9.66.12:8848`；或服务挂了。用 `curl http://10.9.66.12:8848/healthz` 测一下 |
| `⚠️ No Hermes session traces found` | Hermes 还没写过任何 session 文件；先聊几句再存 |
| 想换到别的服务器 | `export TRACE_LEADERBOARD_URL=http://x.x.x.x:PORT` |
| 想升级到最新版 | 再跑一次那条 `curl \| bash` 就行，会覆盖旧文件、幂等 |
| 想卸载 | `bash ~/.hermes/plugins/trace-saver/uninstall.sh` |
| 装了但 `plugins.enabled` 里没加进去 | 手动编辑 `~/.hermes/config.yaml`，在 `plugins.enabled` 列表里加一行 `- trace-saver` |

---

## 八、一句话版本（发给同事）

> 一键装：
> `curl -fsSL https://raw.githubusercontent.com/MichaelYang-lyx/hermes-trace-saver/main/install.sh | bash`
> 装完**重开 Hermes 会话**，然后 `/save-trace` 就能把 trace 上传到
> <http://10.9.66.12:8848> 上榜。

---

## 九、依赖 & 无依赖模式

优先用 Python 的 `requests`（Hermes venv 里一般都有）；找不到就自动退回标准库 `urllib`，**零额外依赖**。安装脚本也只用 `bash` + `python3` + `git`。
