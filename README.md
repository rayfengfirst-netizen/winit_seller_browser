# winit 数据分析

在万邑通卖家后台（`seller.winit.com.cn`）完成浏览器自动化登录，为后续数据采集与分析提供会话基础。

**不熟悉 Git / 服务器时，先看 → [BEGINNER_GUIDE.md](./BEGINNER_GUIDE.md)**（本地怎么保存代码、怎么推 GitHub、服务器怎么更新，都写在一起）。  
**要录操作并生成代码（和 myapp 里 `playwright codegen` 一样）：** 本机执行 `./scripts/winit_codegen.sh`，见 [RECORDING.md](./RECORDING.md)。

## 与 myapp 的关系

| 项目 | 服务器路径（约定） | 进程 |
|------|-------------------|------|
| **myapp** | `/opt/myapp` | `systemd: myapp` → Gunicorn `127.0.0.1:8000`，Nginx 反代对外 |
| **本项目** | `/opt/winit-analytics` | 独立虚拟环境；默认 **不** 占用 8000 端口，**不** 改 myapp 的 unit / Nginx |

线上与 myapp **同一台主机**：`ssh root@8.218.58.28`（见 `myapp/README_DEPLOY.md`）。  
两者仅共用同一台机器与系统级 Chromium 依赖（若已给 myapp 装过 Playwright，可复用或在本项目 venv 内再执行一次 `playwright install chromium`）。

## 仓库目录

### 双轨约束（重要）

- **老链路（库存）保持不变**：入口固定 `https://seller.winit.com.cn/Australia/index`，由 `run_daily_winit_job.py` 驱动。
- **新链路（inventoryFlow）独立新增**：入口固定 `https://seller.winit.com.cn/Australia/inventoryFlow`，由 `run_inventory_inout_job.py` 驱动。
- 两条链路分别使用独立任务入口与定时器，互不覆盖、互不替换。

- `login_winit.py` — 仅登录
- **`step01_australia_index.py`** — **第一步**：登录 → Australia/index → 截图  
- **`step02_australia_export.py`** — **第二步**：登录 → 澳大利亚页 → 导出 SKU 仓库级库存 → 导出中心 → 下载（文件在 `downloads/`）
- **`step03_unpack_winit_export.py`** — **第三步**：解压导出 zip → 预览 xlsx 表头与样例行 → 可选 `--export-csv`（依赖 `openpyxl`）
- **`run_daily_winit_job.py`** — **定时主线**：按顺序对每个已配置账号执行 step02 下载 → 解压 → 写入 **SQLite 日快照**（`artifacts/winit_inventory.db`，路径可用 `WINIT_SQLITE_PATH` 覆盖）
- **`run_inventory_inout_job.py`** — **5 点链路**：按账号打开 `Australia/inventoryFlow` 导出，去导出中心下载名含 `InventoryInoutSeller` 的最新文件，解压表格后入**独立 SQLite**（按账号覆盖写入，不保留每日历史）并推飞书
- `winit_inventory_db.py` / `winit_inventory_ingest.py` — 表结构 `inventory_daily`（按 `snapshot_date` + `account_id` 整批替换）与 `sync_runs` 运行记录
- `winit_inventory_inout_db.py` — 独立库 `winit_inout.db`（默认）与表 `inventory_inout_current` / `inventory_inout_latest_meta`
- `winit_inout_shelf_report.py` — 从独立库筛选备注「标准入库-上架」「国内直发入库-上架」；`/report/inout-shelf`：按业务日分块、日内按账号分表；**明细固定 7 列**，商品编码可点击复制；同账号同日下相同 SKU 自动合并（数量汇总，减少重复行，列别名见 `.env.example`）
- **`run_inout_shelf_morning_job.py`** — **10:10 飞书摘要**（`WINIT_FEISHU_WEBHOOK_INOUT_SHELF`）；依赖当日已入库的 inout 数据
- `deploy/winit-daily-sync.service.example` + `winit-daily-sync.timer.example` — systemd **每天本地 06:00** 触发；**请将服务器时区设为 `Asia/Shanghai`** 即北京时间早 6 点入库（见 timer 文件头注释）；完成后飞书「sync」场景通知（`WINIT_FEISHU_WEBHOOK_SYNC` 或兼容 `WINIT_FEISHU_WEBHOOK_URL`，见 `winit_feishu_webhook.py`）
- `deploy/winit-inout-sync.service.example` + `winit-inout-sync.timer.example` — systemd **每天本地 05:00** 触发 inventoryFlow 导出/下载（北京时间 5 点）
- `deploy/winit-inout-shelf-alert.service.example` + `.timer.example` — 默认每天 **本地 10:10** 发入库上架类流水飞书摘要（与 inout 库一致）
- **`inventory_viewer.py`** — 只读网页浏览 SQLite（表格化界面；**服务器上通常用 8765 作库存首页**，与 `WINIT_PUBLIC_BASE_URL` 端口对齐）
  - 无域名：在 `.env` 设 `WINIT_VIEWER_HOST=0.0.0.0`、`WINIT_VIEWER_USER` / `WINIT_VIEWER_PASSWORD` 后访问 `http://公网IP:8765/`（安全组只放行你的 IP）
  - 常驻：`deploy/inventory-viewer.service.example` → `systemctl enable --now inventory-viewer`
