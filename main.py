import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
import asyncpg
from datetime import datetime, timedelta
from aiogram.types import InlineKeyboardButton as AiogramInlineKeyboardButton, InlineKeyboardMarkup as AiogramInlineKeyboardMarkup

BOT_TOKEN = "YOUR_BOT_TOKEN"

DB_CONFIG = {
    "user": "YOUR_DATABASE_USERNAME",
    "password": "YOUR_DATABASE_PASSWORD",
    "database": "YOUR_DATABASE_NAME",
    "host": "YOUR_HOST",
    "port": "YOUR_PORT",
}

class BookingForm(StatesGroup):
    name = State()
    phone = State()
    date = State()
    time = State()
    num = State()
    allergy = State()
    comment = State()

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

async def create_db_pool():
    return await asyncpg.create_pool(**DB_CONFIG)

async def booking_exists(pool, phone, date, time):
    async with pool.acquire() as connection:
        query = """
        SELECT COUNT(*) FROM main_tickets
        WHERE phone = $1 AND date = $2 AND time = $3
        """
        count = await connection.fetchval(query, phone, date, time)
        return count > 0

async def booking_count(pool, date, time):
    async with pool.acquire() as connection:
        query = """
        SELECT COUNT(*) FROM main_tickets
        WHERE date = $1 AND time = $2
        """
        count = await connection.fetchval(query, date, time)
        return count

async def delete_expired_bookings(pool):
    async with pool.acquire() as connection:
        query = """
        DELETE FROM main_tickets
        WHERE (date + time::interval) < NOW()
        """
        await connection.execute(query)

async def add_booking(pool, name, phone, date, time, num, allergy, comment):
    async with pool.acquire() as connection:
        query = """
        INSERT INTO main_tickets (name, phone, date, time, num, allergy, comment)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        """
        await connection.execute(query, name, phone, date, time, num, allergy, comment)

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    keyboard = types.ReplyKeyboardMarkup(
        keyboard=[[types.KeyboardButton(text="Забронировать столик")]],
        resize_keyboard=True
    )
    await message.answer(
        "Привет! Я бот кофейни COFFEE.\n"
        "Нажмите кнопку ниже, чтобы забронировать столик.",
        reply_markup=keyboard
    )

@dp.message(StateFilter(BookingForm.name))
async def process_name(message: types.Message, state: FSMContext):
    name = message.text.strip()
    if any(char.isdigit() for char in name):
        await message.answer("Имя не должно содержать цифр. Пожалуйста, введите имя без цифр:")
        await state.set_state(BookingForm.name)
        return
    await state.update_data(name=name)
    await message.answer("Введите ваш телефон:")
    await state.set_state(BookingForm.phone)

@dp.message(StateFilter(BookingForm.phone))
async def process_phone(message: types.Message, state: FSMContext):
    phone = message.text.strip()
    if not (phone.isdigit() and len(phone) == 11):
        await message.answer("Неверный формат номера телефона. Введите номер из 11 цифр без пробелов и символов:")
        await state.set_state(BookingForm.phone)
        return
    await state.update_data(phone=phone)

    await send_date_selection(message)
    await state.set_state(BookingForm.date)

