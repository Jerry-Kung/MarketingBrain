# Marketing Brain 前端

Marketing Brain 的审计工作台前端（Vite + React）。

## 开发

```bash
npm install
npm run dev        # 启动 Vite dev server (5173)，/api 代理到后端 19783
```

## 构建

```bash
npm run build      # 构建产物到 dist/，由 Docker 多阶段构建放入 app/static/
```

## V0.1 视图

- 数据概览：视频数、评论数、时间范围
- 任务入口：自由文本输入，展示结构化理解结果（任务理解/意图识别）
- 历史任务：任务列表与状态

精简方案：不使用重型状态管理库，仅用 React 内置 hooks（useState / useEffect）。
