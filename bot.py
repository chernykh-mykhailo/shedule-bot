import emoji
import re
import asyncio
import copy
import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta

import pytz
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.error import BadRequest
from apscheduler.schedulers.background import BackgroundScheduler

from config import TELEGRAM_TOKEN  # Імпорт токену з конфігураційного файлу
from config import ADMIN_IDS  # Імпорт списку з айдішками адмінів


def keep_alive():
    while True:
        print("Bot is still running...")
        time.sleep(1800)  # 1800 секунд = 30 хвилин


def remove_emoji(text):
    return emoji.replace_emoji(text, replace='')


def format_name(name):
    # Зберігаємо перший емодзі
    first_emoji = ''
    for c in name:
        if emoji.is_emoji(c):
            first_emoji = c  # Зберігаємо перший емодзі
            break  # Зупиняємося, як тільки знайшли перший емодзі

    # Видаляємо всі емодзі з імені
    clean_name = remove_emoji(name).strip()

    # Перевіряємо наявність дужок
    if '(' in clean_name:
        # Обрізаємо до закритої дужки
        match = re.search(r'[^()]*\)', clean_name)
        if match:
            clean_name = clean_name[:match.end()].strip()  # Обрізаємо до закритої дужки
    else:
        # Обрізаємо на першому пробілі, якщо дужок немає
        match = re.search(r'([^ ]+)', clean_name)
        if match:
            clean_name = match.group(1).strip()  # Обрізаємо на першому пробілі

    # Повертаємо ім'я з першим емодзі без пробілів
    return f"{first_emoji}{clean_name}".strip() if first_emoji else clean_name.strip()


# Налаштування логування
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

logger = logging.getLogger(__name__)

# Імена файлів для графіків
TODAY_SCHEDULE_FILE = "today_schedule.json"
TOMORROW_SCHEDULE_FILE = "tomorrow_schedule.json"
DEFAULT_SCHEDULE_FILE = "default_schedule.json"
WEEKDAY_DEFAULT_SCHEDULE_FILE = "weekday_default_schedule.json"
WEEKEND_DEFAULT_SCHEDULE_FILE = "weekend_default_schedule.json"


# Завантажити графік з файлів або ініціалізувати їх
def load_schedule(file_name, weekday_default, weekend_default):
    if os.path.exists(file_name):
        with open(file_name, 'r', encoding='utf-8') as f:
            return json.load(f)
    else:
        # Determine if today is a weekday or weekend
        today = datetime.today().weekday()
        if today < 5:  # Monday to Friday are considered weekdays
            schedule = weekday_default
        else:  # Saturday and Sunday are considered weekends
            schedule = weekend_default

        # Create the file with the appropriate default schedule
        with open(file_name, 'w', encoding='utf-8') as f:
            json.dump(schedule, f, ensure_ascii=False, indent=4)
        return schedule


# Зберегти графік у файл
def save_schedule(file_name, schedule):
    with open(file_name, 'w', encoding='utf-8') as f:
        json.dump(schedule, f, ensure_ascii=False, indent=4)


empty_weekday = {
        "15:00 - 16:00": [],
        "16:00 - 17:00": [],
        "17:00 - 18:00": [],
        "18:00 - 19:00": [],
        "19:00 - 20:00": [],
        "20:00 - 21:00": [],
        "21:00 - 22:00": [],
        "22:00 - 23:00": [],
        "23:00 - 00:00": [],
        "00:00 - 01:00": []
}

empty_weekend = {
        "09:00 - 10:00": [],
        "10:00 - 11:00": [],
        "11:00 - 12:00": [],
        "12:00 - 13:00": [],
        "13:00 - 14:00": [],
        "14:00 - 15:00": [],
        "15:00 - 16:00": [],
        "16:00 - 17:00": [],
        "17:00 - 18:00": [],
        "18:00 - 19:00": [],
        "19:00 - 20:00": [],
        "20:00 - 21:00": [],
        "21:00 - 22:00": [],
        "22:00 - 23:00": [],
        "23:00 - 00:00": [],
        "00:00 - 01:00": []
}

today_schedule = copy.deepcopy(load_schedule(
    'today_schedule.json', empty_weekday, empty_weekend))
tomorrow_schedule = copy.deepcopy(load_schedule(
    'tomorrow_schedule.json', empty_weekday, empty_weekend))
weekday_default_schedule = copy.deepcopy(load_schedule(
    'weekday_default_schedule.json', empty_weekday, {}))
