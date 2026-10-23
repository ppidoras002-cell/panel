import asyncio
import json
import logging
import uuid
import re
from pathlib import Path
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message, CallbackQuery, MessageEntity,
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove,
    InlineKeyboardMarkup, InlineKeyboardButton
)

# ==================== НАСТРОЙКИ ====================
BOT_TOKEN = "8552730540:AAHNpM-QheoZC6dNJ5qlxEAJQrSJMiCKYnc"

ADMINS = [
    7666289511,      # твой ID
    5083974050,2120598576    # другие админы
]

QUESTIONS = [
    "Откуда узнал о нас?",
    "Сколько готов уделять время работе в день?",
    "Был ли у тебя опыт, если да, то расскажи какой? (расскажи коротко)",
]

USERS_FILE = Path("users.json")
MENUS_FILE = Path("menus.json")
PAYOUTS_FILE = Path("payouts.json")

# ===================================================

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
router = Router()

# -------------------- Состояния --------------------

class ApplicationForm(StatesGroup):
    answering = State()

class AdminStates(StatesGroup):
    waiting_menu_name = State()
    waiting_button_text = State()
    waiting_button_content = State()
    waiting_edit_content = State()
    waiting_payout_amount = State()

class WalletStates(StatesGroup):
    waiting_address = State()

class PayoutStates(StatesGroup):
    waiting_screenshot_1 = State()
    waiting_screenshot_2 = State()
    confirm_payout = State()

class WithdrawStates(StatesGroup):
    waiting_amount = State()

# -------------------- Работа с данными --------------------

def load_json(path: Path, default):
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default

