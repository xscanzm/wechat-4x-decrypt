#!/usr/bin/env python3
"""query_messages.py -- 在解密后的微信 4.x 数据库中检索并导出消息。

无第三方依赖，仅标准库（sqlite3）。所有表名/列名自动探测，不写死版本细节。

用法:
    # 1) 先看有哪些会话（不给关键词时列出活跃会话）
    python query_messages.py --decrypted-dir decrypted --list --since 2026-07-29

    # 2) 按关键词 + 时间窗检索并导出
    python query_messages.py --decrypted-dir decrypted \
        --keywords "AI,招聘,HR,面试" --since 2026-07-29 --until 2026-08-12 \
        --out-dir out

    # 3) 导出指定会话的全部消息（不过滤关键词）
    python query_messages.py --decrypted-dir decrypted \
        --session "53618658790@chatroom" --since 2026-07-29 --out-dir out

产出:
    <out-dir>/digest.txt    按会话分组、时间排序的可读摘录（含发言人与时间戳）
    <out-dir>/matches.json  全量结构化结果，供复查与二次处理
"""
import argparse
import datetime as dt
import glob
import json
import os
import sqlite3
import sys
from collections import defaultdict


# ---------------------------------------------------------------- 工具函数

def ts_to_str(ts):
    try:
        return dt.datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(ts)


def date_to_ts(s, end_of_day=False):
    if not s:
        return None
    d = dt.datetime.strptime(s, "%Y-%m-%d")
    if end_of_day:
        d = d.replace(hour=23, minute=59, second=59)
    return int(d.timestamp())


def open_ro(path):
    """只读打开，避免误改原库。"""
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.text_factory = lambda b: b.decode("utf-8", "replace")
    return con


def table_cols(con, table):
    try:
        return [r[1] for r in con.execute(f'PRAGMA table_info("{table}")')]
    except sqlite3.Error:
        return []


def all_tables(con):
    try:
        return [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view')")]
    except sqlite3.Error:
        return []


def pick(cols, *candidates):
    """在 cols 中按优先级挑一个存在的列名。"""
    low = {c.lower(): c for c in cols}
    for cand in candidates:
        if cand.lower() in low:
            return low[cand.lower()]
    return None


# ---------------------------------------------------------------- 结构探测

def find_fts_dbs(root):
    """找出全文索引库（消息正文最省事的来源）。"""
    hits = []
    for p in glob.glob(os.path.join(root, "**", "*.db"), recursive=True):
        base = os.path.basename(p).lower()
        if "fts" in base:
            hits.append(p)
    return sorted(hits)


def find_content_tables(con):
    """探测所有存放消息正文的表: 需同时具备 正文列 + 会话id + 时间。

    关键: 消息 FTS 是**分片**的（如 message_fts_v4_0 ~ _3），必须全部扫描并合并。
    只取第一张会漏掉绝大部分消息（实测 4 张分片共 24 万行，第一张仅 3.8 万行）。
    同时跳过 FTS5 影子表（_data/_idx/_docsize/_config/_content）与空表。

    返回 [(table, col_content, col_session, col_sender, col_time), ...]
    """
    shadow_suffix = ("_data", "_idx", "_docsize", "_config", "_content")
    out = []
    for t in all_tables(con):
        if t.lower().endswith(shadow_suffix):
            continue
        cols = table_cols(con, t)
        if not cols:
            continue
        c_content = pick(cols, "acontent", "content", "message_content", "msg_content")
        c_sess = pick(cols, "session_id", "sessionid", "chat_id", "talker_id")
        c_time = pick(cols, "create_time", "createtime", "timestamp", "msg_time")
        if not (c_content and c_sess and c_time):
            continue
        try:
            if con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] == 0:
                continue
        except sqlite3.Error:
            continue
        c_send = pick(cols, "sender_id", "senderid", "from_id", "talker")
        out.append((t, c_content, c_sess, c_send, c_time))
    return out


def load_name2id(con):
    """FTS 的 session_id / sender_id 都是 name2id 表的 rowid。

    这是关键点: 不要用 message_resource.db 的 ChatName2Id/SenderName2Id，
    那两张表只覆盖部分会话，高位 id 会解析不出来。
    """
    for t in all_tables(con):
        if "name2id" not in t.lower():
            continue
        cols = table_cols(con, t)
        c_name = pick(cols, "user_name", "username", "name", "str_value")
        if not c_name:
            continue
        c_id = pick(cols, "id", "local_id", "rowid")
        sel = f'SELECT {c_id or "rowid"}, "{c_name}" FROM "{t}"'
        try:
            return {int(r[0]): r[1] for r in con.execute(sel) if r[0] is not None}
        except sqlite3.Error:
            continue
    return {}


def load_display_names(root):
    """收集 username -> 显示名（群名/好友名）。缺失则降级为 username。

    正确来源: contact.db 的 contact / stranger 表。
    优先级 remark（用户自定义备注）> nick_name（原始昵称）> alias。

    注意: **不要**用 session.db 的 summary 列——那是"最后一条消息预览"，
    拿它当群名会得到一堆聊天内容片段。
    """
    names = {}
    for path in glob.glob(os.path.join(root, "**", "contact*.db"), recursive=True):
        try:
            con = open_ro(path)
        except sqlite3.Error:
            continue
        for t in all_tables(con):
            cols = table_cols(con, t)
            c_user = pick(cols, "user_name", "username")
            if not c_user:
                continue
            c_remark = pick(cols, "remark")
            c_nick = pick(cols, "nick_name", "nickname", "display_name")
            c_alias = pick(cols, "alias")
            picks = [c for c in (c_remark, c_nick, c_alias) if c]
            if not picks:
                continue
            sel = 'SELECT "%s", %s FROM "%s"' % (
                c_user, ", ".join('"%s"' % c for c in picks), t)
            try:
                for row in con.execute(sel):
                    user, vals = row[0], row[1:]
                    if not user or user in names:
                        continue
                    for v in vals:
                        if v and str(v).strip():
                            names[user] = str(v).strip()
                            break
            except sqlite3.Error:
                continue
        con.close()
    return names


