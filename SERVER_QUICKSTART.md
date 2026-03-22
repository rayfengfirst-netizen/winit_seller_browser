# 把 GitHub 代码部署到线上服务器（首次）

服务器与 **myapp 同机**：`root@8.218.58.28`（与 `myapp/README_DEPLOY.md` 一致；若以后换 IP 以该文档为准）。  
**不要**动 `/opt/myapp`。本项目放在 **`/opt/winit-analytics`**（目录名固定，方便和 `deploy/*.example` 一致；仓库名可以是 `winit_seller_browser`）。

**仓库地址：** https://github.com/rayfengfirst-netizen/winit_seller_browser  

**容易混的一点：** 登录服务器后，**没有** Mac 上的路径（例如 `/Users/xxx/...`）。  
从 **§2** 起的 `cd`、`git clone` 都在 **Linux 服务器**里执行，项目目录是 **`/opt/winit-analytics`**。  
`scripts/verify_ssh.sh` 只在 **Mac 本机** 的项目文件夹里用来测 SSH，**不要在服务器上跑**。

---

## 1. 登录服务器

在本机 Mac 终端（与 **myapp** 同一台，见 `myapp/README_DEPLOY.md`）：

```bash
ssh root@8.218.58.28
```

---

## 2. 克隆代码

在服务器上执行：

```bash
mkdir -p /opt
cd /opt
git clone https://github.com/rayfengfirst-netizen/winit_seller_browser.git winit-analytics
cd /opt/winit-analytics
```

说明：最后一个参数 `winit-analytics` 是**本地文件夹名**；内容来自 GitHub 上的 `winit_seller_browser` 仓库。

若服务器已能 `git clone` 用 SSH，也可：

```bash
git clone git@github.com:rayfengfirst-netizen/winit_seller_browser.git winit-analytics
```

（需先在服务器配置 GitHub SSH 密钥。）

---

## 3. 安装 Python 依赖与浏览器

仍在 `/opt/winit-analytics`：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
playwright install-deps chromium
```

Ubuntu/Debian 上 `install-deps` 可能需要 root，已在 root 下则无妨。

---

## 4. 配置环境变量（不上传 GitHub）

```bash
cd /opt/winit-analytics
cp .env.example .env
nano .env
chmod 600 .env
```

填写 `WINIT_USERNAME`、`WINIT_PASSWORD`，并加上：

```env
WINIT_HEADLESS=true
```

---

## 5. 验证能否跑通

```bash
cd /opt/winit-analytics
source .venv/bin/activate
./scripts/verify_server_run.sh
```

或：

```bash
WINIT_HEADLESS=true python login_winit.py
echo $?
```

退出码为 **0** 即表示线上环境与登录脚本正常。

---

## 6. 以后更新代码（只拉取、不碰 myapp）

```bash
cd /opt/winit-analytics
git pull origin main
source .venv/bin/activate
pip install -r requirements.txt
```

按需再执行 `playwright install chromium`。**无需** `systemctl restart myapp`。

---

更多：与 myapp 同机隔离说明见 [DEPLOY_WITH_MYAPP.md](./DEPLOY_WITH_MYAPP.md)。
