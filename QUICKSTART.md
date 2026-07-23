# trace-saver 用法

## 装(一次)

```bash
curl -fsSL https://raw.githubusercontent.com/MichaelYang-lyx/hermes-trace-saver/main/install.sh | bash
```

装完**重开 Hermes 会话**才生效。

## 用

在 Hermes 会话里输入 `/save-trace` —— 它会把**本次会话的 trace + 会话里读写过的文件**一起打包上传。

```
/save-trace              先预览:会传哪个 trace + 附带哪些文件
/save-trace --yes        确认,直接上传到排行榜(+1 分)
/save-trace --yes --local   只存本地,不上传 → ~/hermes-traces/
```

**微调附带的文件(可选,和 --yes 一起用):**
```
/save-trace --yes -x debug.log        去掉某个文件
/save-trace --yes -a extra.csv        再补一个文件
/save-trace --yes -x *.log -a a.pdf   去 *.log,加 a.pdf
/save-trace --yes --only *.xlsx       只保留 xlsx
/save-trace --yes --no-files          只传 trace,不带文件
```

- `-x`=排除,`-a`=补充,`--only`=白名单。匹配文件名 / 完整路径 / 通配符(`*.log`)。
- 自动跳过 `.env` / `*.key` / SSH 密钥、大于 50MB 的文件、`.git` / `.hermes` / `node_modules` 等目录。
- 选别的 session:`/save-trace --yes all`(全部)或 `/save-trace --yes <session-id>`。

**预览里会告诉你选中的是不是"当前会话":**

```
session trace: session_20260722_120000_xxx.json  [当前会话]     ← ✅ 就是你现在正在聊的这个
session trace: session_20260722_120000_xxx.json  [按最近修改时间猜测]   ← ⚠️  可能选错,注意核对
```

如果显示的是"按最近修改时间猜测"或不是你现在的会话,用 `/save-trace --yes <session-id>` 手动指定。

看榜:<http://10.9.66.12:8848>

## 改名字(可选)

```bash
export TRACE_LEADERBOARD_NAME="你的名字"
```