- **`scripts/run_full_inventory_sync.sh`** — 一键：两账号（或全部已配置账号）依次 **下载 zip → 解压 → 入库**（内部调用 `run_daily_winit_job.py`）
- **`run_no_sales_morning_job.py`** — **无动销预警（定时任务）**：规则与排期见下文 **「无动销预警」**；飞书走 `WINIT_FEISHU_WEBHOOK_NO_SALES`
- `deploy/winit-no-sales-alert.service.example` + `.timer.example` — 默认每天 **本地 10:00** 发飞书（时区 `Asia/Shanghai` 即北京时间 10:00，与 06:00 入库独立）

### 无动销预警（规则与定时）

- **定位**：定时任务；读 SQLite 里每个账号的**最新快照日**数据，算完后发飞书，并给出只读详情页链接。
- **基础条件**（**每个仓库行**先满足；同一 SKU 只对满足条件的行做加总再分类）：
  - 单仓 **可用库存 ≠ 0**（无动销**不使用**「7 天平均库存」字段，避免口径偏差）
- **分类**（仅当聚合后 **7 天平均日销量为 0** 的 SKU 才计数；缺失销量按 0 处理）：  
  **①** 7 天=0，且 15、30 天均≠0；**②** 7、15 天=0，且 30 天≠0；**③** 7、15、30 天均为 0；其它边界形态单独统计（若有）。
- **飞书正文**：**按账号**输出 ①②③（及其它）的 **SKU 个数**。
- **详情页**：`/report/no-sales`，**多账号时 Tab 切换**；块内再分 ①②③（及其它）；表内为**仓库行**（可用整数、均销为小数）。
- **排期**：**北京时间每天 10:00**（timer 为本地 10:00 + 系统时区 `Asia/Shanghai`）。宜在 **当日北京时间 06:00 入库完成之后**，保证用的是刚更新的快照。

### 其它脚本与配置

- `winit_feishu_webhook.py` — 多场景飞书 Webhook（`feishu_send_text(..., channel="sync"|"no_sales"|…)`）
- `winit_view_theme.py` / `winit_view_format.py` — 只读页共用样式与表格整数格式
- `test_no_sales_feishu.py` — 预览/试发无动销飞书
- `download_winit.py` — 登录后按流程下载（等你把「模拟操作」脚本发给我再接）
- `winit_download_flow.py` — 流程步骤解析
- `download_flow.example.json` — 流程示例（复制为 `download_flow.json` 后自行修改）
- `requirements.txt`
- `deploy/` — systemd / 定时任务示例
- `DEPLOY_WITH_MYAPP.md` — **与现有 myapp 同机部署的逐步说明**
- **`OPERATIONS.md`** — **需求与代码对照、Git 发布与线上测试清单**
- **`RETROSPECTIVE_AND_NEXT.md`** — **与 myapp 同机复盘、业务/UI/定时沉淀、后续工作建议**

## 本地运行

```bash
cd winit_seller_browser
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env
python login_winit.py
```

## 线上部署

- **从 GitHub 拉到服务器（一步步）：** [SERVER_QUICKSTART.md](./SERVER_QUICKSTART.md)  
- **与 myapp 同机、互不干扰：** [DEPLOY_WITH_MYAPP.md](./DEPLOY_WITH_MYAPP.md)  
- **发布前回顾、`.env`、systemd、飞书、线上验证：** [OPERATIONS.md](./OPERATIONS.md)  
- **阶段性复盘与下一步：** [RETROSPECTIVE_AND_NEXT.md](./RETROSPECTIVE_AND_NEXT.md)

## v0（连通 + 推送 + 服务器跑通）

按 [V0_SETUP.md](./V0_SETUP.md) 使用 `scripts/verify_local.sh`、`verify_ssh.sh` 与 `verify_server_run.sh`。
