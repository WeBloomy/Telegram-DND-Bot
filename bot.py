import os
import json
import psycopg2
import re
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
from dotenv import load_dotenv

load_dotenv()

# ============= НАСТРОЙКИ =============
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_API_URL = os.getenv("GROQ_API_URL")

# ============= МОДЕЛИ ДАННЫХ =============
@dataclass
class Character:
    user_id: int
    name: str
    level: int = 1
    hp: int = 100
    max_hp: int = 100
    armor: int = 5
    strength: int = 10
    agility: int = 10
    intelligence: int = 10
    experience: int = 0
    gold: int = 50
    current_location: str = "Начальная деревня"
    location_state: str = "" 
    equipped_weapon: str = "" 
    equipped_armor: str = "" 
    
@dataclass
class Item:
    name: str
    type: str 
    damage: int = 0
    armor_bonus: int = 0
    heal: int = 0
    description: str = ""
    item_id: str = "" 

@dataclass
class Enemy:
    name: str
    hp: int
    max_hp: int
    armor: int
    damage: int
    experience_reward: int
    gold_reward: int

# ============= БАЗА ДАННЫХ =============
class Database:
    def __init__(self):
        self.conn = psycopg2.connect(
            host=os.getenv("DB_HOST"),
            port=int(os.getenv("DB_PORT", 5432)),
            database=os.getenv("DB_NAME"),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASSWORD")
        )
        self.create_tables()
    
    def create_tables(self):
        cursor = self.conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS characters (
                user_id BIGINT PRIMARY KEY,
                name TEXT,
                level INTEGER,
                hp INTEGER,
                max_hp INTEGER,
                armor INTEGER,
                strength INTEGER,
                agility INTEGER,
                intelligence INTEGER,
                experience INTEGER,
                gold INTEGER,
                current_location TEXT,
                location_state TEXT,
                equipped_weapon TEXT,
                equipped_armor TEXT
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS inventory (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                item_data TEXT,
                FOREIGN KEY (user_id) REFERENCES characters (user_id)
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS active_battles (
                user_id BIGINT PRIMARY KEY,
                enemy_data TEXT,
                FOREIGN KEY (user_id) REFERENCES characters (user_id)
            )
        """)
        
        self.conn.commit()
        cursor.close()
    
    def save_character(self, char: Character):
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO characters VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE SET
                name = EXCLUDED.name,
                level = EXCLUDED.level,
                hp = EXCLUDED.hp,
                max_hp = EXCLUDED.max_hp,
                armor = EXCLUDED.armor,
                strength = EXCLUDED.strength,
                agility = EXCLUDED.agility,
                intelligence = EXCLUDED.intelligence,
                experience = EXCLUDED.experience,
                gold = EXCLUDED.gold,
                current_location = EXCLUDED.current_location,
                location_state = EXCLUDED.location_state,
                equipped_weapon = EXCLUDED.equipped_weapon,
                equipped_armor = EXCLUDED.equipped_armor
        """, (char.user_id, char.name, char.level, char.hp, char.max_hp, char.armor,
              char.strength, char.agility, char.intelligence, char.experience, char.gold,
              char.current_location, char.location_state, char.equipped_weapon, char.equipped_armor))
        self.conn.commit()
        cursor.close()
    
    def get_character(self, user_id: int) -> Optional[Character]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM characters WHERE user_id = %s", (user_id,))
        row = cursor.fetchone()
        cursor.close()
        if row:
            return Character(*row)
        return None
    
    def add_item(self, user_id: int, item: Item):
        cursor = self.conn.cursor()
        cursor.execute("INSERT INTO inventory (user_id, item_data) VALUES (%s, %s)",
                      (user_id, json.dumps(asdict(item))))
        self.conn.commit()
        cursor.close()
    
    def get_inventory(self, user_id: int) -> List[Item]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT item_data FROM inventory WHERE user_id = %s", (user_id,))
        rows = cursor.fetchall()
        cursor.close()
        return [Item(**json.loads(row[0])) for row in rows]
    
    def save_battle(self, user_id: int, enemy: Enemy):
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO active_battles VALUES (%s, %s)
            ON CONFLICT (user_id) DO UPDATE SET enemy_data = EXCLUDED.enemy_data
        """, (user_id, json.dumps(asdict(enemy))))
        self.conn.commit()
        cursor.close()
    
    def get_battle(self, user_id: int) -> Optional[Enemy]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT enemy_data FROM active_battles WHERE user_id = %s", (user_id,))
        row = cursor.fetchone()
        cursor.close()
        if row:
            return Enemy(**json.loads(row[0]))
        return None
    
    def clear_battle(self, user_id: int):
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM active_battles WHERE user_id = %s", (user_id,))
        self.conn.commit()
        cursor.close()

# ============= AI ГЕНЕРАЦИЯ =============
class AIGenerator:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
    
    def generate(self, prompt: str, max_tokens: int = 500) -> str:
        """Генерация текста через Groq API"""
        data = {
            "model": "llama-3.3-70b-versatile",  
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": 0.8
        }
        
        try:
            response = requests.post(
                GROQ_API_URL, 
                headers=self.headers, 
                json=data,
                timeout=30
            )
            response.raise_for_status()
            result = response.json()
            return result["choices"][0]["message"]["content"]
        except requests.exceptions.RequestException as e:
            print(f"API Error: {e}")
            if hasattr(e.response, 'text'):
                print(f"Response: {e.response.text}")
            return f"Ошибка генерации: {str(e)}"
        except Exception as e:
            print(f"Unexpected error: {e}")
            return f"Неожиданная ошибка: {str(e)}"
    
    def generate_location(self, location_name: str, char: Character) -> str:
        """Генерация описания локации"""
        prompt = f"""Ты - мастер D&D игры. Опиши локацию "{location_name}" для игрока уровня {char.level}.
Включи:
- Атмосферное описание (2-3 предложения)
- Что видит персонаж
- 2-3 варианта действий
- Иногда (30% вероятность) можешь добавить встречу с враждебным существом

Если добавляешь врага, начни строку с "ВРАГ:" и опиши его появление.

Формат ответа:
ОПИСАНИЕ: [описание]
ВРАГ: [если есть - описание врага и его появления]
ДЕЙСТВИЯ:
1. [действие 1]
2. [действие 2]
3. [действие 3]"""
        
        return self.generate(prompt)
    
    def generate_enemy(self, level: int, location: str) -> Dict:
        """Генерация противника"""
        prompt = f"""Создай врага для D&D игры уровня {level} в локации "{location}".
Верни ТОЛЬКО JSON в формате:
{{
    "name": "название", 
    "hp": число,
    "armor": число,
    "damage": число,
    "description": "краткое описание"
}}

HP: {50 + level * 20}-{100 + level * 30}
Armor: {level * 2}-{level * 5}
Damage: {5 + level * 3}-{10 + level * 5}"""
        
        response = self.generate(prompt, 200)
        try:
            json_match = re.search(r'\{[^}]+\}', response, re.DOTALL)
            if json_match:
                enemy_data = json.loads(json_match.group())
                return {
                    "name": enemy_data.get("name", "Неизвестный враг"),
                    "hp": enemy_data.get("hp", 50),
                    "armor": enemy_data.get("armor", 5),
                    "damage": enemy_data.get("damage", 10),
                    "description": enemy_data.get("description", "")
                }
        except:
            pass
        
        return {
            "name": "Дикий волк",
            "hp": 50 + level * 20,
            "armor": level * 3,
            "damage": 5 + level * 3,
            "description": "Агрессивный хищник"
        }
    
    def calculate_damage(self, action: str, char: Character, enemy: Enemy) -> Dict:
        """Расчёт урона на основе описания действия"""
        prompt = f"""Игрок (Сила: {char.strength}, Ловкость: {char.agility}) атакует врага:
Враг: {enemy.name} (Броня: {enemy.armor})

Действие игрока: "{action}"

Оцени атаку и верни ТОЛЬКО JSON:
{{
    "damage": число_урона (5-50),
    "critical": true/false,
    "description": "описание результата атаки (1 предложение)"
}}

Учти креативность, точность описания и характеристики персонажа."""
        
        response = self.generate(prompt, 150)
        try:
            json_match = re.search(r'\{[^}]+\}', response, re.DOTALL)
            if json_match:
                return json.loads(json_match.group())
        except:
            pass
        
        base_damage = char.strength + (char.agility // 2)
        critical = "крит" in action.lower() or "точно" in action.lower()
        damage = int(base_damage * (1.5 if critical else 1) - enemy.armor * 0.3)
        
        return {
            "damage": max(1, damage),
            "critical": critical,
            "description": "Удар достигает цели!"
        }

# ============= ИГРОВАЯ ЛОГИКА =============
class GameEngine:
    def __init__(self, db: Database, ai: AIGenerator):
        self.db = db
        self.ai = ai
    
    def create_character(self, user_id: int, name: str) -> Character:
        char = Character(user_id=user_id, name=name)
        self.db.save_character(char)
        
        self.db.add_item(user_id, Item("Ржавый меч", "weapon", damage=5, description="Старый меч"))
        self.db.add_item(user_id, Item("Кожаная броня", "armor", armor_bonus=3, description="Простая броня"))
        self.db.add_item(user_id, Item("Зелье лечения", "potion", heal=30, description="Восстанавливает 30 HP"))
        
        return char
    
    def process_action(self, user_id: int, action: str) -> Dict:
        """Обработка действия игрока в локации"""
        char = self.db.get_character(user_id)
        
        prompt = f"""Игрок в локации "{char.current_location}".
Предыдущая ситуация:
{char.location_state}

Действие игрока: {action}

Золото игрока: {char.gold}

Опиши результат действия (2-3 предложения) и предложи 2-3 новых варианта действий.
Иногда (30% вероятность) можешь добавить встречу с враждебным существом.

ВАЖНО: Если игрок покупает предмет, начни строку с "ПОКУПКА:" и укажи:
ПОКУПКА: название_предмета | цена | тип (weapon/armor/potion/misc) | характеристики

Если действие приводит к бою, начни строку с "ВРАГ:" и опиши врага.

Формат:
РЕЗУЛЬТАТ: [что произошло]
ПОКУПКА: [если есть - название | цена | тип | урон или броня или лечение]
ВРАГ: [если есть - описание врага]
ДЕЙСТВИЯ:
1. [действие 1]
2. [действие 2]"""
        
        result_text = self.ai.generate(prompt, 400)
        
        purchase_match = re.search(r'ПОКУПКА:\s*([^|]+)\|\s*(\d+)\|\s*(\w+)\|\s*(.+)', result_text)
        purchase_info = None
        
        if purchase_match:
            item_name = purchase_match.group(1).strip()
            price = int(purchase_match.group(2))
            item_type = purchase_match.group(3).strip()
            stats = purchase_match.group(4).strip()
            
            if char.gold >= price:
                char.gold -= price
                
                damage = 0
                armor_bonus = 0
                heal = 0
                
                if 'урон' in stats.lower() or 'damage' in stats.lower():
                    damage_match = re.search(r'(\d+)', stats)
                    if damage_match:
                        damage = int(damage_match.group(1))
                
                if 'броня' in stats.lower() or 'armor' in stats.lower():
                    armor_match = re.search(r'(\d+)', stats)
                    if armor_match:
                        armor_bonus = int(armor_match.group(1))
                
                if 'лечение' in stats.lower() or 'heal' in stats.lower() or 'hp' in stats.lower():
                    heal_match = re.search(r'(\d+)', stats)
                    if heal_match:
                        heal = int(heal_match.group(1))
                
                new_item = Item(
                    name=item_name,
                    type=item_type,
                    damage=damage,
                    armor_bonus=armor_bonus,
                    heal=heal,
                    description=stats
                )
                
                self.db.add_item(user_id, new_item)
                self.db.save_character(char)
                
                purchase_info = {
                    "success": True,
                    "item": item_name,
                    "price": price,
                    "gold_left": char.gold
                }
            else:
                purchase_info = {
                    "success": False,
                    "item": item_name,
                    "price": price,
                    "gold_needed": price - char.gold
                }
        
        has_enemy = "ВРАГ:" in result_text or ("враг" in result_text.lower() and any(word in result_text.lower() for word in ["нападает", "атакует", "бросается", "выскакивает"]))
        
        return {
            "text": result_text,
            "has_enemy": has_enemy,
            "purchase": purchase_info
        }
    
    def start_battle(self, user_id: int) -> tuple[Character, Enemy]:
        char = self.db.get_character(user_id)
        enemy_data = self.ai.generate_enemy(char.level, char.current_location)
        
        enemy = Enemy(
            name=enemy_data["name"],
            hp=enemy_data["hp"],
            max_hp=enemy_data["hp"],
            armor=enemy_data["armor"],
            damage=enemy_data["damage"],
            experience_reward=20 * char.level,
            gold_reward=10 * char.level
        )
        
        self.db.save_battle(user_id, enemy)
        return char, enemy
    
    def process_attack(self, user_id: int, action: str) -> Dict:
        char = self.db.get_character(user_id)
        enemy = self.db.get_battle(user_id)
        
        if not enemy:
            return {"error": "Нет активного боя"}
        
        attack_result = self.ai.calculate_damage(action, char, enemy)
        damage = attack_result["damage"]
        enemy.hp -= damage
        
        result = {
            "player_damage": damage,
            "attack_description": attack_result["description"],
            "critical": attack_result.get("critical", False),
            "enemy_defeated": enemy.hp <= 0
        }
        
        if enemy.hp <= 0:
            char.experience += enemy.experience_reward
            char.gold += enemy.gold_reward
            
            exp_needed = char.level * 100
            if char.experience >= exp_needed:
                char.level += 1
                char.max_hp += 20
                char.hp = char.max_hp
                char.strength += 2
                char.agility += 2
                result["level_up"] = True
            
            self.db.save_character(char)
            self.db.clear_battle(user_id)
            
            result["rewards"] = {
                "exp": enemy.experience_reward,
                "gold": enemy.gold_reward
            }
            
        else:
            enemy_damage = max(1, enemy.damage - char.armor)
            char.hp -= enemy_damage
            result["enemy_damage"] = enemy_damage
            result["enemy_hp"] = enemy.hp
            
            if char.hp <= 0:
                char.hp = char.max_hp // 2
                char.gold = max(0, char.gold - 20)
                result["player_defeated"] = True
                self.db.clear_battle(user_id)
            else:
                self.db.save_battle(user_id, enemy)
            
            self.db.save_character(char)
        
        return result

# ============= TELEGRAM BOT =============
db = Database()
ai = AIGenerator(GROQ_API_KEY)
game = GameEngine(db, ai)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    char = db.get_character(user_id)
    
    if char:
        await update.message.reply_text(
            f"С возвращением, {char.name}!\n\n"
            f"🎯 Уровень: {char.level}\n"
            f"❤️ HP: {char.hp}/{char.max_hp}\n"
            f"💰 Золото: {char.gold}\n\n"
            f"Используй /explore для исследования!"
        )
    else:
        await update.message.reply_text(
            "Добро пожаловать в мир приключений! 🗺️\n\n"
            "Введи имя своего персонажа:"
        )
        context.user_data['awaiting_name'] = True

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    
    if context.user_data.get('awaiting_name'):
        char = game.create_character(user_id, text)
        context.user_data['awaiting_name'] = False
        
        await update.message.reply_text(
            f"Персонаж {char.name} создан! ⚔️\n\n"
            f"📊 Характеристики:\n"
            f"Сила: {char.strength}\n"
            f"Ловкость: {char.agility}\n"
            f"Интеллект: {char.intelligence}\n\n"
            f"Используй /explore для начала приключения!"
        )
        return
    
    battle = db.get_battle(user_id)
    if battle:
        await update.message.reply_text("⏳ Обрабатываю атаку...")
        result = game.process_attack(user_id, text)
        
        if result.get("error"):
            await update.message.reply_text(result["error"])
            return
        
        response = f"⚔️ {result['attack_description']}\n"
        response += f"💥 Урон: {result['player_damage']}"
        
        if result.get("critical"):
            response += " 🔥 КРИТИЧЕСКИЙ УДАР!"
        
        if result.get("enemy_defeated"):
            response += f"\n\n🎉 Враг повержен!\n"
            response += f"📈 +{result['rewards']['exp']} опыта\n"
            response += f"💰 +{result['rewards']['gold']} золота"
            
            if result.get("level_up"):
                response += f"\n\n✨ НОВЫЙ УРОВЕНЬ! ✨"
            
            char = db.get_character(user_id)
            keyboard = [
                [InlineKeyboardButton("🎒 Инвентарь", callback_data="inventory")],
                [InlineKeyboardButton("📊 Статистика", callback_data="stats")],
                [InlineKeyboardButton("🔄 Новая локация", callback_data="new_location")]
            ]
            
            continuation = f"\n\n📍 {char.current_location}\n"
            if char.location_state:
                continuation += f"{char.location_state}\n\n"
            continuation += "💬 Что будешь делать дальше?"
            
            await update.message.reply_text(
                response + continuation,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            enemy_hp_bar = "█" * int(result['enemy_hp'] / battle.max_hp * 10)
            response += f"\n\n🧟 {battle.name}\n"
            response += f"❤️ HP: {result['enemy_hp']}/{battle.max_hp} {enemy_hp_bar}\n\n"
            response += f"💢 Враг контратакует! Урон: {result['enemy_damage']}"
            
            char = db.get_character(user_id)
            response += f"\n\n👤 Ваше HP: {char.hp}/{char.max_hp}"
            
            if result.get("player_defeated"):
                response += "\n\n💀 Вы потерпели поражение! -20 золота"
                
                char = db.get_character(user_id)
                keyboard = [
                    [InlineKeyboardButton("🎒 Инвентарь", callback_data="inventory")],
                    [InlineKeyboardButton("📊 Статистика", callback_data="stats")],
                    [InlineKeyboardButton("🔄 Новая локация", callback_data="new_location")]
                ]
                
                continuation = f"\n\n📍 {char.current_location}\n"
                if char.location_state:
                    continuation += f"{char.location_state}\n\n"
                continuation += "💬 Приходишь в себя. Что будешь делать?"
                
                await update.message.reply_text(
                    response + continuation,
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            else:
                await update.message.reply_text(
                    response + "\n\n💬 Опиши своё следующее действие!"
                )
        return
    
    char = db.get_character(user_id)
    if char and char.location_state:
        await update.message.reply_text("⏳ Обрабатываю действие...")
        
        action_result = game.process_action(user_id, text)
        
        char.location_state = action_result["text"]
        db.save_character(char)
        
        response_text = f"📍 {char.current_location}\n\n{action_result['text']}"
        
        if action_result.get("purchase"):
            purchase = action_result["purchase"]
            if purchase["success"]:
                response_text += f"\n\n✅ Покупка успешна!\n"
                response_text += f"🎁 Получен: {purchase['item']}\n"
                response_text += f"💰 Потрачено: {purchase['price']} золота\n"
                response_text += f"💰 Осталось: {purchase['gold_left']} золота"
            else:
                response_text += f"\n\n❌ Недостаточно золота!\n"
                response_text += f"💰 Нужно ещё: {purchase['gold_needed']} золота"
        
        if action_result["has_enemy"]:
            char_obj, enemy = game.start_battle(user_id)
            
            await update.message.reply_text(
                f"{response_text}\n\n"
                f"⚔️ БОЙ НАЧАЛСЯ! ⚔️\n\n"
                f"🧟 {enemy.name}\n"
                f"❤️ HP: {enemy.hp}\n"
                f"🛡️ Броня: {enemy.armor}\n"
                f"💢 Урон: {enemy.damage}\n\n"
                f"💬 Опиши своё действие в бою!\n"
                f"Например: 'Замахиваюсь мечом и бью в голову'"
            )
        else:
            keyboard = [
                [InlineKeyboardButton("🎒 Инвентарь", callback_data="inventory")],
                [InlineKeyboardButton("📊 Статистика", callback_data="stats")],
                [InlineKeyboardButton("🔄 Новая локация", callback_data="new_location")]
            ]
            
            await update.message.reply_text(
                response_text + "\n\n💬 Что будешь делать дальше?",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
    else:
        await update.message.reply_text(
            "Используй /explore для начала исследования!"
        )

async def explore(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    char = db.get_character(user_id)
    
    if not char:
        await update.message.reply_text("Сначала создай персонажа через /start")
        return
    
    if char.location_state:
        keyboard = [
            [InlineKeyboardButton("🎒 Инвентарь", callback_data="inventory")],
            [InlineKeyboardButton("📊 Статистика", callback_data="stats")],
            [InlineKeyboardButton("🔄 Новая локация", callback_data="new_location")]
        ]
        
        await update.message.reply_text(
            f"📍 {char.current_location}\n\n{char.location_state}\n\n"
            f"💬 Напиши, что хочешь сделать:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return
    
    await update.message.reply_text("🗺️ Генерирую локацию...")
    
    location_desc = ai.generate_location(char.current_location, char)
    char.location_state = location_desc
    db.save_character(char)
    
    has_enemy = "ВРАГ:" in location_desc
    
    if has_enemy:
        char_obj, enemy = game.start_battle(user_id)
        
        await update.message.reply_text(
            f"📍 {char.current_location}\n\n{location_desc}\n\n"
            f"⚔️ БОЙ НАЧАЛСЯ! ⚔️\n\n"
            f"🧟 {enemy.name}\n"
            f"❤️ HP: {enemy.hp}\n"
            f"🛡️ Броня: {enemy.armor}\n"
            f"💢 Урон: {enemy.damage}\n\n"
            f"💬 Опиши своё действие в бою!"
        )
    else:
        keyboard = [
            [InlineKeyboardButton("🎒 Инвентарь", callback_data="inventory")],
            [InlineKeyboardButton("📊 Статистика", callback_data="stats")]
        ]
        
        await update.message.reply_text(
            f"📍 {char.current_location}\n\n{location_desc}\n\n"
            f"💬 Напиши, что хочешь сделать:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    
    if query.data == "inventory":
        char = db.get_character(user_id)
        items = db.get_inventory(user_id)
        
        inv_text = f"🎒 Инвентарь {char.name}\n\n"
        for item in items:
            inv_text += f"▫️ {item.name} ({item.type})\n"
            if item.damage: inv_text += f"  ⚔️ Урон: +{item.damage}\n"
            if item.armor_bonus: inv_text += f"  🛡️ Броня: +{item.armor_bonus}\n"
            if item.heal: inv_text += f"  💚 Лечение: +{item.heal}\n"
        
        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="back_to_location")]]
        await query.edit_message_text(inv_text, reply_markup=InlineKeyboardMarkup(keyboard))
    
    elif query.data == "stats":
        char = db.get_character(user_id)
        
        stats_text = f"📊 {char.name}\n\n"
        stats_text += f"🎯 Уровень: {char.level}\n"
        stats_text += f"⭐ Опыт: {char.experience}/{char.level * 100}\n"
        stats_text += f"❤️ HP: {char.hp}/{char.max_hp}\n"
        stats_text += f"🛡️ Броня: {char.armor}\n"
        stats_text += f"💪 Сила: {char.strength}\n"
        stats_text += f"🎯 Ловкость: {char.agility}\n"
        stats_text += f"🧠 Интеллект: {char.intelligence}\n"
        stats_text += f"💰 Золото: {char.gold}\n"
        
        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="back_to_location")]]
        await query.edit_message_text(stats_text, reply_markup=InlineKeyboardMarkup(keyboard))
    
    elif query.data == "back_to_location":
        char = db.get_character(user_id)
        
        keyboard = [
            [InlineKeyboardButton("🎒 Инвентарь", callback_data="inventory")],
            [InlineKeyboardButton("📊 Статистика", callback_data="stats")],
            [InlineKeyboardButton("🔄 Новая локация", callback_data="new_location")]
        ]
        
        await query.edit_message_text(
            f"📍 {char.current_location}\n\n{char.location_state}\n\n"
            f"💬 Напиши, что хочешь сделать:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    elif query.data == "new_location":
        char = db.get_character(user_id)
        
        char.location_state = ""
        db.save_character(char)
        
        await query.edit_message_text("🗺️ Генерирую новую локацию...")
        
        location_desc = ai.generate_location(char.current_location, char)
        char.location_state = location_desc
        db.save_character(char)
        
        has_enemy = "ВРАГ:" in location_desc
        
        if has_enemy:
            char_obj, enemy = game.start_battle(user_id)
            
            await query.edit_message_text(
                f"📍 {char.current_location}\n\n{location_desc}\n\n"
                f"⚔️ БОЙ НАЧАЛСЯ! ⚔️\n\n"
                f"🧟 {enemy.name}\n"
                f"❤️ HP: {enemy.hp}\n"
                f"🛡️ Броня: {enemy.armor}\n"
                f"💢 Урон: {enemy.damage}\n\n"
                f"💬 Опиши своё действие в бою!"
            )
        else:
            keyboard = [
                [InlineKeyboardButton("🎒 Инвентарь", callback_data="inventory")],
                [InlineKeyboardButton("📊 Статистика", callback_data="stats")]
            ]
            
            await query.edit_message_text(
                f"📍 {char.current_location}\n\n{location_desc}\n\n"
                f"💬 Напиши, что хочешь сделать:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
    
    elif query.data == "explore":
        char = db.get_character(user_id)
        
        keyboard = [
            [InlineKeyboardButton("🎒 Инвентарь", callback_data="inventory")],
            [InlineKeyboardButton("📊 Статистика", callback_data="stats")],
            [InlineKeyboardButton("🔄 Новая локация", callback_data="new_location")]
        ]
        
        message_text = f"📍 {char.current_location}\n\n"
        if char.location_state:
            message_text += f"{char.location_state}\n\n"
        message_text += "💬 Что будешь делать дальше?"
        
        await query.edit_message_text(
            message_text,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("explore", explore))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(button_callback))
    
    print("🤖 Бот запущен!")
    app.run_polling()

if __name__ == "__main__":
    main()