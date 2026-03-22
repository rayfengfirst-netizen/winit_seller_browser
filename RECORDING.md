# 用 Playwright 录制器生成代码（和 myapp / 店小秘那边一样）

你要的是：**打开 Playwright Codegen（录制器）→ 在网页里点元素 → 右侧自动生成 Python 代码 → 复制给我**。

这和在 myapp 里手动执行 `playwright codegen ...` 是同一套工具；本项目里已经做成**一条脚本**，避免你记长命令。

---

## 1. 一条命令启动录制器（本机 Mac）

在终端执行：

```bash
cd /Users/fengchangrui/Desktop/cursor/winit_seller_browser
chmod +x scripts/winit_codegen.sh
./scripts/winit_codegen.sh
```

默认会打开 **`https://seller.winit.com.cn/Australia/index`**。若要别的起始页：

```bash
./scripts/winit_codegen.sh "https://seller.winit.com.cn/某页面"
```

会出现：

- **一个浏览器窗口**（你点页面）
- **Playwright Inspector**（里面会实时出现 **Python 代码**）

你在页面上**点击、输入**，下面代码区会跟着变。**把 Inspector 里生成的代码复制**出来发给我即可。

关闭录制器窗口或终端里 `Ctrl+C` 结束。

---

## 2.（可选）先保存登录态，录制时不用再登录

第一次若没有 Cookie，录制器里要先手动登录。可以像 myapp 一样先存一份登录态：

```bash
cd /Users/fengchangrui/Desktop/cursor/winit_seller_browser
source .venv/bin/activate
WINIT_HEADLESS=false python save_winit_storage.py
```

浏览器里登录万邑通 → 回车 → 会生成 **`.playwright/winit_storage.json`**（已加入 `.gitignore`，不会进 Git）。

之后**再**执行：

```bash
./scripts/winit_codegen.sh
```

脚本会自动带上 `--load-storage`，一般已处于登录状态。

---

## 3. 与 `record_manual.py` 的区别

| 脚本 | 作用 |
|------|------|
| **`scripts/winit_codegen.sh`** | **Playwright 官方录制器**，会**生成可复制代码**（你要的） |
| `record_manual.py` | 只保持浏览器打开方便录**屏幕**，**不自动生成 locator 代码** |

---

## 4. 依赖

确保已安装：

```bash
pip install -r requirements.txt
playwright install chromium
```
