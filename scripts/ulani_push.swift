import Foundation
import CoreBluetooth

// 永恆曆 (ULANI P7320E-B) 傳送程式。
//   info: 解鎖後只送 06 00 / 0A 00 / 0C 00 這類查詢，然後 09 03 結束。不寫圖。
//   push: 上傳一張 frame 到指定 slot（會覆蓋該 slot 的圖）。
//   show: 只切換顯示頁，不上傳。
// 用法:
//   ULANIPush info <逾時秒> <輸出JSON>
//   ULANIPush show <逾時秒> <輸出JSON> <slot 1-4>
//   ULANIPush push <逾時秒> <輸出JSON> <slot 1-4> <frame.bin> <CRC32 hex> <CRC16 hex> [show]
let args = CommandLine.arguments
let mode = args.count > 1 ? args[1] : "info"
let timeout = Double(args.count > 2 ? args[2] : "150") ?? 150
let outPath = args.count > 3 ? args[3] : "ulani_run.json"

let svcUUID = CBUUID(string: "1234A200-7CBC-11E9-8F9E-2A86E4085A59")
let ctlUUID = CBUUID(string: "1234A201-7CBC-11E9-8F9E-2A86E4085A59")
let datUUID = CBUUID(string: "1234A202-7CBC-11E9-8F9E-2A86E4085A59")
let nameKey = "ULANI Calendar"

func hex(_ b: [UInt8]) -> String { b.map { String(format: "%02X", $0) }.joined(separator: " ") }
func be32(_ v: UInt32) -> [UInt8] { [UInt8(v >> 24), UInt8((v >> 16) & 255), UInt8((v >> 8) & 255), UInt8(v & 255)] }
func be16(_ v: UInt16) -> [UInt8] { [UInt8(v >> 8), UInt8(v & 255)] }

// show: 只切換顯示頁（0B SS），不上傳任何圖。用法: ULANIPush show <逾時秒> <輸出JSON> <slot 1-4>
var showSlot: UInt8 = 0
if mode == "show" {
    guard args.count >= 5, let s = UInt8(args[4]), (1...4).contains(s) else {
        print("錯誤：show 要給 slot 1-4"); exit(64)
    }
    showSlot = s
}

var pushSlot: UInt8 = 0
var frame = Data()
var crc32v: UInt32 = 0
var crc16v: UInt16 = 0
var showAfter = false
if mode == "push" {
    guard args.count >= 8, let s = UInt8(args[4]), (1...4).contains(s),
          let d = FileManager.default.contents(atPath: args[5]), d.count == 192000,
          let c32 = UInt32(args[6], radix: 16), let c16 = UInt16(args[7], radix: 16) else {
        print("錯誤：push 參數不對（slot 要 1-4、frame 要剛好 192000 bytes、CRC 要是 hex）"); exit(64)
    }
    pushSlot = s; frame = d; crc32v = c32; crc16v = c16
    showAfter = args.count > 8 && args[8] == "show"
}

final class Runner: NSObject, CBCentralManagerDelegate, CBPeripheralDelegate {
    var central: CBCentralManager!
    var p: CBPeripheral?
    var ctl: CBCharacteristic?
    var dat: CBCharacteristic?
    var events: [[String: Any]] = []
    var t0 = Date()
    var finished = false
    var notifyReady = Set<String>()
    var pendingOpcode: UInt8?
    var onReply: (([UInt8]) -> Void)?
    var onData: (([UInt8]) -> Void)?
    var offset = 0
    var chunk = 20
    var streaming = false
    var headerTries = 0
    var result: [String: Any] = [:]

    override init() { super.init(); central = CBCentralManager(delegate: self, queue: nil) }

    func log(_ s: String) {
        let t = String(format: "%6.1fs", Date().timeIntervalSince(t0))
        print("\(t) \(s)")
        events.append(["t": Date().timeIntervalSince(t0), "msg": s])
    }