def save_json(path: Path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_users() -> dict:
    return load_json(USERS_FILE, {})

def save_users(data: dict):
    save_json(USERS_FILE, data)

def get_user(user_id: int) -> dict | None:
    return load_users().get(str(user_id))

def set_user(user_id: int, info: dict):
    data = load_users()
    data[str(user_id)] = info
    save_users(data)

def ensure_user_fields(user: dict) -> dict:
    if "balance" not in user:
        user["balance"] = 0.0
    if "ton_wallet" not in user:
        user["ton_wallet"] = None
    return user

def load_menus() -> dict:
    default = {"main": {"title": "Главное меню", "buttons": []}}
    return load_json(MENUS_FILE, default)

def save_menus(data: dict):
    save_json(MENUS_FILE, data)

def load_payouts() -> dict:
    return load_json(PAYOUTS_FILE, {})

def save_payouts(data: dict):
    save_json(PAYOUTS_FILE, data)

def is_admin(user_id: int) -> bool:
    return user_id in ADMINS

# -------------------- Премиум-эмодзи (entities) --------------------

def serialize_entities(entities: list[MessageEntity] | None) -> list[dict]:
    if not entities:
        return []
    result = []
    for e in entities:
        result.append({
            "type": e.type,
            "offset": e.offset,
            "length": e.length,
            "url": e.url,
            "language": e.language,
            "custom_emoji_id": e.custom_emoji_id,
        })
    return result

def deserialize_entities(data: list[dict] | None) -> list[MessageEntity] | None:
    if not data:
        return None
    entities = []
    for e in data:
        entities.append(MessageEntity(
            type=e["type"],
            offset=e["offset"],
            length=e["length"],
            url=e.get("url"),
            language=e.get("language"),
            custom_emoji_id=e.get("custom_emoji_id"),
        ))
    return entities

# -------------------- Клавиатуры --------------------

def get_member_main_keyboard() -> InlineKeyboardMarkup:
    """Инлайн-меню для принятых участников"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="👤 Профиль", callback_data="member_profile"),
            InlineKeyboardButton(text="💎 Привязать TON", callback_data="member_wallet"),
        ],
        [
            InlineKeyboardButton(text="📤 Заявка на выплату", callback_data="member_payout"),
            InlineKeyboardButton(text="💸 Вывести деньги", callback_data="member_withdraw"),
        ],
    ])

def build_reply_keyboard(menu_id: str = "main") -> ReplyKeyboardMarkup | None:
    """Для совместимости с админскими меню (если используются)"""
    menus = load_menus()
    menu = menus.get(menu_id)
    if not menu or not menu.get("buttons"):
        return None

    buttons = []
    row = []
    for btn in menu["buttons"]:
        row.append(KeyboardButton(text=btn["text"]))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def get_cancel_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Отменить анкету")]],
        resize_keyboard=True
    )

def get_cancel_action_keyboard(text: str = "❌ Отмена"):
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=text)]],
        resize_keyboard=True
    )

def admin_main_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Список меню", callback_data="admin_list_menus")],
        [InlineKeyboardButton(text="➕ Создать меню", callback_data="admin_create_menu")],
        [InlineKeyboardButton(text="🔘 Управление кнопками", callback_data="admin_manage_buttons")],
        [InlineKeyboardButton(text="📊 Статистика заявок", callback_data="admin_stats")],
        [InlineKeyboardButton(text="💰 Заявки на выплату", callback_data="admin_payouts")],
    ])

def menus_list_kb():
    menus = load_menus()
    keyboard = []
    for menu_id, menu in menus.items():
        keyboard.append([
            InlineKeyboardButton(
                text=f"📁 {menu['title']} ({menu_id})",
                callback_data=f"admin_menu_{menu_id}"
            )
        ])
    keyboard.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin_back")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def menu_actions_kb(menu_id: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить кнопку", callback_data=f"admin_addbtn_{menu_id}")],
        [InlineKeyboardButton(text="📝 Список кнопок", callback_data=f"admin_btns_{menu_id}")],
        [InlineKeyboardButton(text="🗑 Удалить меню", callback_data=f"admin_delmenu_{menu_id}")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_list_menus")],
    ])

def buttons_list_kb(menu_id: str):
    menus = load_menus()
    menu = menus.get(menu_id, {})
    keyboard = []
    for btn in menu.get("buttons", []):
        keyboard.append([
            InlineKeyboardButton(
                text=f"🔹 {btn['text']}",
                callback_data=f"admin_btn_{menu_id}_{btn['id']}"
            )
        ])
    keyboard.append([InlineKeyboardButton(text="◀️ Назад", callback_data=f"admin_menu_{menu_id}")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def button_actions_kb(menu_id: str, btn_id: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Изменить текст ответа", callback_data=f"admin_editcontent_{menu_id}_{btn_id}")],
        [InlineKeyboardButton(text="🗑 Удалить кнопку", callback_data=f"admin_delbtn_{menu_id}_{btn_id}")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data=f"admin_btns_{menu_id}")],
    ])

def action_type_kb(menu_id: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📄 Просто текст (с премиум-эмодзи)", callback_data=f"admin_type_text_{menu_id}")],
        [InlineKeyboardButton(text="📁 Открыть другое меню", callback_data=f"admin_type_menu_{menu_id}")],
        [InlineKeyboardButton(text="💎 Запросить TON кошелёк", callback_data=f"admin_type_wallet_{menu_id}")],
        [InlineKeyboardButton(text="◀️ Отмена", callback_data=f"admin_menu_{menu_id}")],
    ])

def choose_target_menu_kb(current_menu_id: str):
    menus = load_menus()
    keyboard = []
    for menu_id, menu in menus.items():
        if menu_id != current_menu_id:
            keyboard.append([
                InlineKeyboardButton(
                    text=menu["title"],
                    callback_data=f"admin_settarget_{current_menu_id}_{menu_id}"
                )
            ])
    keyboard.append([InlineKeyboardButton(text="◀️ Отмена", callback_data=f"admin_menu_{current_menu_id}")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def payout_confirm_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Отправить на выплату", callback_data="payout_submit")],
        [InlineKeyboardButton(text="❌ Отменить", callback_data="payout_cancel")],
    ])

def admin_payout_actions_kb(payout_id: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Одобрить (начислить)", callback_data=f"payout_approve_{payout_id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"payout_reject_{payout_id}"),
        ]
    ])

def back_to_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ В меню", callback_data="member_menu")]
    ])

# -------------------- Анкета --------------------

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    user = get_user(user_id)

    if user and user.get("status") == "accepted":
        user = ensure_user_fields(user)
        set_user(user_id, user)
        await message.answer(
            f"С возвращением, {message.from_user.first_name}! 👋\nТы в команде.",
            reply_markup=ReplyKeyboardRemove()
        )
        await message.answer("📋 Главное меню:", reply_markup=get_member_main_keyboard())
        return

    if user and user.get("status") == "applied":
        await message.answer("Ты уже отправил анкету. Ожидай решения.", reply_markup=ReplyKeyboardRemove())
        return

    if user and user.get("status") == "rejected":
        await message.answer("Твоя заявка была отклонена.", reply_markup=ReplyKeyboardRemove())
        return

    await state.set_state(ApplicationForm.answering)
    await state.update_data(answers=[], current_question=0)

    await message.answer(
        "Привет! 👋\nЭто анкета для отбора в команду.\n\n"
        "Отвечай на вопросы по порядку.\nЧтобы отменить — нажми «❌ Отменить анкету».",
        reply_markup=get_cancel_keyboard()
    )
    await ask_question(message, state)

@router.message(Command("cancel"))
@router.message(F.text == "❌ Отменить анкету")
async def cancel_form(message: Message, state: FSMContext):
    if await state.get_state() is None:
        await message.answer("Анкета не начата.")
        return
    await state.clear()
    await message.answer("Анкета отменена.", reply_markup=ReplyKeyboardRemove())

@router.message(ApplicationForm.answering)
async def process_answer(message: Message, state: FSMContext):
    data = await state.get_data()
    answers = data.get("answers", [])
    current = data.get("current_question", 0)

    answers.append(message.text)
    await state.update_data(answers=answers)

    next_q = current + 1
    if next_q < len(QUESTIONS):
        await state.update_data(current_question=next_q)
        await ask_question(message, state)
    else:
        await finish_form(message, state)

async def ask_question(message: Message, state: FSMContext):
    data = await state.get_data()
    current = data.get("current_question", 0)
    await message.answer(
        f"<b>Вопрос {current + 1} из {len(QUESTIONS)}</b>\n\n{QUESTIONS[current]}",
        parse_mode="HTML"
    )

async def finish_form(message: Message, state: FSMContext):
    data = await state.get_data()
    answers = data.get("answers", [])
    user_id = message.from_user.id

    set_user(user_id, {
        "status": "applied",
        "full_name": message.from_user.full_name,
        "username": message.from_user.username,
        "answers": answers,
        "balance": 0.0,
        "ton_wallet": None
    })

    text = (
        f"📋 <b>Новая анкета!</b>\n\n"
        f"От: {message.from_user.full_name}\n"
        f"Username: @{message.from_user.username or 'нет'}\n"
        f"ID: <code>{user_id}</code>\n\n"
    )
    for i, (q, a) in enumerate(zip(QUESTIONS, answers), 1):
        text += f"<b>{i}. {q}</b>\n{a}\n\n"

    for admin_id in ADMINS:
        try:
            await bot.send_message(
                admin_id, text, parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="✅ Принять", callback_data=f"accept_{user_id}"),
                    InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_{user_id}")
                ]])
            )
        except Exception as e:
            logger.error(e)

    await message.answer("✅ Анкета отправлена! Ожидай решения.", reply_markup=ReplyKeyboardRemove())
    await state.clear()

# -------------------- Принятие / отклонение --------------------

@router.callback_query(F.data.startswith("accept_"))
async def accept_user(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return

    user_id = int(callback.data.split("_")[1])
    user = get_user(user_id)
    if not user or user.get("status") != "applied":
        await callback.answer("Заявка уже обработана", show_alert=True)
        return

    user["status"] = "accepted"
    user = ensure_user_fields(user)
    set_user(user_id, user)

    try:
        await bot.send_message(
            user_id,
            "🎉 Поздравляем! Ты принят в команду!\nТеперь тебе доступно меню.",
            reply_markup=ReplyKeyboardRemove()
        )
        await bot.send_message(
            user_id,
            "📋 Главное меню:",
            reply_markup=get_member_main_keyboard()
        )
    except:
        pass

    await callback.message.edit_text(
        callback.message.text + f"\n\n✅ Принят ({callback.from_user.full_name})",
        parse_mode="HTML"
    )
    await callback.answer("Принят!")

@router.callback_query(F.data.startswith("reject_"))
async def reject_user(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return

    user_id = int(callback.data.split("_")[1])
    user = get_user(user_id)
    if not user or user.get("status") != "applied":
        await callback.answer("Заявка уже обработана", show_alert=True)
        return

    user["status"] = "rejected"
    set_user(user_id, user)

    try:
        await bot.send_message(user_id, "К сожалению, твоя заявка отклонена.")
    except:
        pass

    await callback.message.edit_text(
        callback.message.text + f"\n\n❌ Отклонён ({callback.from_user.full_name})",
        parse_mode="HTML"
    )
    await callback.answer("Отклонён")

# -------------------- Админ-панель --------------------

@router.message(Command("admin"))
async def admin_panel(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    await message.answer("🛠 <b>Админ-панель</b>", parse_mode="HTML", reply_markup=admin_main_kb())

@router.callback_query(F.data == "admin_back")
async def admin_back(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("🛠 <b>Админ-панель</b>", parse_mode="HTML", reply_markup=admin_main_kb())
    await callback.answer()

@router.callback_query(F.data == "admin_list_menus")
async def admin_list_menus(callback: CallbackQuery):
    await callback.message.edit_text("📁 <b>Список меню</b>", parse_mode="HTML", reply_markup=menus_list_kb())
    await callback.answer()

@router.callback_query(F.data == "admin_create_menu")
async def admin_create_menu(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.waiting_menu_name)
    await callback.message.edit_text("Введи название нового меню:")
    await callback.answer()

@router.message(AdminStates.waiting_menu_name)
async def process_new_menu_name(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    name = message.text.strip()
    menu_id = str(uuid.uuid4())[:8]

    menus = load_menus()
    menus[menu_id] = {"title": name, "buttons": []}
    save_menus(menus)

    await state.clear()
    await message.answer(f"✅ Меню «{name}» создано!", reply_markup=admin_main_kb())

@router.callback_query(F.data.startswith("admin_menu_"))
async def admin_menu_view(callback: CallbackQuery):
    menu_id = callback.data.replace("admin_menu_", "")
    menus = load_menus()
    menu = menus.get(menu_id)
    if not menu:
        await callback.answer("Меню не найдено", show_alert=True)
        return

    text = f"📁 <b>{menu['title']}</b>\nID: <code>{menu_id}</code>\nКнопок: {len(menu.get('buttons', []))}"
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=menu_actions_kb(menu_id))
    await callback.answer()

@router.callback_query(F.data.startswith("admin_addbtn_"))
async def admin_add_button_start(callback: CallbackQuery, state: FSMContext):
    menu_id = callback.data.replace("admin_addbtn_", "")
    await state.update_data(admin_menu_id=menu_id)
    await callback.message.edit_text("Выбери тип кнопки:", reply_markup=action_type_kb(menu_id))
    await callback.answer()

@router.callback_query(F.data.startswith("admin_type_text_"))
async def admin_type_text(callback: CallbackQuery, state: FSMContext):
    menu_id = callback.data.replace("admin_type_text_", "")
    await state.update_data(admin_menu_id=menu_id, admin_btn_type="text")
    await state.set_state(AdminStates.waiting_button_text)
    await callback.message.edit_text("Введи текст кнопки (то, что будет на самой кнопке):")
    await callback.answer()

@router.callback_query(F.data.startswith("admin_type_menu_"))
async def admin_type_menu(callback: CallbackQuery, state: FSMContext):
    menu_id = callback.data.replace("admin_type_menu_", "")
    await state.update_data(admin_menu_id=menu_id, admin_btn_type="menu")
    await state.set_state(AdminStates.waiting_button_text)
    await callback.message.edit_text("Введи текст кнопки:")
    await callback.answer()

@router.callback_query(F.data.startswith("admin_type_wallet_"))
async def admin_type_wallet(callback: CallbackQuery, state: FSMContext):
    menu_id = callback.data.replace("admin_type_wallet_", "")
    await state.update_data(admin_menu_id=menu_id, admin_btn_type="wallet")
    await state.set_state(AdminStates.waiting_button_text)
    await callback.message.edit_text(
        "Введи текст кнопки (например: 💎 Кошелёк или 💰 Указать TON кошелёк):"
    )
    await callback.answer()

@router.message(AdminStates.waiting_button_text)
async def process_button_text(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    data = await state.get_data()
    btn_type = data.get("admin_btn_type")
    await state.update_data(admin_btn_text=message.text.strip())

    if btn_type == "text":
        await state.set_state(AdminStates.waiting_button_content)
        await message.answer(
            "Теперь отправь текст, который будет приходить при нажатии на кнопку.\n\n"
            "Можно использовать <b>премиум-эмодзи</b> — просто вставь их в сообщение."
        )
    elif btn_type == "wallet":
        menu_id = data.get("admin_menu_id")
        btn_text = message.text.strip()

        menus = load_menus()
        new_btn = {
            "id": str(uuid.uuid4())[:8],
            "text": btn_text,
            "action": "wallet"
        }
        menus[menu_id]["buttons"].append(new_btn)
        save_menus(menus)

        await state.clear()
        await message.answer(
            f"✅ Кнопка «{btn_text}» добавлена!\n"
            "При нажатии бот попросит прислать адрес TON-кошелька.",
            reply_markup=admin_main_kb()
        )
    else:
        menu_id = data.get("admin_menu_id")
        await message.answer(
            "Выбери, какое меню открывать при нажатии:",
            reply_markup=choose_target_menu_kb(menu_id)
        )

@router.callback_query(F.data.startswith("admin_settarget_"))
async def admin_set_target(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    current_menu = parts[2]
    target_menu = parts[3]

    data = await state.get_data()
    btn_text = data.get("admin_btn_text")

    menus = load_menus()
    new_btn = {
        "id": str(uuid.uuid4())[:8],
        "text": btn_text,
        "action": "menu",
        "target": target_menu
    }
    menus[current_menu]["buttons"].append(new_btn)
    save_menus(menus)

    await state.clear()
    await callback.message.edit_text(f"✅ Кнопка «{btn_text}» добавлена!")
    await callback.message.answer("Админ-панель:", reply_markup=admin_main_kb())
    await callback.answer()

@router.message(AdminStates.waiting_button_content)
async def process_button_content(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    data = await state.get_data()
    menu_id = data.get("admin_menu_id")
    btn_text = data.get("admin_btn_text")

    content = message.text or message.caption or ""
    entities = serialize_entities(message.entities or message.caption_entities)

    menus = load_menus()
    new_btn = {
        "id": str(uuid.uuid4())[:8],
        "text": btn_text,
        "action": "text",
        "content": content,
        "entities": entities
    }
    menus[menu_id]["buttons"].append(new_btn)
    save_menus(menus)

    await state.clear()
    await message.answer(f"✅ Кнопка «{btn_text}» добавлена!", reply_markup=admin_main_kb())

@router.callback_query(F.data.startswith("admin_btns_"))
async def admin_buttons_list(callback: CallbackQuery):
    menu_id = callback.data.replace("admin_btns_", "")
    await callback.message.edit_text("🔘 Кнопки в меню:", reply_markup=buttons_list_kb(menu_id))
    await callback.answer()

@router.callback_query(F.data.startswith("admin_btn_"))
async def admin_button_view(callback: CallbackQuery):
    parts = callback.data.split("_")
    menu_id = parts[2]
    btn_id = parts[3]

    menus = load_menus()
    menu = menus.get(menu_id, {})
    btn = next((b for b in menu.get("buttons", []) if b["id"] == btn_id), None)

    if not btn:
        await callback.answer("Кнопка не найдена", show_alert=True)
        return

    if btn["action"] == "text":
        action_info = "Текст (поддерживает премиум-эмодзи)"
    elif btn["action"] == "menu":
        action_info = f"Открывает меню: {btn.get('target')}"
    elif btn["action"] == "wallet":
        action_info = "Запрашивает TON-кошелёк"
    else:
        action_info = btn["action"]

    text = f"🔹 <b>{btn['text']}</b>\n\nТип: {action_info}"
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=button_actions_kb(menu_id, btn_id))
    await callback.answer()

@router.callback_query(F.data.startswith("admin_editcontent_"))
async def admin_edit_content(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    menu_id = parts[2]
    btn_id = parts[3]
    await state.update_data(edit_menu_id=menu_id, edit_btn_id=btn_id)
    await state.set_state(AdminStates.waiting_edit_content)
    await callback.message.edit_text(
        "Отправь новый текст ответа.\nМожно использовать премиум-эмодзи."
    )
    await callback.answer()

@router.message(AdminStates.waiting_edit_content)
async def process_edit_content(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    data = await state.get_data()
    menu_id = data.get("edit_menu_id")
    btn_id = data.get("edit_btn_id")

    content = message.text or ""
    entities = serialize_entities(message.entities)

    menus = load_menus()
    for btn in menus.get(menu_id, {}).get("buttons", []):
        if btn["id"] == btn_id:
            btn["content"] = content
            btn["entities"] = entities
            break
    save_menus(menus)

    await state.clear()
    await message.answer("✅ Текст обновлён!", reply_markup=admin_main_kb())

@router.callback_query(F.data.startswith("admin_delbtn_"))
async def admin_delete_button(callback: CallbackQuery):
    parts = callback.data.split("_")
    menu_id = parts[2]
    btn_id = parts[3]

    menus = load_menus()
    if menu_id in menus:
        menus[menu_id]["buttons"] = [b for b in menus[menu_id]["buttons"] if b["id"] != btn_id]
        save_menus(menus)

    await callback.message.edit_text("✅ Кнопка удалена")
    await callback.message.answer("Админ-панель:", reply_markup=admin_main_kb())
    await callback.answer()

@router.callback_query(F.data.startswith("admin_delmenu_"))
async def admin_delete_menu(callback: CallbackQuery):
    menu_id = callback.data.replace("admin_delmenu_", "")
    if menu_id == "main":
        await callback.answer("Главное меню нельзя удалить", show_alert=True)
        return

    menus = load_menus()
    if menu_id in menus:
        del menus[menu_id]
        save_menus(menus)

    await callback.message.edit_text("✅ Меню удалено")
    await callback.message.answer("Админ-панель:", reply_markup=admin_main_kb())
    await callback.answer()

@router.callback_query(F.data == "admin_manage_buttons")
async def admin_manage_buttons(callback: CallbackQuery):
    await callback.message.edit_text("Выбери меню:", reply_markup=menus_list_kb())
    await callback.answer()

@router.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery):
    data = load_users()
    total = len(data)
    applied = sum(1 for u in data.values() if u.get("status") == "applied")
    accepted = sum(1 for u in data.values() if u.get("status") == "accepted")
    rejected = sum(1 for u in data.values() if u.get("status") == "rejected")

    text = (
        f"📊 <b>Статистика</b>\n\n"
        f"Всего заявок: {total}\n"
        f"На рассмотрении: {applied}\n"
        f"Принято: {accepted}\n"
        f"Отклонено: {rejected}"
    )
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=admin_main_kb())
    await callback.answer()

# -------------------- Меню участников (инлайн) --------------------

@router.callback_query(F.data == "member_menu")
async def show_member_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    user = get_user(callback.from_user.id)
    if not user or user.get("status") != "accepted":
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.message.edit_text("📋 Главное меню:", reply_markup=get_member_main_keyboard())
    await callback.answer()

@router.callback_query(F.data == "member_profile")
async def show_profile(callback: CallbackQuery, state: FSMContext):
    current_state = await state.get_state()
    if current_state is not None:
        await callback.answer("Сначала заверши текущее действие или отмени его", show_alert=True)
        return

    user = get_user(callback.from_user.id)
    if not user or user.get("status") != "accepted":
        await callback.answer("Нет доступа", show_alert=True)
        return

    user = ensure_user_fields(user)
    set_user(callback.from_user.id, user)

    wallet = user.get("ton_wallet") or "не привязан"
    balance = user.get("balance", 0.0)

    text = (
        f"👤 <b>Твой профиль</b>\n\n"
        f"Имя: {user.get('full_name', callback.from_user.full_name)}\n"
        f"Username: @{user.get('username') or callback.from_user.username or 'нет'}\n"
        f"ID: <code>{callback.from_user.id}</code>\n"
        f"TON-кошелёк: <code>{wallet}</code>\n"
        f"Баланс: <b>{balance:.2f} TON</b>"
    )
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=back_to_menu_kb())
    await callback.answer()

@router.callback_query(F.data == "member_wallet")
async def start_bind_wallet(callback: CallbackQuery, state: FSMContext):
    current_state = await state.get_state()
    if current_state is not None:
        await callback.answer("Сначала заверши текущее действие", show_alert=True)
        return

    user = get_user(callback.from_user.id)
    if not user or user.get("status") != "accepted":
        await callback.answer("Нет доступа", show_alert=True)
        return

    await state.set_state(WalletStates.waiting_address)
    await callback.message.edit_text(
        "💎 Отправь адрес своего TON-кошелька <b>одним сообщением</b>.\n\n"
        "Пример: <code>UQBxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx</code>\n\n"
        "Или напиши /cancel чтобы отменить.",
        parse_mode="HTML"
    )
    await callback.answer()

@router.message(Command("cancel"))
async def cancel_any(message: Message, state: FSMContext):
    current = await state.get_state()
    if current is None:
        await message.answer("Нечего отменять.")
        return
    await state.clear()
    user = get_user(message.from_user.id)
    if user and user.get("status") == "accepted":
        await message.answer("Отменено.", reply_markup=ReplyKeyboardRemove())
        await message.answer("📋 Главное меню:", reply_markup=get_member_main_keyboard())
    else:
        await message.answer("Отменено.", reply_markup=ReplyKeyboardRemove())

@router.message(F.text == "❌ Отмена")
async def cancel_action_text(message: Message, state: FSMContext):
    current = await state.get_state()
    if current is None:
        return
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=ReplyKeyboardRemove())
    await message.answer("📋 Главное меню:", reply_markup=get_member_main_keyboard())

@router.message(WalletStates.waiting_address)
async def process_wallet_address(message: Message, state: FSMContext):
    address = (message.text or "").strip()

    if not re.match(r'^(UQ|EQ)[A-Za-z0-9_-]{46}$', address):
        await message.answer(
            "❌ Неверный формат адреса TON.\n\n"
            "Адрес должен начинаться с <code>UQ</code> или <code>EQ</code> и иметь длину 48 символов.\n"
            "Попробуй ещё раз или напиши /cancel.",
            parse_mode="HTML"
        )
        return

    user_id = message.from_user.id
    user = get_user(user_id) or {}
    user = ensure_user_fields(user)
    user["ton_wallet"] = address
    set_user(user_id, user)

    text = (
        f"💎 <b>Привязан TON-кошелёк</b>\n\n"
        f"От: {message.from_user.full_name}\n"
        f"Username: @{message.from_user.username or 'нет'}\n"
        f"ID: <code>{user_id}</code>\n\n"
        f"Адрес:\n<code>{address}</code>"
    )
    for admin_id in ADMINS:
        try:
            await bot.send_message(admin_id, text, parse_mode="HTML")
        except Exception as e:
            logger.error(e)

    await state.clear()
    await message.answer(
        "✅ Адрес кошелька сохранён в профиле!",
        reply_markup=ReplyKeyboardRemove()
    )
    await message.answer("📋 Главное меню:", reply_markup=get_member_main_keyboard())

# -------------------- Заявка на выплату (2 скрина) --------------------

@router.callback_query(F.data == "member_payout")
async def start_payout_request(callback: CallbackQuery, state: FSMContext):
    current_state = await state.get_state()
    if current_state is not None:
        await callback.answer("Сначала заверши текущее действие", show_alert=True)
        return

    user = get_user(callback.from_user.id)
    if not user or user.get("status") != "accepted":
        await callback.answer("Нет доступа", show_alert=True)
        return

    user = ensure_user_fields(user)
    if not user.get("ton_wallet"):
        await callback.message.edit_text(
            "❌ Сначала привяжи TON-кошелёк в профиле!",
            reply_markup=back_to_menu_kb()
        )
        await callback.answer()
        return

    await state.set_state(PayoutStates.waiting_screenshot_1)
    await state.update_data(payout_photos=[])
    await callback.message.edit_text(
        "📤 <b>Заявка на выплату</b>\n\n"
        "Пришли <b>первый скриншот</b> — диалог с продавцом.\n\n"
        "Можно отправить фото или файл-картинку.\n"
        "Чтобы отменить — /cancel",
        parse_mode="HTML"
    )
    await callback.answer()

@router.message(PayoutStates.waiting_screenshot_1, F.photo | F.document)
async def payout_screenshot_1(message: Message, state: FSMContext):
    file_id = None
    if message.photo:
        file_id = message.photo[-1].file_id
    elif message.document and message.document.mime_type and message.document.mime_type.startswith("image/"):
        file_id = message.document.file_id
    else:
        await message.answer("Пришли именно <b>изображение</b> (фото или картинку).", parse_mode="HTML")
        return

    data = await state.get_data()
    photos = data.get("payout_photos", [])
    photos.append(file_id)
    await state.update_data(payout_photos=photos)

    await state.set_state(PayoutStates.waiting_screenshot_2)
    await message.answer(
        "✅ Первый скрин получен.\n\n"
        "Теперь пришли <b>второй скриншот</b> — подтверждение от продавца, что подарок передан.",
        parse_mode="HTML"
    )

@router.message(PayoutStates.waiting_screenshot_1)
async def payout_screenshot_1_wrong(message: Message, state: FSMContext):
    await message.answer("Пожалуйста, отправь <b>фото</b> или изображение. Или /cancel", parse_mode="HTML")

@router.message(PayoutStates.waiting_screenshot_2, F.photo | F.document)
async def payout_screenshot_2(message: Message, state: FSMContext):
    file_id = None
    if message.photo:
        file_id = message.photo[-1].file_id
    elif message.document and message.document.mime_type and message.document.mime_type.startswith("image/"):
        file_id = message.document.file_id
    else:
        await message.answer("Пришли именно <b>изображение</b> (фото или картинку).", parse_mode="HTML")
        return

    data = await state.get_data()
    photos = data.get("payout_photos", [])
    photos.append(file_id)
    await state.update_data(payout_photos=photos)

    await state.set_state(PayoutStates.confirm_payout)
    await message.answer(
        "✅ Оба скриншота получены!\n\n"
        "Нажми кнопку ниже, чтобы отправить заявку на выплату админам.",
        reply_markup=payout_confirm_kb()
    )

@router.message(PayoutStates.waiting_screenshot_2)
async def payout_screenshot_2_wrong(message: Message, state: FSMContext):
    await message.answer("Пожалуйста, отправь <b>фото</b> или изображение. Или /cancel", parse_mode="HTML")

@router.callback_query(F.data == "payout_submit")
async def payout_submit(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    photos = data.get("payout_photos", [])
    if len(photos) < 2:
        await callback.answer("Не хватает скриншотов", show_alert=True)
        return

    user_id = callback.from_user.id
    user = get_user(user_id) or {}
    user = ensure_user_fields(user)

    payout_id = str(uuid.uuid4())[:8]
    payouts = load_payouts()
    payouts[payout_id] = {
        "user_id": user_id,
        "full_name": user.get("full_name") or callback.from_user.full_name,
        "username": user.get("username") or callback.from_user.username,
        "ton_wallet": user.get("ton_wallet"),
        "photos": photos,
        "status": "pending",
        "created_at": str(asyncio.get_event_loop().time())
    }
    save_payouts(payouts)

    caption = (
        f"💰 <b>Новая заявка на выплату</b>\n\n"
        f"ID заявки: <code>{payout_id}</code>\n"
        f"От: {payouts[payout_id]['full_name']}\n"
        f"Username: @{payouts[payout_id]['username'] or 'нет'}\n"
        f"User ID: <code>{user_id}</code>\n"
        f"Кошелёк: <code>{user.get('ton_wallet')}</code>\n\n"
        f"Скриншоты ниже ↓"
    )

    for admin_id in ADMINS:
        try:
            await bot.send_photo(
                admin_id,
                photo=photos[0],
                caption=caption + "\n\n1️⃣ Диалог с продавцом",
                parse_mode="HTML"
            )
            await bot.send_photo(
                admin_id,
                photo=photos[1],
                caption=f"2️⃣ Подтверждение передачи подарка\nЗаявка: <code>{payout_id}</code>",
                parse_mode="HTML",
                reply_markup=admin_payout_actions_kb(payout_id)
            )
        except Exception as e:
            logger.error(f"Ошибка отправки админу {admin_id}: {e}")

    await state.clear()
    await callback.message.edit_text("✅ Заявка отправлена админам! Ожидай решения.")
    await callback.message.answer("📋 Главное меню:", reply_markup=get_member_main_keyboard())
    await callback.answer()

@router.callback_query(F.data == "payout_cancel")
async def payout_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("Заявка отменена.")
    await callback.message.answer("📋 Главное меню:", reply_markup=get_member_main_keyboard())
    await callback.answer()

# -------------------- Админ: одобрение/отклонение выплаты --------------------

@router.callback_query(F.data.startswith("payout_approve_"))
async def payout_approve_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return

    payout_id = callback.data.replace("payout_approve_", "")
    payouts = load_payouts()
    payout = payouts.get(payout_id)

    if not payout or payout.get("status") != "pending":
        await callback.answer("Заявка уже обработана или не найдена", show_alert=True)
        return

    await state.set_state(AdminStates.waiting_payout_amount)
    await state.update_data(payout_id=payout_id)
    await callback.message.answer(
        f"Введи сумму начисления в TON для заявки <code>{payout_id}</code>\n"
        f"(будет добавлена на баланс пользователя):",
        parse_mode="HTML"
    )
    await callback.answer()

@router.message(AdminStates.waiting_payout_amount)
async def process_payout_amount(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    try:
        amount = float(message.text.strip().replace(",", "."))
        if amount <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введи корректное положительное число (например: 5.5)")
        return

    data = await state.get_data()
    payout_id = data.get("payout_id")
    payouts = load_payouts()
    payout = payouts.get(payout_id)

    if not payout or payout.get("status") != "pending":
        await state.clear()
        await message.answer("Заявка уже обработана.")
        return

    user_id = payout["user_id"]
    user = get_user(user_id) or {}
    user = ensure_user_fields(user)
    user["balance"] = float(user.get("balance", 0)) + amount
    set_user(user_id, user)

    payout["status"] = "approved"
    payout["amount"] = amount
    payout["approved_by"] = message.from_user.id
    save_payouts(payouts)

    try:
        await bot.send_message(
            user_id,
            f"✅ Твоя заявка на выплату одобрена!\n"
            f"На баланс начислено: <b>{amount:.2f} TON</b>\n"
            f"Текущий баланс: <b>{user['balance']:.2f} TON</b>",
            parse_mode="HTML"
        )
        await bot.send_message(
            user_id,
            "📋 Главное меню:",
            reply_markup=get_member_main_keyboard()
        )
    except Exception as e:
        logger.error(e)

    await state.clear()
    await message.answer(
        f"✅ Начислено {amount:.2f} TON пользователю {payout.get('full_name')}.\n"
        f"Новый баланс: {user['balance']:.2f} TON",
        reply_markup=admin_main_kb()
    )

@router.callback_query(F.data.startswith("payout_reject_"))
async def payout_reject(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return

    payout_id = callback.data.replace("payout_reject_", "")
    payouts = load_payouts()
    payout = payouts.get(payout_id)

    if not payout or payout.get("status") != "pending":
        await callback.answer("Заявка уже обработана или не найдена", show_alert=True)
        return

    payout["status"] = "rejected"
    payout["rejected_by"] = callback.from_user.id
    save_payouts(payouts)

    try:
        await bot.send_message(
            payout["user_id"],
            "❌ Твоя заявка на выплату отклонена."
        )
        await bot.send_message(
            payout["user_id"],
            "📋 Главное меню:",
            reply_markup=get_member_main_keyboard()
        )
    except:
        pass

    await callback.message.answer(f"❌ Заявка {payout_id} отклонена.")
    await callback.answer("Отклонено")

@router.callback_query(F.data == "admin_payouts")
async def admin_payouts_list(callback: CallbackQuery):
    payouts = load_payouts()
    pending = [pid for pid, p in payouts.items() if p.get("status") == "pending"]
    text = f"💰 <b>Заявки на выплату</b>\n\nОжидают: {len(pending)}\nВсего: {len(payouts)}"
    if pending:
        text += "\n\nПоследние pending:\n" + "\n".join(f"• <code>{pid}</code>" for pid in pending[-10:])
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=admin_main_kb())
    await callback.answer()

# -------------------- Вывод денег --------------------

@router.callback_query(F.data == "member_withdraw")
async def start_withdraw(callback: CallbackQuery, state: FSMContext):
    current_state = await state.get_state()
    if current_state is not None:
        await callback.answer("Сначала заверши текущее действие", show_alert=True)
        return

    user = get_user(callback.from_user.id)
    if not user or user.get("status") != "accepted":
        await callback.answer("Нет доступа", show_alert=True)
        return

    user = ensure_user_fields(user)
    set_user(callback.from_user.id, user)

    if not user.get("ton_wallet"):
        await callback.message.edit_text(
            "❌ Сначала привяжи TON-кошелёк!",
            reply_markup=back_to_menu_kb()
        )
        await callback.answer()
        return

    balance = float(user.get("balance", 0))
    if balance <= 0:
        await callback.message.edit_text(
            f"❌ На балансе недостаточно средств.\nТекущий баланс: <b>{balance:.2f} TON</b>",
            parse_mode="HTML",
            reply_markup=back_to_menu_kb()
        )
        await callback.answer()
        return

    await state.set_state(WithdrawStates.waiting_amount)
    await callback.message.edit_text(
        f"💸 <b>Вывод средств</b>\n\n"
        f"Твой баланс: <b>{balance:.2f} TON</b>\n"
        f"Кошелёк: <code>{user['ton_wallet']}</code>\n\n"
        f"Введи сумму для вывода (не больше баланса).\n"
        f"Или /cancel для отмены.",
        parse_mode="HTML"
    )
    await callback.answer()

@router.message(WithdrawStates.waiting_amount)
async def process_withdraw_amount(message: Message, state: FSMContext):
    try:
        amount = float(message.text.strip().replace(",", "."))
        if amount <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введи корректное положительное число (например: 10 или 5.5)\nИли /cancel")
        return

    user_id = message.from_user.id
    user = get_user(user_id) or {}
    user = ensure_user_fields(user)
    balance = float(user.get("balance", 0))

    if amount > balance:
        await message.answer(
            f"❌ Недостаточно средств.\n"
            f"Ты хочешь вывести {amount:.2f} TON, а на балансе только <b>{balance:.2f} TON</b>.\n"
            f"Введи меньшую сумму или /cancel.",
            parse_mode="HTML"
        )
        return

    user["balance"] = balance - amount
    set_user(user_id, user)

    text = (
        f"💸 <b>Заявка на вывод</b>\n\n"
        f"От: {user.get('full_name') or message.from_user.full_name}\n"
        f"Username: @{user.get('username') or message.from_user.username or 'нет'}\n"
        f"ID: <code>{user_id}</code>\n\n"
        f"Сумма: <b>{amount:.2f} TON</b>\n"
        f"Кошелёк: <code>{user.get('ton_wallet')}</code>\n"
        f"Остаток баланса: {user['balance']:.2f} TON"
    )
    for admin_id in ADMINS:
        try:
            await bot.send_message(admin_id, text, parse_mode="HTML")
        except Exception as e:
            logger.error(e)

    await state.clear()
    await message.answer(
        f"✅ Заявка на вывод <b>{amount:.2f} TON</b> отправлена!\n"
        f"Остаток на балансе: <b>{user['balance']:.2f} TON</b>\n"
        f"Админы скоро переведут на твой кошелёк.",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove()
    )
    await message.answer("📋 Главное меню:", reply_markup=get_member_main_keyboard())

# -------------------- Обработка кнопок участников (динамические меню) --------------------

@router.message(F.text)
async def handle_member_buttons(message: Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is not None:
        return

    user = get_user(message.from_user.id)
    if not user or user.get("status") != "accepted":
        return

    text = message.text
    menus = load_menus()

    for menu_id, menu in menus.items():
        for btn in menu.get("buttons", []):
            if btn["text"] == text:
                if btn["action"] == "text":
                    entities = deserialize_entities(btn.get("entities"))
                    await message.answer(
                        btn.get("content", "Пусто"),
                        entities=entities
                    )
                elif btn["action"] == "menu":
                    target = btn.get("target")
                    target_menu = menus.get(target)
                    if target_menu:
                        kb = build_reply_keyboard(target)
                        await message.answer(
                            f"📁 {target_menu.get('title', 'Меню')}",
                            reply_markup=kb or ReplyKeyboardRemove()
                        )
                    else:
                        await message.answer("Меню не найдено")
                elif btn["action"] == "wallet":
                    await state.set_state(WalletStates.waiting_address)
                    await message.answer(
                        "💎 Отправь адрес своего TON-кошелька <b>одним сообщением</b>.\n\n"
                        "Пример: <code>UQBxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx</code>\n"
                        "Или /cancel",
                        parse_mode="HTML"
                    )
                return

# -------------------- Запуск --------------------

async def main():
    menus = load_menus()
    if "main" not in menus:
        menus["main"] = {"title": "Главное меню", "buttons": []}
        save_menus(menus)

    if not PAYOUTS_FILE.exists():
        save_payouts({})

    dp.include_router(router)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
