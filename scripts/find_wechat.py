#!/usr/bin/env python3
"""find_wechat.py -- 自动发现微信版本、进程、数据目录与活跃账号。

无第三方依赖，仅标准库。

用法:
    python find_wechat.py

输出: 进程列表、数据根目录、各账号 db_storage 路径及新鲜度（用于判断哪个账号在用）。
退出码: 0 找到可用的 4.x 目标; 2 只找到 3.x; 3 什么都没找到。
"""
import os
import re
import glob
import subprocess
import sys
import time

PROC_4X = "weixin.exe"          # 微信 4.x 主进程（目标）
PROC_3X = "wechat.exe"          # 微信 3.x 主进程（本 skill 不适用）
PROC_NOISE = ("wechatappex.exe", "wechatdevtools.exe", "wechatutility.exe")


def list_processes():
    """返回 [(name, pid, mem_kb)]，只保留微信相关进程。"""
    try:
        out = subprocess.run(
            ["tasklist", "/fo", "csv", "/nh"],
            capture_output=True, text=True, timeout=30,
            errors="replace",
        ).stdout
    except Exception as e:
        print(f"[!] tasklist 执行失败: {e}")
        return []

    found = []
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) < 5:
            continue
        name, pid, mem = parts[0].strip('"'), parts[1], parts[4].rstrip('"')
        low = name.lower()
        if "wechat" in low or "weixin" in low:
            kb = re.sub(r"[^\d]", "", mem) or "0"
            found.append((name, pid, int(kb)))
    return found


def exe_version(pid):
    """尽力获取进程可执行文件版本号，失败返回 None。"""
    ps = (
        "$p=Get-Process -Id {pid} -ErrorAction SilentlyContinue;"
        "if($p){{$p.Path; $p.MainModule.FileVersionInfo.FileVersion}}"
    ).format(pid=pid)
    try:
        out = subprocess.run(
            ["powershell", "-NonInteractive", "-NoProfile", "-Command", ps],
            capture_output=True, text=True, timeout=30, errors="replace",
        ).stdout.strip().splitlines()
        if out:
            return " | ".join(x.strip() for x in out if x.strip())
    except Exception:
        pass
    return None


def data_roots():
    """从配置文件读取微信数据根目录，返回去重后的候选列表。"""
    roots, seen = [], set()

    appdata = os.environ.get("APPDATA", "")
    patterns = []
    if appdata:
        # 4.x 用 xwechat；3.x 用 WeChat（一并探测便于识别版本）
        patterns += [
            os.path.join(appdata, "Tencent", "xwechat", "config", "*.ini"),
            os.path.join(appdata, "Tencent", "WeChat", "config", "*.ini"),
        ]

    for pat in patterns:
        for ini in glob.glob(pat):
            try:
                with open(ini, "rb") as f:
                    raw = f.read()
            except OSError:
                continue
            # ini 内容可能是 UTF-16 / UTF-8 / GBK，逐个尝试
            for enc in ("utf-8", "utf-16-le", "gbk"):
                try:
                    txt = raw.decode(enc).strip("\x00 \r\n\t")
                except UnicodeDecodeError:
                    continue
                # 取形似路径的行
                for line in txt.splitlines():
                    cand = line.strip().strip('"')
                    if len(cand) > 2 and (":" in cand[:3] or cand.startswith("\\\\")):
                        if os.path.isdir(cand) and cand.lower() not in seen:
                            seen.add(cand.lower())
                            roots.append(cand)
                break

    # 兜底：常见默认位置
    for d in (
        os.path.join(os.path.expanduser("~"), "Documents", "xwechat_files"),
        os.path.join(os.path.expanduser("~"), "Documents", "WeChat Files"),
    ):
        parent = os.path.dirname(d)
        if os.path.isdir(d) and parent.lower() not in seen:
            seen.add(parent.lower())
            roots.append(parent)

    return roots


def find_accounts(roots):
    """在候选根目录下找 4.x 的 db_storage 目录。返回 [(account_dir, db_dir, newest_mtime, n_db)]"""
    results = []
    for root in roots:
        # 4.x 结构: <root>/xwechat_files/<account>/db_storage/*.db
        for db_dir in glob.glob(os.path.join(root, "xwechat_files", "*", "db_storage")):
            dbs = glob.glob(os.path.join(db_dir, "**", "*.db"), recursive=True)
            if not dbs:
                continue
            newest = max(os.path.getmtime(p) for p in dbs)
            results.append((os.path.dirname(db_dir), db_dir, newest, len(dbs)))
    results.sort(key=lambda x: x[2], reverse=True)
    return results


def main():
    print("=" * 68)
    print("微信进程")
    print("=" * 68)
    procs = list_processes()
    if not procs:
        print("  (未发现微信相关进程)")
    has_4x = has_3x = False
    for name, pid, kb in sorted(procs, key=lambda x: -x[2]):
        low = name.lower()
        if low == PROC_4X:
            tag, has_4x = "<== 4.x 主进程（目标）", True
        elif low == PROC_3X:
            tag, has_3x = "<== 3.x 主进程（本 skill 不适用）", True
        elif low in PROC_NOISE:
            tag = "(辅助进程，非目标)"
        else:
            tag = ""
        print(f"  {name:<24} pid={pid:<8} {kb/1024:8.1f} MB  {tag}")

    if has_4x:
        main_pid = next(p for n, p, _ in procs if n.lower() == PROC_4X)
        ver = exe_version(main_pid)
        if ver:
            print(f"\n  版本信息: {ver}")

    print()
    print("=" * 68)
    print("数据目录")
    print("=" * 68)
    roots = data_roots()
    if not roots:
        print("  (未从配置文件解析出数据根目录)")
    for r in roots:
        print(f"  root: {r}")

    accounts = find_accounts(roots)
    print()
    if not accounts:
        print("  (未找到 4.x 的 db_storage 目录)")
    else:
        now = time.time()
        print("  账号（按数据新鲜度排序，最新的通常就是当前登录账号）:")
        for acc, db_dir, mt, n in accounts:
            age = now - mt
            age_s = f"{age:.0f}s 前" if age < 3600 else f"{age/3600:.1f}h 前"
            print(f"    {os.path.basename(acc):<28} db={n:<3} 最近写入={age_s}")
            print(f"      --db-dir \"{db_dir}\"")

    print()
    if has_4x and accounts:
        print("[OK] 可继续下一步：提取密钥。把上面的 --db-dir 传给 wcdb_key_tool_windows.py extract")
        return 0
    if has_3x and not has_4x:
        print("[NG] 只发现微信 3.x。本 skill 不适用，请改用 pywxdump。")
        return 2
    if not has_4x:
        print("[NG] 微信 4.x 未在运行。密钥必须从进程内存提取——请启动并登录微信后重试。")
        return 3
    print("[NG] 微信在运行但未找到数据目录。手动确认 %APPDATA%\\Tencent\\xwechat\\config\\*.ini 指向的路径。")
    return 3


if __name__ == "__main__":
    sys.exit(main())
