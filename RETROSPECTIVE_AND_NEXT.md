# Winit 数据分析项目 — 复盘与后续工作指引

本文对当前能力、与 **myapp** 同机部署要点、飞书/定时/无动销等业务规则、UI 与配置经验做结构化整理，便于后续迭代与交接。

---

## 1. 项目定位与本次交付范围

| 维度 | 说明 |
|------|------|
| **目标** | 万邑通卖家后台自动化：导出库存 zip → 解压 → 写入 **SQLite 日快照**；只读 Web 浏览；**无动销预警**飞书推送 + 详情页。 |
| **代码路径（约定）** | 服务器 **`/opt/winit-analytics`**（与 `deploy/*.example` 一致）；仓库名可为 `winit_seller_browser`。 |
| **与 myapp** | 同机、**目录/端口/进程/环境变量隔离**；不修改 myapp 的 systemd 与 Nginx 已有 `location /`。详见 [DEPLOY_WITH_MYAPP.md](./DEPLOY_WITH_MYAPP.md)。 |

**已落地能力（摘要）**

- 多账号（`winit_accounts` + `WINIT_ACCOUNT_n_LABEL` 展示 **LZ / LX** 等）。
- 每日入库：`run_daily_winit_job.py` → `inventory_daily` / `sync_runs`。
- 飞书 **分场景 Webhook**：`winit_feishu_webhook.py` — `sync`（入库完成）与 `no_sales`（无动销）分离。
- 只读站：`inventory_viewer.py`，默认 **8765**，首页 + `/table` + `/runs` + `/report/no-sales`。
- 无动销：`winit_no_sales_report.py` + `run_no_sales_morning_job.py`；规则与定时见 README「无动销预警」及下文 §5。
- UI：`winit_view_theme.py` + `winit_view_format.py`；无动销详情 **多账号 Tab** + ①②③ 色块 + SKU 卡片；可用整数、均销小数。

---

## 2. 与 myapp 同机部署 — 必须遵守的事项

（与 [DEPLOY_WITH_MYAPP.md](./DEPLOY_WITH_MYAPP.md)、[OPERATIONS.md](./OPERATIONS.md) 一致，此处为决策级摘要。）

| 事项 | 做法 |
|------|------|
| **目录** | winit 仅使用 `/opt/winit-analytics`，**勿**覆盖 `/opt/myapp`。 |
| **端口** | myapp 常用 **8000**（Gunicorn）；winit 只读站用 **8765**（或其它未占用端口），**不要**抢 8000。 |
| **Nginx** | 新增反代时 **新增** `location`，**不要**改指向 myapp 的 `proxy_pass`。 |
| **环境变量** | winit 密钥只在 **`/opt/winit-analytics/.env`**，**不要**写进 myapp 进程环境。 |
| **发布** | myapp 用其自有 `deploy.sh`；winit 仅 `git pull` + `pip install` + **重启 winit 相关 unit**，**无需** `systemctl restart myapp`。 |
| **验收 myapp 未受影响** | `systemctl status myapp`、`curl -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/` 与部署前一致即可。 |

---

## 3. 数据流与「抓取下载」经验（后续仍会用）

```
万邑通后台（Playwright）
    → step02 导出 zip 到 downloads（按账号分子目录）
    → step03 解压
    → winit_inventory_ingest 写入 SQLite（按 snapshot_date + account_id 整批替换）
    → inventory_viewer 只读展示
    → 无动销脚本读最新快照 → 飞书 + 详情链接
```

**经验沉淀（便于下次排障）**

- **表头/字段**以万邑通导出 xlsx 为准（如 `7天平均库存`、`7天平均日销量`）；代码里对 **7日/7天** 等做了别名兼容。
- **服务器**跑浏览器需 `WINIT_HEADLESS=true`，并安装 `playwright install chromium` 与系统依赖（`install-deps`）。
- **仅重跑入库、不下载**：`WINIT_SKIP_DOWNLOAD=1 python run_daily_winit_job.py`。
- **快照日期**：默认本机日历日；可用 `WINIT_SNAPSHOT_DATE` 覆盖。

---

## 4. 飞书、定时、systemd — 配置与踩坑

### 4.1 飞书多 Webhook

| 场景 | `channel` | 环境变量 |
|------|-----------|----------|
| 入库完成 | `sync` | `WINIT_FEISHU_WEBHOOK_SYNC` 或兼容 `WINIT_FEISHU_WEBHOOK_URL` |
| 无动销 | `no_sales` | **`WINIT_FEISHU_WEBHOOK_NO_SALES`（必填，不回退到 URL）** |
| 扩展 | 自定义 | `WINIT_FEISHU_WEBHOOK_<大写>` + `feishu_send_text(..., channel="snake_case")` |

**飞书限流重试**（`winit_feishu_webhook.py`）：遇 **11232 / 429** 等可自动等待重试；可选环境变量 `WINIT_FEISHU_RATE_LIMIT_RETRIES`（默认 3）、`WINIT_FEISHU_RATE_LIMIT_DELAY_SEC`（默认 45）。

