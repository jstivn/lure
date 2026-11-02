"""
Письмо 2: тем, кто ответил на письмо 1, уходит ответ в той же цепочке с картинкой-макетом.
Ответы ищутся во входящих того же Gmail (IMAP). Короткий отказ («no», «stop», «unsubscribe») письма 2 не получает,
адрес уходит в стоп-лист. Запуск из Lure или вручную: python send_followups.py --send
"""

import argparse
import csv
import email
import email.utils
import imaplib
import json
import logging
import random
import re
import smtplib
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid
from pathlib import Path

from send_emails import connect  # заодно включает UTF-8 для консоли Windows
from core import email_campaign as ec

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("email_followup")

IMAP_HOST = "imap.gmail.com"
FOLLOWUPS_PATH = ec.DATA_DIR / "email_followups.csv"
FOLLOWUP_FIELDS = ["email", "sent_at", "status", "note"]
MOCKUP_DIR = ec.DATA_DIR / "mockups"

OPT_OUT_RE = re.compile(r"\b(unsubscribe|not interested|not relevant|remove me|stop (emailing|writing|contacting)|don'?t (email|contact|write))\b", re.I)
BARE_NO_RE = re.compile(r"^\W*(no|nope|no thanks?|no thank you|stop)\W*$", re.I)


def is_opt_out(text: str) -> bool:
    """Короткий отказ: голое «no» или явная просьба не писать. Длинные содержательные ответы отказом не считаются."""
    first = next((ln for ln in text.splitlines() if ln.strip()), "")
    return len(text) < 200 and bool(BARE_NO_RE.match(first) and len(text) < 40 or OPT_OUT_RE.search(text))
AUTO_SUBJECT_RE = re.compile(r"auto(matic)?[- ]?reply|out of office|autoreply|away from|undeliver|delivery status", re.I)


def read_followups() -> dict:
    rows = ec._read_csv(FOLLOWUPS_PATH)
    return {r["email"].lower(): r for r in rows if r.get("email")}


def log_followup(addr: str, status: str, note: str = ""):
    FOLLOWUPS_PATH.parent.mkdir(parents=True, exist_ok=True)
    is_new = not FOLLOWUPS_PATH.exists() or FOLLOWUPS_PATH.stat().st_size == 0
    with open(FOLLOWUPS_PATH, "a", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FOLLOWUP_FIELDS)
        if is_new:
            w.writeheader()
        w.writerow({"email": addr.lower(), "sent_at": datetime.now().isoformat(timespec="seconds"),
                    "status": status, "note": note[:200]})


def header_text(msg, name: str) -> str:
    try:
        return str(make_header(decode_header(msg.get(name, "")))).strip()
    except Exception:
        return msg.get(name, "") or ""


def own_text(msg) -> str:
    """Текст ответа без цитирования нашего письма."""
    part = msg
    if msg.is_multipart():
        part = next((p for p in msg.walk() if p.get_content_type() == "text/plain"), None)
        if part is None:
            return ""
    try:
        text = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", errors="replace")
    except Exception:
        return ""
    lines = []
    for ln in text.splitlines():
        if ln.startswith(">") or re.match(r"^(On .+wrote:|Le .+écrit)", ln.strip()):
            break
        lines.append(ln)
    return "\n".join(lines).strip()


def is_auto(msg) -> bool:
    auto = (msg.get("Auto-Submitted") or "no").lower()
    if auto != "no" or msg.get("X-Autoreply") or msg.get("X-Autorespond"):
        return True
    if (msg.get("Precedence") or "").lower() in ("bulk", "auto_reply", "junk"):
        return True
    sender = (msg.get("From") or "").lower()
    return bool(AUTO_SUBJECT_RE.search(header_text(msg, "Subject"))) or "mailer-daemon" in sender or "postmaster" in sender