# ---------------------------------------------------------------- 主流程

def main():
    ap = argparse.ArgumentParser(description="检索解密后的微信 4.x 消息")
    ap.add_argument("--decrypted-dir", required=True, help="解密输出目录")
    ap.add_argument("--keywords", default="", help="逗号分隔；留空则不按内容过滤")
    ap.add_argument("--session", default="", help="只看某个会话（username，如 xxx@chatroom）")
    ap.add_argument("--since", default="", help="起始日期 YYYY-MM-DD")
    ap.add_argument("--until", default="", help="结束日期 YYYY-MM-DD")
    ap.add_argument("--list", action="store_true", help="只列出会话排行，不导出正文")
    ap.add_argument("--top", type=int, default=15, help="导出前 N 个会话")
    ap.add_argument("--max-per-session", type=int, default=400)
    ap.add_argument("--out-dir", default="out")
    args = ap.parse_args()

    root = args.decrypted_dir
    if not os.path.isdir(root):
        print(f"[NG] 目录不存在: {root}")
        return 1

    kws = [k.strip().lower() for k in args.keywords.split(",") if k.strip()]
    t_from = date_to_ts(args.since)
    t_to = date_to_ts(args.until, end_of_day=True)

    fts_dbs = find_fts_dbs(root)
    if not fts_dbs:
        print(f"[NG] 在 {root} 下没找到 *fts*.db。确认解密步骤已成功完成。")
        return 1
    print(f"[*] 全文索引库 {len(fts_dbs)} 个")

    display = load_display_names(root)
    print(f"[*] 显示名映射 {len(display)} 条")

    by_session = defaultdict(list)
    total_scanned = 0

    for db in fts_dbs:
        try:
            con = open_ro(db)
        except sqlite3.Error as e:
            print(f"[!] 打开失败 {os.path.basename(db)}: {e}")
            continue

        tables = find_content_tables(con)
        if not tables:
            con.close()
            continue
        id2name = load_name2id(con)
        print(f"    {os.path.basename(db)}: 正文分片 {len(tables)} 张, "
              f"name2id {len(id2name)} 条")

        for tbl, c_content, c_sess, c_send, c_time in tables:
            send_expr = '"%s"' % c_send if c_send else "NULL"
            sql = ('SELECT "%s", %s, "%s", "%s" FROM "%s"'
                   % (c_sess, send_expr, c_time, c_content, tbl))
            where, params = [], []
            if t_from is not None:
                where.append('"%s" >= ?' % c_time)
                params.append(t_from)
            if t_to is not None:
                where.append('"%s" <= ?' % c_time)
                params.append(t_to)
            if where:
                sql += " WHERE " + " AND ".join(where)

            try:
                rows = con.execute(sql, params).fetchall()
            except sqlite3.Error as e:
                print(f"[!] 查询失败 {os.path.basename(db)}.{tbl}: {e}")
                continue

            for sid, send_id, ts, content in rows:
                total_scanned += 1
                if not content:
                    continue
                sess_user = id2name.get(int(sid), f"?sid{sid}") if sid is not None else "?"
                if args.session and sess_user != args.session:
                    continue
                if kws:
                    low = str(content).lower()
                    if not any(k in low for k in kws):
                        continue
                sender = id2name.get(int(send_id), f"?uid{send_id}") if send_id else ""
                by_session[sess_user].append({
                    "time": int(ts), "sender": sender, "content": str(content),
                })
        con.close()

    print(f"[*] 扫描 {total_scanned} 条，命中 {sum(len(v) for v in by_session.values())} 条，"
          f"涉及 {len(by_session)} 个会话")

    ranked = sorted(by_session.items(), key=lambda kv: len(kv[1]), reverse=True)

    print("\n会话排行:")
    for user, msgs in ranked[:args.top]:
        label = display.get(user, user)
        kind = "群" if user.endswith("@chatroom") else "私聊"
        print(f"  {len(msgs):>5} 条  [{kind}] {label}")
        if label != user:
            print(f"            {user}")

    if args.list:
        return 0

    os.makedirs(args.out_dir, exist_ok=True)
    digest_path = os.path.join(args.out_dir, "digest.txt")
    json_path = os.path.join(args.out_dir, "matches.json")

    payload = []
    with open(digest_path, "w", encoding="utf-8") as f:
        for user, msgs in ranked[:args.top]:
            label = display.get(user, user)
            msgs.sort(key=lambda m: m["time"])
            f.write("=" * 72 + "\n")
            f.write(f"会话: {label}  ({user})  命中 {len(msgs)} 条\n")
            f.write("=" * 72 + "\n")
            for m in msgs[:args.max_per_session]:
                sender = display.get(m["sender"], m["sender"]) or "?"
                text = " ".join(m["content"].split())
                f.write(f"[{ts_to_str(m['time'])}] {sender}: {text}\n")
            f.write("\n")
            payload.append({
                "session": user, "display": label, "count": len(msgs),
                "messages": [
                    {"time": ts_to_str(m["time"]), "ts": m["time"],
                     "sender": display.get(m["sender"], m["sender"]),
                     "content": m["content"]}
                    for m in msgs[:args.max_per_session]
                ],
            })

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"\n[OK] 摘录 -> {digest_path}")
    print(f"[OK] 结构化 -> {json_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
