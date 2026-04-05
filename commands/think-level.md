切换到指定等级模型。用法：/think-level 1、/think-level 2、/think-level 3。

从用户输入中提取等级数字 N，然后执行：

```bash
curl --noproxy '*' -s "http://127.0.0.1:3457/think-level?level=N"
```

- 如果返回 `{"error":...}` 或连接失败，告诉用户：此命令仅在 cr 模式下可用，请用 `cr` 启动。
- 否则告诉用户已切换到 Level N。不要解释原理。
