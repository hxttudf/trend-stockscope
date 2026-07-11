# StockScope — A股K线看板

策略选股结果可视化工具，配合 `trend-shrink-picks` 选股策略使用。

## 功能

- 搜索A股股票（代码/名称模糊搜索）
- 专业K线图（lightweight-charts，红涨绿跌）
- MA5/10/20/60 均线
- 成交量柱（同步颜色）
- 信号标注（选股策略买入标记）
- 前复权/不复权切换
- 时间范围切换（1月/3月/6月/1年/全部）
- 十字光标OHLC数据
- 自选股管理（增/删）
- 每日选股结果面板

## 快速开始

```bash
# 安装后端依赖
python3 -m venv venv
venv/bin/pip install -r backend/requirements.txt

# 安装前端依赖并构建
cd frontend && npm install && npx vite build && cd ..

# 初始化数据库
python3 scripts/init_db.py

# 运行
venv/bin/python server.py 8004
```

## 技术栈

- **前端**: React 18 + TypeScript + Vite + lightweight-charts
- **后端**: Python Flask
- **数据库**: SQLite（项目DB + Sequoia选股.db + trend_picks.db）
- **部署**: Systemd 服务

## 端口

- 8004: StockScope 前端 + API