@dp.callback_query(lambda c: c.data and c.data.startswith("date_"))
async def process_date_callback(callback_query: types.CallbackQuery, state: FSMContext):
    raw_date = callback_query.data[5:]
    print(f"DEBUG: selected date: '{raw_date}'")
    try:
        date_obj = datetime.strptime(raw_date, "%Y-%m-%d").date()
        today = datetime.now().date()
        max_date = today + timedelta(days=30)
        if date_obj < today:
            await callback_query.message.answer("Дата не может быть в прошлом. Пожалуйста, выберите корректную дату:")
            await send_date_selection(callback_query.message)
            await state.set_state(BookingForm.date)
            await callback_query.answer()
            return
        if date_obj > max_date:
            await callback_query.message.answer("Дата не может быть позже, чем через 30 дней. Пожалуйста, выберите корректную дату:")
            await send_date_selection(callback_query.message)
            await state.set_state(BookingForm.date)
            await callback_query.answer()
            return
        await state.update_data(date=date_obj)
        await callback_query.message.answer(f"Вы выбрали дату: {raw_date}\nТеперь выберите время бронирования:")
        await send_time_selection(callback_query.message, date_obj)
        await state.set_state(BookingForm.time)
        await callback_query.answer()
    except ValueError as ve:
        print(f"ValueError in process_date_callback: {ve}")
        await callback_query.message.answer("Неверный формат даты. Пожалуйста, выберите дату из кнопок.")
        await send_date_selection(callback_query.message)
        await state.set_state(BookingForm.date)
        await callback_query.answer()
    except Exception as ex:
        print(f"Unexpected error in process_date_callback: {ex}")
        await callback_query.message.answer("Произошла ошибка. Пожалуйста, попробуйте снова.")
        await send_date_selection(callback_query.message)
        await state.set_state(BookingForm.date)
        await callback_query.answer()

async def send_date_selection(message):
    today = datetime.now().date()
    dates = [today + timedelta(days=i) for i in range(30)]
    buttons = []
    for d in dates:
        day_str = d.strftime("%d.%m")
        callback_data = f"date_{d.isoformat()}"
        buttons.append(AiogramInlineKeyboardButton(text=day_str, callback_data=callback_data))

    def chunk_buttons(buttons, n):
        return [buttons[i:i + n] for i in range(0, len(buttons), n)]
    chunked_buttons = chunk_buttons(buttons, 7)
    keyboard = AiogramInlineKeyboardMarkup(inline_keyboard=chunked_buttons)
    await message.answer("Выберите дату бронирования:", reply_markup=keyboard)

async def send_time_selection(message, date_obj):
    weekday = date_obj.weekday() 
    if weekday >= 5: 
        start_hour = 10
    else:
        start_hour = 8
    end_hour = 22
    time_buttons = []
    pool = dp['db_pool']

    for hour in range(start_hour, end_hour):
        time_str_00 = f"{hour:02d}:00"
        count_00 = await booking_count(pool, date_obj, time_str_00)
        if count_00 < 3:
            time_buttons.append(AiogramInlineKeyboardButton(text=time_str_00, callback_data=f"time_{time_str_00}"))
        time_str_30 = f"{hour:02d}:30"
        count_30 = await booking_count(pool, date_obj, time_str_30)
        if count_30 < 3:
            time_buttons.append(AiogramInlineKeyboardButton(text=time_str_30, callback_data=f"time_{time_str_30}"))

    count_22 = await booking_count(pool, date_obj, "22:00")
    if count_22 < 3:
        time_buttons.append(AiogramInlineKeyboardButton(text="22:00", callback_data="time_22:00"))

    chunked_buttons = [time_buttons[i:i + 4] for i in range(0, len(time_buttons), 4)]
    keyboard = AiogramInlineKeyboardMarkup(inline_keyboard=chunked_buttons)
    await message.answer("Выберите время бронирования:", reply_markup=keyboard)

@dp.callback_query(lambda c: c.data and c.data.startswith("time_"))
async def process_time_callback(callback_query: types.CallbackQuery, state: FSMContext):
    selected_time = callback_query.data[5:] 

    data = await state.get_data()
    date_obj = data.get("date")
    if not date_obj:
        await callback_query.message.answer("Сначала выберите дату.")
        await send_date_selection(callback_query.message)
        await state.set_state(BookingForm.date)
        await callback_query.answer()
        return
    weekday = date_obj.weekday()
    if weekday >= 0 and weekday <= 4: 
        if selected_time < "08:00" or selected_time > "22:00":
            await callback_query.message.answer("В будние дни время бронирования должно быть с 08:00 до 22:00. Пожалуйста, выберите корректное время.")
            await send_time_selection(callback_query.message, date_obj)
            await callback_query.answer()
            return
    else:
        if selected_time < "10:00" or selected_time > "22:00":
            await callback_query.message.answer("В выходные дни время бронирования должно быть с 10:00 до 22:00. Пожалуйста, выберите корректное время.")
            await send_time_selection(callback_query.message, date_obj)
            await callback_query.answer()
            return
    await state.update_data(time=selected_time)
    await callback_query.message.answer(f"Вы выбрали время: {selected_time}\nВведите количество персон (от 1 до 5):")
    await state.set_state(BookingForm.num)
    await callback_query.answer()

