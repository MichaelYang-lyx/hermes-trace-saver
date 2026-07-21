# trace-saver 用法

## 装(一次)

```bash
curl -fsSL https://raw.githubusercontent.com/MichaelYang-lyx/hermes-trace-saver/main/install.sh | bash
```

装完**重开 Hermes 会话**才生效。

## 用

在 Hermes 会话里输入:

```
/save-trace                    上传最近一次 session 到排行榜(+1 分)
/save-trace all                上传所有 session
/save-trace --local            只存本地,不上传 → ~/hermes-traces/
/save-trace --local -o <目录>  存到指定目录
```

看榜:<http://10.9.66.12:8848>

## 改名字(可选)

```bash
export TRACE_LEADERBOARD_NAME="你的名字"
```
