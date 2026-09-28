"""
Фоновая задача: раз в config.SCHEDULER_TICK_SECONDS проверяет, каким
рассылкам пора отправляться, и рассылает их во все активные целевые чаты
через forward_message (форвард исходного поста рекламодателя из ЛС бота).
Между КАЖДОЙ отправкой - задержка config.SEND_DELAY_SECONDS против флуда.

send_broadcast() вынесена отдельно и переиспользуется в двух местах:
  1. здесь, в фоновом тике - для плановых повторных отправок;
  2. в handlers_user.cb_toggle - чтобы рассылка уходила мгновенно в момент
     включения, а не только после того, как истечёт первый интервал.

Общий замок (_send_lock) на весь бот: пауза между отправками (SEND_DELAY_
SECONDS) защищает только ВНУТРИ одного вызова send_broadcast(). Если два
вызова стартуют почти одновременно (например, кто-то нажал "Включить"
ровно в тот момент, когда фоновый тик шлёт плановую рассылку), без общего
замка их отправки могут наложиться друг на друга. Правило простое: если
сейчас ничего не отправляется - отправляй сразу; если что-то уже
отправляется - жди своей очереди. Захватывается замок в начале функции и
держится всё время, пока идёт рассылка (включая паузы между чатами) - так
получается единая очередь на все рассылки бота, независимо от того, что их
вызвало."""

import asyncio
import logging

from aiogram import Bot

import config
import database

log = logging.getLogger(__name__)

# Общий на весь бот - им и защищаемся от наложения параллельных отправок.
_send_lock = asyncio.Lock()


async def send_broadcast(bot: Bot, broadcast) -> int:
    """Пересылает пост одной рассылки во все активные целевые чаты.
    Возвращает число чатов, куда отправка прошла успешно (0, если целевых
    чатов ещё нет или пост не загружен).

    Захватывает общий _send_lock на всё время работы - если параллельно
    вызвана ещё одна рассылка (из планировщика или по кнопке "Включить"),
    она дождётся освобождения замка и только потом начнёт отправку."""
    if not broadcast["source_message_id"]:
        return 0
    async with _send_lock:
        targets = await database.list_target_chats()
        sent = 0
        for target in targets:
            try:
                await bot.forward_message(
                    chat_id=target["chat_id"],
                    from_chat_id=broadcast["source_chat_id"],
                    message_id=broadcast["source_message_id"],
                )
                sent += 1
                await database.log_send(broadcast["id"], broadcast["key_id"], target["chat_id"])
            except Exception as exc:
                log.exception(
                    "Не удалось переслать рассылку id=%s в чат %s",
                    broadcast["id"], target["chat_id"],
                )
                await database.log_send_error(broadcast["id"], broadcast["key_id"], target["chat_id"], str(exc))
            await asyncio.sleep(config.SEND_DELAY_SECONDS)
        return sent


async def run_scheduler(bot: Bot) -> None:
    while True:
        try:
            await _tick(bot)
        except Exception:
            log.exception("Ошибка в планировщике рассылок")
        await asyncio.sleep(config.SCHEDULER_TICK_SECONDS)


async def _notify_orphans_disabled(bot: Bot, orphans) -> None:
    """Рассылка потеряла ключ (обычно - правка базы в обход бота, не через
    /adm) - deactivate_orphan_broadcasts() уже её выключила, здесь только
    сообщаем админам, чтобы не потерялось молча. Шлётся один раз - на
    следующем тике эти рассылки уже is_active=0 и сюда не попадут снова."""
    lines = ["⚠️ Нашёл рассылки без ключа и выключил их:"]
    for b in orphans:
        lines.append(f"  #{b['id']} «{b['title']}» (интервал {b['interval_hours']} ч.)")
    lines.append("\nПодробнее: /adm -> Рассылки -> Без ключа.")
    text = "\n".join(lines)
    for admin_id in await database.list_admins():
        try:
            await bot.send_message(admin_id, text)
        except Exception:
            pass  # админ мог не открыть ЛС с ботом - не критично


async def _tick(bot: Bot) -> None:
    orphans = await database.deactivate_orphan_broadcasts()
    if orphans:
        await _notify_orphans_disabled(bot, orphans)

    due = await database.due_broadcasts()
    if not due:
        return
    for broadcast in due:
        if not broadcast["source_message_id"]:
            # пост не загружен - пропускаем, но всё равно переносим время,
            # чтобы не спамить проверками каждый тик
            await database.reschedule_broadcast(broadcast["id"], broadcast["interval_hours"])
            continue
        await send_broadcast(bot, broadcast)
        await database.reschedule_broadcast(broadcast["id"], broadcast["interval_hours"])
