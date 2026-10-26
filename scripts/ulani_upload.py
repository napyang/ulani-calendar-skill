#!/usr/bin/env python3
"""把 1–4 張照片依序上傳到永恆曆 (ULANI P7320E-B) 的第 1–4 頁。

用法:
  python3 ulani_upload.py 照片1 照片2 照片3 照片4 [--fit cover|contain] [--no-dither]
  python3 ulani_upload.py 照片A 照片B --slots 2,3      # 只覆蓋指定頁
  python3 ulani_upload.py ... --dry-run                 # 只轉檔出預覽，不碰裝置
  python3 ulani_upload.py ... --show 1                  # 上傳完切到第 1 頁（會再刷新一次）

順序規則：先傳「目前沒在顯示」的頁，最後才傳正在顯示的頁，這樣面板只刷新一次。
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import ulani_convert as conv  # noqa: E402

APP = HERE / "ULANIPush.app"
BIN = APP / "Contents" / "MacOS" / "ULANIPush"
SRC = HERE / "ulani_push.swift"


def ensure_app():
    if BIN.exists() and BIN.stat().st_mtime >= SRC.stat().st_mtime:
        return
    print("編譯傳送程式…")
    subprocess.run([str(HERE / "build_push.sh")], check=True)


def run_app(args, workdir, tag, app_timeout):
    log = workdir / f"{tag}.log"
    out = workdir / f"{tag}.json"
    for p in (log, out):
        p.unlink(missing_ok=True)
    subprocess.run(
        ["open", "-W", "-n", str(APP), "--stdout", str(log), "--stderr", str(log),
         "--args", args[0], str(app_timeout), str(out), *args[1:]],
        timeout=app_timeout + 60, check=False)
    if not out.exists():
        return None, log
    return json.loads(out.read_text()), log


def query_info(workdir, tries=3):
    for i in range(tries):
        r, log = run_app(["info"], workdir, f"info_{int(time.time())}", 90)
        if r and r.get("exit_code") == 0 and "shown_slot" in r:
            return r
        print(f"  查詢失敗（第 {i + 1}/{tries} 次），詳見 {log}")
        time.sleep(5)
    return None


def switch_only(slot, workdir):
    """只切換顯示頁（0B SS），不上傳。切到不同頁時面板會刷新約 40 秒。"""
    workdir.mkdir(parents=True, exist_ok=True)
    ensure_app()
    info = query_info(workdir)
    if not info:
        sys.exit("無法連上裝置。請照 SKILL.md 的「連線引導」逐步處理。")
    if info["shown_slot"] == slot:
        print(f"目前已經是第 {slot} 頁，不用切換。")
        return
    print(f"目前第 {info['shown_slot']} 頁 → 切到第 {slot} 頁…")
    for attempt in range(1, 4):
        r, log = run_app(["show", str(slot)], workdir, f"show_{attempt}", 120)
        if r and r.get("exit_code") == 0 and r.get("switched"):
            print(f"已切到第 {slot} 頁，裝置回報 CRC32 {r['shown_crc32']}。面板刷新約 40 秒。")
            return
        print(f"  第 {attempt} 次失敗，詳見 {log}")
        time.sleep(10)
    sys.exit("切換失敗，已停止。")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("photos", nargs="*")
    ap.add_argument("--switch", type=int, choices=[1, 2, 3, 4], help="只切換顯示頁，不上傳（不用給照片）")
    ap.add_argument("--slots", help="逗號分隔的頁數，預設 1..N")
    ap.add_argument("--fit", choices=["cover", "contain"], default="cover")
    ap.add_argument("--no-dither", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--show", type=int, choices=[1, 2, 3, 4])
    ap.add_argument("--workdir")
    a = ap.parse_args()

    if a.switch:
        switch_only(a.switch, Path(a.workdir or f"/tmp/ulani_switch_{time.strftime('%H%M%S')}"))
        return
    if not a.photos:
        sys.exit("錯誤：要給照片路徑，或用 --switch N 只切換顯示頁")

    slots = [int(s) for s in a.slots.split(",")] if a.slots else list(range(1, len(a.photos) + 1))
    if len(slots) != len(a.photos) or len(set(slots)) != len(slots) or not all(1 <= s <= 4 for s in slots):
        sys.exit("錯誤：照片數量要等於頁數，頁數要在 1–4 且不重複，最多 4 張")
    for p in a.photos:
        if not Path(p).expanduser().is_file():
            sys.exit(f"錯誤：找不到照片 {p}")

    workdir = Path(a.workdir or f"/tmp/ulani_run_{time.strftime('%Y%m%d_%H%M%S')}")
    workdir.mkdir(parents=True, exist_ok=True)

    print("== 轉檔 ==")
    jobs = {}
    for slot, photo in zip(slots, a.photos):
        prefix = str(workdir / f"slot{slot}")
        meta = conv.convert(str(Path(photo).expanduser()), prefix, a.fit, not a.no_dither)
        meta["photo"] = photo
        jobs[slot] = meta
        print(f"  第 {slot} 頁 ← {photo}\n    預覽 {prefix}_preview.png  CRC32 {meta['crc32']}")
    if a.dry_run:
        print(f"\n--dry-run：沒有碰裝置。預覽在 {workdir}")
        return

    ensure_app()
    print("\n== 查詢裝置 ==")
    info = query_info(workdir)
    if not info:
        sys.exit("無法連上裝置。請照 SKILL.md 的「連線引導」逐步處理，"
                 f"紀錄在 {workdir}")
    shown = info["shown_slot"]
    print(f"  電量 {info.get('battery')}/5，目前顯示第 {shown} 頁，CRC32 {info.get('shown_crc32')}")

    order = sorted(jobs, key=lambda s: (s == shown, s))  # 正在顯示的最後傳
    print(f"\n== 上傳（順序：{' → '.join(map(str, order))}）==")
    failed = []
    for slot in order:
        m = jobs[slot]
        ok = False
        for attempt in range(1, 4):
            print(f"  第 {slot} 頁（第 {attempt} 次）…", flush=True)
            r, log = run_app(["push", str(slot), m["bin"], m["crc32"], m["crc16"]],
                             workdir, f"push_slot{slot}_{attempt}", 180)
            if r and r.get("exit_code") == 0 and (r.get("stored") or r.get("already_present")):
                print("    " + ("這頁已是同一張圖，略過" if r.get("already_present") else "已儲存"))
                ok = True
                break
            print(f"    失敗，詳見 {log}")
            time.sleep(45 if slot == shown else 8)  # 刷新中連不上，多等一下
        if not ok:
            failed.append(slot)
            break  # 一頁失敗就停，不要硬傳後面的

    if failed:
        sys.exit(f"\n第 {failed[0]} 頁上傳失敗，已停止。前面的頁已儲存。紀錄在 {workdir}")

    if a.show:
        m = jobs.get(a.show)
        if m:
            print(f"\n== 切到第 {a.show} 頁 ==")
            r, log = run_app(["push", str(a.show), m["bin"], m["crc32"], m["crc16"], "show"],
                             workdir, "show", 180)
            print(f"  CRC 吻合：{r.get('crc_match') if r else '無回應'}")
        else:
            print(f"（--show {a.show} 不在這次上傳的頁裡，略過）")
    else:
        time.sleep(45 if shown in jobs else 0)  # 等面板刷新完再驗證
    print("\n== 驗證 ==")
    info2 = query_info(workdir)
    if info2:
        s2 = info2["shown_slot"]
        want = jobs[s2]["crc32"] if s2 in jobs else None
        status = "吻合" if want and want == info2["shown_crc32"] else ("（此頁不在這次上傳）" if want is None else "不吻合")
        print(f"  顯示第 {s2} 頁，裝置 CRC32 {info2['shown_crc32']}，本機 {want or '-'}  {status}")
    print(f"\n完成。預覽與紀錄在 {workdir}")


if __name__ == "__main__":
    main()
