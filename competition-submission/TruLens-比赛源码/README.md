# TruLens

TruLens 是一个面向美妆种草内容的多模态可信度核验平台。系统围绕账号（T）、图片（R）、评论（U）和文案（S）四个维度生成证据链、交叉验证关系、风险分级与分角色建议，并提供消费者核验、创作者发布前自检和团队复核队列。

## 目录

- `frontend/`：React + TypeScript + Vite 前端。
- `backend/`：FastAPI、异步 SQLAlchemy、任务/报告/团队/创作者接口。
- `content-trust-engine-v10/`：可解释内容可信度分析引擎、提示词、测试和示例数据。
- `xhs/XhsExtractor/`：基于 DrissionPage 的小红书笔记抓取器。
- `docs/AI-Agent设计文档.md`：智能体架构、数据流转、模型调用和业务闭环说明。
- `运行指南.md` / `运行指南.pdf`：环境要求、本地启动、测试和可选能力配置。

## 技术栈

- 前端：React 19、TypeScript、Vite、Motion。
- 后端：Python 3.11+、FastAPI、Pydantic、SQLAlchemy Async、SQLite/PostgreSQL。
- 分析引擎：DeepSeek 文本/评论/账号分析、GLM/Qwen 视觉语义分析，以及确定性交叉验证和风险聚合规则。
- 链接抓取：DrissionPage + Chromium（可选）。

## 快速启动

详见 [运行指南.md](./运行指南.md) 或 [运行指南.pdf](./运行指南.pdf)。

默认最小运行模式不要求模型 API Key，也不需要 PostgreSQL：

```bash
cd backend
python -m venv .venv
# Windows: .\.venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python run.py
```

另开终端：

```bash
cd frontend
npm ci
npm run dev
```

浏览器访问 `http://localhost:8443`。

## 配置与安全

仓库只提供 `.env.example`，不包含真实密钥、数据库、上传文件、浏览器登录态或本地运行产物。启用完整模型能力和小红书链接抓取前，请自行创建 `backend/.env` 并填写配置。
