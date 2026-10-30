# 雨课堂课件导出

将当前学生账号可见的一节课堂课件图片整理为 PDF，按课堂播放记录中首次出现的顺序排列，完全相同的页面只保留一次。同时保存播放顺序、原课件页码、时长与去重映射，便于学习和回查。

支持官方扫码登录、选择课程与课堂、断点缓存、离线重建 PDF，以及额外生成保留回翻的版本。程序使用学生端可见数据，跳过不可见页面，不获取教师端隐藏文件；仍在上课的课堂需等结束后导出。

本仓库提供程序和合成数据测试，不附带账号、登录状态或真实课件。

## 快速开始（Windows）

1. 安装 Python 3.11，安装时保留 Python Launcher 和 Tcl/Tk 支持。
2. 下载并解压本仓库，双击 `install.cmd`。它会在本目录创建 `.venv`，安装 Python 依赖及 Playwright 无头浏览器，首次安装需要联网。
3. 双击 `start.cmd`，根据提示输入课程名，选择一节已结束的课。默认课程名为“计算机网络”，默认课堂为最近一节已结束的课。
4. 初次使用会弹出二维码窗口，用微信或雨课堂 App 扫码并在手机上确认登录。登录状态有效时会直接复用。

导出资料默认保存在本目录的 `output/` 中。二维码窗口需要图形桌面，不适合在无桌面的服务器上交互登录。

## 命令行用法

以下命令均在仓库根目录的 PowerShell 中执行。完成 `install.cmd` 后，使用本目录的虚拟环境：

```powershell
# 交互选择课程和课堂
.\.venv\Scripts\python.exe -X utf8 .\yuketang_export.py wizard

# 初次登录或登录失效后重新扫码
.\.venv\Scripts\python.exe -X utf8 .\yuketang_export.py login

# 查看课程和课堂列表
.\.venv\Scripts\python.exe -X utf8 .\yuketang_export.py courses
.\.venv\Scripts\python.exe -X utf8 .\yuketang_export.py lessons --course '课程名称'

# 把“课堂ID”替换为 lessons 输出的 id
.\.venv\Scripts\python.exe -X utf8 .\yuketang_export.py export --course '课程名称' --lesson-id '课堂ID'

# 不指定课堂 ID，导出该课程最近一节已结束的课
.\.venv\Scripts\python.exe -X utf8 .\yuketang_export.py export --course '课程名称'

# 额外生成保留回翻的 PDF
.\.venv\Scripts\python.exe -X utf8 .\yuketang_export.py export --course '课程名称' --lesson-id '课堂ID' --keep-duplicates

# 使用已导出的清单与图片离线重建 PDF；不需要登录
.\.venv\Scripts\python.exe -X utf8 .\yuketang_export.py rebuild --manifest '.\output\课堂目录\manifest.json'
```

按名称选择课堂可用 `--title '课堂名称'`，匹配结果须唯一；同名课程可以用 `--classroom-id '班级ID'` 区分。`--out '目标目录'` 可改变导出位置。`rebuild` 命令加 `--keep-duplicates` 时生成保留回翻的版本。

也可以手动安装和运行：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium --only-shell
.\.venv\Scripts\python.exe -X utf8 .\yuketang_export.py wizard
```

## 输出文件

| 文件 | 用途 |
| --- | --- |
| `课件汇总_去重.pdf` | 每页完整放置一张课件图片，保持宽高比与分辨率 |
| `images/` | 去重后的图片；适合直接保留的 JPEG 保存原始字节，其他情况转为无损 PNG |
| `播放顺序与去重.csv` | 保留每次播放、原页码、对应 PDF 页码、时间和时长 |
| `manifest.json` | 课堂信息、图片校验值及页码映射，支持离线重建 |
| `.cache/` | 图片下载缓存，重复运行可复用 |
| `课堂顺序_含回翻.pdf` | 仅在使用 `--keep-duplicates` 时额外生成 |

播放顺序以接口原始记录为准。`sequence` 是播放记录序号，`pdf_page` 是去重后的页码，`duplicate_of_sequence` 指向同图首次出现的记录，`slide_index` 是平台返回的原课件页码。时间戳为毫秒，`relative_seconds` 为相对开课时间的秒数。

平台 `firstTime` 可能始终指向该页第一次播放，程序不会据此重新排序；清单保留该字段及 `dt` 对应的事件时间，以便核对。

## 去重规则

图片解码后，程序统一 EXIF 显示方向、白色底色与 RGB 像素，对宽、高和完整像素计算 SHA-256。文件名、附加元数据和临时图片地址不参与比较。

例如 A → B → A，会生成 A、B 两页，同时保留三条播放记录。一个像素、批注或动画状态发生变化都会分别保留。轻微压缩差异也可能使页面不被合并；程序不使用相似图片的模糊去重。

JPEG 可直接嵌入 PDF 时不会再次有损压缩。PDF 主要由图片构成，不附带 OCR 文字层，也不能恢复源 PPT 中的可编辑对象和动画。课件与播放记录能支持按页学习；老师的逐字讲述、口头举例和未记录的板书，需要另有录音或笔记。

## 登录状态与仓库管理

扫码后，登录 Cookie 等状态仅保存在 `.local/session.json`，程序不要求输入或保存账号密码。该目录和全部导出资料已列入 `.gitignore`。

准备提交时可在本机检查：

```powershell
git status --short
git check-ignore .local/session.json output/example/manifest.json
```

`.gitignore` 对已经被 Git 跟踪的文件不生效。上传前检查暂存区，只提交源码与说明。若登录数据曾被提交，应在平台撤销相应登录，并处理仓库历史；单纯删除当前文件不会移除历史记录。

只导出自己有权查看的课堂内容，分享课件前确认相应使用权限。

## 测试与已知边界

```powershell
.\.venv\Scripts\python.exe -X utf8 -m unittest -v test_export.py
```

测试使用程序内生成的图片和模拟接口，覆盖像素相同但元数据不同的去重、单像素变化保留、原始顺序映射、不可见页跳过、缓存复用及失败续跑。测试不登录真实账号，也不访问雨课堂。

GitHub Actions 对 Python 3.11、3.12 配置了相同的离线测试；工作流是否通过，以仓库上传后的实际运行结果为准。

发生下载失败时，程序保留缓存并写入 `manifest.partial.json`。再次执行同一导出命令可重新获取临时图片地址并继续。只有数据处理完成才生成正式清单并尝试生成 PDF。若目录中已有上次成功导出的 PDF，应同时检查本次退出状态及清单更新时间。

本工具曾于 2026-10-21 完成真实课堂导出验证。仓库整理未重新验证线上接口；网站后续改版可能需要同步修改字段、接口或扫码入口。登录失效时先执行 `login`；提示结构变化时，应检查实际页面后再更新代码。

## 文件结构

```text
.
├── yuketang_export.py          # 程序入口
├── test_export.py             # 离线测试
├── requirements.txt           # Python 依赖
├── install.cmd                # Windows 首次安装
├── start.cmd                  # Windows 交互启动
├── .gitignore                 # 排除登录状态、导出资料和缓存
├── .gitattributes             # 统一文本与 Windows 启动脚本换行
└── .github/workflows/tests.yml # 离线测试工作流
```
