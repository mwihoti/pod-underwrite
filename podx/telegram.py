"""Telegram bot: send a task and a netlist, get the same verdict.

Deliberately thin. It calls `engine.audit()` exactly like the web form does; if
this file grows a second code path, the bot has become a second product.
"""

from __future__ import annotations

from typing import Any

import httpx

from . import engine, samples, store
from .config import settings

_subscribers: set[int] = set()


def _api(method: str, payload: dict) -> dict | None:
    s = settings()
    if not s.telegram_token:
        return None
    try:
        with httpx.Client(timeout=15) as c:
            r = c.post(f"https://api.telegram.org/bot{s.telegram_token}/{method}", json=payload)
            return r.json() if r.content else None
    except Exception:
        return None


def send(chat_id: int, text: str) -> None:
    _api("sendMessage", {"chat_id": chat_id, "text": text[:3800], "disable_web_page_preview": False})


def broadcast(text: str) -> None:
    for chat in list(_subscribers):
        send(chat, text)


def _compose(report: dict, audit_id: int) -> str:
    verdict = report.get("verdict", "?")
    sentence = report.get("sentence", "")
    prov = report.get("provenance") or {}
    base = settings().base_url
    lines = [f"{verdict}: {sentence}"]
    cx = report.get("counterexample")
    if cx:
        lines.append(f"counterexample input {cx['input']} (official {cx['expected']}, got {cx['got']})")
    econ = report.get("economics") or {}
    if econ.get("cost") is not None:
        seat = f" vs seat {econ['seat_best']}" if econ.get("seat_best") else ""
        lines.append(f"C={econ['cost']}{seat}, depth={econ.get('depth')}, nand={econ.get('nand')}, latch={econ.get('latch')}")
    if prov.get("block"):
        lines.append(f"pinned at block {prov['block']} ({prov.get('block_time')})")
    if base and audit_id:
        lines.append(f"{base}/a/{audit_id}")
    return "\n".join(lines)


def _parse(text: str) -> tuple[int, str] | None:
    """Accept `12 0x00…` or a sample slug, and nothing else.

    Netlists are long, so a chat is not the place to paste one by hand; the
    practical path is `/sample inverter` or a task id with a sample attached.
    """
    parts = text.split()
    if not parts:
        return None
    head = parts[0].lower()
    if head in ("/sample", "sample") and len(parts) > 1:
        s = samples.by_slug(parts[1])
        return (s.task_id, s.hex) if s else None
    if len(parts) >= 2 and parts[0].isdigit() and parts[1].startswith(("0x", "0X")):
        return int(parts[0]), parts[1]
    if head in ("/start", "/help"):
        return None
    return None


async def handle_update(update: dict[str, Any]) -> None:
    msg = update.get("message") or update.get("edited_message") or {}
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    text = (msg.get("text") or "").strip()
    if not chat_id:
        return
    _subscribers.add(int(chat_id))
    if not text or text.startswith(("/start", "/help")):
        send(
            int(chat_id),
            "PoD Underwrite — pre-purchase audits for PoD circuits on X Layer.\n\n"
            "Send: <task id> <netlist hex>\n"
            "Or:   /sample inverter | /sample nand2 | /sample wrong-inverter\n\n"
            "You get the verdict, the counterexample if there is one, the cost, and a link.",
        )
        return
    parsed = _parse(text)
    if parsed is None:
        send(int(chat_id), "Could not read that. Send `<task id> <netlist hex>` or `/sample inverter`.")
        return
    task_id, netlist_hex = parsed
    try:
        result = await __import__("asyncio").to_thread(
            engine.audit, engine.AuditRequest(task_id=task_id, netlist_hex=netlist_hex)
        )
        audit_id = store.save_audit(result)
        send(int(chat_id), _compose(result.as_dict(), audit_id))
    except Exception as exc:
        send(int(chat_id), f"Audit failed: {type(exc).__name__}: {exc}")
