# 运维说明：需求对照、Git 发布、线上测试

本文与 [README.md](./README.md) 配合使用：实现细节以代码与 `.env.example` 为准。

---

## 1. 需求与代码对照（回顾）

| 能力 | 入口 / 模块 | 说明 |
|------|-------------|------|
| 多账号登录与导出 | `winit_accounts.py`、`step02_australia_export.py` | `.env` 中 `WINIT_USERNAME` / `WINIT_ACCOUNT_n_*`、`WINIT_ACCOUNT_n_LABEL`（页面/飞书展示标识） |
| 每日下载 zip + 解压 + 写入 SQLite | `run_daily_winit_job.py` | 表 `inventory_daily`、`sync_runs`；`WINIT_SQLITE_PATH` 可选 |
| 入库完成飞书 | `winit_feishu_webhook.py` → `channel="sync"` | `WINIT_FEISHU_WEBHOOK_SYNC` 或兼容 `WINIT_FEISHU_WEBHOOK_URL` |
| 只读库存网站（首页 8765） | `inventory_viewer.py` | `/`、`/table`、`/runs`、`/report/no-sales`；`WINIT_VIEWER_*`、`WINIT_PUBLIC_BASE_URL` |
| 无动销统计与飞书 | `winit_no_sales_report.py`、`run_no_sales_morning_job.py` | `channel="no_sales"` → **`WINIT_FEISHU_WEBHOOK_NO_SALES`（必填，不与 sync 混用）** |
| 无动销规则 | `winit_no_sales_report.py`、README「无动销预警」 | 基础：可用≠0、7 日均库>0；飞书按账号报 7/15/30 均销为 0 条数；详情页仅「五项全满足」 |
| 页面样式与表格整数 | `winit_view_theme.py`、`winit_view_format.py` | 供 `inventory_viewer` 与无动销 HTML 共用 |
| 试发无动销飞书 | `test_no_sales_feishu.py` | 打印模板并可真发 |

---

## 2. Git 发布前（本地）

1. **勿提交** `.env`（仓库应已 `.gitignore`）。
2. 建议自测：
   ```bash
   cd winit_seller_browser && source .venv/bin/activate
   python -c "from winit_feishu_webhook import feishu_webhook_url; print('ok')"
   python test_no_sales_feishu.py   # 不配 NO_SALES 时只打印文案
   ```
3. `git status` 确认无密钥、无本机大文件误加。

---

## 3. 服务器环境变量（上线必填摘要）

完整模板见 [`.env.example`](./.env.example)。

| 变量 | 用途 |
|------|------|
| `WINIT_USERNAME` / `WINIT_PASSWORD`（及多账号） | 万邑通自动化 |
| `WINIT_HEADLESS=true` | 服务器无界面跑浏览器 |
| `WINIT_ACCOUNT_n_LABEL` | 首页/无动销/飞书中的账号标识（如 LZ、LX） |
| `WINIT_FEISHU_WEBHOOK_SYNC` 或 `WINIT_FEISHU_WEBHOOK_URL` | 入库完成通知 |
| `WINIT_FEISHU_WEBHOOK_NO_SALES` | 无动销晨间通知 |
| `WINIT_PUBLIC_BASE_URL` | 飞书里「详情」链接（与 viewer 公网地址一致，常带 `:8765`） |
| `WINIT_SQLITE_PATH` | 可选；默认可用 `artifacts/winit_inventory.db` |
| `WINIT_VIEWER_HOST` / `PORT` / `USER` / `PASSWORD` | 公网访问 viewer 时建议 `0.0.0.0` + Basic 认证 |

---

## 4. 服务器时区与 systemd（北京时间）

1. **时区**（与 timer 示例一致：`OnCalendar` 用**本地时区**，不设 `Asia/Shanghai` 后缀）：
   ```bash
   sudo timedatectl set-timezone Asia/Shanghai
   timedatectl
   ```
2. **每日 06:00 入库**（北京时间）：
   ```bash
   sudo cp /opt/winit-analytics/deploy/winit-daily-sync.service.example /etc/systemd/system/winit-daily-sync.service
   sudo cp /opt/winit-analytics/deploy/winit-daily-sync.timer.example /etc/systemd/system/winit-daily-sync.timer
   sudo systemctl daemon-reload
   sudo systemctl enable --now winit-daily-sync.timer
   systemctl list-timers winit-daily-sync.timer --all
   ```
3. **每日 10:00 无动销飞书**（北京时间）：
   ```bash
   sudo cp /opt/winit-analytics/deploy/winit-no-sales-alert.service.example /etc/systemd/system/winit-no-sales-alert.service
   sudo cp /opt/winit-analytics/deploy/winit-no-sales-alert.timer.example /etc/systemd/system/winit-no-sales-alert.timer
   sudo systemctl daemon-reload
   sudo systemctl enable --now winit-no-sales-alert.timer
   ```
4. **只读站点常驻**（示例端口 8765）：
   ```bash
   sudo cp /opt/winit-analytics/deploy/inventory-viewer.service.example /etc/systemd/system/inventory-viewer.service
   # 编辑其中 WorkingDirectory / User / EnvironmentFile 若需要
   sudo systemctl daemon-reload
   sudo systemctl enable --now inventory-viewer
   ```

若 `restart …timer` 报 **Job failed**，多为旧版 `OnCalendar=… Asia/Shanghai`：**请拉最新代码**，timer 应仅为 `06:00:00` / `10:00:00` + 系统时区上海。

---

## 5. 发布到线上（更新代码）

```bash
cd /opt/winit-analytics
git pull
source .venv/bin/activate
pip install -r requirements.txt
# 按需：playwright install chromium
sudo systemctl restart inventory-viewer   # 若改了 viewer 代码
```

**不要** `systemctl restart myapp`（与 myapp 隔离，见 [DEPLOY_WITH_MYAPP.md](./DEPLOY_WITH_MYAPP.md)）。

---

## 6. 线上测试清单

按顺序勾选：

- [ ] `timedatectl` 为 `Asia/Shanghai`
- [ ] `systemctl status inventory-viewer` 为 active；浏览器打开 `http://<公网IP>:8765/`（及 Basic）
- [ ] 打开 `/report/no-sales`：分账号、数字为整数、样式正常
- [ ] `systemctl list-timers` 中 `winit-daily-sync`、`winit-no-sales-alert` 有 **NEXT** 时间合理
- [ ] 手动跑一次入库：`cd /opt/winit-analytics && source .venv/bin/activate && python run_daily_winit_job.py`（或等定时）；看飞书 sync 与 `journalctl -u winit-daily-sync.service -n 80`
- [ ] 手动无动销：`python test_no_sales_feishu.py` 或 `python run_no_sales_morning_job.py`；飞书 no_sales 群收到消息，**详情链接可点开**（`WINIT_PUBLIC_BASE_URL` 正确）

---

## 7. 相关文档

| 文档 | 内容 |
|------|------|
| [SERVER_QUICKSTART.md](./SERVER_QUICKSTART.md) | 首次 clone、venv、基础 `.env` |
| [DEPLOY_WITH_MYAPP.md](./DEPLOY_WITH_MYAPP.md) | 与 myapp 同机隔离原则 |
| [README.md](./README.md) | 仓库总览、无动销业务规则摘要 |