**详情链接**：`WINIT_PUBLIC_BASE_URL` 必须为 **公网可访问** 基址（无尾斜杠），否则飞书里仍是 `127.0.0.1`。

### 4.2 北京时间定时

- **入库**：本地 **`06:00`** + 系统时区 **`Asia/Shanghai`** = 北京时间早 6 点。  
- **无动销**：本地 **`10:00`** + 同上 = 北京时间早 10 点。  
- **不要在 `OnCalendar` 里写 `Asia/Shanghai` 后缀**（部分 systemd 不兼容）；用 **改系统时区** 保证「本地时间 = 北京」。

### 4.3 systemd 要点

- **timer 与 service 成对安装**：仅 `.timer` 无对应 `.service` 会报 **「unit … to trigger not loaded」**。  
- **日志**：oneshot 建议 `[Service]` 中 `StandardOutput=journal`、`StandardError=journal`（已写入 `deploy/*.service.example`）。  
- **无动销 oneshot 超时**：飞书 **11232 频率限制** 时会自动重试等待；`winit-no-sales-alert.service` 需 **`TimeoutStartSec=300`**（或更大），否则重试中被 systemd 杀掉。  
- **验收**：`systemctl list-timers`、`journalctl -u winit-no-sales-alert.service`、`systemctl start …` 手动试跑。

---

## 5. 无动销需求 — 结构化定义（业务口径）

**基础条件（按账号内 SKU，仅将满足条件的仓行加总）**

1. 单仓 **可用库存 ≠ 0**（无动销不再使用「7 天平均库存」字段）

**飞书正文（按账号）**

- 在满足基础条件前提下，仅对「聚合后 7 天均销=0」的 SKU 计数，并分 **①②③**（及其它）类 **SKU 个数**。  
- 文末固定 **统计口径** 文案。

**详情页 `/report/no-sales`**

- **多账号 Tab 切换**，块内再分情况；表内为 **仓库行**（可用整数、均销小数）。

**定时**

- 建议 **北京时间 10:00**，且在 **当日 06:00 入库之后**，保证基于最新快照。

---

## 6. UI 与体验 — 一致性方向（当前状态 + 后续）

**当前已实现**

- 共用主题变量（背景、顶栏渐变、卡片、表格斑马纹、数字列强调）。  
- 首页与无动销页：无动销 **多账号 Tab**；飞书按账号分段。  
- 表格：可用等 **整数**；无动销均销列 **小数**。

**与 myapp 的「一致性」后续可做**

- 若未来要 **品牌色/字体** 与 myapp 前台统一：抽一层 **设计 token**（CSS 变量或独立 `theme_tokens.css`），与 myapp 文档约定色值与圆角规范。  
- 生产环境 Flask **开发服务器** 警告：长期可改为 **gunicorn** 等 WSGI（需单独评估与 inventory-viewer.service 的 ExecStart 调整）。

---

## 7. 账号标识（1 号不显示 LZ 类问题）

- **1 号账号**若只用 `WINIT_USERNAME` / `WINIT_PASSWORD`，仍需单独配置 **`WINIT_ACCOUNT_1_LABEL=LZ`**。  
- **2 号**为 `WINIT_ACCOUNT_2_LABEL=LX` 等。  
- 改 `.env` 后 **`systemctl restart inventory-viewer`** 使只读站进程重读环境（脚本类每次运行会 `load_dotenv`）。

---

## 8. 文档地图（避免重复造轮子）

| 文档 | 用途 |
|------|------|
| [README.md](./README.md) | 仓库总览、无动销规则摘要、目录索引 |
| [OPERATIONS.md](./OPERATIONS.md) | 需求↔代码表、`.env` 摘要、systemd、**线上测试清单** |
| [DEPLOY_WITH_MYAPP.md](./DEPLOY_WITH_MYAPP.md) | 与 myapp 同机原则 + **§8 当前主线单元** |
| [SERVER_QUICKSTART.md](./SERVER_QUICKSTART.md) | 首次 clone、venv、基础 `.env` |
| [.env.example](./.env.example) | 全量变量模板（含飞书多场景） |
| `deploy/*.example` | systemd 样例（路径按需改） |

---

## 9. 建议的下一步工作（可按优先级）

1. **日常观察**：6:00 入库 + 10:00 无动销各看一次 `journalctl`、飞书、**Tab 详情页**链接是否正常。  
2. **可选**：`inventory_viewer` 长期用 **gunicorn** 替代 Flask 开发服务器；安全组仅放行可信 IP，**HTTP Basic** 保持。  
3. **新需求**：新业务飞书 → 新增 `WINIT_FEISHU_WEBHOOK_*` + `channel`；新报表 → 复用 `VIEWER_THEME_CSS`、`cell_int_str` / 无动销页的模块化 HTML/CSS 模式。  
4. **与 myapp 视觉对齐**：约定主色/辅色/字号后回填 `winit_view_theme.py` 的 `:root`。

