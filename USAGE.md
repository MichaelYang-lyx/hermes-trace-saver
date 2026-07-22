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

## 三、在 Hermes 会话里保存(trace + 文件一起)

`/save-trace` 会把**本次会话的 trace(会话记录)+ 会话里读/写过的工作文件**(比如 `input.xlsx`、`output.xlsx`)打包成**一个 zip** 一起处理。

### 方式 A:斜杠命令(最常用)

**先预览,再上传(推荐):**

```
/save-trace          扫描并列出:会传哪个 trace + 附带哪些文件(不上传)
/save-trace --yes    确认,上传到排行榜(+1 分)
```

预览会列出附带的文件(✓)和被跳过的文件(✗,带原因)。

**只本地保存,不上传:**

```
/save-trace --yes --local            存到 ~/hermes-traces/
/save-trace --yes --local -o <目录>  存到指定目录
```

**微调附带的文件(和 `--yes` 一起用,一句话搞定):**

| 指令 | 作用 |
|------|------|
| `/save-trace --yes -x debug.log` | 去掉 `debug.log` |
| `/save-trace --yes -a extra.csv` | 再补上 `extra.csv` |
| `/save-trace --yes -x *.log -a a.pdf` | 去掉所有 `*.log`,加上 `a.pdf` |
| `/save-trace --yes --only *.xlsx` | 只保留 `.xlsx` 文件 |
| `/save-trace --yes --no-files` | 只传 trace,不带任何文件 |

- `-x`=排除,`-a`=补充,`--only`=白名单。可重复叠加。匹配文件名 / 完整路径 / 通配符(`*.log`)。
- **自动跳过**:`.env` / `*.key` / `*.pem` / SSH 密钥,大于 50MB 的文件,以及 `.git` / `.hermes` / `.claude` / `node_modules` 等目录里的文件。

**选别的 session / 改名字:**

| 指令 | 作用 |
|------|------|
| `/save-trace --yes all` | 把所有 session 都打进 zip |
| `/save-trace --yes <session-id>` | 指定某个 session |
| `/save-trace --yes --name 李四` | 临时改榜上名字 |
| `/save-trace help` | 显示完整帮助 |

**成功输出**:

```
📤 Uploaded trace + 2 file(s) as '张三' (34.5 KB). See http://10.9.66.12:8848/u/张三
💾 Saved trace + 2 file(s) locally as '张三' (34.5 KB) -> /home/you/hermes-traces/hermes_trace_...zip
```

上传 📤,本地 💾。**失败**以 `⚠️` 开头,写清原因(连不上、没有 session、名字为空等)。

### 方式 B:让 agent 自己调用

直接说:

> 把这次的 trace 和用到的文件都传到排行榜

agent 会调用 `save_trace` 工具。参数:
- `session`(可选):`latest`(默认)/ `all` / session-id
- `name`(可选):榜上名字
- `with_files`(可选,bool):是否附带本次读写的文件,默认 `true`
- `local`(可选,bool):`true` = 只本地保存。默认 `false`
- `out_dir`(可选):`local=true` 时的输出目录

> 另有一个 `upload_files` 工具(仅 agent 用),用于**只传文件、不带 trace**的场景。

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
├── __init__.py       # 注册 save_trace / upload_files 工具 + /save-trace 命令
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
