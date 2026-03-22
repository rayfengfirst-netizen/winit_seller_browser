# winit 数据分析 — 小白操作手册（本地 / GitHub / 服务器）

把这份当作**总说明书**。你每次不确定「在哪台机器敲命令」时，先看第一节的表。

---

## 一、先搞懂：东西放在哪三台「地方」

| 地方 | 是什么 | 你平时干什么 |
|------|--------|----------------|
| **① 你的 Mac（本机）** | 你电脑上的文件夹 | 用 Cursor 改代码、保存文件、用终端 `git` 推到 GitHub |
| **② GitHub（网站）** | 代码的「网盘 + 版本记录」 | 存代码历史；换电脑也能 `clone` 下来 |
| **③ 线上服务器** | 和 **myapp 同一台**：`8.218.58.28` | 真正「自动跑登录脚本」的地方，目录是 **`/opt/winit-analytics`** |

**重要：**  
- Mac 上有路径：`/Users/fengchangrui/Desktop/cursor/winit_seller_browser`  
- **服务器上没有** `/Users/...`！服务器上只有 Linux 路径，例如 `/opt/winit-analytics`。

---

## 二、这个项目是干什么的

- **名字：** winit 数据分析（仓库名：`winit_seller_browser`）  
- **现在做的事：** 用 Python + Playwright **自动打开万邑通卖家后台并登录**（为以后抓数据、分析打基础）。  
- **和 myapp：** 同一台服务器，但 **不同文件夹、不同虚拟环境**，一般不互相影响（见文末「和 myapp」）。

---

## 三、本机（Mac）：怎么改代码、怎么「保存代码」到 GitHub

「保存代码」分两层：

1. **保存文件**：在 Cursor 里 `Cmd + S`，只保存在你电脑上。  
2. **保存到 GitHub**：还要在下面终端里做 `git add`、`git commit`、`git push`，**别人和服务器才能通过 Git 拿到最新版**。

### 3.1 每次改了代码，推到 GitHub（照抄即可）

**在哪里操作：** ① **Mac**，打开「终端」应用。

**第一步：进入项目文件夹**

```bash
cd /Users/fengchangrui/Desktop/cursor/winit_seller_browser
```

**第二步：看改了哪些文件**

```bash
git status
```

**第三步：把改动加入本次提交（不要提交密码文件）**

```bash
git add -A
```

确认 **没有** 把 `.env` 加进去（一般 `.env` 在 `.gitignore` 里，不会出现；若 `git status` 里出现 `.env`，**不要** `commit`，先检查）。

**第四步：写一句说明 + 提交**

```bash
git commit -m "说明：你这次改了什么，用中文也行"
```

若提示 `nothing to commit`，说明**没有新改动**，不用再 push。

**第五步：推到 GitHub**

```bash
git push
```

看到 `Everything up-to-date` 或上传进度走完，就成功了。

### 3.2 本机想自己跑一遍登录（有浏览器界面）

```bash
cd /Users/fengchangrui/Desktop/cursor/winit_seller_browser
source .venv/bin/activate
WINIT_HEADLESS=false python login_winit.py
```

账号密码在 **本机** 的 `.env` 里（和服务器上的 `.env` **不是同一个文件**，两边要分别改）。

---

## 四、服务器：怎么部署、怎么更新代码

### 4.1 你已经在服务器上装过一次了

目录固定为：

```text
/opt/winit-analytics
```

里面有：`git 拉下来的代码`、`.venv`、`.env`（**只在服务器上**，不要提交到 GitHub）。

### 4.2 以后每次：本机 push 之后，让服务器和 GitHub 一致

**在哪里操作：** 先 **SSH 登录服务器**（在 Mac 终端里）：

```bash
ssh root@8.218.58.28
```

登录成功后，提示符会变成 `root@xxxx`，**下面命令都在服务器上执行**：

```bash
cd /opt/winit-analytics
git pull origin main
source .venv/bin/activate
pip install -r requirements.txt
```

- 若 **`requirements.txt` 没改**，`pip install` 会很快，几乎无变化。  
- 若 **Playwright 大版本升级**，有时需要再执行：`playwright install chromium`

**不要**在服务器上执行 `systemctl restart myapp`，那是 **myapp** 的服务，和本项目无关。

### 4.3 服务器上改密码 / 账号

只在服务器改这个文件：

```bash
cd /opt/winit-analytics
nano .env
```

改完 `Ctrl+O` 回车保存，`Ctrl+X` 退出。  
**不要**把服务器上的 `.env` 复制进 Git 再 push。

### 4.4 在服务器上手动跑一次登录（检查是否正常）

```bash
cd /opt/winit-analytics
source .venv/bin/activate
WINIT_HEADLESS=true python login_winit.py
echo $?
```

最后一行 **`0`** 一般表示脚本按成功路径结束。

---

## 五、常见情况对照

| 你想做的事 | 在哪做 | 命令/操作 |
|------------|--------|-----------|
| 改 Python/文档 | Mac，Cursor | 编辑后 `Cmd+S` |
| 把改动同步到 GitHub | Mac，终端 | `cd` 项目 → `git add` → `git commit` → `git push` |
| 让服务器代码和 GitHub 一样 | Mac 先 `ssh`，再在服务器 | `cd /opt/winit-analytics` → `git pull` → `pip install -r requirements.txt` |
| 测本机能否 SSH 到服务器 | Mac，项目目录 | `./scripts/verify_ssh.sh root@8.218.58.28` |
| 本机检查项目文件是否正常 | Mac，项目目录 | `./scripts/verify_local.sh` |

---

## 六、和 myapp 会不会互相干扰

- **目录不同：** myapp → `/opt/myapp`，本项目 → `/opt/winit-analytics`。  
- **Python 环境不同：** 各自 `source` 各自的 `.venv`。  
- **端口：** myapp 占用 **8000**（网站）；本项目默认 **不** 开网站端口。  
- **可能一起抢的只有：** CPU、内存（例如两边同时跑很重的浏览器任务时会变慢）。

---

## 七、其它文档（进阶/细节）

| 文档 | 内容 |
|------|------|
| [SERVER_QUICKSTART.md](./SERVER_QUICKSTART.md) | 服务器第一次从 GitHub 克隆、装依赖（你已做过可当备查） |
| [V0_SETUP.md](./V0_SETUP.md) | v0 自检：本机/SSH/推送/服务器跑通 |
| [DEPLOY_WITH_MYAPP.md](./DEPLOY_WITH_MYAPP.md) | 与 myapp 同机、systemd 定时示例 |

---

## 八、你下次可以怎么问我

直接说你的目标，例如：

- 「我在 Mac 上改好了，怎么推到 GitHub？」  
- 「GitHub 更新了，服务器怎么拉？」  
- 「服务器上登录失败，看哪里的日志？」  

我会按 **① Mac 还是 ③ 服务器** 分步写给你，避免再混用 `/Users/...` 路径。
