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
- UI：`winit_view_theme.py` + `winit_view_format.py`（分块、配色、表格 **整数**）；按 **账号分块**展示。

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

**详情链接**：`WINIT_PUBLIC_BASE_URL` 必须为 **公网可访问** 基址（无尾斜杠），否则飞书里仍是 `127.0.0.1`。

### 4.2 北京时间定时

- **入库**：本地 **`06:00`** + 系统时区 **`Asia/Shanghai`** = 北京时间早 6 点。  
- **无动销**：本地 **`10:00`** + 同上 = 北京时间早 10 点。  
- **不要在 `OnCalendar` 里写 `Asia/Shanghai` 后缀**（部分 systemd 不兼容）；用 **改系统时区** 保证「本地时间 = 北京」。

### 4.3 systemd 要点

- **timer 与 service 成对安装**：仅 `.timer` 无对应 `.service` 会报 **「unit … to trigger not loaded」**。  
- **日志**：oneshot 建议 `[Service]` 中 `StandardOutput=journal`、`StandardError=journal`（已写入 `deploy/*.service.example`）。  
- **验收**：`systemctl list-timers`、`journalctl -u winit-no-sales-alert.service`、`systemctl start …` 手动试跑。

---

## 5. 无动销需求 — 结构化定义（业务口径）

**基础条件（同时满足才参与「均销为 0」相关计数）**

1. 可用库存 **≠ 0**  
2. **7 天平均库存 > 0**（字段一般为「7天平均库存」，兼容「7日平均库存」）

**飞书正文（按账号）**

- 在满足基础条件前提下，分别统计：**7 / 15 / 30 天平均日销量为 0** 的 SKU **条数**。  
- 另报 **五项全满足** 条数：基础两条 + 三种均销均为 0。  
- 文末固定 **统计口径** 文案。

**详情页 `/report/no-sales`**

- 仅列出 **五项全满足** SKU；**按账号分块**；表内 **可用库存降序**；数量 **整数** 展示。

**定时**

- 建议 **北京时间 10:00**，且在 **当日 06:00 入库之后**，保证基于最新快照。

---

## 6. UI 与体验 — 一致性方向（当前状态 + 后续）

**当前已实现**

- 共用主题变量（背景、顶栏渐变、卡片、表格斑马纹、数字列强调）。  
- 首页与无动销页：**按账号分块**；飞书模板按账号分段。  
- 表格数值：**整数**（`winit_view_format.cell_int_str`）。

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

1. **观察首个完整日**：6:00 入库 + 10:00 无动销各看一次 `journalctl` 与飞书、详情链接。  
2. **提交 Git**：将 `deploy` 中 `StandardOutput=journal` 等改动与本文一并纳入版本管理。  
3. **可选**：inventory_viewer 改为 gunicorn；安全组/防火墙仅放行可信 IP + **HTTP Basic** 已配则保持。  
4. **新需求入库**：新业务飞书场景 → 新增 `WINIT_FEISHU_WEBHOOK_*` + `channel`；新报表页 → 复用 `VIEWER_THEME_CSS` 与 `cell_int_str`。  
5. **与 myapp 视觉对齐**：与前端约定主色/辅色/字号后，回填到 `winit_view_theme.py` 的 `:root` 变量。

---

*本文档随项目演进可继续增补；与代码不一致时以代码与 `.env.example` 为准。*