weekend_default_schedule = copy.deepcopy(load_schedule(
    'weekend_default_schedule.json', empty_weekend, {}))


def is_weekend(date):
    # Вихідними днями є субота (5) і неділя (6)
    return date.weekday() in (5, 6)


# Функція для оновлення графіків на новий день
def update_schedules():
    global today_schedule, tomorrow_schedule

    kyiv_tz = pytz.timezone('Europe/Kiev')
    today_date = datetime.now(kyiv_tz)
    tomorrow_date = today_date + timedelta(days=1)

    # Графік на сьогодні стає графіком на завтра
    today_schedule = copy.deepcopy(tomorrow_schedule)

    # Визначити, чи сьогодні чи завтра вихідний
    if is_weekend(tomorrow_date):
        default_for_tomorrow = weekend_default_schedule  # Вихідний графік
    else:
        default_for_tomorrow = weekday_default_schedule  # Будній графік

    # Новий графік на завтра - це стандартний графік
    tomorrow_schedule = copy.deepcopy(default_for_tomorrow)

    # Зберегти оновлені графіки
    save_schedule(TODAY_SCHEDULE_FILE, today_schedule)
    save_schedule(TOMORROW_SCHEDULE_FILE, tomorrow_schedule)


def process_hours(input_range):
    hours = input_range.split('-')

    # Переконайтеся, що у вас два значення
    if len(hours) != 2:
        return "Будь ласка, введіть правильний час (формат: x-y)."

    try:
        start_hour = int(hours[0]) % 24  # Перетворюємо на 24-годинний формат
        end_hour = int(hours[1]) % 24
    except ValueError:
        return "Будь ласка, введіть правильний час (тільки числа)."

    time_slots = []

    # Обробка переходу через північ
    if start_hour <= end_hour:
        for hour in range(start_hour, end_hour + 1):
            next_hour = (hour + 1) % 24
            time_slot = f"{hour:02d}:00 - {next_hour:02d}:00"
            time_slots.append(time_slot)
    else:
        # Обробка часових слотів при переході через північ
        for hour in range(start_hour, 24):
            next_hour = (hour + 1) % 24
            time_slot = f"{hour:02d}:00 - {next_hour:02d}:00"
            time_slots.append(time_slot)
        for hour in range(0, end_hour + 1):
            next_hour = (hour + 1) % 24
            time_slot = f"{hour:02d}:00 - {next_hour:02d}:00"
            time_slots.append(time_slot)

    return time_slots


async def get_schedule_text(schedule, date_label, context):
    text = f"Графік роботи Адміністраторів на {date_label}\n\n"

    for time_slot, user_ids in schedule.items():
        admins = []
        for user_id in user_ids:
            try:
                chat = await context.bot.get_chat(user_id)
                if chat.first_name:
                    admins.append(format_name(chat.first_name))
                else:
                    admins.append("–")
            except BadRequest:
                admins.append("unknown")

        admins_str = ' – '.join(admins) if admins else "–"
        text += f"{time_slot} – {admins_str}\n"

    return text


# Функція для початку розмови
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    logging.info(f"Starting conversation with user: {user.first_name}")
    await update.message.reply_text(
        "Вітаємо! Використовуйте команди /today для сьогоднішнього графіка, "
        "/tomorrow для завтрашнього, та /default для стандартного графіка.")