    // MARK: 連線
    func centralManagerDidUpdateState(_ c: CBCentralManager) {
        switch c.state {
        case .poweredOn:
            log("掃描 \(nameKey)（廣播很稀疏，請耐心等）")
            c.scanForPeripherals(withServices: nil, options: nil)
        case .unauthorized: log("錯誤：沒有藍牙權限"); finish(2)
        case .poweredOff: log("錯誤：藍牙是關的"); finish(3)
        default: break
        }
    }
    func centralManager(_ c: CBCentralManager, didDiscover per: CBPeripheral, advertisementData ad: [String: Any], rssi: NSNumber) {
        let n = per.name ?? (ad[CBAdvertisementDataLocalNameKey] as? String) ?? ""
        guard p == nil, n.contains(nameKey) else { return }
        p = per; per.delegate = self; c.stopScan()
        log("找到 \(n)，連線中")
        c.connect(per, options: nil)
    }
    func centralManager(_ c: CBCentralManager, didConnect per: CBPeripheral) {
        log("已連線，探索 service")
        per.discoverServices([svcUUID])
    }
    func centralManager(_ c: CBCentralManager, didFailToConnect per: CBPeripheral, error: Error?) {
        log("連線失敗：\(error?.localizedDescription ?? "?")"); finish(4)
    }
    func centralManager(_ c: CBCentralManager, didDisconnectPeripheral per: CBPeripheral, error: Error?) {
        log("已斷線\(error.map { "：" + $0.localizedDescription } ?? "")")
        finish(finished ? 0 : 7)
    }
    func peripheral(_ per: CBPeripheral, didDiscoverServices error: Error?) {
        guard let s = per.services?.first(where: { $0.uuid == svcUUID }) else { log("找不到自訂 service"); finish(5); return }
        per.discoverCharacteristics([ctlUUID, datUUID], for: s)
    }
    func peripheral(_ per: CBPeripheral, didDiscoverCharacteristicsFor s: CBService, error: Error?) {
        ctl = s.characteristics?.first { $0.uuid == ctlUUID }
        dat = s.characteristics?.first { $0.uuid == datUUID }
        guard let c = ctl, let d = dat else { log("找不到 A201/A202"); finish(5); return }
        log("開啟通知（這一步若需要配對，macOS 會跳出配對視窗）")
        per.setNotifyValue(true, for: c)
        per.setNotifyValue(true, for: d)
    }
    func peripheral(_ per: CBPeripheral, didUpdateNotificationStateFor ch: CBCharacteristic, error: Error?) {
        if let e = error {
            log("開啟通知失敗：\(e.localizedDescription)（多半是還沒配對。請按住裝置按鈕約 5 秒到綠燈閃，再重跑）")
            finish(8); return
        }
        notifyReady.insert(ch.uuid.uuidString)
        if notifyReady.count == 2 {
            log("兩個通知都已開啟，送解鎖指令")
            command([0x04, 0x4E, 0x42], expect: 0x04) { [self] r in
                guard r.count >= 2, r[1] == 0x00 else { log("解鎖失敗"); finish(9); return }
                switch mode {
                case "push": beginPush()
                case "show": runShow()
                default: runInfo()
                }
            }
        }
    }

    // MARK: 指令
    func command(_ bytes: [UInt8], expect: UInt8, _ then: @escaping ([UInt8]) -> Void) {
        pendingOpcode = expect; onReply = then
        log("→ A201 \(hex(bytes))")
        p?.writeValue(Data(bytes), for: ctl!, type: .withResponse)
    }
    func peripheral(_ per: CBPeripheral, didWriteValueFor ch: CBCharacteristic, error: Error?) {
        if let e = error { log("寫入失敗：\(e.localizedDescription)"); finish(10) }
    }
    func peripheral(_ per: CBPeripheral, didUpdateValueFor ch: CBCharacteristic, error: Error?) {
        guard error == nil, let v = ch.value else { return }
        let b = [UInt8](v)
        if ch.uuid == ctlUUID {
            log("← A201 \(hex(b))")
            if let op = pendingOpcode, let f = b.first, f == op, let cb = onReply {
                pendingOpcode = nil; onReply = nil; cb(b)
            }
        } else {
            log("← A202 \(hex(b))")
            if let cb = onData { onData = nil; cb(b) }
        }
    }

    // MARK: info
    func runInfo() {
        command([0x06, 0x00], expect: 0x06) { [self] r in
            result["battery"] = r.count > 1 ? Int(r[1]) : -1
            command([0x0A, 0x00], expect: 0x0A) { [self] r in
                if r.count >= 7 { result["mac"] = r[1...6].map { String(format: "%02X", $0) }.joined(separator: ":") }
                command([0x0C, 0x00], expect: 0x0C) { [self] r in
                    if r.count >= 6 {
                        result["shown_slot"] = Int(r[1])
                        result["shown_crc32"] = r[2...5].map { String(format: "%02X", $0) }.joined()
                    }
                    goodbye()
                }
            }
        }
    }

