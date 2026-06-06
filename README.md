# Deepsearch Agent：多智能体深度搜索系统

Deepsearch Agent 是一个基于 **DeepAgents + FastAPI + Vue 3** 构建的多智能体深度搜索系统，面向复杂研究、知识库问答、数据库分析和报告生成场景。系统通过主智能体统一规划任务，并调用网络搜索、数据库查询、RAGFlow 知识库三类子智能体完成多源信息获取，最终支持直接回答、生成 Markdown 报告、转换 PDF、展示执行过程和管理会话文件。

## 核心能力

- **多智能体任务编排**：主智能体负责理解问题、拆解任务、调度子智能体并汇总最终答案。
- **网络搜索**：通过 Tavily 检索互联网公开信息，支持搜索质量记录与缓存。
- **数据库查询**：通过 MySQL 工具列出表、预览数据、执行 SQL 查询，用于业务数据分析。
- **RAGFlow 知识库问答**：查询 RAGFlow 中的可用助手，并向指定知识库助手发起问题。
- **上传文件分析**：支持读取 Markdown、PDF、Word、Excel、文本等上传文件，并让智能体基于文件内容分析。
- **报告生成**：支持生成 Markdown 文档，并按需转换为 PDF。
- **会话与记忆管理**：按 session/thread 隔离会话、消息、工作目录和生成文件，支持长期记忆列表与删除。
- **实时进度推送**：通过 WebSocket 将工具调用、子智能体调用、任务结果和错误信息推送到前端。
- **任务生命周期管理**：支持任务创建、状态查询、取消、重试和 Trace 评估记录。

## 系统架构

```text
用户 / 前端 Vue
    |
    | HTTP + WebSocket
    v
FastAPI 后端
    |
    | 创建任务、管理会话、上传文件、推送进度
    v
Main Agent（DeepAgents）
    |
    |-- 网络搜索助手：Tavily
    |-- 数据库查询助手：MySQL
    |-- RAGFlow 助手：私有知识库
    |
    |-- Markdown 生成工具
    |-- PDF 转换工具
    |-- 上传文件读取工具
```

## 目录结构

```text
deep_search/
├── agent/                 # 主智能体、子智能体、LLM 和提示词加载
│   ├── main_agent.py
│   ├── llm.py
│   ├── prompts.py
│   └── subagents/
├── api/                   # FastAPI 服务、会话、记忆、监控、评估
│   ├── server.py
│   ├── monitor.py
│   ├── memory_store.py
│   └── evaluation.py
├── prompt/                # prompts.yml 提示词配置
├── skills/project/        # DeepAgents 项目技能
├── tools/                 # Tavily、MySQL、RAGFlow、文件和报告工具
├── ui/                    # Vue 3 + TypeScript 前端
├── data/                  # 会话、记忆、checkpoint、评估数据
├── output/                # 每个会话生成的结果文件
├── updated/               # 每个会话上传的文件
├── 知识库文件/             # 本地知识库示例文件
├── requirements.txt
└── README.md
```

## 环境要求

- Python 3.10+
- Node.js 18+
- MySQL，可选，用于数据库查询助手
- RAGFlow 服务，可选，用于私有知识库问答
- Tavily API Key，用于联网搜索
- OpenAI 兼容接口或 DashScope/OpenAI 风格模型服务

## 后端配置

在项目根目录创建或检查 `.env` 文件，常用配置如下：

```env
# LLM
OPENAI_API_KEY=your_api_key
OPENAI_BASE_URL=https://your-openai-compatible-endpoint/v1
LLM_QWEN_MAX=your_model_name

# Tavily
TAVILY_API_KEY=your_tavily_api_key
SEARCH_CACHE_TTL_SECONDS=3600

# RAGFlow
RAGFLOW_API_URL=http://your-ragflow-host
RAGFLOW_API_KEY=your_ragflow_api_key

# MySQL
MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=your_password
MYSQL_DATABASE=your_database
MYSQL_CHARSET=utf8mb4

# API
HOST=0.0.0.0
PORT=8001
SESSION_CLEANUP_DAYS=2
SESSION_CLEANUP_INTERVAL_SECONDS=3600
```

## 安装与启动

### 1. 安装后端依赖

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. 启动后端服务

```powershell
python api\server.py
```

默认服务地址：

```text
http://127.0.0.1:8001
```

### 3. 安装前端依赖

```powershell
cd ui
npm install
```

### 4. 启动前端开发服务

```powershell
npm run dev
```

前端会连接：

```text
API: http://127.0.0.1:8001
WS:  ws://127.0.0.1:8001
```

