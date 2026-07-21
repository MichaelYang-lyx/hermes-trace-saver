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
/upload-files                  自动扫本次会话读写过的文件 → 预览
/upload-files --yes            扫完直接上传(不预览)

# 一句话微调扫出来的清单(和 --yes 一起用直接上传):
/upload-files --yes -x big.log            扫,但去掉 big.log
/upload-files --yes -a extra.csv          扫,再补上 extra.csv
/upload-files --yes -x *.log -a a.pdf     去 *.log,加 a.pdf
/upload-files --yes --only *.xlsx         只保留 xlsx 文件

# 手动指定文件(跳过扫描):
/upload-files a.xlsx b.xlsx               直接把这两个文件打成 zip 上传
/upload-files --local a.xlsx              只存本地,不上传
```

模式:`-x` = `--exclude`,`-a` = `--add`,`--only` = 白名单。可重复叠加。匹配规则:完整路径 / 文件名 / 通配符(`*.log`)都行。

自动扫会跳过 `.env` / `*.key` / SSH 密钥、大于 50MB 的文件、以及 `.git` / `.hermes` / `node_modules` 等目录里的文件。

看榜:<http://10.9.66.12:8848>

## 改名字(可选)

```bash
export TRACE_LEADERBOARD_NAME="你的名字"
```
