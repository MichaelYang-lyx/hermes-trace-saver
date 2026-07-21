# trace-saver 用法

## 装(一次)

```bash
curl -fsSL https://raw.githubusercontent.com/MichaelYang-lyx/hermes-trace-saver/main/install.sh | bash
```

装完**重开 Hermes 会话**才生效。

## 用

在 Hermes 会话里输入:

**上传会话 trace(Hermes session):**
```
/save-trace                    上传最近一次 session 到排行榜(+1 分)
/save-trace all                上传所有 session
/save-trace --local            只存本地,不上传 → ~/hermes-traces/
```

**上传任意文件(input / output 等):**
```
/upload-files a.xlsx b.xlsx    直接把这两个文件打成 zip 上传(+1 分)
/upload-files                  自动扫本次会话读写过的文件 → 预览
/upload-files --yes            扫完直接上传(不预览)
/upload-files --local a.xlsx   只存本地,不上传
/upload-files -n "本周分析"    加一段备注(写进 zip 的 manifest)
```

自动扫描会自动跳过 `.env` / `*.key` / SSH 密钥、大于 50MB 的文件、以及 `.git` / `.hermes` / `node_modules` 等目录里的文件。

看榜:<http://10.9.66.12:8848>

## 改名字(可选)

```bash
export TRACE_LEADERBOARD_NAME="你的名字"
```
