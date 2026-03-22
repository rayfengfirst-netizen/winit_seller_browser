# v0：本地 ↔ 服务器连通、能推送、服务器能跑

## 先搞清楚：命令要在「哪里」敲？

只有两种地方，不要混：

| 地方 | 是什么 | 怎么进去 |
|------|--------|----------|
| **① 本机（你的 Mac）** | 放代码的文件夹 `winit_seller_browser` | 打开终端，先 `cd` 进这个文件夹 |
| **② 服务器（Linux）** | 以后代码在 `/opt/winit-analytics` | 终端里执行 `ssh root@服务器IP`，登录后再 `cd` |

下面每一步都会写清楚：**① 本机** 还是 **② 服务器**，以及**必须先 `cd` 到哪里**。

---

## 步骤 1 — 本机：检查项目是否正常

**在哪里：** ① 本机（Mac）  
**必须先进入项目文件夹**（路径按你实际位置改，下面是你当前常见路径）：

```bash
cd /Users/fengchangrui/Desktop/cursor/winit_seller_browser
chmod +x scripts/*.sh
./scripts/verify_local.sh
```

看到 **`LOCAL_OK`** 就过。  
（说明：必须在 `winit_seller_browser` 里执行，因为用的是 `./scripts/...`。）

---

## 步骤 2 — 本机：看和服务器 SSH 通不通

**在哪里：** ① 本机（Mac）  
**仍然要先：**

```bash
cd /Users/fengchangrui/Desktop/cursor/winit_seller_browser
```

**再执行**（把 `服务器IP` 换成真实 IP，例如 `8.218.58.28`）：

```bash
./scripts/verify_ssh.sh root@服务器IP
```

看到 **`SSH_OK`** 就过。

---

## 步骤 3 — 本机：Git 初始化并推送到 GitHub（只做一次）

**在哪里：** ① 本机（Mac）  
**先在网页上**到 GitHub（或 Gitee）**新建一个空仓库**（例如名：`winit-analytics`）。

**再在终端：**

```bash
cd /Users/fengchangrui/Desktop/cursor/winit_seller_browser
git init
git checkout -b main
git add README.md login_winit.py requirements.txt .env.example .gitignore \
  DEPLOY_WITH_MYAPP.md V0_SETUP.md scripts deploy
git commit -m "chore: v0 baseline"
git remote add origin git@github.com:你的GitHub用户名/winit-analytics.git
git push -u origin main
```

把 `你的GitHub用户名`、仓库地址改成你自己的。  
**不要**提交 `.env`（里面密码不要进 Git）。

---

## 步骤 4 — 服务器：把代码拉下来并安装环境

**在哪里：** ② 服务器  

**4.1 用本机终端登录服务器**（还在 Mac 上敲）：

```bash
ssh root@服务器IP
```

登录成功后，提示符会变成服务器上的，**下面命令都在服务器上敲**。

**4.2 在服务器上克隆仓库**（把仓库地址改成你的）：

```bash
mkdir -p /opt
cd /opt
git clone git@github.com:你的GitHub用户名/winit-analytics.git winit-analytics
cd /opt/winit-analytics
```

**4.3 仍在服务器、且当前目录已是 `/opt/winit-analytics`**，安装 Python 环境：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
playwright install-deps chromium
```

**4.4 配置账号密码（仍在 `/opt/winit-analytics`）**：

```bash
cp .env.example .env
nano .env
chmod 600 .env
```

在 `.env` 里填好 `WINIT_USERNAME`、`WINIT_PASSWORD`，并加上一行：

```env
WINIT_HEADLESS=true
```

保存退出 `nano`（Ctrl+O 回车，Ctrl+X）。

---

## 步骤 5 — 服务器：跑登录脚本，确认能跑通

**在哪里：** ② 服务器  
**必须先：**

```bash
cd /opt/winit-analytics
source .venv/bin/activate
```

**再执行任选一种：**

```bash
./scripts/verify_server_run.sh
```

或：

```bash
WINIT_HEADLESS=true python login_winit.py
echo $?
```

最后一行 **`echo $?`** 显示 **0** 且上面有成功提示，v0 就算完成。

---

## （可选）在本机一条命令让服务器跑步骤 5

**在哪里：** ① 本机（Mac）  
**必须先：**

```bash
cd /Users/fengchangrui/Desktop/cursor/winit_seller_browser
```

**再执行**（IP 换成你的）：

```bash
export WINIT_REMOTE=root@服务器IP
./scripts/verify_server_remote.sh
```

这会在服务器上自动 `cd /opt/winit-analytics` 并执行 `verify_server_run.sh`。  
若你服务器上的路径不是 `/opt/winit-analytics`，可再设：

```bash
export WINIT_REMOTE_DIR=/你的路径
```

---

## 小结：顺序背这个就行

1. **Mac** → `cd` 进 `winit_seller_browser` → `verify_local.sh`  
2. **Mac** → 仍在该目录 → `verify_ssh.sh`  
3. **Mac** → 仍在该目录 → `git init` / `push`  
4. **ssh 上服务器** → `cd /opt/winit-analytics` → 装 venv、依赖、`.env`  
5. **仍在服务器** → `cd /opt/winit-analytics` + `source .venv/bin/activate` → 跑 `login_winit.py` 或 `verify_server_run.sh`  

更细的部署（定时任务、和 myapp 同机）见 [DEPLOY_WITH_MYAPP.md](./DEPLOY_WITH_MYAPP.md)。
