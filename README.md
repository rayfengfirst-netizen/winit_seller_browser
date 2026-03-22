# winit 数据分析

在万邑通卖家后台（`seller.winit.com.cn`）完成浏览器自动化登录，为后续数据采集与分析提供会话基础。

## 与 myapp 的关系

| 项目 | 服务器路径（约定） | 进程 |
|------|-------------------|------|
| **myapp** | `/opt/myapp` | `systemd: myapp` → Gunicorn `127.0.0.1:8000`，Nginx 反代对外 |
| **本项目** | `/opt/winit-analytics` | 独立虚拟环境；默认 **不** 占用 8000 端口，**不** 改 myapp 的 unit / Nginx |

两者仅共用同一台机器与系统级 Chromium 依赖（若已给 myapp 装过 Playwright，可复用或在本项目 venv 内再执行一次 `playwright install chromium`）。

## 仓库目录

- `login_winit.py` — 登录脚本（读 `.env`）
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

见 [DEPLOY_WITH_MYAPP.md](./DEPLOY_WITH_MYAPP.md)。

## v0（连通 + 推送 + 服务器跑通）

按 [V0_SETUP.md](./V0_SETUP.md) 使用 `scripts/verify_local.sh`、`verify_ssh.sh` 与 `verify_server_run.sh`。
