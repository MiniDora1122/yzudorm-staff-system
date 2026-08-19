"""Dorm attendance kiosk: keyboard-emulating card reader + online account punch.

Device credentials and queued card payloads are protected with Windows DPAPI.
ENCRYPTED_HTTP additionally protects every API payload with AES-256-GCM.
"""

from __future__ import annotations

import base64
import ctypes
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


APP_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "DormAttendanceTerminal"
CONFIG_PATH = APP_DIR / "device.json"
DB_PATH = APP_DIR / "queue.db"


class ApiError(RuntimeError):
    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.status = status


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _blob(data: bytes):
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    return DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))), buffer


def dpapi(data: bytes, *, decrypt=False) -> bytes:
    if os.name != "nt":
        raise RuntimeError("打卡終端僅支援 Windows DPAPI。")
    source, keepalive = _blob(data)
    output = DATA_BLOB()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(DATA_BLOB), wintypes.LPCWSTR, ctypes.POINTER(DATA_BLOB),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DATA_BLOB),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(DATA_BLOB), ctypes.c_void_p, ctypes.POINTER(DATA_BLOB),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DATA_BLOB),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    function = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    if decrypt:
        ok = function(ctypes.byref(source), None, None, None, None, 0, ctypes.byref(output))
    else:
        ok = function(ctypes.byref(source), None, None, None, None, 0, ctypes.byref(output))
    if not ok:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(output.pbData, ctypes.c_void_p))


def load_config() -> dict | None:
    if not CONFIG_PATH.exists():
        return None
    data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    data["secret"] = dpapi(base64.b64decode(data.pop("secret_dpapi")), decrypt=True).decode()
    return data


def save_config(data: dict):
    APP_DIR.mkdir(parents=True, exist_ok=True)
    stored = dict(data)
    stored["secret_dpapi"] = base64.b64encode(dpapi(stored.pop("secret").encode())).decode()
    temporary = CONFIG_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(stored, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, CONFIG_PATH)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _transport_key(secret: bytes, direction: str) -> bytes:
    return hmac.new(secret, f"dorm-attendance/{direction}/v1".encode(), hashlib.sha256).digest()


def import_package(path: str, passphrase: str) -> dict:
    return import_package_data(Path(path).read_text(encoding="utf-8"), passphrase)


def import_package_data(package_text: str, passphrase: str) -> dict:
    envelope = json.loads(package_text)
    if envelope.get("format") != "dorm-attendance-provision-v1":
        raise ValueError("不是有效的打卡終端註冊包。")
    salt, nonce = _unb64(envelope["salt"]), _unb64(envelope["nonce"])
    key = PBKDF2HMAC(
        algorithm=hashes.SHA256(), length=32, salt=salt,
        iterations=int(envelope.get("iterations", 300000)),
    ).derive(passphrase.encode())
    plaintext = AESGCM(key).decrypt(
        nonce, _unb64(envelope["ciphertext"]), b"dorm-attendance-provision-v1"
    )
    config = json.loads(plaintext)
    if config.get("format") != "dorm-attendance-device-v1":
        raise ValueError("註冊包內容格式不相容。")
    save_config(config)
    return config


