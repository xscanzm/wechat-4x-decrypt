---
name: wechat-4x-decrypt
description: 在 Windows 上解密并检索微信 4.x（Weixin.exe / xwechat_files / db_storage）的本地聊天数据库，用于导出、搜索、总结聊天记录。当用户要求"从微信里找/总结/导出聊天记录"、"解密微信数据库"、"读取微信本地消息"、"分析某个群最近的讨论"时使用。要求微信正在运行且已登录（密钥从进程内存提取）。微信 3.x 不适用，改用 pywxdump。
agent_created: true
---

> **出品**：skill 出品 · 周一 · 皮皮未来

# 微信 4.x 数据库解密与消息检索

流程：`探测环境 → 装依赖 → 提密钥 → 解密 → 检索导出`。

本目录脚本（均为纯标准库，无第三方依赖）：
- `scripts/find_wechat.py` — 自动探测版本 / 进程 / 数据目录 / 活跃账号
- `scripts/query_messages.py` — 在解密结果中按关键词与时间窗检索并导出

---

## 1. 环境探测（先跑这个）

```bash
python scripts/find_wechat.py
```

输出数据根目录、各账号 `db_storage` 路径及**最近写入时间**（最新的即当前登录账号），并直接给出可复制的 `--db-dir`。

退出码：`0` 可继续 ／ `2` 只有 3.x（本 skill 不适用）／ `3` 微信未运行或找不到目录。

进程名对照（找错进程是最常见的时间浪费）：

| 进程 | 含义 |
|---|---|
| `Weixin.exe` | **微信 4.x 主进程 ← 目标** |
| `WeChat.exe` | 微信 3.x 主进程 → 本 skill 不适用 |
| `WeChatAppEx.exe` | 小程序运行时 → 不是目标 |
| `wechatdevtools.exe` | 开发者工具 → 不是目标 |

**全程保持微信运行且登录**，密钥只存在于进程内存，退出即失效。

---

## 2. 安装依赖

**不需要任何第三方 Python 包。** AES 走 Windows 系统自带的 `bcrypt.dll`（CNG，经 ctypes 调用），
HMAC/PBKDF2 用标准库 `hashlib`。**不要装 pycryptodome / cryptography**——装了也用不上。
只需两样：Python 3.10+ 和 `wcdb_key_tool_windows.py` 这一个文件。

### 2.1 Python 3.10+

```bash
python -V || py -V
ls ~/.workbuddy/binaries/python/versions/    # 受管解释器，存在则优先用
```

都没有：优先用 `install_binary` 工具装 Python 3.13，否则从 python.org 安装。
后续命令中的 `python` 一律替换为实际解释器**绝对路径**（受管解释器不要用裸 `python`）。

需 3.10+ 的原因：工具用了 `X | Y` 形式的类型标注。

### 2.2 取密钥工具

```bash
git --version || echo "需先装 git"
git clone --depth 1 https://github.com/TANGandXUE/wcdb-key-tool.git
```

无 git 时下载 zip 解压即可，实际只用到 `wcdb_key_tool_windows.py` 单文件：
`https://github.com/TANGandXUE/wcdb-key-tool/archive/refs/heads/main.zip`

### 2.3 验证

```bash
python -c "import ctypes, hashlib, hmac, sqlite3; ctypes.WinDLL('bcrypt'); print('deps OK')"
python wcdb-key-tool/wcdb_key_tool_windows.py --help
```

两条都不报错才继续。`WinDLL('bcrypt')` 失败说明不在 Windows 或系统组件缺失——本 skill 仅支持 Windows。

---

## 3. 提取密钥

```bash
cd wcdb-key-tool
python wcdb_key_tool_windows.py extract \
  --db-dir "<步骤1输出的 db_storage 路径>" \
  --output all_keys.json
```

预期：秒级完成，命中数≈数据库总数。少数 `migrate\*.db`（如 `unspportmsg.db`）取不到属正常，不影响聊天记录。

**原理**（命中为 0 时据此排查）：微信 4.1+ 内存中**没有明文密钥**，只有一个 WCDB passphrase 对象。工具的做法是定位 `com.Tencent.WCDB.Config.Cipher` 字符串对象 → 沿对象链取 blob → XOR 解码（27 字节掩码）→ 得到 `x'<key><salt>'` → HMAC-SHA512 校验。

因此**任何依赖"内存里搜 `x'...'` 明文"或"取 salt 相邻 32 字节"的老方案（pywxdump / NTLx-wechat-decrypt / ZedeX 脚本）在 4.1+ 上必然 0 命中**，不要在这些路线上排查。

---

## 4. 解密

```bash
python wcdb_key_tool_windows.py decrypt \
  --keys all_keys.json \
  --output decrypted
```

产出明文 SQLite 到 `decrypted/`，按 `message/`、`session/`、`contact/` 等子目录组织。

---

## 5. 检索与导出

```bash
# 先看有哪些会话（不导出正文）
python scripts/query_messages.py --decrypted-dir decrypted \
  --keywords "AI,招聘,HR" --since 2026-07-29 --until 2026-08-12 --list

# 导出前 N 个会话的正文
python scripts/query_messages.py --decrypted-dir decrypted \
  --keywords "AI,招聘,HR" --since 2026-07-29 --until 2026-08-12 \
  --top 10 --out-dir out

# 导出单个会话全部消息（不过滤关键词；--session 支持微信号、备注名、昵称或原始 username）
python scripts/query_messages.py --decrypted-dir decrypted \
  --session "53618658790@chatroom" --since 2026-07-29 --out-dir out
```