    // MARK: show（只切頁）
    func runShow() {
        command([0x0B, showSlot], expect: 0x0B) { [self] r in
            guard r.count >= 2, r[1] == 0x00 else { log("切頁被拒絕：\(hex(r))"); finish(14); return }
            log("裝置接受切頁（面板刷新約 40 秒），查詢目前頁")
            command([0x0C, 0x00], expect: 0x0C) { [self] r in
                if r.count >= 6 {
                    result["shown_slot"] = Int(r[1])
                    result["shown_crc32"] = r[2...5].map { String(format: "%02X", $0) }.joined()
                    result["switched"] = (r[1] == showSlot)
                }
                goodbye()
            }
        }
    }

    // MARK: push
    func beginPush() { sendHeader() }
    func sendHeader() {
        headerTries += 1
        var h: [UInt8] = [0x01] + be32(UInt32(frame.count)) + [pushSlot, 0x01] + be32(crc32v) + be16(crc16v)
        h = Array(h)
        command(h, expect: 0x01) { [self] r in
            let code = r.count > 1 ? r[1] : 0xFF
            switch code {
            case 0x00: log("裝置接受，開始傳 frame"); stream()
            case 0x01:
                if headerTries >= 8 { log("裝置一直忙碌，放棄"); finish(11); return }
                log("裝置忙碌，3 秒後重送標頭")
                DispatchQueue.main.asyncAfter(deadline: .now() + 3) { self.sendHeader() }
            case 0x02: log("這個 slot 已經是這張圖，不用傳"); result["already_present"] = true; afterUpload()
            default: log("未知回覆 \(hex(r))"); finish(12)
            }
        }
    }
    func stream() {
        chunk = min(p!.maximumWriteValueLength(for: .withoutResponse), 230)
        log("封包大小 \(chunk) bytes，共 \((frame.count + chunk - 1) / chunk) 包")
        offset = 0; streaming = true
        onData = { [self] r in
            streaming = false
            if r.count >= 2 && r[0] == 0x02 && r[1] == 0x00 { log("裝置回報已儲存"); result["stored"] = true; afterUpload() }
            else { log("裝置回報失敗 \(hex(r))"); result["stored"] = false; finish(13) }
        }
        pump()
    }
    func pump() {
        guard streaming, let per = p, let d = dat else { return }
        while offset < frame.count && per.canSendWriteWithoutResponse {
            let end = min(offset + chunk, frame.count)
            per.writeValue(frame.subdata(in: offset..<end), for: d, type: .withoutResponse)
            offset = end
        }
        if offset >= frame.count && streaming { log("frame 已全部送出，等裝置確認（刷新約 40 秒）") }
    }
    func peripheralIsReady(toSendWriteWithoutResponse per: CBPeripheral) { pump() }

    func afterUpload() {
        guard showAfter else { goodbye(); return }
        command([0x0B, pushSlot], expect: 0x0B) { [self] _ in
            command([0x0C, 0x00], expect: 0x0C) { [self] r in
                if r.count >= 6 {
                    let got = r[2...5].map { String(format: "%02X", $0) }.joined()
                    result["shown_slot"] = Int(r[1]); result["shown_crc32"] = got
                    result["crc_match"] = (got == String(format: "%08X", crc32v))
                    log("顯示中 slot \(r[1])，CRC32 \(got)，本機 \(String(format: "%08X", crc32v))，\(got == String(format: "%08X", crc32v) ? "吻合" : "不吻合")")
                }
                goodbye()
            }
        }
    }

    func goodbye() {
        command([0x09, 0x03], expect: 0x09) { [self] _ in finish(0) }
    }

    func finish(_ code: Int32) {
        if finished { return }
        finished = true
        result["exit_code"] = Int(code); result["events"] = events
        if let d = try? JSONSerialization.data(withJSONObject: result, options: [.prettyPrinted, .sortedKeys]) {
            try? d.write(to: URL(fileURLWithPath: outPath))
        }
        if let per = p { central.cancelPeripheralConnection(per) }
        print("結果已存到 \(outPath)")
        exit(code)
    }
}

let runner = Runner()
DispatchQueue.main.asyncAfter(deadline: .now() + timeout) {
    runner.log("整體逾時"); runner.finish(6)
}
RunLoop.main.run()
