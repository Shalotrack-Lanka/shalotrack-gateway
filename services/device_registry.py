import threading
from datetime import datetime, timezone

_by_imei: dict[str, dict] = {}
_by_socket: dict[int, str] = {}
_lock = threading.RLock()


def register_device(imei: str, ip: str, device_id: str, conn) -> None:
    with _lock:
        existing = _by_imei.get(imei)
        if existing:
            _by_socket.pop(id(existing["socket"]), None)

        entry = {
            "device_id": device_id,
            "ip": ip,
            "socket": conn,
            "connected_at": datetime.now(timezone.utc),
            "last_seen": datetime.now(timezone.utc),
        }
        _by_imei[imei] = entry
        _by_socket[id(conn)] = imei


def unregister_device(imei: str) -> None:
    with _lock:
        entry = _by_imei.pop(imei, None)
        if entry:
            _by_socket.pop(id(entry["socket"]), None)


def get_imei_by_socket(conn) -> str | None:
    with _lock:
        return _by_socket.get(id(conn))


def get_device(imei: str) -> dict | None:
    with _lock:
        return _by_imei.get(imei)


def update_last_seen(imei: str) -> None:
    with _lock:
        device = _by_imei.get(imei)
        if device:
            device["last_seen"] = datetime.now(timezone.utc)


def get_socket(imei: str):
    with _lock:
        device = _by_imei.get(imei)
        return device["socket"] if device else None


def is_online(imei: str) -> bool:
    with _lock:
        return imei in _by_imei


def get_all_devices() -> dict:
    with _lock:
        return dict(_by_imei)