def find_reply(imap, row):
    """Первое настоящее письмо от получателя после нашего: по заголовку In-Reply-To или по адресу отправителя."""
    since = (datetime.fromisoformat(row["sent_at"]) - timedelta(days=1)).strftime("%d-%b-%Y")
    ids = []
    if row.get("message_id"):
        typ, data = imap.search(None, "HEADER", "In-Reply-To", f'"{row["message_id"]}"')
        if typ == "OK" and data[0]:
            ids += data[0].split()
    typ, data = imap.search(None, "SINCE", since, "FROM", f'"{row["email"]}"')
    if typ == "OK" and data[0]:
        ids += [i for i in data[0].split() if i not in ids]
    for num in ids:
        typ, raw = imap.fetch(num, "(RFC822)")
        if typ != "OK" or not raw or not raw[0]:
            continue
        msg = email.message_from_bytes(raw[0][1])
        if is_auto(msg):
            continue
        return msg
    return None


def make_mockup(lead_name: str, lead_id) -> Path | None:
    """PNG-макет для лида: по id лида в базе или по названию (core.mockup рисует в Playwright)."""
    py = ec.META_DIR / "venv" / "Scripts" / "python.exe"
    args = ["--meta-id", str(lead_id)] if lead_id else ["--name", lead_name, "--geo", "AE_DUBAI"]
    try:
        proc = subprocess.run([str(py if py.exists() else sys.executable), "-m", "core.mockup", *args, "--out", str(MOCKUP_DIR)],
                              cwd=str(ec.META_DIR), capture_output=True, text=True, encoding="utf-8", timeout=150)
        out = json.loads(proc.stdout.strip().splitlines()[-1])
        path = Path(out["path"])
        return path if path.exists() else None
    except Exception as e:
        logger.error(f"  Не удалось нарисовать макет для {lead_name}: {e}")
        return None


def lead_info(addr: str):
    """(id в leads.db, mockup_path) по адресу почты."""
    try:
        conn = sqlite3.connect(str(ec.DB_PATH), timeout=30.0)
        try:
            row = conn.execute("SELECT id, mockup_path FROM leads WHERE lower(email) = ?", (addr.lower(),)).fetchone()
        finally:
            conn.close()
        return (row[0], row[1]) if row else (None, None)
    except sqlite3.Error:
        return None, None


def mark_replied(addr: str):
    now = datetime.now(timezone.utc).isoformat()
    try:
        conn = sqlite3.connect(str(ec.DB_PATH), timeout=30.0)
        try:
            conn.execute("UPDATE leads SET replied_at = ?, updated_at = ? WHERE lower(email) = ? AND replied_at IS NULL",
                         (now, now, addr.lower()))
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error:
        pass


def build_followup(settings, template, row, reply, image: Path) -> MIMEMultipart:
    name = ec.clean_company_name(row.get("business_name", ""))
    body = ec.spin(template.get("followup_body", "")).replace("[BUSINESS]", name)
    msg = MIMEMultipart()
    orig_subject = row.get("subject") or "Question"
    msg["Subject"] = orig_subject if orig_subject.lower().startswith("re:") else f"Re: {orig_subject}"
    msg["From"] = f"{settings['sender_name']} <{settings['sender']}>"
    msg["To"] = row["email"]
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=settings["sender"].rsplit("@", 1)[-1])
    reply_id = reply.get("Message-ID", "").strip()
    refs = " ".join(x for x in (row.get("message_id", ""), reply_id) if x)
    msg["In-Reply-To"] = reply_id or row.get("message_id", "")
    msg["References"] = refs
    msg.attach(MIMEText(body, "plain", "utf-8"))
    img = MIMEImage(image.read_bytes(), _subtype=image.suffix.lstrip(".").lower().replace("jpg", "jpeg"))
    img.add_header("Content-Disposition", "attachment", filename=image.name)
    msg.attach(img)
    return msg


