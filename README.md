# ulani-calendar：永恆曆照片更新工具（Claude Code skill）

用 Mac 的藍牙，把照片直接更新到「永恆曆」（ULANI 電子紙日曆，型號 P7320E-B）。
官方 App 已不再支援時，可以靠這個 skill 讓 Claude 幫你連線、轉檔、上傳、切換照片。

> 非官方工具。協定細節參考社群文件 [markhhm/ulani-epaper](https://github.com/markhhm/ulani-epaper) 的 `shared/PROTOCOL.md`，**沒有使用該專案的程式碼**，所有程式都是自己寫的。
> 使用風險自負：上傳會覆蓋裝置上該頁的照片，而且無法從裝置救回。

## 能做什麼

- 從頭引導藍牙連線與配對，卡住時依錯誤訊息告訴你下一步。
- 把 1–4 張照片轉成面板格式（800×480、7 色抖動），並產生預覽圖。
- 依序上傳到第 1–4 頁，用裝置回報的校驗碼（CRC32）驗證。
- 只切換目前顯示的照片，不上傳。
- 直式或比例不對的照片，裁成橫式，人物放在中央（由 Claude 看圖決定裁切）。

## 需求

| 項目 | 說明 |
|---|---|
| 電腦 | macOS（用 CoreBluetooth），需有藍牙 |
| 工具 | `swiftc`（沒有請執行 `xcode-select --install`）、Python 3 + Pillow + numpy |
| 裝置 | 永恆曆 P7320E-B，韌體 v1.0.09（只測過這一種） |
| Claude | Claude Code（或能讀 skill 的 Agent） |

## 安裝

```bash
git clone https://github.com/napyang/ulani-calendar-skill ~/.claude/skills/ulani-calendar
```

重新開啟 Claude Code，輸入 `/ulani-calendar`，或直接說「更新永恆曆」就會觸發。

## 第一次使用（要配對）

裝置同一時間只記得一台主機。**手機配對過的話，配對這台 Mac 會讓手機失效。**

1. 系統設定 → 藍牙。如果已經有 `ULANI CalendarXXXXXX`，按 ⓘ → 「忘記此裝置」。
2. 按住永恆曆背面按鈕約 **5 秒**，到綠燈閃（配對模式）。
3. 在「附近的裝置」按下 `ULANI CalendarXXXXXX` 的「連線」，同意配對。
4. 對 Claude 說「幫我測試永恆曆連線」。

第一次執行時，macOS 會問藍牙權限（ULANIPush），按「允許」。

## 日常使用

直接跟 Claude 說，例如：

- 「把 ~/Downloads/uni 裡四張照片更新到永恆曆」：依序傳到第 1–4 頁。
- 「第 2、3 頁換成這兩張照片」：只換指定頁。
- 「永恆曆切到第 1 頁」：只切換，不上傳。
- 「永恆曆連不上」：Claude 會照 `SKILL.md` 的診斷表一步一步處理。

也可以自己下指令（`$S` = `~/.claude/skills/ulani-calendar/scripts`）：

```bash
python3 -I $S/ulani_upload.py 照片1 照片2 照片3 照片4            # 上傳到第 1–4 頁
python3 -I $S/ulani_upload.py 照片A 照片B --slots 2,3            # 只覆蓋第 2、3 頁
python3 -I $S/ulani_upload.py 照片1 --dry-run                    # 只轉檔看預覽，不碰裝置
python3 -I $S/ulani_upload.py --switch 2                         # 只切換到第 2 頁
```

參數：`--fit cover`（預設，填滿裁邊）／`--fit contain`（留白邊）、`--no-dither`（不抖動）、`--show N`（上傳完切到第 N 頁）。

## 要注意的事

- **上傳會覆蓋原圖，救不回來。** 沒提到的頁不會被動。
- 傳到「正在顯示的頁」時，面板會刷新約 40 秒，期間裝置不回應。腳本會先傳沒在顯示的頁，最後傳顯示中的頁，讓面板只刷新一次。
- 裝置為了省電很少發藍牙訊號。掃不到時，靠近電腦按一下按鈕喚醒（**按鈕同時會切到下一頁**）。
- 程式重新編譯（改過 `.swift`、換電腦）後，macOS 會重新問一次藍牙權限，沒按允許程式會卡住。
- 預覽圖的 7 種顏色是估計值，面板實際顏色會略有不同。

## 檔案結構

```
SKILL.md                  給 Claude 看的流程：連線引導 → 轉檔 → 上傳 → 驗證
gatt_dump.json            只讀探索得到的 GATT 資料（已去除裝置識別）
scripts/
  ulani_upload.py         一鍵流程：轉檔、上傳、切頁、驗證
  ulani_convert.py        照片 → 800×480、4 bit、7 色 frame（含 CRC）
  ulani_push.swift        藍牙傳送程式（info / push / show 三種模式）
  build_push.sh           編譯並包成 ULANIPush.app（藍牙權限只認 .app）
```

`ULANIPush.app` 是編譯產物，不放在 repo 裡，第一次執行時會自動編譯。

## 運作原理（簡述）

- 兩個自訂 BLE 欄位：控制（寫入＋通知）與資料（無回應寫入＋通知），都要先配對加密。
- 上傳流程：解鎖 → 送 13 byte 標頭（長度、頁數、CRC32、CRC16）→ 以 ≤230 bytes 分包串流 192000 bytes → 裝置回報成功或失敗。
- 沒有「刷新」指令：傳到正在顯示的頁就會自動刷新。
- 詳細規格與指令表在 `SKILL.md` 的「規格」一節。

## 已知限制

- 只在 P7320E-B、韌體 v1.0.09、macOS 上測過。
- `SKILL.md` 的連線診斷表中，只有「成功流程」和 `Peer removed pairing information` 這一種失敗是實際重現過的，其他是依程式輸出與官方文件整理。
- 附近有多台永恆曆時，程式連到先掃到的那一台。