class Queue:
    def __init__(self):
        APP_DIR.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(DB_PATH, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS queue (sequence INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE, payload BLOB NOT NULL, synced_at TEXT, response TEXT)")
        self.db.commit()
        self.lock = threading.Lock()

    def add_card(self, uid: str) -> dict:
        event_id = str(uuid.uuid4())
        with self.lock:
            cursor = self.db.execute("INSERT INTO queue(event_id,payload) VALUES(?,?)", (event_id, b""))
            sequence = cursor.lastrowid
            payload = {
                "event_id": event_id, "sequence": sequence, "occurred_at": datetime.now().astimezone().isoformat(),
                "method": "CARD", "card_uid": uid, "offline": False,
            }
            self.db.execute("UPDATE queue SET payload=? WHERE sequence=?", (dpapi(json.dumps(payload).encode()), sequence))
            self.db.commit()
        return payload

    def pending(self):
        with self.lock:
            rows = self.db.execute("SELECT sequence,event_id,payload FROM queue WHERE synced_at IS NULL ORDER BY sequence").fetchall()
        for sequence, event_id, protected in rows:
            yield sequence, event_id, json.loads(dpapi(protected, decrypt=True))

    def synced(self, sequence: int, response: dict):
        with self.lock:
            protected = base64.b64encode(dpapi(json.dumps(response, ensure_ascii=False).encode())).decode()
            self.db.execute("UPDATE queue SET synced_at=?,response=? WHERE sequence=?", (datetime.now().astimezone().isoformat(), protected, sequence))
            self.db.commit()

    def next_sequence(self) -> int:
        with self.lock:
            return int(self.db.execute("SELECT COALESCE(MAX(sequence),0)+1 FROM queue").fetchone()[0])


def unsigned_json(url: str, path: str, payload: dict) -> dict:
    body = json.dumps(payload, separators=(",", ":")).encode()
    request = urllib.request.Request(url.rstrip("/") + path, body, {"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read())


def signed_json(config: dict, path: str, payload: dict) -> dict:
    if config.get("transport_mode") == "ENCRYPTED_HTTP":
        return encrypted_json(config, path, payload)
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    timestamp = str(int(time.time()))
    nonce = secrets.token_hex(16)
    canonical = "\n".join(["POST", path, timestamp, nonce, hashlib.sha256(body).hexdigest()]).encode()
    signature = hmac.new(config["secret"].encode(), canonical, hashlib.sha256).hexdigest()
    headers = {
        "Content-Type": "application/json", "X-Attendance-Device": config["device_id"],
        "X-Attendance-Timestamp": timestamp, "X-Attendance-Nonce": nonce,
        "X-Attendance-Signature": signature,
    }
    request = urllib.request.Request(config["server"].rstrip("/") + path, body, headers)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        try:
            problem = json.loads(exc.read())["error"]
            message = problem["message"]
        except Exception:
            message = f"伺服器錯誤 HTTP {exc.code}"
        raise ApiError(message, exc.code) from exc


def encrypted_json(config: dict, path: str, payload: dict) -> dict:
    request_id = secrets.token_hex(16)
    timestamp = str(int(time.time()))
    nonce = secrets.token_bytes(12)
    method = "POST"
    aad = "\n".join([
        method, path, config["device_id"], request_id, timestamp, "1"
    ]).encode()
    plaintext = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    secret = config["secret"].encode()
    envelope = {
        "device_id": config["device_id"], "request_id": request_id,
        "timestamp": timestamp, "key_version": 1, "nonce": _b64(nonce),
        "ciphertext": _b64(AESGCM(_transport_key(secret, "request")).encrypt(nonce, plaintext, aad)),
    }
    body = json.dumps(envelope, separators=(",", ":")).encode()
    request = urllib.request.Request(
        config["server"].rstrip("/") + path, body, {"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            response_status, response_body = response.status, response.read()
    except urllib.error.HTTPError as exc:
        response_status, response_body = exc.code, exc.read()
    try:
        response_envelope = json.loads(response_body)
        if response_envelope.get("request_id") != request_id or int(response_envelope["status"]) != response_status:
            raise ValueError
        response_nonce = _unb64(response_envelope["nonce"])
        response_aad = "\n".join([
            str(response_status), path, config["device_id"], request_id, "1"
        ]).encode()
        result = json.loads(AESGCM(_transport_key(secret, "response")).decrypt(
            response_nonce, _unb64(response_envelope["ciphertext"]), response_aad
        ))
    except Exception as exc:
        raise RuntimeError(f"伺服器回應無法驗證（HTTP {response_status}）。") from exc
    if response_status >= 400:
        raise ApiError(result.get("error", {}).get("message", f"伺服器錯誤 HTTP {response_status}"), response_status)
    return result


KIOSK_HTML = """<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>宿舍工讀生上下班打卡</title><style>
*{box-sizing:border-box}body{margin:0;font-family:"Microsoft JhengHei UI",sans-serif;background:#f3f6fb;color:#102a43}.top{background:#0e3768;color:#fff;padding:18px 28px}.top small{color:#bdd7f2}.wrap{max-width:980px;margin:34px auto;padding:0 22px}.card{background:#fff;border-radius:18px;box-shadow:0 10px 30px #17324d18;padding:42px;text-align:center}.eyebrow{color:#1556a3;font-size:13px;font-weight:800;letter-spacing:.12em}.status{font-size:34px;font-weight:800;margin:18px 0 8px}.detail{color:#61758a;min-height:28px}.scan-box{max-width:520px;margin:26px auto 0;text-align:left}.scan-box label{font-weight:700}.scan{font-size:20px;letter-spacing:.08em;text-align:center;border:2px solid #8fb5df}.scan:focus{border-color:#1556a3;box-shadow:0 0 0 4px #1556a322;outline:0}.actions{display:flex;gap:12px;justify-content:center;margin-top:28px;flex-wrap:wrap}button{border:0;border-radius:10px;padding:13px 22px;font:600 16px inherit;cursor:pointer}.primary{background:#1556a3;color:#fff}.muted{background:#e8eef7;color:#12355b}.warning{color:#b54708}.error{color:#b42318}.success{color:#198754}dialog{border:0;border-radius:16px;padding:26px;box-shadow:0 20px 60px #0004;min-width:min(440px,90vw)}label{display:block;text-align:left;margin:12px 0 5px}input{width:100%;padding:11px;border:1px solid #cbd5e1;border-radius:8px;font:inherit}.hint{font-size:13px;color:#708090;margin-top:16px}@media(max-width:600px){.card{padding:28px 18px}.status{font-size:26px}}
</style><body><header class="top"><b>宿舍工讀生上下班打卡</b><br><small>Dorm Attendance Terminal</small></header><main class="wrap"><section class="card"><div class="eyebrow" id="device">ATTENDANCE</div><div class="status" id="status">請刷學生證或使用帳號打卡</div><div class="detail" id="detail">Scan student card or use your account</div><div class="scan-box"><label for="card">學生證刷卡區 <small>Student card scanner</small></label><input class="scan" id="card" autocomplete="off" inputmode="none" placeholder="請刷學生證，讀卡機輸入 UID 後按 Enter" autofocus><div class="hint">請先點一下此欄位再刷卡；一般 USB 讀卡機會像鍵盤一樣輸入卡片 UID 並送出 Enter。</div></div><div class="actions"><button class="primary" onclick="account.showModal()">使用帳號打卡 <small>Account</small></button><button class="muted" onclick="setup.showModal()">裝置註冊 <small>Device setup</small></button></div><p class="hint">刷卡後請勿重複操作；離線刷卡會安全保存並自動同步。</p></section></main>
<dialog id="account"><form method="dialog" onsubmit="accountPunch(event)"><h2>帳號打卡 <small>Account punch</small></h2><label>帳號 Username</label><input id="username" required><label>密碼 Password</label><input id="password" type="password" required><div class="actions"><button class="muted" type="button" onclick="account.close()">取消</button><button class="primary">打卡</button></div></form></dialog>
<dialog id="setup"><form method="dialog" onsubmit="register(event)"><h2>匯入加密註冊包</h2><label>.dormclock 檔案</label><input id="package" type="file" accept=".dormclock" required><label>註冊包密碼</label><input id="packagePassword" type="password" required><div class="actions"><button class="muted" type="button" onclick="setup.close()">取消</button><button class="primary">匯入</button></div></form></dialog>
<script>const token='__TOKEN__',card=document.querySelector('#card'),statusEl=document.querySelector('#status'),detail=document.querySelector('#detail');
async function api(path,data){let r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-Kiosk-Token':token},body:JSON.stringify(data)}),j=await r.json();if(!r.ok)throw Error(j.error||'操作失敗');return j}function show(t,d='',kind=''){statusEl.textContent=t;statusEl.className='status '+kind;detail.textContent=d;setTimeout(()=>{statusEl.textContent='請刷學生證或使用帳號打卡';statusEl.className='status';detail.textContent='Scan student card or use your account'},6000)}
function handle(r){let direction={IN:'上班',OUT:'下班',UNKNOWN:'待確認'}[r.direction]||r.direction;show(`${r.student}｜${direction}打卡成功`,`${r.location}｜${r.shift||'未對應排班'}`,r.status==='NORMAL'?'success':'warning');if(r.requires_reason)setTimeout(()=>reason(r),200)}
async function reason(r){let category=prompt('原因分類（例如交通延誤、忘記刷卡）','其他'),text=prompt('請說明原因；取消可稍後至學生系統填寫');if(!text)return;let arrival=r.requires_arrival_time?prompt('實際到班時間，例如 2026-08-19T09:03'):null;try{await api('/reason',{event_id:r.event_id,category:category||'其他',reason:text,claimed_arrival_at:arrival});show('事由已送交管理員','打卡紀錄已保留。','success')}catch(e){show('事由尚未送出',e.message,'error')}}
card.addEventListener('keydown',async e=>{if(e.key!=='Enter')return;let uid=card.value.trim();card.value='';if(!uid)return;try{handle(await api('/card',{uid}))}catch(e){show('刷卡未受理',e.message,'error')}});async function accountPunch(e){e.preventDefault();let u=username.value,p=password.value;password.value='';account.close();try{handle(await api('/account',{username:u,password:p}))}catch(x){show('帳號打卡失敗',x.message,'error')}}
async function register(e){e.preventDefault();let f=document.querySelector('#package').files[0],p=packagePassword.value;if(!f)return;setup.close();try{let r=await api('/register',{package:await f.text(),password:p});show('裝置註冊成功',`${r.device_name}｜${r.location}`,'success');load()}catch(x){show('註冊失敗',x.message,'error')}}async function load(){try{let r=await api('/status',{});document.querySelector('#device').textContent=r.registered?`${r.device_name}｜${r.location}`:'尚未註冊｜Not registered'}catch{}}setInterval(()=>{if(!document.querySelector('dialog[open]')&&document.activeElement!==card)card.focus()},500);load();card.focus();</script></body></html>"""


class Kiosk:
    def __init__(self, control_token: str = ""):
        self.queue = Queue()
        self.config_data = load_config()
        self.token = secrets.token_urlsafe(32)
        self.control_token = control_token
        self.lock = threading.Lock()

    def punch_card(self, uid: str) -> dict:
        if not self.config_data:
            raise RuntimeError("裝置尚未完成註冊。")
        payload = self.queue.add_card(uid)
        try:
            result = signed_json(self.config_data, "/attendance-api/punch", payload)
            self.queue.synced(payload["sequence"], result)
            return result
        except ApiError as exc:
            self.queue.synced(payload["sequence"], {"error": str(exc), "status": exc.status})
            raise
        except Exception:
            return {"student": "刷卡已保存", "direction": "UNKNOWN", "location": self.config_data["location"], "shift": "中央離線，恢復後自動同步", "status": "OFFLINE", "requires_reason": False}

    def punch_account(self, username: str, password: str) -> dict:
        if not self.config_data:
            raise RuntimeError("裝置尚未完成註冊。")
        payload = {"event_id": str(uuid.uuid4()), "sequence": self.queue.next_sequence(), "occurred_at": datetime.now().astimezone().isoformat(), "method": "ACCOUNT", "username": username, "password": password, "offline": False}
        return signed_json(self.config_data, "/attendance-api/punch", payload)

    def sync(self):
        while True:
            if self.config_data:
                for sequence, _event_id, payload in self.queue.pending():
                    payload["offline"] = True
                    try:
                        self.queue.synced(sequence, signed_json(self.config_data, "/attendance-api/punch", payload))
                    except ApiError as exc:
                        if exc.status < 500 and exc.status not in {408, 429}:
                            self.queue.synced(sequence, {"error": str(exc), "status": exc.status})
                            continue
                        break
                    except Exception:
                        break
            time.sleep(30)


def handler_for(kiosk: Kiosk):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, payload: bytes, content_type="application/json; charset=utf-8"):
            self.send_response(status); self.send_header("Content-Type", content_type); self.send_header("Content-Length", str(len(payload))); self.send_header("Cache-Control", "no-store"); self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'"); self.send_header("X-Content-Type-Options", "nosniff"); self.send_header("X-Frame-Options", "DENY"); self.send_header("Referrer-Policy", "no-referrer"); self.end_headers(); self.wfile.write(payload)

        def do_GET(self):
            if self.path == "/health":
                self._send(200, b'{"running":true}')
            elif self.path == "/":
                self._send(200, KIOSK_HTML.replace("__TOKEN__", kiosk.token).encode(), "text/html; charset=utf-8")
            else:
                self._send(404, b"{}")

        def do_POST(self):
            try:
                if self.path == "/__shutdown":
                    if not kiosk.control_token or self.headers.get("X-Control-Token") != kiosk.control_token:
                        raise PermissionError("停止打卡驗證失敗。")
                    self._send(200, b'{"stopping":true}')
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                    return
                if self.headers.get("X-Kiosk-Token") != kiosk.token:
                    raise PermissionError("本機操作驗證失敗，請重新開啟打卡畫面。")
                length = int(self.headers.get("Content-Length", "0"))
                if length > 300_000: raise ValueError("資料過大。")
                data = json.loads(self.rfile.read(length) or b"{}")
                if self.path == "/card": result = kiosk.punch_card(str(data.get("uid", "")))
                elif self.path == "/account": result = kiosk.punch_account(str(data.get("username", "")), str(data.get("password", "")))
                elif self.path == "/register":
                    kiosk.config_data = import_package_data(str(data.get("package", "")), str(data.get("password", "")))
                    result = {"device_name": kiosk.config_data["device_name"], "location": kiosk.config_data["location"]}
                elif self.path == "/reason":
                    if not kiosk.config_data: raise RuntimeError("裝置尚未註冊。")
                    result = signed_json(kiosk.config_data, f"/attendance-api/events/{data.get('event_id', '')}/reason", {"category": data.get("category", "其他"), "reason": data.get("reason", ""), "claimed_arrival_at": data.get("claimed_arrival_at")})
                elif self.path == "/status":
                    result = {"registered": bool(kiosk.config_data), "device_name": kiosk.config_data.get("device_name") if kiosk.config_data else None, "location": kiosk.config_data.get("location") if kiosk.config_data else None}
                else: self._send(404, b'{"error":"Not found"}'); return
                self._send(200, json.dumps(result, ensure_ascii=False).encode())
            except Exception as exc:
                message = str(exc) if isinstance(exc, (ApiError, RuntimeError, PermissionError, ValueError)) else "操作失敗，請檢查註冊包或網路。"
                self._send(400, json.dumps({"error": message}, ensure_ascii=False).encode())

        def log_message(self, _format, *_args):
            return
    return Handler


if __name__ == "__main__":
    control_token = next((arg.split("=", 1)[1] for arg in sys.argv[1:] if arg.startswith("--control-token=")), "")
    kiosk = Kiosk(control_token)
    threading.Thread(target=kiosk.sync, daemon=True).start()
    ThreadingHTTPServer(("127.0.0.1", 47831), handler_for(kiosk)).serve_forever()