## 主要接口

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/version` | 查看后端版本和编码信息 |
| `GET` | `/api/sessions` | 查询会话列表 |
| `POST` | `/api/sessions` | 创建新会话 |
| `GET` | `/api/sessions/{session_id}` | 获取会话详情 |
| `DELETE` | `/api/sessions/{session_id}` | 删除会话及相关文件 |
| `POST` | `/api/task` | 创建智能体任务 |
| `GET` | `/api/task/{task_id}` | 查询任务状态 |
| `GET` | `/api/tasks` | 查询任务列表 |
| `POST` | `/api/task/{task_id}/cancel` | 取消任务 |
| `POST` | `/api/task/{task_id}/retry` | 重试任务 |
| `POST` | `/api/upload` | 上传会话文件 |
| `GET` | `/api/files` | 查询会话生成文件 |
| `GET` | `/api/download` | 下载生成文件 |
| `GET` | `/api/memories` | 查询长期记忆 |
| `DELETE` | `/api/memories/{memory_id}` | 删除长期记忆 |
| `GET` | `/api/eval/summary` | 查看评估摘要 |
| `GET` | `/api/eval/traces` | 查看任务 Trace |
| `GET` | `/api/eval/searches` | 查看搜索质量记录 |
| `WS` | `/ws/{thread_id}` | 接收实时进度事件 |

## 使用流程

1. 在前端创建或选择一个会话。
2. 输入研究问题，必要时上传文件。
3. 后端创建任务并绑定 `session_id`、`thread_id`、`task_id`。
4. 主智能体根据问题判断是否调用网络搜索、数据库查询或 RAGFlow 助手。
5. 前端通过 WebSocket 展示工具调用、子智能体调用和最终结果。
6. 若生成报告，文件会保存到 `output/session_{thread_id}`，并在右侧文件栏展示。

示例问题：

```text
请结合知识库和互联网资料，生成一份沃华医药经营情况分析报告，并输出 Markdown。
```

```text
查询数据库中药品相关表，分析库存异常情况，并给出处理建议。
```

```text
读取我上传的 PDF，总结核心内容，并生成一份结构化研究报告。
```

## 数据与文件约定

- `updated/session_{thread_id}`：保存用户上传文件。
- `output/session_{thread_id}`：保存智能体生成文件和临时结果。
- `data/chat_sessions.json`：兼容旧版会话数据。
- `data/memory.sqlite3`：长期记忆数据。
- `data/agent_checkpoints.sqlite3`：LangGraph/DeepAgents checkpoint。
- 生成文件必须限制在当前会话工作目录中，避免跨会话读写。

## 评测与监控

系统内置基础评测能力：

- 对 Tavily 搜索结果进行质量记录。
- 对重复搜索进行缓存，减少外部调用。
- 按任务保存 Trace，便于复盘智能体执行链路。
- 记录任务状态，包括 `queued`、`running`、`cancelling`、`completed`、`failed`、`cancelled`。
- 通过 WebSocket 推送 `tool_start`、`assistant_call`、`search_quality`、`task_result`、`error` 等事件。

可通过以下接口查看：

```text
GET /api/eval/summary
GET /api/eval/traces
GET /api/eval/searches
```

## 常见问题

### 1. 前端没有实时进度

检查后端是否启动在 `8001` 端口，并确认前端中的 `API_BASE` 和 `WS_BASE` 与后端一致。

### 2. 中文输出乱码

后端已在 `api/server.py` 中强制设置 UTF-8 输出，并给文本响应补充 `charset=utf-8`。如果仍有乱码，优先检查终端编码和文件读取编码。

### 3. 数据库工具不可用

检查 `.env` 中的 `MYSQL_HOST`、`MYSQL_PORT`、`MYSQL_USER`、`MYSQL_PASSWORD`、`MYSQL_DATABASE` 是否完整，并确认 MySQL 服务可连接。

### 4. RAGFlow 查询失败

检查 `RAGFLOW_API_URL` 和 `RAGFLOW_API_KEY`，并确认 RAGFlow 中已经创建聊天助手且助手关联了知识库。

### 5. 生成文件看不到

确认任务已完成，并检查 `output/session_{thread_id}` 是否存在文件。前端右侧文件栏可手动刷新。

## 开发备注

- 后端入口：`api/server.py`
- 主智能体入口：`agent/main_agent.py`
- 提示词配置：`prompt/prompts.yml`
- 前端入口：`ui/src/App.vue`
- 工具实现：`tools/`
- 项目技能：`skills/project/`

后续扩展可以优先从三个方向推进：增加更严格的 SQL 只读保护、完善 RAGFlow 引用来源展示、为评测 Trace 增加可视化页面。