async def mechanical_update_schedules(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id

    # Перевірка, чи є користувач адміністратором
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("У вас немає прав доступу до цієї команди.")
        return

    await update.message.reply_text("Графік змінено", update_schedules())


# Функція для показу сьогоднішнього графіку
async def show_today_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = await get_schedule_text(today_schedule, datetime.now(pytz.timezone('Europe/Kiev')).strftime("%d.%m.%Y"),
                                   context)
    await update.message.reply_text(text)


# Функція для показу завтрашнього графіку
async def show_tomorrow_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = await get_schedule_text(tomorrow_schedule,
                                   (datetime.now(pytz.timezone('Europe/Kiev')) + timedelta(days=1)).strftime(
                                       "%d.%m.%Y"), context)
    await update.message.reply_text(text)


# Функція для показу стандартного графіку
async def show_default_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    current_day = datetime.now(pytz.timezone('Europe/Kiev')).weekday()  # 0 - понеділок, 6 - неділя
    if current_day < 5:  # Якщо будній день (понеділок - п’ятниця)
        text = await get_schedule_text(weekday_default_schedule, "стандартний графік (будній день)", context)
    else:  # Вихідний день (субота, неділя)
        text = await get_schedule_text(weekend_default_schedule, "стандартний графік (вихідний день)", context)
    await update.message.reply_text(text)


# Функція для редагування графіків
async def edit_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message.text.strip()

    # Перевірка на формат +цифри чи -цифри
    if not (message.startswith('+') or message.startswith('-')):
        return  # Якщо повідомлення не містить + або -, ігноруємо його

    user_id = update.effective_user.id
    user_name = update.effective_user.first_name or update.effective_user.username

    # Витягуємо графік
    if update.message.reply_to_message:
        reply_text = update.message.reply_to_message.text

        # Перевірка стандартного графіка
        if "Графік роботи Адміністраторів на стандартний графік (будній день)" in reply_text:
            schedule = weekday_default_schedule
        elif "Графік роботи Адміністраторів на стандартний графік (вихідний день)" in reply_text:
            schedule = weekend_default_schedule
        elif "Графік роботи Адміністраторів на " in reply_text:
            schedule_date = update.message.reply_to_message.text.split('на ')[1].strip().split()[0]
            current_date = datetime.now(pytz.timezone('Europe/Kiev')).strftime("%d.%m.%Y")
            tomorrow_date = (datetime.now(pytz.timezone('Europe/Kiev')) + timedelta(days=1)).strftime("%d.%m.%Y")

            # Перевіряємо, який саме графік змінюємо
            if schedule_date == current_date:
                schedule = today_schedule  # Зміна лише сьогоднішнього графіка
            elif schedule_date == tomorrow_date:
                schedule = tomorrow_schedule  # Зміна лише завтрашнього графіка
            else:
                return
        else:
            return
    else:
        return

    # Витягуємо дії з команди
    operation = 'remove' if message[0] == '-' else 'add'
    hours_range = message[1:].strip()  # Отримуємо години без знака

    updated_hours = []
    if '+' in hours_range:  # Додаємо обробку символа +
        hour = int(hours_range[1:])
        time_slot = f"{hour:02d}:00 - {hour + 1:02d}:00" if hour < 23 else "23:00 - 00:00"
        if operation == 'add':
            if user_id not in schedule[time_slot]:
                schedule[time_slot].append(user_id)
                updated_hours.append(time_slot)
        elif operation == 'remove':
            if user_id in schedule[time_slot]:
                schedule[time_slot].remove(user_id)
                updated_hours.append(time_slot)

        # Логіка для обробки діапазону годин
    if '-' in hours_range:
        # Handle the range input
        try:
            start_hour, end_hour = map(int, hours_range.split('-'))

            # Adjust for negative inputs (e.g., -22 means from 22:00 to 00:00)
            if start_hour < 0:
                start_hour += 24
            if end_hour < 0:
                end_hour += 24

            if start_hour < 0 or end_hour > 24 or start_hour >= end_hour:
                await update.message.reply_text("Будь ласка, введіть правильний час (9-24).\n ")
                return

            for hour in range(start_hour, end_hour):
                # Обробка години 24
                if hour == 23:
                    time_slot = f"{hour:02d}:00 - 00:00"
                else:
                    time_slot = f"{hour:02d}:00 - {hour + 1:02d}:00"

                if operation == 'add':
                    if user_id not in schedule[time_slot]:
                        schedule[time_slot].append(user_id)
                        updated_hours.append(time_slot)  # Зберігаємо оновлені години
                elif operation == 'remove':
                    if user_id in schedule[time_slot]:
                        schedule[time_slot].remove(user_id)
                        updated_hours.append(time_slot)  # Зберігаємо оновлену годину
        except ValueError:
            await update.message.reply_text("Будь ласка, введіть правильний час (9-24).")
            return
    else:
        # Логіка для обробки одного часу
        try:
            hour = int(hours_range)

            # Adjust for negative inputs (e.g., -22 means 22:00 to 00:00)
            if hour < 0:
                hour += 24

            # Заміна +24 на 00
            if hour == 24:
                hour = 0

            time_slot = f"{hour:02d}:00 - {hour + 1:02d}:00"

            # Обробка години 24
            if hour == 23:
                time_slot = f"{hour:02d}:00 - 00:00"

            if operation == 'add':
                if user_id not in schedule[time_slot]:
                    schedule[time_slot].append(user_id)
                    updated_hours.append(time_slot)  # Зберігаємо оновлену годину
            elif operation == 'remove':
                if user_id in schedule[time_slot]:
                    schedule[time_slot].remove(user_id)
                    updated_hours.append(time_slot)  # Зберігаємо оновлену годину
        except ValueError:
            await update.message.reply_text("Будь ласка, введіть правильний час (9-24).")
            return

    # Оновлення графіка у файлі
    if schedule is today_schedule:
        save_schedule(TODAY_SCHEDULE_FILE, today_schedule)
    elif schedule is tomorrow_schedule:
        save_schedule(TOMORROW_SCHEDULE_FILE, tomorrow_schedule)
    elif schedule is weekday_default_schedule:
        # Додаємо текст для буднього дня
        save_schedule(WEEKDAY_DEFAULT_SCHEDULE_FILE, weekday_default_schedule)
    elif schedule is weekend_default_schedule:
        # Додаємо текст для вихідного дня
        save_schedule(WEEKEND_DEFAULT_SCHEDULE_FILE, weekend_default_schedule)

    # Формування повідомлення
    if operation == 'add':
        if updated_hours:
            start_time = updated_hours[0].split('-')[0]
            end_time = updated_hours[-1].split('-')[1]
            response_message = f"{user_name} було додано до графіка на {start_time} - {end_time}."
        else:
            response_message = "Не вдалося додати години."
    else:
        if updated_hours:
            start_time = updated_hours[0].split('-')[0]
            end_time = updated_hours[-1].split('-')[1]
            response_message = f"{user_name} було видалено з графіка на {start_time} - {end_time}."
        else:
            response_message = "Не вдалося видалити години."

    # Формування оновленого графіка
    if schedule is today_schedule:
        date_label = datetime.now(pytz.timezone('Europe/Kiev')).strftime("%d.%m.%Y")
    elif schedule is tomorrow_schedule:
        date_label = (datetime.now(pytz.timezone('Europe/Kiev')) + timedelta(days=1)).strftime("%d.%m.%Y")
    elif schedule is weekday_default_schedule:
        date_label = "стандартний графік (будній день)"
    elif schedule is weekend_default_schedule:
        date_label = "стандартний графік (вихідний день)"
    else:
        date_label = "незнайомий графік"  # Залишаємо цей варіант на випадок, якщо графік не знайдено

    updated_schedule_message = f"Графік роботи Адміністраторів на {date_label}\n\n"
    for time_slot in schedule:
        users = schedule[time_slot]

        # Список асинхронних завдань для отримання імен користувачів
        user_name_tasks = [context.bot.get_chat_member(chat_id=update.effective_chat.id, user_id=user) for user in
                           users]

        # Очікуємо результати
        user_names_results = await asyncio.gather(*user_name_tasks)

        # Отримуємо імена користувачів
        user_names = ' – '.join([format_name(member.user.first_name) for member in user_names_results]) or "–"
        updated_schedule_message += f"{time_slot}: {user_names}\n"

    # Редагуємо старе повідомлення
    try:
        await update.message.reply_to_message.edit_text(updated_schedule_message + '\n' + response_message)
    except Exception as e:
        await update.message.reply_text("Не вдалося редагувати повідомлення. Спробуйте ще раз.")
        print(e)


# Функція для показу стандартного графіка на будній день
async def show_weekday_default_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = await get_schedule_text(weekday_default_schedule, "стандартний графік (будній день)", context)
    await update.message.reply_text(text)


# Функція для показу стандартного графіка на вихідний день
async def show_weekend_default_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = await get_schedule_text(weekend_default_schedule, "стандартний графік (вихідний день)", context)
    await update.message.reply_text(text)


def main() -> None:
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("today", show_today_schedule))
    app.add_handler(CommandHandler("tomorrow", show_tomorrow_schedule))
    app.add_handler(CommandHandler("default", show_default_schedule))
    app.add_handler(CommandHandler("weekday", show_weekday_default_schedule))
    app.add_handler(CommandHandler("weekend", show_weekend_default_schedule))
    app.add_handler(CommandHandler("update", mechanical_update_schedules))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, edit_schedule))

    # Створення планувальника
    scheduler = BackgroundScheduler()
    kyiv_tz = pytz.timezone('Europe/Kiev')
    scheduler.add_job(update_schedules, 'cron', hour=0, minute=0, timezone=kyiv_tz)
    scheduler.start()

    # Запуск функції keep_alive в окремому потоці
    threading.Thread(target=keep_alive, daemon=True).start()

    app.run_polling()


if __name__ == "__main__":
    main()