@dp.message(StateFilter(BookingForm.num))
async def process_num(message: types.Message, state: FSMContext):
    num_text = message.text.strip()
    if not num_text.isdigit():
        await message.answer("Количество персон должно быть числом от 1 до 5. Пожалуйста, введите корректное количество:")
        await state.set_state(BookingForm.num)
        return
    num = int(num_text)
    if num <= 0 or num > 5:
        await message.answer("Количество персон должно быть от 1 до 5. Пожалуйста, введите корректное количество:")
        await state.set_state(BookingForm.num)
        return
    await state.update_data(num=num)
    await message.answer("Укажите аллергии (если нет, напишите 'нет'):")
    await state.set_state(BookingForm.allergy)

@dp.message(StateFilter(BookingForm.allergy))
async def process_allergy(message: types.Message, state: FSMContext):
    await state.update_data(allergy=message.text)
    await message.answer("Введите комментарий (если нет, напишите 'нет'):")
    await state.set_state(BookingForm.comment)

@dp.message(StateFilter(BookingForm.comment))
async def process_comment(message: types.Message, state: FSMContext):
    await state.update_data(comment=message.text)
    data = await state.get_data()

    print(f"DEBUG: type of date in state data: {type(data['date'])}")

    pool = dp['db_pool']

    time_str = data['time'] if isinstance(data['time'], str) else str(data['time'])

    exists = await booking_exists(pool, data['phone'], data['date'], time_str)
    if exists:
        await message.answer("У вас уже есть бронь на это время.")
    else:
        try:
            keyboard = types.ReplyKeyboardMarkup(
                keyboard=[[types.KeyboardButton(text="Забронировать столик")]],
                resize_keyboard=True
            )
            
            await add_booking(
                pool,
                data['name'],
                data['phone'],
                data['date'],
                time_str,
                str(data['num']),
                data['allergy'],
                data['comment']
            )
            await message.answer("Спасибо! Ваша бронь успешно создана.", reply_markup=keyboard)
        except Exception as e:
            print(f"Error adding booking: {e}")
            keyboard = types.ReplyKeyboardMarkup(
                keyboard=[[types.KeyboardButton(text="Забронировать столик")]],
                resize_keyboard=True
            )
            await message.answer("Что-то пошло не так! Возможно, на это время уже забронирован столик. Попробуем снова?", reply_markup=keyboard)

    await state.clear()

async def main():

    dp['db_pool'] = await create_db_pool()
    await delete_expired_bookings(dp['db_pool'])

    try:
        print("Бот запущен...")
        await dp.start_polling(bot)
    finally:
        await dp['db_pool'].close()
        await bot.session.close()

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    keyboard = types.ReplyKeyboardRemove()
    await message.answer(
        "Привет! Я бот кофейни COFFEE.\n"
        "Хотите забронировать столик? Для начала, пожалуйста, введите ваше имя.",
        reply_markup=keyboard
    )
    await state.set_state(BookingForm.name)

@dp.message()
async def unknown_message(message: types.Message, state: FSMContext):
    keyboard = types.ReplyKeyboardMarkup(
        keyboard=[[types.KeyboardButton(text="Забронировать столик")]],
        resize_keyboard=True
    )

    if message.text == "Забронировать столик":
        keyboard_remove = types.ReplyKeyboardRemove()
        await message.answer("Пожалуйста, введите ваше имя для начала бронирования.", reply_markup=keyboard_remove)
        await state.set_state(BookingForm.name)
    else:
        await message.answer("К сожалению, я не понимаю таких команд :(", reply_markup=keyboard)

if __name__ == "__main__":
    asyncio.run(main())
