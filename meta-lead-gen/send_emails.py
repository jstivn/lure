"""
Рассылка холодных писем (Шаг 1) через Gmail SMTP.
Получатели, шаблон и журнал: core/email_campaign.py. Тексты: email_template.json. Отправитель: config.json -> email.
Запуск из Lure (кнопка «Рассылка на почту») или вручную: python send_emails.py --send --limit 20
"""

import argparse
import logging
import random
import smtplib
import sys
import time

# Установка UTF-8 для консоли Windows
if sys.platform == "win32":
    import io
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "buffer"):
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from core import email_campaign as ec

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("email_sender")


def connect(settings):
    server = smtplib.SMTP(settings["smtp_host"], settings["smtp_port"], timeout=60)
    server.starttls()
    server.login(settings["sender"], settings["app_password"])
    return server


def main():
    parser = argparse.ArgumentParser(description="Рассылка холодных писем.")
    parser.add_argument("--send", action="store_true", help="Флаг реальной отправки писем")
    parser.add_argument("--test", help="Отправить 1 тестовое письмо на указанный email")
    parser.add_argument("--limit", type=int, default=50, help="Сколько писем отправить за прогон (по умолч. 50)")
    parser.add_argument("--delay-min", type=int, help="Мин. пауза между письмами, сек (по умолч. из config.json)")
    parser.add_argument("--delay-max", type=int, help="Макс. пауза между письмами, сек")
    parser.add_argument("--source", default="all", choices=["all", *ec.SOURCES],
                        help="leads: лиды из мессенджеров и Maps, rentals: автопрокаты с сайтов")
    parser.add_argument("--niche", default="all",
                        help="all или hook_niche получателей: beauty, renovation, rental, generic...")
    args = parser.parse_args()

    settings = ec.load_settings()
    if not settings["sender"] or not settings["app_password"]:
        logger.error("В config.json не заполнен блок email (sender, app_password).")
        sys.exit(1)

    delay_min = args.delay_min if args.delay_min is not None else settings["delay_min_sec"]
    delay_max = max(delay_min, args.delay_max if args.delay_max is not None else settings["delay_max_sec"])
    limit = max(1, min(args.limit, settings["max_per_run"]))

    template = ec.load_template()

    # Режим тестирования: одно письмо на указанный адрес, на примере первого получателя очереди
    if args.test:
        queue = ec.build_queue(args.source, args.niche)
        item = dict(queue[0]) if queue else {"name": "Demo Company", "hook_niche": "generic", "demo_link": ""}
        item["email"] = args.test
        subject, body = ec.render_message(item, template, settings)
        logger.info(f"Отправка тестового письма на {args.test}...")
        try:
            server = connect(settings)
            server.sendmail(settings["sender"], [args.test], ec.build_mime(settings, item, subject, body).as_string())
            server.quit()
            logger.info("Тестовое письмо успешно доставлено!")
        except Exception as e:
            logger.error(f"Ошибка отправки тестового письма: {e}")
            sys.exit(1)
        return

    queue = ec.build_queue(args.source, args.niche)
    total_available = len(queue)
    targets = queue[:limit]
    total = len(targets)
    logger.info(f"В очереди на этот прогон: {total} из {total_available} доступных получателей")

    if total == 0:
        logger.info("Все письма уже отправлены или список пуст.")
        return

    if not args.send:
        logger.warning("РЕЖИМ ПРЕДПРОСМОТРА (Dry-run). Письма не отправляются.")
        print("\nСписок получателей:")
        for idx, t in enumerate(targets, 1):
            print(f"  [{idx}/{total}] {t['name']} -> {t['email']} ({t['hook_niche']})")
        subject, body = ec.render_message(targets[0], template, settings)
        print(f"\nПример темы: {subject}\nПример текста:\n{body}\n")
        return

    logger.info("Подключение к Gmail SMTP...")
    try:
        server = connect(settings)
        logger.info("Авторизация в Gmail успешна. Начинаем отправку...")
    except Exception as e:
        logger.error(f"Не удалось подключиться к SMTP: {e}")
        sys.exit(1)

    sent_count = 0
    failed_count = 0
    for idx, t in enumerate(targets, 1):
        logger.info(f"[{idx}/{total}] Отправка: {t['name']} <{t['email']}>")
        subject, body = ec.render_message(t, template, settings)
        try:
            msg = ec.build_mime(settings, t, subject, body)
            try:
                server.sendmail(settings["sender"], [t["email"]], msg.as_string())
            except smtplib.SMTPServerDisconnected:
                logger.warning("Соединение разорвано, переподключаюсь...")
                server = connect(settings)
                server.sendmail(settings["sender"], [t["email"]], msg.as_string())
            ec.log_result(t, subject, "sent", message_id=msg["Message-ID"])
            sent_count += 1
            logger.info(f"  ✔ Письмо отправлено: {t['email']}")
        except smtplib.SMTPRecipientsRefused as e:
            ec.log_result(t, subject, "failed", str(e), permanent=True)
            failed_count += 1
            logger.error(f"  ✖ Ошибка: адрес отклонён {t['email']}: {e}")
        except smtplib.SMTPSenderRefused as e:
            logger.error(f"  ✖ Ошибка: Gmail отказал отправителю (вероятно, дневной лимит): {e}")
            break
        except Exception as e:
            ec.log_result(t, subject, "failed", str(e))
            failed_count += 1
            logger.error(f"  ✖ Ошибка при отправке на {t['email']}: {e}")

        if idx < total:
            delay = random.uniform(delay_min, delay_max)
            logger.info(f"  Пауза {delay:.0f} сек до следующего письма")
            time.sleep(delay)

    try:
        server.quit()
    except Exception:
        pass
    logger.info(f"Готово! Отправлено {sent_count} из {total}, ошибок {failed_count}.")


if __name__ == "__main__":
    main()

