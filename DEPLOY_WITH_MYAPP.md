# winit 数据分析 — 与 myapp 同机部署（互不干扰）

目标：在 **已有 myapp** 的服务器（文档中为 `/opt/myapp`、`systemctl myapp`、`127.0.0.1:8000`）上，增加 **winit 数据分析** 项目，使用 **独立目录、独立 venv、独立 systemd 单元**，**不修改** myapp 的 service 文件与 Nginx 中已有的 `location /`。

---

## 1. 原则（避免影响 myapp）

1. **代码路径分离**：winit 放在 `/opt/winit-analytics`（勿覆盖 `/opt/myapp`）。
2. **端口分离**：winit 当前阶段仅为 **脚本 / 定时任务**，不监听 HTTP；将来若要做独立 API，再用 **8001 等其它端口**，并在 Nginx **新增** `location`，**不要**改掉指向 myapp 的 `proxy_pass http://127.0.0.1:8000`。
3. **环境变量分离**：winit 的账号密码只放在 `/opt/winit-analytics/.env`，**不要**写进 `/opt/myapp` 的进程环境。
4. **发布分离**：myapp 继续用 `bash /opt/myapp/deploy.sh`；winit 用本仓库提供的 `deploy.sh` 或手动 `git pull` + 仅重启 **winit** 相关 unit。

---

## 2. 服务器上创建项目（首次）

SSH 登录服务器后（示例与 myapp 文档一致：`ssh root@8.218.58.28`）：

```bash
mkdir -p /opt/winit-analytics
cd /opt
```

**代码来源二选一：**

- **A. 新建 Git 仓库**（推荐）：在 GitHub/Gitee 建库 `winit-analytics`，然后：

  ```bash
  git clone <你的仓库URL> /opt/winit-analytics
  ```

- **B. rsync 从本机推**（无远程仓库时）：

  ```bash
  # 在你自己的 Mac 上执行
  rsync -avz --exclude '.venv' --exclude '__pycache__' \
    /Users/fengchangrui/Desktop/cursor/winit_seller_browser/ \
    root@8.218.58.28:/opt/winit-analytics/
  ```

进入目录并创建虚拟环境：

```bash
cd /opt/winit-analytics
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
playwright install-deps chromium
```

在服务器上创建密钥文件（**勿提交 Git**）：

```bash
cp .env.example .env
nano .env
chmod 600 .env
```

`.env` 中至少：`WINIT_USERNAME`、`WINIT_PASSWORD`；服务器上建议 `WINIT_HEADLESS=true`。

---

## 3. systemd：定时跑登录（示例）

与 **myapp** 的 unit **文件名不同**，不会互相覆盖。

```bash
sudo cp /opt/winit-analytics/deploy/winit-analytics.service.example \
  /etc/systemd/system/winit-analytics.service
sudo cp /opt/winit-analytics/deploy/winit-analytics.timer.example \
  /etc/systemd/system/winit-analytics.timer
sudo systemctl daemon-reload
sudo systemctl enable --now winit-analytics.timer
```

查看执行情况：

```bash
systemctl list-timers | grep winit
journalctl -u winit-analytics.service -n 80 --no-pager
```

如需改执行时间，编辑 `winit-analytics.timer` 里的 `OnCalendar=` 后执行 `daemon-reload` 并 `restart winit-analytics.timer`。

---

## 4. 发布更新（不影响 myapp）

在服务器：

```bash
cd /opt/winit-analytics
git pull
source .venv/bin/activate
pip install -r requirements.txt
# 若 Playwright 有升级：playwright install chromium
```

若使用 **oneshot 服务**，一般 **无需** `restart myapp`；仅在有定时器时确保 timer 仍 `enabled` 即可。

可选：把本仓库 `deploy/deploy.sh.example` 复制为 `/opt/winit-analytics/deploy.sh` 并 `chmod +x`，内容仅为 `git pull` + `pip install`，**不要** 写 `systemctl restart myapp`。

---

## 5. 与 myapp「对接」的推荐方式

| 方式 | 说明 |
|------|------|
| **松耦合（当前）** | 两台项目同机、各跑各的；数据分析脚本、定时任务全部在 `/opt/winit-analytics`。 |
| **以后由 myapp 触发** | 在 myapp 里增加「调用子进程」或「请求本机另一端口」的逻辑时，再单独改 myapp 代码；winit 侧可先提供 CLI 入口（如 `python -m ...`）。 |
| **不推荐** | 把 winit 代码直接塞进 `/opt/myapp` 并共用同一 Gunicorn 进程 — 依赖与超时策略混在一起，排障困难。 |

---

## 6. 资源与 Playwright

- 与 myapp 一样，长时间跑浏览器会占 CPU/内存；定时任务错开业务高峰即可。
- 若 myapp 已安装 Chromium 依赖，winit 仍建议在 **本目录 venv** 内执行 `playwright install chromium`，避免路径不一致。

---

## 7. 故障时如何确认「没有动到 myapp」

```bash
systemctl status myapp --no-pager
curl -sS -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/
```

上述与部署 winit **前** 行为一致即表示 myapp 未受影响。
