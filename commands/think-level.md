切换到指定等级模型。用法：/think-level 1、/think-level 2、/think-level 3

从用户输入中提取等级数字 N，然后执行：

```bash
curl --noproxy '*' -s "http://127.0.0.1:3457/think-level?session=$CR_SESSION&level=N"
```

执行后告诉用户已切换到 Level N。不要解释原理。
