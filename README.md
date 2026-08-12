# 微信 4.x 本地聊天记录解密与检索 Skill

> **skill 出品 · 周一 · 皮皮未来**
>
> 一个面向 [WorkBuddy](https://www.workbuddy.cn) / Claude Code / Codex 等 AI Agent 的 Skill，用于在 **Windows 本机**上解密并检索 **微信 4.x**（Weixin.exe / xwechat_files / db_storage）的本地聊天数据库，从而导出、搜索、总结自己的聊天记录。

---

## 一、这是什么

微信 4.x 把聊天记录存成本地加密的 SQLite 数据库（WCDB / SQLCipher 变体）。本 Skill 提供一套**零第三方依赖**（仅用 Windows 自带 `bcrypt.dll` + Python 标准库）的纯脚本流程，让 AI Agent 能：

1. 自动探测当前运行的微信版本、进程、数据目录与活跃账号；
2. 从**正在运行的微信进程内存**中提取解密密钥；
3. 把加密库批量解密成明文 SQLite；
4. 按关键词 + 时间窗检索、导出、总结聊天内容。

> ⚠️ **仅支持微信 4.x（Weixin.exe）**。微信 3.x（WeChat.exe）请改用 [pywxdump](https://github.com/xaoyaoo/PyWxDump)。
> ⚠️ **仅支持 Windows**。密钥提取依赖 Windows CNG（`bcrypt.dll`）。

---

## 二、适用 / 不适用

| 适用场景 | 不适用 |
|---|---|
| 导出自己的微信聊天记录做备份 | 微信 3.x（用 pywxdump） |
| 在本地检索某段对话、某个群 | 非 Windows 系统 |
| 让 AI 帮你总结/归纳某段时间聊了什么 | 读取他人账号、未登录状态 |
| 分析某个群最近的讨论热点 | 在线/云端微信数据 |

**合规性**：仅限用户本机、用户自己的账号、且用户明确要求时操作；不读取与任务无关的会话，不将聊天内容传给第三方服务，不在产出里留存无关个人隐私信息。

---

## 三、工作原理（30 秒版）

- 微信 4.1+ 进程内存里**没有明文密钥**，只有一个 WCDB passphrase 对象；
- 密钥工具定位 `com.Tencent.WCDB.Config.Cipher` 字符串对象 → 沿对象链取 blob → XOR 解码（27 字节掩码）→ 得到 `x'<key><salt>'` → HMAC-SHA512 校验；
- 解密算法是 SQLCipher 4 变体（AES-256-CBC + HMAC-SHA512，page_size=4096，reserve=80）；
- 解密后，`message_fts.db` 的 `message_fts_v4_*` 表里的 `acontent` 列已是解码后的文本，配合 `name2id` 表即可还原发言人与会话名。

> 因此任何依赖"内存里搜 `x'...'` 明文"或"取 salt 相邻 32 字节"的老方案（pywxdump / NTLx-wechat-decrypt / ZedeX 脚本）在 4.1+ 上必然 0 命中——本 Skill 不在此列。

---

## 四、安装到 Agent

把本仓库整目录复制到 Agent 的 skills 目录即可（目录名即 skill 名）：

```bash
# WorkBuddy（用户级）
git clone https://github.com/xscanzm/wechat-4x-decrypt.git \
  ~/.workbuddy/skills/wechat-4x-decrypt

# 或 Claude Code / Codex：放到你的 skills 目录下
git clone https://github.com/xscanzm/wechat-4x-decrypt.git \
  ~/.claude/skills/wechat-4x-decrypt
```

目录结构：

```
wechat-4x-decrypt/
├── SKILL.md                 # Skill 定义（Agent 读取的指令）
├── README.md                # 本说明文件
├── LICENSE
├── .gitignore               # 防误提交密钥与隐私数据
└── scripts/
    ├── find_wechat.py       # 环境探测：版本/进程/数据目录/活跃账号
    └── query_messages.py    # 解密结果检索与导出
```

> 密钥提取工具 `wcdb-key-tool` **不随本仓库分发**，运行时按 SKILL.md 第 2.2 节从上游 `git clone` 即可（只用到单文件 `wcdb_key_tool_windows.py`）。

---

## 五、使用流程

> 全流程**保持微信运行且已登录**——密钥只存在于进程内存，退出即失效。

### 步骤 1 · 环境探测

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

### 步骤 2 · 安装依赖

**不需要任何第三方 Python 包。** 只需：

- **Python 3.10+**（工具用了 `X | Y` 类型标注，低于 3.10 会 `TypeError`）；
- **密钥提取工具**单文件：

```bash
git clone --depth 1 https://github.com/TANGandXUE/wcdb-key-tool.git
# 无 git 时下载 zip：https://github.com/TANGandXUE/wcdb-key-tool/archive/refs/heads/main.zip
```

验证环境：

```bash
python -c "import ctypes, hashlib, hmac, sqlite3; ctypes.WinDLL('bcrypt'); print('deps OK')"
python wcdb-key-tool/wcdb_key_tool_windows.py --help
```

> 两条都不报错才继续。`WinDLL('bcrypt')` 失败说明不在 Windows 或系统组件缺失——本 skill 仅支持 Windows。
> **不要装 pycryptodome / cryptography**——装了也用不上。

### 步骤 3 · 提取密钥

```bash
cd wcdb-key-tool
python wcdb_key_tool_windows.py extract \
  --db-dir "<步骤1输出的 db_storage 路径>" \
  --output all_keys.json
```

预期：秒级完成，命中数≈数据库总数。少数 `migrate\*.db`（如 `unspportmsg.db`）取不到属正常，不影响聊天记录。

### 步骤 4 · 解密

```bash
python wcdb_key_tool_windows.py decrypt \
  --keys all_keys.json \
  --output decrypted
```

产出明文 SQLite 到 `decrypted/`，按 `message/`、`session/`、`contact/` 等子目录组织。

### 步骤 5 · 检索与导出

```bash
# 先看有哪些会话（不导出正文）
python scripts/query_messages.py --decrypted-dir decrypted \
  --keywords "AI,招聘,HR" --since 2026-07-29 --until 2026-08-12 --list

# 导出前 N 个会话的正文
python scripts/query_messages.py --decrypted-dir decrypted \
  --keywords "AI,招聘,HR" --since 2026-07-29 --until 2026-08-12 \
  --top 10 --out-dir out

# 导出单个会话全部消息（不过滤关键词）
python scripts/query_messages.py --decrypted-dir decrypted \
  --session "53618658790@chatroom" --since 2026-07-29 --out-dir out
```

产出：

- `out/digest.txt`：按会话分组、时间排序、含发言人与时间戳的可读摘录；
- `out/matches.json`：全量结构化结果，供复查与二次处理。

> 让 AI 做总结时，**以 `digest.txt` 为唯一事实来源**，每个结论都能回溯到发言人与时间戳，不要凭印象补写。

---

## 六、数据库结构参考（自行写查询时用）

解密后为 WCDB 风格，表名与 ID 均做过哈希/映射：

| 需要什么 | 位置 |
|---|---|
| **消息正文** | `message_fts.db` 的 `message_fts_v4_*` 表，`acontent` 列已是解码后文本 |
| 会话 / 发言人 ID 还原 | `message_fts.db` 的 `name2id` 表，**rowid** ↔ username |
| 群名 / 好友名 | `contact.db` 的 `contact` 表，优先 `remark` 再 `nick_name` |
| 会话列表与末条预览 | `session.db` 的 `SessionTable`（`summary` = 末条预览） |
| 原始消息表 | `message_*.db` 的 `Msg_<md5(username)>`，正文是 protobuf blob |

**三个已踩过的坑**（写查询时务必注意）：

1. **正文表是分片的**：`message_fts_v4_0` ~ `_3` 四张，实测共 24 万行而第一张仅 3.8 万行。只读第一张会漏掉 ~84% 消息，表现为"某个明明很活跃的群一条都搜不到"。必须全部遍历合并，并跳过 FTS5 影子表（`_data`/`_idx`/`_docsize`/`_config`/`_content`）。
2. **ID 映射只认 `message_fts.db` 的 `name2id`**：不要用 `message_resource.db` 的 `ChatName2Id` / `SenderName2Id`，那两张表只覆盖部分会话，高位 ID 会解析成 `?sid1234`。
3. **群名不要取 `SessionTable.summary`**：那是最后一条消息预览，拿来当群名会得到一堆聊天内容片段。
4. `create_time` 为 Unix 秒。

---

## 七、加密参数（需自行实现解密时）

SQLCipher 4 变体，AES-256-CBC + HMAC-SHA512：

- `page_size = 4096`，`reserve = 80`（IV 16 + HMAC 64）
- `salt` = 每个 db 文件 page1 的前 16 字节
- 逐页解密 IV = `page[4016:4032]`，即 `PAGE_SZ - RESERVE : PAGE_SZ - RESERVE + 16`
- MAC key 派生：`PBKDF2-HMAC-SHA512(enc_key, salt XOR 0x3a, iter=2, dklen=32)`
- 校验密钥是否正确：**只信 HMAC 校验**，不要用"解出的头是不是 `SQLite format 3\0`"当唯一判据。

---

## 八、故障排查

| 现象 | 原因 | 处理 |
|---|---|---|
| 找不到 `Weixin.exe` | 微信未运行 / 版本是 3.x | 启动并登录微信；3.x 改用 pywxdump |
| extract 命中 0 | 微信版本高于工具适配，WCDB 对象偏移变了 | 看工具打印的 needle→node→candidate 计数，判断链条断在哪一步；needle 为 0 说明特征串没找到，需按新版本调偏移 |
| extract 命中但 decrypt 报 HMAC 失败 | 密钥与该库不匹配 / 库正被写入 | 重跑 extract；必要时复制 db 到别处再解 |
| 找不到 `*fts*.db` | 解密未成功或输出目录给错 | 检查 `decrypted/` 是否真有文件 |
| 活跃群搜不到消息 | 只扫了第一张 FTS 分片 | 见第六节点 1 |
| 会话名显示为 `?sid1234` | ID 映射表用错 | 见第六节点 2 |
| 群名是一段聊天内容 | 误用 `summary` 当名字 | 见第六节点 3 |
| `ImportError: No module named Crypto` | 误按旧说明装/引用了 pycryptodome | 本流程零第三方依赖，不需要它 |
| `WinDLL('bcrypt')` 失败 | 非 Windows 环境 | 本 skill 仅支持 Windows |
| `TypeError: unsupported operand ... |` | Python < 3.10 | 换 3.10+ 解释器 |

---

## 九、文件清单与脚本接口

### `scripts/find_wechat.py`

无第三方依赖，仅标准库。自动发现微信版本、进程、数据目录与活跃账号。

```bash
python scripts/find_wechat.py
```

退出码：`0` 找到可用的 4.x 目标；`2` 只找到 3.x；`3` 什么都没找到。

### `scripts/query_messages.py`

无第三方依赖，仅标准库（`sqlite3`）。所有表名/列名自动探测，不写死版本细节。

```bash
# 列出活跃会话
python scripts/query_messages.py --decrypted-dir decrypted --list --since 2026-07-29

# 关键词 + 时间窗检索并导出
python scripts/query_messages.py --decrypted-dir decrypted \
    --keywords "AI,招聘,HR,面试" --since 2026-07-29 --until 2026-08-12 --out-dir out

# 导出指定会话全部消息
python scripts/query_messages.py --decrypted-dir decrypted \
    --session "53618658790@chatroom" --since 2026-07-29 --out-dir out
```

参数：`--decrypted-dir`（必填）、`--keywords`、`--session`、`--since`、`--until`、`--list`、`--top`（默认 15）、`--max-per-session`（默认 400）、`--out-dir`（默认 `out`）。

---

## 十、许可与安全

- 本仓库仅包含**脚本与文档**，**不包含任何密钥、聊天内容或第三方工具的源码**；
- `.gitignore` 已屏蔽 `all_keys.json`、`decrypted/`、`out*/` 等隐私产物，请勿强行提交；
- 密钥提取工具 `wcdb-key-tool` 版权归原作者所有，按其上游 LICENSE 使用；
- 本 Skill 以 MIT 许可证发布，详见 [LICENSE](./LICENSE)。

---

**出品：skill 出品 · 周一 · 皮皮未来**