---

## 10. 迭代归档快照（便于续做）

**时间线（约 2025-03）— 已合入 `main` 的主题**

| 主题 | 说明 | 主要文件 |
|------|------|----------|
| 飞书限流补偿 | 11232/429 时重试；无动销 service 建议 `TimeoutStartSec=300` | `winit_feishu_webhook.py`、`deploy/winit-no-sales-alert.service.example` |
| 无动销统计改版 | **账号 → SKU 聚合（跨仓求和）** → ①②③ 互斥分类；**单仓仅「可用≠0」** 参与（**已弃用 7 日均库**作门槛） | `winit_no_sales_report.py`、`run_no_sales_morning_job.py` |
| 详情页体验 | SKU 卡片 + 聚合指标网格 + 分仓表；**多账号 Tab**；图例与色条 | 同上（`render_no_sales_report_html` 内联 CSS/JS） |
| 文档 | README / OPERATIONS / 本文 / `.env.example` / `inventory_viewer` 注释 | 各 md |

**双轨运行约束（固定）**

- 轨道 A（老需求库存）：`Australia/index` → `run_daily_winit_job.py` → `winit-daily-sync.timer`。  
- 轨道 B（新增 inout）：`Australia/inventoryFlow` → `run_inventory_inout_job.py` → `winit-inout-sync.timer`。  
- 原则：A 不变，B 新增；后续迭代默认只动 B，除非明确提出要改 A。

**后续改无动销时优先打开**

- 业务规则与飞书模板：`winit_no_sales_report.py`（`STAT_RULE_LINE`、`format_no_sales_feishu_text`、`collect_no_sales_rows`）  
- 页面结构/样式：同文件 `render_no_sales_report_html`、`NO_SALES_EXTRA_CSS`  
- 线上发布：`cd /opt/winit-analytics && git pull && pip install -r requirements.txt && sudo systemctl restart inventory-viewer`（**勿** `restart myapp`）

**口径一句话（当前）**

> 单仓可用≠0 的行进入该 SKU 的加总；聚合后若 7 天均销=0，再分 ①（15/30≠0）②（7/15=0 且 30≠0）③（全 0）或其它。

---

## 11. 2026-03-23 迭代回顾（inventoryFlow 上架核对）

### 本次完成

- 新增独立 inout 轨道：`run_inventory_inout_job.py` + `winit_inventory_inout_db.py`（默认 `artifacts/winit_inout.db`，按账号覆盖写入）。
- 新增页面：`/report/inout-shelf`（筛选两类备注；按业务日分块；日内按账号分表；每账号内按数量降序）。
- 日期解析增强：支持 `库存变动日期 北京时间`（如 `2026-03-19 09:36:54` 自动归为 `2026-03-19`）。
- 新增飞书任务：`run_inout_shelf_morning_job.py` + `winit-inout-shelf-alert.timer`（10:10）。
- inout 主任务成功后追加简短提醒：`✅ 账号上架数据已更新，请速速查看。链接：.../report/inout-shelf`。

### 线上验证结果

- 代码已发布到生产并重启 `inventory-viewer`。
- 手动触发 `winit-inout-sync.service` 成功（多账号下载、入库、日志正常）。
- 手动触发 `winit-inout-shelf-alert.service` 成功（飞书 `inout_shelf` 已送达）。
- 补齐线上缺失变量：`WINIT_FEISHU_WEBHOOK_INOUT_SHELF`。

### 运行注意

- 当前可能看到两条推送：详细摘要（sync）+ 简短提醒（inout_shelf）；10:10 还会再发 inout 摘要。
- 若后续希望“只发一条”，可按运营反馈切换策略（保留简短或保留详细）。

### 明细页定型与文档收工（同日）

- `/report/inout-shelf` 明细表**固定 7 列**（顺序：商品编码、数量、仓库、库存变动日期（北京时间）、期初库存、期末库存、单据号）；**商品编码支持点击复制**；矩阵与顶部统计区配色收敛，重点突出数量与 SKU。
- 导出表头与默认列名不一致时，用 `.env` 中 `WINIT_INOUT_SHELF_SKU_KEYS` / `WH_KEYS` / `QTY_BEGIN_KEYS` / `QTY_END_KEYS` / `DOC_KEYS` 追加候选键（见 `.env.example`）。
- 对应实现与部署：`996bc1b`（已合并 `main`，生产 `inventory-viewer` 已 `git pull` + `restart`）。
- 同账号同日相同 SKU 合并：`90f7018`（数量汇总，减少重复行；页面显示“合并后 X 行 / 原 Y 条”）。
- 运维侧：`OPERATIONS.md` 已含 **「新增需求互不干扰（准入清单）」**、inout 相关 timer 与线上测试项，便于后续扩展不串线。

---

*本文档随项目演进可继续增补；与代码不一致时以代码与 `.env.example` 为准。*
