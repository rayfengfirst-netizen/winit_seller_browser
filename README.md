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

- `login_winit.py` — 仅登录
- **`step01_australia_index.py`** — **第一步**：登录 → Australia/index → 截图  
- **`step02_australia_export.py`** — **第二步**：登录 → 澳大利亚页 → 导出 SKU 仓库级库存 → 导出中心 → 下载（文件在 `downloads/`）
- **`step03_unpack_winit_export.py`** — **第三步**：解压导出 zip → 预览 xlsx 表头与样例行 → 可选 `--export-csv`（依赖 `openpyxl`）
- **`run_daily_winit_job.py`** — **定时主线**：按顺序对每个已配置账号执行 step02 下载 → 解压 → 写入 **SQLite 日快照**（`artifacts/winit_inventory.db`，路径可用 `WINIT_SQLITE_PATH` 覆盖）
- `winit_inventory_db.py` / `winit_inventory_ingest.py` — 表结构 `inventory_daily`（按 `snapshot_date` + `account_id` 整批替换）与 `sync_runs` 运行记录
- `deploy/winit-daily-sync.service.example` + `winit-daily-sync.timer.example` — systemd **每天 06:00（服务器本地时区）**触发；完成后可通过 `WINIT_FEISHU_WEBHOOK_URL` 发飞书文本摘要
- **`inventory_viewer.py`** — 只读网页浏览 SQLite（表格化界面，可扩展为后续业务页的基础）
  - 无域名：在 `.env` 设 `WINIT_VIEWER_HOST=0.0.0.0`、`WINIT_VIEWER_USER` / `WINIT_VIEWER_PASSWORD` 后访问 `http://公网IP:8765/`（安全组只放行你的 IP）
  - 常驻：`deploy/inventory-viewer.service.example` → `systemctl enable --now inventory-viewer`
- **`scripts/run_full_inventory_sync.sh`** — 一键：两账号（或全部已配置账号）依次 **下载 zip → 解压 → 入库**（内部调用 `run_daily_winit_job.py`）
- `download_winit.py` — 登录后按流程下载（等你把「模拟操作」脚本发给我再接）
- `winit_download_flow.py` — 流程步骤解析
- `download_flow.example.json` — 流程示例（复制为 `download_flow.json` 后自行修改）
- `requirements.txt`
- `deploy/` — systemd / 定时任务示例
- `DEPLOY_WITH_MYAPP.md` — **与现有 myapp 同机部署的逐步说明**

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

## v0（连通 + 推送 + 服务器跑通）

按 [V0_SETUP.md](./V0_SETUP.md) 使用 `scripts/verify_local.sh`、`verify_ssh.sh` 与 `verify_server_run.sh`。