def main():
    parser = argparse.ArgumentParser(description="Письмо 2 тем, кто ответил на письмо 1.")
    parser.add_argument("--send", action="store_true", help="Реально отправить (без флага только показать, кто ответил)")
    parser.add_argument("--limit", type=int, default=10, help="Максимум писем 2 за прогон")
    parser.add_argument("--days", type=int, default=14, help="Смотреть ответы на письма за последние N дней")
    args = parser.parse_args()

    settings = ec.load_settings()
    template = ec.load_template()
    if not settings["sender"] or not settings["app_password"]:
        logger.error("В config.json не заполнен блок email.")
        sys.exit(1)

    done = read_followups()
    cutoff = datetime.now() - timedelta(days=args.days)
    waiting = {}
    for r in ec._read_csv(ec.LOG_PATH):
        addr = (r.get("email") or "").lower()
        try:
            fresh = datetime.fromisoformat(r["sent_at"]) >= cutoff
        except (KeyError, ValueError):
            continue
        if r.get("status") == "sent" and fresh and addr not in done and r.get("message_id"):
            waiting[addr] = r
    logger.info(f"Ждут ответа: {len(waiting)} адресов")
    if not waiting:
        logger.info("Готово! Отправлено 0 из 0, ошибок 0.")
        return

    try:
        imap = imaplib.IMAP4_SSL(IMAP_HOST)
        imap.login(settings["sender"], settings["app_password"])
        imap.select("INBOX", readonly=True)
    except Exception as e:
        logger.error(f"Не удалось подключиться к почте (IMAP): {e}")
        sys.exit(1)

    replies = []
    for addr, row in waiting.items():
        try:
            msg = find_reply(imap, row)
        except Exception as e:
            logger.error(f"Ошибка поиска ответа от {addr}: {e}")
            continue
        if not msg:
            continue
        text = own_text(msg)
        if is_opt_out(text):
            logger.info(f"Отказ от {addr}: «{text[:60]}», больше не пишем")
            ec._append_line(ec.FAILED_PATH, addr)
            log_followup(addr, "optout", text)
            mark_replied(addr)
            continue
        logger.info(f"Ответ от {row.get('business_name') or addr} <{addr}>")
        mark_replied(addr)
        replies.append((row, msg))
    try:
        imap.logout()
    except Exception:
        pass

    logger.info(f"Новых ответов: {len(replies)}")
    targets = replies[:max(1, args.limit)]
    total = len(targets)
    if not total or not args.send:
        if total:
            logger.warning("РЕЖИМ ПРЕДПРОСМОТРА: письма 2 не отправляются (нужен флаг --send).")
        logger.info(f"Готово! Отправлено 0 из {total}, ошибок 0.")
        return

    try:
        server = connect(settings)
    except Exception as e:
        logger.error(f"Не удалось подключиться к SMTP: {e}")
        sys.exit(1)

    sent_count = failed = 0
    for idx, (row, reply) in enumerate(targets, 1):
        addr = row["email"]
        logger.info(f"[{idx}/{total}] Отправка: {row.get('business_name') or addr} <{addr}>")
        lead_id, mockup = lead_info(addr)
        image = Path(mockup) if mockup and Path(mockup).exists() else make_mockup(row.get("business_name", ""), lead_id)
        if not image:
            failed += 1
            logger.error(f"  ✖ Ошибка: нет макета для {addr}, письмо 2 не отправлено (повторю в следующий раз)")
            continue
        try:
            msg = build_followup(settings, template, row, reply, image)
            try:
                server.sendmail(settings["sender"], [addr], msg.as_string())
            except smtplib.SMTPServerDisconnected:
                server = connect(settings)
                server.sendmail(settings["sender"], [addr], msg.as_string())
            log_followup(addr, "sent", image.name)
            sent_count += 1
            logger.info(f"  ✔ Письмо 2 отправлено: {addr}")
        except Exception as e:
            failed += 1
            logger.error(f"  ✖ Ошибка при отправке на {addr}: {e}")
        if idx < total:
            time.sleep(random.uniform(15, 30))

    try:
        server.quit()
    except Exception:
        pass
    logger.info(f"Готово! Отправлено {sent_count} из {total}, ошибок {failed}.")


if __name__ == "__main__":
    main()