产出：
- `out/digest.txt`：按会话分组、时间排序、含发言人与时间戳的可读摘录；
- `out/matches.json`：全量结构化结果，供复查与二次处理；
- `out/chat.csv`：UTF-8 BOM 编码的表格文件，可直接用 Excel 双击打开分析。

默认 `--max-per-session 0` 为全量无截断导出（可传入正整数限制单会话条数）。

做总结时**以 digest.txt 为唯一事实来源**，每个结论都能回溯到发言人与时间戳，不要凭印象补写。

---

## 6. 数据库结构参考（自行写查询时用）

解密后为 WCDB 风格，表名与 ID 均做过哈希/映射：

| 需要什么 | 位置 |
|---|---|
| **消息正文** | `message_fts.db` 的 `message_fts_v4_*` 表，`acontent` 列已是解码后文本 |
| 会话 / 发言人 ID 还原 | `message_fts.db` 的 `name2id` 表，**rowid** ↔ username |
| 群名 / 好友名 | `contact.db` 的 `contact` 表，优先 `remark` 再 `nick_name` |
| 会话列表与末条预览 | `session.db` 的 `SessionTable`（`summary` = 末条预览） |
| 原始消息表 | `message_*.db` 的 `Msg_<md5(username)>`，正文是 protobuf blob |

四个已踩过的坑，写查询时务必注意：

1. **正文表是分片的**：`message_fts_v4_0` ~ `_3` 四张，实测共 24 万行而第一张仅 3.8 万行。只读第一张会漏掉 ~84% 消息，表现为"某个明明很活跃的群一条都搜不到"。必须全部遍历合并，并跳过 FTS5 影子表（`_data` / `_idx` / `_docsize` / `_config` / `_content`）。
2. **ID 映射只认 `message_fts.db` 的 `name2id`**：不要用 `message_resource.db` 的 `ChatName2Id` / `SenderName2Id`，那两张表只覆盖部分会话，高位 ID 会解析成 `?sid1234`。
3. **群名不要取 `SessionTable.summary`**：那是最后一条消息预览，拿来当群名会得到一堆聊天内容片段。
4. **联系人微信号与内部 username 不一致**：用户日常使用的微信号通常是 `alias` 或备注名（`remark`），而数据库底层所有会话索引均使用内部 `username`（如 `wxid_xxx` 或个性化短 ID）。`query_messages.py` 已内置双向索引映射，支持直接传入微信号/备注/昵称查询；若自行写 SQL 查询，必须先查 `contact.db` 映射出 `username` 再关联 `name2id`。
5. `create_time` 为 Unix 秒。

---

## 7. 故障排查

| 现象 | 原因 | 处理 |
|---|---|---|
| 找不到 `Weixin.exe` | 微信未运行 / 版本是 3.x | 启动并登录微信；3.x 改用 pywxdump |
| extract 命中 0 | 微信版本高于工具适配，WCDB 对象偏移变了 | 看工具打印的 needle→node→candidate 计数，判断链条断在哪一步；needle 为 0 说明特征串没找到，需按新版本调偏移 |
| extract 命中但 decrypt 报 HMAC 失败 | 密钥与该库不匹配 / 库正被写入 | 重跑 extract；必要时复制 db 到别处再解 |
| 找不到 `*fts*.db` | 解密未成功或输出目录给错 | 检查 `decrypted/` 是否真有文件 |
| 活跃群搜不到消息 | 只扫了第一张 FTS 分片 | 见第 6 节坑 1 |
| 会话名显示为 `?sid1234` | ID 映射表用错 | 见第 6 节坑 2 |
| 群名是一段聊天内容 | 误用 `summary` 当名字 | 见第 6 节坑 3 |
| 搜微信号/备注名无结果 | 误用 alias/remark 过滤内部 username | 脚本已支持智能解析；若自行编写需查 contact.db 转换（见第 6 节坑 4） |
| `ImportError: No module named Crypto` | 误按旧说明装/引用了 pycryptodome | 本流程零第三方依赖，不需要它；检查是否用了非官方改版脚本 |
| `WinDLL('bcrypt')` 失败 | 非 Windows 环境 | 本 skill 仅支持 Windows |
| `TypeError: unsupported operand ... |` | Python < 3.10 | 换 3.10+ 解释器 |
| bash 里 `powershell -Command` 被拦 | 沙箱策略 | 改用专用 PowerShell 工具调用 |
| 若确需 pip 装包（本流程不需要） | 沙箱清缓存抛 `OSError` | 加 `--no-cache-dir` |

---

## 8. 加密参数（需自行实现解密时）

SQLCipher 4 变体，AES-256-CBC + HMAC-SHA512：

- `page_size = 4096`，`reserve = 80`（IV 16 + HMAC 64）
- `salt` = 每个 db 文件 page1 的前 16 字节
- 逐页解密 IV = `page[4016:4032]`，即 `PAGE_SZ - RESERVE : PAGE_SZ - RESERVE + 16`
- MAC key 派生：`PBKDF2-HMAC-SHA512(enc_key, salt XOR 0x3a, iter=2, dklen=32)`
- 校验密钥是否正确：**只信 HMAC 校验**，不要用"解出的头是不是 `SQLite format 3\0`"当唯一判据

---

## 9. 合规边界

仅限用户本机、用户自己的账号、且用户明确要求时操作。不读取与任务无关的会话，不将聊天内容传给第三方服务，不在产出里留存无关个人隐私信息。
