# DailyEmail – 每天早上自动发天气邮件的机器人EmailBot

这个仓库是一个完整的小型「工业级」项目：

- **功能**：每天早上 8 点自动给你（和你的家人/朋友）发一封当日天气提醒邮件  
- **技术栈**：
  - AWS Lambda：负责跑 Python 代码
  - AWS SES：负责发邮件
  - AWS EventBridge：负责每天定时触发
  - OpenWeatherMap API：负责获取天气
  - GitHub Actions：负责 CI/CD（一 push 代码就自动部署到 Lambda）
  - Python + pytest：本地开发和单元测试

---

## 1. 仓库结构说明

当前的主要文件/目录：

```text
DailyEmail/
 ├─ lambda_function.py        # Lambda 运行的主代码：拉天气 + 组装邮件 + 调用 SES
 ├─ requirements.txt          # Python 依赖（目前只包含 pytest，用于测试）
 ├─ tests/
 │   └─ test_send_email.py    # 针对 send_email 函数的单元测试
 ├─ .github/
 │   └─ workflows/
 │       └─ deploy.yml        # GitHub Actions 工作流：push 到 main 自动部署到 AWS Lambda
 ├─ README.md                 # 项目说明（你正在看的这个）
 ├─ LICENSE                   # MIT 开源协议（可以随意改/用）
 └─ .gitignore                # 告诉 git 哪些文件不需要提交（如 __pycache__、.DS_Store 等）
