#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
بوت تلجرام للتعامل مع ملفات الصوت وتعديل وسوم ID3
"""

import os
import logging
import telebot
from telebot import types
import tempfile
import requests
import re
import time
import json
from pathlib import Path
import mutagen
from mutagen.id3 import ID3
import psycopg2
from psycopg2 import pool
from datetime import datetime

# إعداد قاعدة البيانات
DATABASE_URL = os.getenv('DATABASE_URL')
connection_pool = None

if DATABASE_URL:
    try:
        # تغيير عنوان URL لاستخدام connection pooling
        pooled_url = DATABASE_URL.replace('.us-east-2', '-pooler.us-east-2')
        connection_pool = pool.SimpleConnectionPool(1, 10, pooled_url)
        logger.info("تم الاتصال بقاعدة البيانات بنجاح")
        
        # إنشاء الجداول إذا لم تكن موجودة
        conn = connection_pool.getconn()
        cur = conn.cursor()
        
        # جدول السجلات
        cur.execute("""
            CREATE TABLE IF NOT EXISTS edit_logs (
                id SERIAL PRIMARY KEY,
                file_name TEXT NOT NULL,
                edit_type TEXT NOT NULL,
                edit_details JSONB,
                edited_by INTEGER,
                edited_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # جدول الإعدادات
        cur.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value JSONB,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        conn.commit()
        cur.close()
        connection_pool.putconn(conn)
        
    except Exception as e:
        logger.error(f"خطأ في الاتصال بقاعدة البيانات: {e}")
        connection_pool = None
else:
    logger.warning("لم يتم العثور على رابط قاعدة البيانات")

def log_edit(file_name, edit_type, edit_details, user_id):
    """تسجيل التعديلات في قاعدة البيانات"""
    if connection_pool:
        try:
            conn = connection_pool.getconn()
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO edit_logs (file_name, edit_type, edit_details, edited_by) VALUES (%s, %s, %s, %s)",
                (file_name, edit_type, json.dumps(edit_details), user_id)
            )
            conn.commit()
            cur.close()
            connection_pool.putconn(conn)
        except Exception as e:
            logger.error(f"خطأ في تسجيل التعديل: {e}")

def save_settings_to_db():
    """حفظ الإعدادات في قاعدة البيانات"""
    if connection_pool:
        try:
            data = {
                'source_channel': SOURCE_CHANNEL,
                'target_channel': TARGET_CHANNEL,
                'current_template_key': current_template_key,
                'templates': templates,
                'replacements': replacements,
                'footers': footers,
                'config': config,
                'album_cover_path': album_cover_path
            }
            
            conn = connection_pool.getconn()
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO settings (key, value) VALUES ('bot_settings', %s) ON CONFLICT (key) DO UPDATE SET value = %s, updated_at = CURRENT_TIMESTAMP",
                (json.dumps(data), json.dumps(data))
            )
            conn.commit()
            cur.close()
            connection_pool.putconn(conn)
            logger.info("تم حفظ الإعدادات في قاعدة البيانات")
        except Exception as e:
            logger.error(f"خطأ في حفظ الإعدادات: {e}")

def load_settings_from_db():
    """تحميل الإعدادات من قاعدة البيانات"""
    if connection_pool:
        try:
            conn = connection_pool.getconn()
            cur = conn.cursor()
            cur.execute("SELECT value FROM settings WHERE key = 'bot_settings'")
            result = cur.fetchone()
            cur.close()
            connection_pool.putconn(conn)
            
            if result:
                data = result[0]
                return data
        except Exception as e:
            logger.error(f"خطأ في تحميل الإعدادات: {e}")
    return None

# تكوين السجلات
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

from dotenv import load_dotenv

# تحميل المتغيرات البيئية
load_dotenv()

# الحصول على رمز البوت
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN غير موجود في ملف .env")

ADMIN_ID = 485527614  # معرف المشرف

# القنوات
SOURCE_CHANNEL = ""  # سيتم تعيينه من خلال لوحة التحكم
TARGET_CHANNEL = ""  # سيتم تعيينه من خلال لوحة التحكم

# حالات البوت - تفعيل/تعطيل الميزات
config = {
    "bot_enabled": True,  # تفعيل/تعطيل البوت بالكامل
    "replacement_enabled": True,  # تفعيل/تعطيل الاستبدال
    "footer_enabled": True,  # تفعيل/تعطيل التذييل
    "remove_links_enabled": True,  # تفعيل/تعطيل حذف الروابط
    "album_cover_enabled": True  # تفعيل/تعطيل صورة الألبوم
}

# مسار صورة الألبوم
album_cover_path = None

# قوالب وسوم ID3
templates = {
    "افتراضي": {
        "name": "القالب الافتراضي",
        "artist": "$artist",  # سيحتفظ بالفنان الأصلي
        "album_artist": "$album_artist",  # سيحتفظ بفنان الألبوم الأصلي
        "album": "$album",  # سيحتفظ باسم الألبوم الأصلي
        "genre": "إنشاد",
        "year": "2025",
        "publisher": "الناشر الافتراضي",
        "copyright": "© 2025 جميع الحقوق محفوظة",
        "comment": "تم المعالجة بواسطة بوت معالجة الصوتيات",
        "website": "https://t.me/EchoAlMasirah",
        "composer": "ملحن افتراضي",
        "lyrics": "كلمات الأغنية الافتراضية",
        "description": "وصف للملف الصوتي"
    },
    "إنشاد": {
        "name": "قالب الأناشيد",
        "artist": "منشد",
        "album_artist": "فرقة الإنشاد",
        "album": "ألبوم الأناشيد",
        "genre": "إنشاد ديني",
        "year": "2025",
        "publisher": "دار النشر الإسلامية",
        "copyright": "© 2025 جميع الحقوق محفوظة",
        "comment": "إنتاج فرقة الإنشاد الإسلامية",
        "website": "https://t.me/EchoAlMasirah",
        "composer": "فرقة الإنشاد",
        "lyrics": "بسم الله الرحمن الرحيم",
        "description": "إنشاد ديني"
    }
}

# القالب الحالي
current_template_key = "افتراضي"

# قواعد الاستبدال
replacements = {
    "1": {
        "name": "استبدال الأخطاء الشائعة",
        "original": "الشيخ",
        "replacement": "الإمام",
        "tags": ["artist", "album_artist"]
    },
    "2": {
        "name": "تصحيح اسم الألبوم",
        "original": "البوم",
        "replacement": "ألبوم",
        "tags": ["album"]
    }
}

# التذييلات
footers = {
    "1": {
        "name": "تذييل الفنان",
        "text": " - منتجات دار الإنشاد",
        "tags": ["artist", "album_artist"]
    },
    "2": {
        "name": "تذييل الألبوم",
        "text": " (الإصدار الرسمي)",
        "tags": ["album"]
    }
}

# الوسوم المتاحة
available_id3_tags = {
    "artist": "الفنان",
    "album_artist": "فنان الألبوم",
    "album": "الألبوم",
    "genre": "النوع",
    "title": "العنوان",
    "year": "السنة",
    "publisher": "الناشر",
    "copyright": "حقوق النشر",
    "comment": "التعليق",
    "website": "رابط الموقع",
    "composer": "الملحن",
    "lyrics": "كلمات الأغنية",
    "description": "الوصف"
}

# حالات المحادثة
STATE_AWAITING_SOURCE_CHANNEL = "awaiting_source"
STATE_AWAITING_TARGET_CHANNEL = "awaiting_target"
STATE_AWAITING_REPLACEMENT_NAME = "awaiting_replacement_name"
STATE_AWAITING_REPLACEMENT_ORIGINAL = "awaiting_replacement_original"
STATE_AWAITING_REPLACEMENT_NEW = "awaiting_replacement_new"
STATE_AWAITING_FOOTER_NAME = "awaiting_footer_name"
STATE_AWAITING_FOOTER_TEXT = "awaiting_footer_text"
STATE_AWAITING_ALBUM_COVER = "awaiting_album_cover"
STATE_AWAITING_TEMPLATE_NAME = "awaiting_template_name"
STATE_AWAITING_TEMPLATE_FIELD = "awaiting_template_field"

# قواميس لتخزين بيانات المستخدمين
user_states = {}
user_channels = {}
temp_data = {}

# إنشاء كائن البوت
bot = telebot.TeleBot(BOT_TOKEN, parse_mode=None)

# وظائف معالجة الملفات والنصوص

def download_file(file_path):
    """تحميل ملف من خادم تلجرام."""
    try:
        file_info = bot.get_file(file_path)
        file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_info.file_path}"

        # إنشاء دليل مؤقت لتخزين الملف المحمّل
        with tempfile.TemporaryDirectory() as temp_dir:
            local_file_path = os.path.join(temp_dir, "audio_file.mp3")

            # تحميل الملف من خادم تلجرام
            response = requests.get(file_url)
            with open(local_file_path, 'wb') as file:
                file.write(response.content)

            logger.info(f"تم تحميل الملف: {local_file_path}")
            return local_file_path
    except Exception as e:
        logger.error(f"خطأ في تحميل الملف: {e}")
        return None


def apply_replacements(text, tag_key):
    """تطبيق قواعد الاستبدال على نص معين."""
    if not config["replacement_enabled"]:
        return text

    result = text
    for rule_id, rule in replacements.items():
        if tag_key in rule["tags"]:
            result = result.replace(rule["original"], rule["replacement"])

    return result


def apply_footer(text, tag_key):
    """إضافة التذييل إلى النص المحدد."""
    if not config["footer_enabled"]:
        return text

    result = text
    for footer_id, footer in footers.items():
        if tag_key in footer["tags"]:
            result = result + footer["text"]

    return result


def remove_links(text):
    """حذف الروابط والمعرفات من النص."""
    if not config["remove_links_enabled"]:
        return text

    # حذف الروابط (http:// و https:// و www.)
    result = re.sub(r'https?://\S+', '', text)
    result = re.sub(r'www\.\S+', '', result)

    # حذف معرفات تلجرام (@username)
    result = re.sub(r'@\w+', '', result)

    return result


def process_audio_tags(file_path, title=None):
    """معالجة الملف الصوتي بتعديل وسوم ID3 وفقاً للقالب."""
    # فحص ما إذا كان البوت مفعل بالكامل
    if not config["bot_enabled"]:
        # إذا كان البوت معطلاً، إرجاع True دون تنفيذ أي تعديلات
        logger.info("البوت معطّل، لن يتم إجراء تعديلات على وسوم ID3")
        return True

    try:
        # محاولة تحميل وسوم ID3 الموجودة أو إنشاء وسوم جديدة
        try:
            audio = ID3(file_path)
        except:
            # الملف لا يحتوي على وسم ID3، قم بإنشاء واحد
            audio = ID3()

        # تعيين العنوان من وصف الرسالة أو استخدام اسم الملف
        if title:
            # تطبيق الوظائف المختلفة على النص
            title_text = title
            title_text = remove_links(title_text)  # حذف الروابط
            title_text = apply_replacements(title_text, "title")  # تطبيق الاستبدالات
            title_text = apply_footer(title_text, "title")  # إضافة التذييل

            audio.add(mutagen.id3.TIT2(encoding=3, text=title_text))
        else:
            # استخدم اسم الملف بدون الامتداد كعنوان
            filename = "audio_file.mp3"
            title_text = filename
            title_text = remove_links(title_text)
            title_text = apply_replacements(title_text, "title")
            title_text = apply_footer(title_text, "title")

            audio.add(mutagen.id3.TIT2(encoding=3, text=title_text))

        # جلب القالب الحالي
        current_template = templates[current_template_key]

        # تحقق ما إذا كان يجب تطبيق القالب على الوسم أو الاحتفاظ بالقيمة الأصلية
        # نقرأ القيم الأصلية للوسوم إذا كانت موجودة
        # الحصول على الوسوم الأصلية بأمان
        try:
            original_artist = str(audio["TPE1"].text[0]) if "TPE1" in audio else ""
        except (KeyError, IndexError, AttributeError):
            original_artist = ""

        try:
            original_album_artist = str(audio["TPE2"].text[0]) if "TPE2" in audio else ""
        except (KeyError, IndexError, AttributeError):
            original_album_artist = ""

        try:
            original_album = str(audio["TALB"].text[0]) if "TALB" in audio else ""
        except (KeyError, IndexError, AttributeError):
            original_album = ""

        try:
            original_genre = str(audio["TCON"].text[0]) if "TCON" in audio else ""
        except (KeyError, IndexError, AttributeError):
            original_genre = ""

        try:
            original_year = str(audio["TYER"].text[0]) if "TYER" in audio else ""
        except (KeyError, IndexError, AttributeError):
            original_year = ""

        try:
            original_publisher = str(audio["TPUB"].text[0]) if "TPUB" in audio else ""
        except (KeyError, IndexError, AttributeError):
            original_publisher = ""

        try:
            original_copyright = str(audio["TCOP"].text[0]) if "TCOP" in audio else ""
        except (KeyError, IndexError, AttributeError):
            original_copyright = ""

        # الحصول على التعليق
        original_comment = ""
        try:
            comments = audio.getall("COMM")
            if comments and len(comments) > 0:
                for comm in comments:
                    if hasattr(comm, 'text') and comm.text:
                        original_comment = str(comm.text[0])
                        break
        except (KeyError, IndexError, AttributeError):
            original_comment = ""

        # الحصول على الملحن
        try:
            original_composer = str(audio["TCOM"].text[0]) if "TCOM" in audio else ""
        except (KeyError, IndexError, AttributeError):
            original_composer = ""

        # الحصول على كلمات الأغنية
        original_lyrics = ""
        try:
            lyrics_frames = audio.getall("USLT")
            if lyrics_frames and len(lyrics_frames) > 0:
                for uslt in lyrics_frames:
                    if hasattr(uslt, 'text') and uslt.text:
                        original_lyrics = str(uslt.text)
                        break
        except (KeyError, IndexError, AttributeError):
            original_lyrics = ""

        # الحصول على الوصف
        try:
            original_description = str(audio["TIT3"].text[0]) if "TIT3" in audio else ""
        except (KeyError, IndexError, AttributeError):
            original_description = ""

        # تطبيق وسوم قالب ID3 مع الوظائف المطلوبة
        # --- الفنان ---
        if current_template["artist"].strip() == "" or current_template["artist"].strip() == "$artist":
            # الاحتفاظ بالقيمة الأصلية إذا كان القالب فارغًا أو يحتوي على علامة خاصة
            artist_text = original_artist
        else:
            artist_text = current_template["artist"]
            artist_text = remove_links(artist_text)
            artist_text = apply_replacements(artist_text, "artist")
            artist_text = apply_footer(artist_text, "artist")

        # --- فنان الألبوم ---
        if current_template["album_artist"].strip() == "" or current_template["album_artist"].strip() == "$album_artist":
            album_artist_text = original_album_artist
        else:
            album_artist_text = current_template["album_artist"]
            album_artist_text = remove_links(album_artist_text)
            album_artist_text = apply_replacements(album_artist_text, "album_artist")
            album_artist_text = apply_footer(album_artist_text, "album_artist")

        # --- الألبوم ---
        if current_template["album"].strip() == "" or current_template["album"].strip() == "$album":
            album_text = original_album
        else:
            album_text = current_template["album"]
            album_text = remove_links(album_text)
            album_text = apply_replacements(album_text, "album")
            album_text = apply_footer(album_text, "album")

        # --- النوع ---
        if current_template["genre"].strip() == "" or current_template["genre"].strip() == "$genre":
            genre_text = original_genre
        else:
            genre_text = current_template["genre"]
            genre_text = remove_links(genre_text)
            genre_text = apply_replacements(genre_text, "genre")
            genre_text = apply_footer(genre_text, "genre")

        # --- السنة ---
        if current_template["year"].strip() == "" or current_template["year"].strip() == "$year":
            year_text = original_year
        else:
            year_text = current_template["year"]
            year_text = remove_links(year_text)
            year_text = apply_replacements(year_text, "year")
            year_text = apply_footer(year_text, "year")

        # --- الناشر ---
        if current_template["publisher"].strip() == "" or current_template["publisher"].strip() == "$publisher":
            publisher_text = original_publisher
        else:
            publisher_text = current_template["publisher"]
            publisher_text = remove_links(publisher_text)
            publisher_text = apply_replacements(publisher_text, "publisher")
            publisher_text = apply_footer(publisher_text, "publisher")

        # --- حقوق النشر ---
        if current_template["copyright"].strip() == "" or current_template["copyright"].strip() == "$copyright":
            copyright_text = original_copyright
        else:
            copyright_text = current_template["copyright"]
            copyright_text = remove_links(copyright_text)
            copyright_text = apply_replacements(copyright_text, "copyright")
            copyright_text = apply_footer(copyright_text, "copyright")

        # --- الوسوم الجديدة ---
        # --- التعليق ---
        if "comment" in current_template:
            if current_template["comment"].strip() == "" or current_template["comment"].strip() == "$comment":
                # الاحتفاظ بالقيمة الأصلية
                if original_comment:
                    audio.add(mutagen.id3.COMM(encoding=3, lang='ara', desc='', text=original_comment))
            else:
                comment_text = current_template["comment"]
                comment_text = remove_links(comment_text)
                comment_text = apply_replacements(comment_text, "comment")
                comment_text = apply_footer(comment_text, "comment")
                audio.add(mutagen.id3.COMM(encoding=3, lang='ara', desc='', text=comment_text))

        # --- رابط الموقع ---
        if "website" in current_template:
            if current_template["website"].strip() == "" or current_template["website"].strip() == "$website":
                # نحتفظ بالروابط الأصلية إذا وجدت
                for woar in audio.getall("WOAR"):
                    audio.add(woar)  # نترك الرابط الأصلي
            else:
                website_text = current_template["website"]
                website_text = remove_links(website_text)
                website_text = apply_replacements(website_text, "website")
                website_text = apply_footer(website_text, "website")
                audio.add(mutagen.id3.WOAR(url=website_text))

        # --- الملحن ---
        if "composer" in current_template:
            if current_template["composer"].strip() == "" or current_template["composer"].strip() == "$composer":
                # الاحتفاظ بالقيمة الأصلية
                if original_composer:
                    audio.add(mutagen.id3.TCOM(encoding=3, text=original_composer))
            else:
                composer_text = current_template["composer"]
                composer_text = remove_links(composer_text)
                composer_text = apply_replacements(composer_text, "composer")
                composer_text = apply_footer(composer_text, "composer")
                audio.add(mutagen.id3.TCOM(encoding=3, text=composer_text))

        # --- كلمات الأغنية ---
        if "lyrics" in current_template:
            if current_template["lyrics"].strip() == "" or current_template["lyrics"].strip() == "$lyrics":
                # الاحتفاظ بالقيمة الأصلية
                if original_lyrics:
                    audio.add(mutagen.id3.USLT(encoding=3, lang='ara', desc='', text=original_lyrics))
            else:
                lyrics_text = current_template["lyrics"]
                lyrics_text = remove_links(lyrics_text)
                lyrics_text = apply_replacements(lyrics_text, "lyrics")
                lyrics_text = apply_footer(lyrics_text, "lyrics")
                audio.add(mutagen.id3.USLT(encoding=3, lang='ara', desc='', text=lyrics_text))

        # --- الوصف ---
        if "description" in current_template:
            if current_template["description"].strip() == "" or current_template["description"].strip() == "$description":
                # الاحتفاظ بالقيمة الأصلية
                if original_description:
                    audio.add(mutagen.id3.TIT3(encoding=3, text=original_description))
            else:
                description_text = current_template["description"]
                description_text = remove_links(description_text)
                description_text = apply_replacements(description_text, "description")
                description_text = apply_footer(description_text, "description")
                audio.add(mutagen.id3.TIT3(encoding=3, text=description_text))

        # إضافة الوسوم إلى الملف
        audio.add(mutagen.id3.TPE1(encoding=3, text=artist_text))
        audio.add(mutagen.id3.TPE2(encoding=3, text=album_artist_text))
        audio.add(mutagen.id3.TALB(encoding=3, text=album_text))
        audio.add(mutagen.id3.TCON(encoding=3, text=genre_text))
        audio.add(mutagen.id3.TYER(encoding=3, text=year_text))
        audio.add(mutagen.id3.TPUB(encoding=3, text=publisher_text))
        audio.add(mutagen.id3.TCOP(encoding=3, text=copyright_text))

        # إضافة صورة الألبوم إذا كانت متاحة وتم تفعيل الميزة
        if config["album_cover_enabled"] and album_cover_path:
            try:
                with open(album_cover_path, 'rb') as cover_file:
                    cover_data = cover_file.read()
                    # حذف أي صور موجودة أولاً
                    for key in list(audio.keys()):
                        if key.startswith('APIC'):
                            audio.delall(key)
                    # إضافة الصورة الجديدة
                    audio.add(mutagen.id3.APIC(
                        encoding=3,
                        mime='image/jpeg',
                        type=3,  # نوع 3 هو "Cover (front)"
                        desc='Cover',
                        data=cover_data
                    ))
                    logger.info("تم إضافة صورة الألبوم بنجاح")
            except Exception as cover_error:
                logger.error(f"خطأ في إضافة صورة الألبوم: {cover_error}")

        # حفظ التغييرات
        audio.save(file_path)
        logger.info(f"تم تعديل وسوم ID3 بنجاح للملف {file_path}")
        return True
    except Exception as e:
        logger.error(f"خطأ في معالجة وسوم الملف الصوتي: {e}")
        return False

# وظائف إنشاء لوحات المفاتيح

def create_control_panel_keyboard():
    """إنشاء لوحة مفاتيح تفاعلية للوحة التحكم الرئيسية"""
    markup = types.InlineKeyboardMarkup(row_width=2)

    # أزرار إدارة القنوات
    btn_set_source = types.InlineKeyboardButton("تعيين قناة المصدر 📥", callback_data="set_source")
    btn_set_target = types.InlineKeyboardButton("تعيين قناة الهدف 📤", callback_data="set_target")
    btn_view_channels = types.InlineKeyboardButton("عرض القنوات الحالية 📋", callback_data="view_channels")

    # أزرار إدارة القوالب
    btn_templates = types.InlineKeyboardButton("إدارة القوالب 🎛", callback_data="manage_templates")

    # أزرار إدارة الاستبدالات
    btn_replacements = types.InlineKeyboardButton("إدارة الاستبدالات 🔄", callback_data="manage_replacements")

    # أزرار إدارة التذييل
    btn_footers = types.InlineKeyboardButton("إدارة التذييل 📝", callback_data="manage_footers")

    # أزرار إدارة حذف الروابط
    btn_links = types.InlineKeyboardButton("إدارة حذف الروابط 🔗", callback_data="manage_links")

    # أزرار إدارة صورة الألبوم
    btn_album_cover = types.InlineKeyboardButton("إدارة صورة الألبوم 🖼️", callback_data="manage_album_cover")

    # زر تفعيل/تعطيل البوت بالكامل
    toggle_text = "تعطيل البوت ❌" if config["bot_enabled"] else "تفعيل البوت ✅"
    btn_toggle_bot = types.InlineKeyboardButton(toggle_text, callback_data="toggle_bot")

    markup.add(btn_set_source, btn_set_target)
    markup.add(btn_view_channels)
    markup.add(btn_templates, btn_replacements)
    markup.add(btn_footers, btn_links)
    markup.add(btn_album_cover)
    markup.add(btn_toggle_bot)

    return markup


def create_templates_keyboard():
    """إنشاء لوحة مفاتيح تفاعلية لإدارة القوالب"""
    markup = types.InlineKeyboardMarkup(row_width=2)

    btn_list_templates = types.InlineKeyboardButton("قائمة القوالب 📋", callback_data="list_templates")
    btn_current_template = types.InlineKeyboardButton("القالب الحالي 📌", callback_data="current_template")
    btn_switch_template = types.InlineKeyboardButton("تبديل القالب 🔄", callback_data="switch_template")
    btn_add_template = types.InlineKeyboardButton("إضافة قالب جديد ➕", callback_data="add_template")
    btn_edit_template = types.InlineKeyboardButton("تعديل قالب ✏️", callback_data="edit_template")
    btn_delete_template = types.InlineKeyboardButton("حذف قالب 🗑️", callback_data="delete_template")
    btn_back = types.InlineKeyboardButton("العودة للوحة التحكم ↩️", callback_data="back_to_main")

    markup.add(btn_list_templates, btn_current_template)
    markup.add(btn_switch_template)
    markup.add(btn_add_template, btn_edit_template)
    markup.add(btn_delete_template)
    markup.add(btn_back)

    return markup


def create_replacements_keyboard():
    """إنشاء لوحة مفاتيح تفاعلية لإدارة الاستبدالات"""
    markup = types.InlineKeyboardMarkup(row_width=2)

    btn_add_replacement = types.InlineKeyboardButton("إضافة استبدال ➕", callback_data="add_replacement")
    btn_list_replacements = types.InlineKeyboardButton("قائمة الاستبدالات 📋", callback_data="list_replacements")
    btn_delete_replacement = types.InlineKeyboardButton("حذف استبدال ➖", callback_data="delete_replacement")

    # زر تفعيل/تعطيل ميزة الاستبدال
    toggle_text = "تعطيل الاستبدال ❌" if config["replacement_enabled"] else "تفعيل الاستبدال ✅"
    btn_toggle = types.InlineKeyboardButton(toggle_text, callback_data="toggle_replacement")

    btn_back = types.InlineKeyboardButton("العودة للوحة التحكم ↩️", callback_data="back_to_main")

    markup.add(btn_add_replacement, btn_list_replacements)
    markup.add(btn_delete_replacement, btn_toggle)
    markup.add(btn_back)

    return markup


def create_footers_keyboard():
    """إنشاء لوحة مفاتيح تفاعلية لإدارة التذييلات"""
    markup = types.InlineKeyboardMarkup(row_width=2)

    btn_add_footer = types.InlineKeyboardButton("إضافة تذييل ➕", callback_data="add_footer")
    btn_list_footers = types.InlineKeyboardButton("التذييلات الحالية 📋", callback_data="list_footers")
    btn_delete_footer = types.InlineKeyboardButton("حذف تذييل ➖", callback_data="delete_footer")

    # زر تفعيل/تعطيل ميزة التذييل
    toggle_text = "تعطيل التذييل ❌" if config["footer_enabled"] else "تفعيل التذييل ✅"
    btn_toggle = types.InlineKeyboardButton(toggle_text, callback_data="toggle_footer")

    btn_back = types.InlineKeyboardButton("العودة للوحة التحكم ↩️", callback_data="back_to_main")

    markup.add(btn_add_footer, btn_list_footers)
    markup.add(btn_delete_footer, btn_toggle)
    markup.add(btn_back)

    return markup


def create_links_keyboard():
    """إنشاء لوحة مفاتيح تفاعلية لإدارة حذف الروابط"""
    markup = types.InlineKeyboardMarkup(row_width=2)

    # زر تفعيل/تعطيل ميزة حذف الروابط
    toggle_text = "تعطيل حذف الروابط ❌" if config["remove_links_enabled"] else "تفعيل حذف الروابط ✅"
    btn_toggle = types.InlineKeyboardButton(toggle_text, callback_data="toggle_links")

    btn_back = types.InlineKeyboardButton("العودة للوحة التحكم ↩️", callback_data="back_to_main")

    markup.add(btn_toggle)
    markup.add(btn_back)

    return markup


def create_album_cover_keyboard():
    """إنشاء لوحة مفاتيح تفاعلية لإدارة صورة الألبوم"""
    markup = types.InlineKeyboardMarkup(row_width=2)

    btn_set_cover = types.InlineKeyboardButton("تعيين صورة 🖼️", callback_data="set_album_cover")
    btn_view_cover = types.InlineKeyboardButton("عرض الصورة الحالية 👁️", callback_data="view_album_cover")
    btn_delete_cover = types.InlineKeyboardButton("حذف الصورة ➖", callback_data="delete_album_cover")

    # زر تفعيل/تعطيل ميزة صورة الألبوم
    toggle_text = "تعطيل صورة الألبوم ❌" if config["album_cover_enabled"] else "تفعيل صورة الألبوم ✅"
    btn_toggle = types.InlineKeyboardButton(toggle_text, callback_data="toggle_album_cover")

    btn_back = types.InlineKeyboardButton("العودة للوحة التحكم ↩️", callback_data="back_to_main")

    markup.add(btn_set_cover, btn_view_cover)
    markup.add(btn_delete_cover, btn_toggle)
    markup.add(btn_back)

    return markup

# معالجات الأحداث

@bot.message_handler(commands=['reset'])
def reset_command(message):
    """إعادة تعيين جميع البيانات إلى القيم الافتراضية"""
    user_id = message.from_user.id
    
    # التحقق مما إذا كان المستخدم هو المشرف
    if user_id != ADMIN_ID:
        bot.reply_to(message, "⛔ هذا الأمر متاح للمشرفين فقط.")
        return
        
    # إنشاء أزرار التأكيد
    markup = types.InlineKeyboardMarkup(row_width=2)
    btn_confirm = types.InlineKeyboardButton("تأكيد ✅", callback_data="confirm_reset")
    btn_cancel = types.InlineKeyboardButton("إلغاء ❌", callback_data="cancel_reset")
    markup.add(btn_confirm, btn_cancel)
    
    bot.reply_to(
        message,
        "⚠️ *تحذير*\n\n"
        "هل أنت متأكد من أنك تريد حذف جميع البيانات وإعادة تعيينها إلى القيم الافتراضية؟\n"
        "سيتم حذف:\n"
        "- قنوات المصدر والهدف\n"
        "- جميع القوالب (ما عدا القالب الافتراضي)\n"
        "- قواعد الاستبدال\n"
        "- التذييلات\n"
        "- صور الألبوم\n\n"
        "هذا الإجراء لا يمكن التراجع عنه!",
        reply_markup=markup,
        parse_mode="Markdown"
    )

@bot.message_handler(commands=['start'])
def send_welcome(message):
    """إرسال رسالة ترحيب عند إصدار الأمر /start"""
    user_id = message.from_user.id
    first_name = message.from_user.first_name
    logger.info(f"المستخدم {user_id} ({first_name}) بدأ البوت")

    bot.reply_to(message, f"مرحباً {first_name}! أنا بوت مخصص لمعالجة الملفات الصوتية وتعديل وسوم ID3.")


@bot.message_handler(commands=['help'])
def help_command(message):
    """إرسال رسالة مساعدة عند إصدار الأمر /help"""
    bot.reply_to(message, 
        "أنا بوت مخصص لمعالجة الملفات الصوتية وتعديل وسوم ID3.\n\n"
        "الأوامر المتاحة:\n"
        "/start - بدء المحادثة مع البوت\n"
        "/help - عرض رسالة المساعدة هذه\n"
        "/control - عرض لوحة التحكم الشفافة (للمشرفين فقط)\n"
    )


@bot.message_handler(commands=['control', 'settings'])
def control_panel(message):
    """عرض لوحة التحكم الشفافة (للمشرفين فقط)"""
    user_id = message.from_user.id

    # التحقق مما إذا كان المستخدم هو المشرف
    if user_id != ADMIN_ID:
        bot.reply_to(message, "⛔ هذا الأمر متاح للمشرفين فقط.")
        return

    markup = create_control_panel_keyboard()

    bot.send_message(
        message.chat.id,
        "🎛 *لوحة تحكم البوت*\n\n"
        "يمكنك إدارة الإعدادات من خلال الأزرار أدناه:",
        reply_markup=markup,
        parse_mode="Markdown"
    )


@bot.callback_query_handler(func=lambda call: True)
def handle_callback_query(call):
    """معالجة الضغط على الأزرار التفاعلية"""
    global current_template_key, album_cover_path

    user_id = call.from_user.id

    # التحقق مما إذا كان المستخدم هو المشرف
    if user_id != ADMIN_ID:
        bot.answer_callback_query(call.id, "⛔ هذا الإجراء متاح للمشرفين فقط.", show_alert=True)
        return

    # استخراج البيانات من زر الاتصال
    action = call.data

    # ==== إدارة القنوات ====
    if action == "set_source":
        # تعيين قناة المصدر
        bot.edit_message_text(
            "📥 *تعيين قناة المصدر*\n\n"
            "الرجاء إرسال معرف قناة المصدر (على سبيل المثال: @channelname أو -100xxxxxxxxx)\n"
            "أو يمكنك إعادة توجيه رسالة من القناة المطلوبة.",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode="Markdown"
        )
        # تعيين حالة المستخدم
        user_states[user_id] = STATE_AWAITING_SOURCE_CHANNEL

    elif action == "set_target":
        # تعيين قناة الهدف
        bot.edit_message_text(
            "📤 *تعيين قناة الهدف*\n\n"
            "الرجاء إرسال معرف قناة الهدف (على سبيل المثال: @channelname أو -100xxxxxxxxx)\n"
            "أو يمكنك إعادة توجيه رسالة من القناة المطلوبة.",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode="Markdown"
        )
        # تعيين حالة المستخدم
        user_states[user_id] = STATE_AWAITING_TARGET_CHANNEL

    elif action == "view_channels":
        # عرض القنوات الحالية
        bot.edit_message_text(
            "📋 *القنوات الحالية*\n\n"
            f"📥 *قناة المصدر*: {SOURCE_CHANNEL or 'غير محدد'}\n"
            f"📤 *قناة الهدف*: {TARGET_CHANNEL or 'غير محدد'}\n\n"
            "يمكنك تعديل هذه القنوات باستخدام الأزرار أدناه:",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=create_control_panel_keyboard(),
            parse_mode="Markdown"
        )

    # ==== إدارة القوالب ====
    elif action == "manage_templates":
        # عرض لوحة إدارة القوالب
        bot.edit_message_text(
            "🎛 *إدارة قوالب وسوم ID3*\n\n"
            "يمكنك إدارة قوالب وسوم ID3 المستخدمة لتعديل الملفات الصوتية من خلال الأزرار أدناه:",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=create_templates_keyboard(),
            parse_mode="Markdown"
        )

    elif action == "current_template":
        # عرض القالب الحالي
        template = templates[current_template_key]

        # بناء معلومات القالب مع كافة الوسوم
        template_info = (
            f"🎵 *الاسم*: {template['name']}\n"
            f"👤 *الفنان*: {template['artist']}\n"
            f"👥 *فنان الألبوم*: {template['album_artist']}\n"
            f"💿 *الألبوم*: {template['album']}\n"
            f"🏷️ *النوع*: {template['genre']}\n"
            f"📅 *السنة*: {template['year']}\n"
            f"🏢 *الناشر*: {template['publisher']}\n"
            f"©️ *حقوق النشر*: {template['copyright']}\n"
        )

        # إضافة الوسوم الإضافية إذا كانت موجودة
        if "comment" in template:
            template_info += f"💬 *التعليق*: {template['comment']}\n"

        if "website" in template:
            template_info += f"🔗 *رابط الموقع*: {template['website']}\n"

        if "composer" in template:
            template_info += f"🎼 *الملحن*: {template['composer']}\n"

        if "description" in template:
            template_info += f"📝 *الوصف*: {template['description']}\n"

        # إضافة كلمات الأغنية مع تنسيق خاص (مختصرة إذا كانت طويلة)
        if "lyrics" in template:
            lyrics = template["lyrics"]
            # إذا كانت كلمات الأغنية طويلة، نعرض جزء منها فقط
            if len(lyrics) > 50:
                lyrics_preview = lyrics[:50] + "..."
                template_info += f"📄 *كلمات الأغنية*: {lyrics_preview}\n"
            else:
                template_info += f"📄 *كلمات الأغنية*: {lyrics}\n"

        # إضافة سطر فارغ في النهاية
        template_info += "\n"

        # إنشاء لوحة مفاتيح للعودة
        markup = types.InlineKeyboardMarkup(row_width=1)
        btn_back = types.InlineKeyboardButton("العودة ↩️", callback_data="manage_templates")
        markup.add(btn_back)

        bot.edit_message_text(
            f"📌 *القالب الحالي*: {template['name']}\n\n"
            f"{template_info}"
            "يمكنك تغيير القالب الحالي من خلال زر 'تبديل القالب'",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=markup,
            parse_mode="Markdown"
        )

    elif action == "switch_template":
        # تبديل القالب الحالي
        markup = types.InlineKeyboardMarkup(row_width=1)

        # إضافة أزرار لجميع القوالب المتاحة
        for key, template in templates.items():
            if key != current_template_key:
                btn = types.InlineKeyboardButton(
                    f"{template['name']} ✅", 
                    callback_data=f"set_current_template:{key}"
                )
                markup.add(btn)

        btn_back = types.InlineKeyboardButton("العودة ↩️", callback_data="manage_templates")
        markup.add(btn_back)

        bot.edit_message_text(
            "🔄 *تبديل القالب الحالي*\n\n"
            f"القالب الحالي: *{templates[current_template_key]['name']}*\n\n"
            "الرجاء اختيار القالب الجديد:",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=markup,
            parse_mode="Markdown"
        )

    elif action.startswith("set_current_template:"):
        # تعيين قالب محدد كقالب حالي
        template_key = action.split(":", 1)[1]

        if template_key in templates:
            # تحديث القالب الحالي
            current_template_key = template_key

            bot.edit_message_text(
                f"✅ تم تعيين *{templates[current_template_key]['name']}* كالقالب الحالي بنجاح.\n\n"
                "يمكنك العودة إلى لوحة إدارة القوالب:",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=create_templates_keyboard(),
                parse_mode="Markdown"
            )

    elif action.startswith("new_template_field:"):
        # تعديل حقل في قالب جديد
        field_key = action.split(":", 1)[1]

        if user_id in temp_data and temp_data[user_id]["type"] == "template":
            # الحصول على الاسم العربي للحقل
            field_name = available_id3_tags.get(field_key, field_key)

            # حفظ الحقل الحالي
            temp_data[user_id]["current_field"] = field_key

            # الحصول على القيمة الحالية للحقل
            current_value = temp_data[user_id]["template"].get(field_key, "")

            bot.edit_message_text(
                f"✏️ *تعديل حقل {field_name} في القالب الجديد*\n\n"
                f"القيمة الحالية: {current_value}\n\n"
                "الرجاء إرسال القيمة الجديدة:",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                parse_mode="Markdown"
            )

            # تعيين حالة المستخدم
            user_states[user_id] = STATE_AWAITING_TEMPLATE_FIELD
        else:
            bot.answer_callback_query(call.id, "⚠️ حدث خطأ في العملية. الرجاء المحاولة مرة أخرى.", show_alert=True)

    elif action == "save_new_template":
        # حفظ قالب جديد
        if user_id in temp_data and temp_data[user_id]["type"] == "template" and "template" in temp_data[user_id]:
            # إنشاء معرف فريد للقالب الجديد
            template_key = temp_data[user_id]["template"]["name"]

            # تحويل المعرف إلى نص عربي مناسب وتجنب التكرار
            counter = 0
            original_key = template_key
            while template_key in templates:
                counter += 1
                template_key = f"{original_key}_{counter}"

            # إضافة القالب الجديد
            templates[template_key] = temp_data[user_id]["template"]

            # تنظيف البيانات المؤقتة
            del temp_data[user_id]

            bot.edit_message_text(
                f"✅ تم إضافة القالب الجديد *{templates[template_key]['name']}* بنجاح.\n\n"
                "يمكنك العودة إلى لوحة إدارة القوالب:",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=create_templates_keyboard(),
                parse_mode="Markdown"
            )
        else:
            bot.answer_callback_query(call.id, "⚠️ حدث خطأ في العملية. الرجاء المحاولة مرة أخرى.", show_alert=True)

    elif action == "cancel_new_template":
        # إلغاء إنشاء قالب جديد
        if user_id in temp_data:
            del temp_data[user_id]

        bot.edit_message_text(
            "❌ تم إلغاء إنشاء القالب الجديد.\n\n"
            "يمكنك العودة إلى لوحة إدارة القوالب:",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=create_templates_keyboard(),
            parse_mode="Markdown"
        )

    elif action.startswith("edit_template:"):
        # تحرير قالب محدد
        template_key = action.split(":", 1)[1]

        if template_key in templates:
            # إنشاء لوحة مفاتيح لحقول القالب لتعديلها
            markup = types.InlineKeyboardMarkup(row_width=2)

            # إضافة أزرار للحقول الأساسية
            btn_name = types.InlineKeyboardButton("الاسم ✏️", callback_data=f"edit_field:{template_key}:name")
            btn_artist = types.InlineKeyboardButton("الفنان ✏️", callback_data=f"edit_field:{template_key}:artist")
            btn_album_artist = types.InlineKeyboardButton("فنان الألبوم ✏️", callback_data=f"edit_field:{template_key}:album_artist")
            btn_album = types.InlineKeyboardButton("الألبوم ✏️", callback_data=f"edit_field:{template_key}:album")
            btn_genre = types.InlineKeyboardButton("النوع ✏️", callback_data=f"edit_field:{template_key}:genre")
            btn_year = types.InlineKeyboardButton("السنة ✏️", callback_data=f"edit_field:{template_key}:year")
            btn_publisher = types.InlineKeyboardButton("الناشر ✏️", callback_data=f"edit_field:{template_key}:publisher")
            btn_copyright = types.InlineKeyboardButton("حقوق النشر ✏️", callback_data=f"edit_field:{template_key}:copyright")

            # إضافة أزرار للحقول الإضافية
            btn_comment = types.InlineKeyboardButton("التعليق ✏️", callback_data=f"edit_field:{template_key}:comment")
            btn_website = types.InlineKeyboardButton("الموقع ✏️", callback_data=f"edit_field:{template_key}:website")
            btn_composer = types.InlineKeyboardButton("الملحن ✏️", callback_data=f"edit_field:{template_key}:composer")
            btn_lyrics = types.InlineKeyboardButton("كلمات الأغنية ✏️", callback_data=f"edit_field:{template_key}:lyrics")
            btn_description = types.InlineKeyboardButton("الوصف ✏️", callback_data=f"edit_field:{template_key}:description")

            btn_back = types.InlineKeyboardButton("العودة ↩️", callback_data="edit_template")

            # إضافة الأزرار إلى لوحة المفاتيح
            markup.add(btn_name)
            markup.add(btn_artist, btn_album_artist)
            markup.add(btn_album, btn_genre)
            markup.add(btn_year, btn_publisher)
            markup.add(btn_copyright)
            markup.add(btn_comment, btn_website)
            markup.add(btn_composer, btn_lyrics)
            markup.add(btn_description)
            markup.add(btn_back)

            template = templates[template_key]
            template_info = f"*تعديل قالب: {template['name']}*\n\n"
            template_info += "اختر الحقل الذي ترغب في تعديله:"

            bot.edit_message_text(
                template_info,
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=markup,
                parse_mode="Markdown"
            )
        else:
            bot.edit_message_text(
                "⚠️ *القالب غير موجود*\n\n"
                "تعذر العثور على القالب المحدد.\n\n"
                "يمكنك العودة إلى لوحة إدارة القوالب:",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=create_templates_keyboard(),
                parse_mode="Markdown"
            )

    elif action.startswith("edit_field:"):
        # تعديل حقل معين في قالب محدد
        _, template_key, field_key = action.split(":", 2)

        if template_key in templates:
            # حفظ البيانات المؤقتة
            temp_data[user_id] = {
                "type": "template_edit",
                "template_key": template_key,
                "field_key": field_key
            }

            # الحصول على الاسم العربي للحقل
            field_name = available_id3_tags.get(field_key, field_key)

            # الحصول على القيمة الحالية للحقل
            current_value = templates[template_key].get(field_key, "")

            bot.edit_message_text(
                f"✏️ *تعديل حقل {field_name}*\n\n"
                f"القيمة الحالية: {current_value}\n\n"
                "الرجاء إرسال القيمة الجديدة:",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                parse_mode="Markdown"
            )

            # تعيين حالة المستخدم
            user_states[user_id] = STATE_AWAITING_TEMPLATE_FIELD
        else:
            bot.edit_message_text(
                "⚠️ *القالب غير موجود*\n\n"
                "تعذر العثور على القالب المحدد.\n\n"
                "يمكنك العودة إلى لوحة إدارة القوالب:",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=create_templates_keyboard(),
                parse_mode="Markdown"
            )

    elif action == "list_templates":
        # عرض قائمة بجميع القوالب المتوفرة
        if not templates:
            bot.edit_message_text(
                "⚠️ *لا توجد قوالب متاحة*\n\n"
                "يمكنك العودة إلى لوحة إدارة القوالب:",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=create_templates_keyboard(),
                parse_mode="Markdown"
            )
            return

        # إنشاء قائمة بالقوالب
        templates_list = "📋 *قائمة القوالب المتاحة*:\n\n"

        for key, template in templates.items():
            current_mark = "✅ " if key == current_template_key else ""
            templates_list += f"{current_mark}*{template['name']}*\n"
            templates_list += f"👤 الفنان: {template['artist']}\n"
            templates_list += f"💿 الألبوم: {template['album']}\n"
            templates_list += f"🏷️ النوع: {template['genre']}\n"

            # إضافة الوسوم الجديدة إذا كانت موجودة
            if "comment" in template:
                templates_list += f"💬 التعليق: {template['comment']}\n"
            if "website" in template:
                templates_list += f"🔗 الموقع: {template['website']}\n"
            if "composer" in template:
                templates_list += f"🎼 الملحن: {template['composer']}\n"
            if "lyrics" in template:
                lyrics_preview = template['lyrics'][:30] + "..." if len(template['lyrics']) > 30 else template['lyrics']
                templates_list += f"📝 كلمات: {lyrics_preview}\n"

            templates_list += "\n"

        # إنشاء لوحة مفاتيح للعودة
        markup = types.InlineKeyboardMarkup(row_width=1)
        btn_back = types.InlineKeyboardButton("العودة ↩️", callback_data="manage_templates")
        markup.add(btn_back)

        bot.edit_message_text(
            templates_list,
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=markup,
            parse_mode="Markdown"
        )

    elif action == "add_template":
        # بدء عملية إضافة قالب جديد
        temp_data[user_id] = {
            "type": "template",
            "template": {},
            "current_field": None
        }

        bot.edit_message_text(
            "➕ *إضافة قالب جديد*\n\n"
            "الرجاء إرسال اسم القالب الجديد:",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode="Markdown"
        )

        # تعيين حالة المستخدم
        user_states[user_id] = STATE_AWAITING_TEMPLATE_NAME

    elif action == "delete_template":
        # عرض قائمة القوالب لاختيار قالب لحذفه
        if len(templates) <= 1:
            bot.edit_message_text(
                "⚠️ *لا يمكن حذف جميع القوالب*\n\n"
                "يجب أن يبقى قالب واحد على الأقل متاحاً.",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=create_templates_keyboard(),
                parse_mode="Markdown"
            )
            return

        # إنشاء لوحة مفاتيح للقوالب المتاحة للحذف
        markup = types.InlineKeyboardMarkup(row_width=1)

        for key, template in templates.items():
            # لا نسمح بحذف القالب الحالي المستخدم
            if key != current_template_key:
                btn = types.InlineKeyboardButton(
                    f"حذف: {template['name']} 🗑️", 
                    callback_data=f"delete_template:{key}"
                )
                markup.add(btn)

        btn_back = types.InlineKeyboardButton("العودة ↩️", callback_data="manage_templates")
        markup.add(btn_back)

        bot.edit_message_text(
            "🗑️ *حذف قالب*\n\n"
            "الرجاء اختيار القالب الذي ترغب في حذفه:",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=markup,
            parse_mode="Markdown"
        )

    elif action.startswith("delete_template:"):
        # حذف قالب محدد
        template_key = action.split(":", 1)[1]

        if template_key in templates:
            template_name = templates[template_key]["name"]
            del templates[template_key]

            bot.edit_message_text(
                f"✅ تم حذف القالب *{template_name}* بنجاح.\n\n"
                "يمكنك العودة إلى لوحة إدارة القوالب:",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=create_templates_keyboard(),
                parse_mode="Markdown"
            )
        else:
            bot.edit_message_text(
                "⚠️ *القالب غير موجود*\n\n"
                "تعذر العثور على القالب المحدد.\n\n"
                "يمكنك العودة إلى لوحة إدارة القوالب:",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=create_templates_keyboard(),
                parse_mode="Markdown"
            )

    elif action == "edit_template":
        # عرض قائمة القوالب لاختيار قالب لتعديله
        if not templates:
            bot.edit_message_text(
                "⚠️ *لا توجد قوالب متاحة للتعديل*\n\n"
                "يمكنك إضافة قالب جديد باستخدام زر 'إضافة قالب جديد'.",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=create_templates_keyboard(),
                parse_mode="Markdown"
            )
            return

        # إنشاء لوحة مفاتيح للقوالب المتاحة للتعديل
        markup = types.InlineKeyboardMarkup(row_width=1)

        for key, template in templates.items():
            btn = types.InlineKeyboardButton(
                f"تعديل: {template['name']} ✏️", 
                callback_data=f"edit_template:{key}"
            )
            markup.add(btn)

        btn_back = types.InlineKeyboardButton("العودة ↩️", callback_data="manage_templates")
        markup.add(btn_back)

        bot.edit_message_text(
            "✏️ *تعديل قالب*\n\n"
            "الرجاء اختيار القالب الذي ترغب في تعديله:",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=markup,
            parse_mode="Markdown"
        )

    # ==== إدارة الاستبدالات ====
    elif action == "manage_replacements":
        # عرض لوحة إدارة الاستبدالات
        status = "✅ مفعّل" if config["replacement_enabled"] else "❌ معطّل"

        bot.edit_message_text(
            f"🔄 *إدارة الاستبدالات*\n\n"
            f"حالة ميزة الاستبدال: {status}\n\n"
            "يمكنك إدارة قواعد الاستبدال من خلال الأزرار أدناه:",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=create_replacements_keyboard(),
            parse_mode="Markdown"
        )

    elif action == "add_replacement":
        # بدء عملية إضافة قاعدة استبدال جديدة
        temp_data[user_id] = {"type": "replacement"}

        bot.edit_message_text(
            "➕ *إضافة قاعدة استبدال جديدة*\n\n"
            "الرجاء إرسال اسم قاعدة الاستبدال الجديدة:",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode="Markdown"
        )

        # تعيين حالة المستخدم
        user_states[user_id] = STATE_AWAITING_REPLACEMENT_NAME

    elif action == "list_replacements":
        # عرض قائمة بجميع قواعد الاستبدال المتوفرة
        if not replacements:
            bot.edit_message_text(
                "⚠️ *لا توجد قواعد استبدال متاحة*\n\n"
                "يمكنك إضافة قاعدة جديدة باستخدام زر 'إضافة استبدال'.",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=create_replacements_keyboard(),
                parse_mode="Markdown"
            )
            return

        # إنشاء قائمة بقواعد الاستبدال
        replacements_list = "📋 *قائمة قواعد الاستبدال المتاحة*:\n\n"

        for key, rule in replacements.items():
            replacements_list += f"*{rule['name']}*\n"
            replacements_list += f"النص الأصلي: {rule['original']}\n"
            replacements_list += f"النص البديل: {rule['replacement']}\n"

            # تحويل أسماء الوسوم إلى أسماء مفهومة بالعربية
            tag_names = []
            for tag in rule["tags"]:
                arabic_name = available_id3_tags.get(tag, tag)
                tag_names.append(arabic_name)

            replacements_list += f"الوسوم المطبقة: {', '.join(tag_names)}\n\n"

        # إنشاء لوحة مفاتيح للعودة
        markup = types.InlineKeyboardMarkup(row_width=1)
        btn_back = types.InlineKeyboardButton("العودة ↩️", callback_data="manage_replacements")
        markup.add(btn_back)

        bot.edit_message_text(
            replacements_list,
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=markup,
            parse_mode="Markdown"
        )

    elif action == "delete_replacement":
        # عرض قائمة قواعد الاستبدال لاختيار قاعدة لحذفها
        if not replacements:
            bot.edit_message_text(
                "⚠️ *لا توجد قواعد استبدال متاحة للحذف*\n\n"
                "يمكنك إضافة قاعدة جديدة باستخدام زر 'إضافة استبدال'.",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=create_replacements_keyboard(),
                parse_mode="Markdown"
            )
            return

        # إنشاء لوحة مفاتيح لقواعد الاستبدال المتاحة للحذف
        markup = types.InlineKeyboardMarkup(row_width=1)

        for key, rule in replacements.items():
            btn = types.InlineKeyboardButton(
                f"حذف: {rule['name']} 🗑️", 
                callback_data=f"delete_rule:{key}"
            )
            markup.add(btn)

        btn_back = types.InlineKeyboardButton("العودة ↩️", callback_data="manage_replacements")
        markup.add(btn_back)

        bot.edit_message_text(
            "🗑️ *حذف قاعدة استبدال*\n\n"
            "الرجاء اختيار القاعدة التي ترغب في حذفها:",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=markup,
            parse_mode="Markdown"
        )

    elif action.startswith("delete_rule:"):
        # حذف قاعدة استبدال محددة
        rule_key = action.split(":", 1)[1]

        if rule_key in replacements:
            rule_name = replacements[rule_key]["name"]
            del replacements[rule_key]

            bot.edit_message_text(
                f"✅ تم حذف قاعدة الاستبدال *{rule_name}* بنجاح.\n\n"
                "يمكنك العودة إلى لوحة إدارة الاستبدالات:",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=create_replacements_keyboard(),
                parse_mode="Markdown"
            )
        else:
            bot.edit_message_text(
                "⚠️ *القاعدة غير موجودة*\n\n"
                "تعذر العثور على قاعدة الاستبدال المحددة.\n\n"
                "يمكنك العودة إلى لوحة إدارة الاستبدالات:",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=create_replacements_keyboard(),
                parse_mode="Markdown"
            )

    elif action == "toggle_replacement":
        # تفعيل أو تعطيل ميزة الاستبدال
        config["replacement_enabled"] = not config["replacement_enabled"]

        status = "✅ مفعّل" if config["replacement_enabled"] else "❌ معطّل"

        bot.edit_message_text(
            f"🔄 *إدارة الاستبدالات*\n\n"
            f"تم {('تفعيل' if config['replacement_enabled'] else 'تعطيل')} ميزة الاستبدال بنجاح.\n"
            f"الحالة الحالية: {status}\n\n"
            "يمكنك إدارة قواعد الاستبدال من خلال الأزرار أدناه:",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=create_replacements_keyboard(),
            parse_mode="Markdown"
        )

    # ==== إدارة التذييل ====
    elif action == "manage_footers":
        # عرض لوحة إدارة التذييلات
        status = "✅ مفعّل" if config["footer_enabled"] else "❌ معطّل"

        bot.edit_message_text(
            f"📝 *إدارة التذييل*\n\n"
            f"حالة ميزة التذييل: {status}\n\n"
            "يمكنك إدارة التذييلات من خلال الأزرار أدناه:",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=create_footers_keyboard(),
            parse_mode="Markdown"
        )

    elif action == "add_footer":
        # بدء عملية إضافة تذييل جديد
        temp_data[user_id] = {"type": "footer"}

        bot.edit_message_text(
            "➕ *إضافة تذييل جديد*\n\n"
            "الرجاء إرسال اسم التذييل الجديد:",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode="Markdown"
        )

        # تعيين حالة المستخدم
        user_states[user_id] = STATE_AWAITING_FOOTER_NAME

    elif action == "list_footers":
        # عرض قائمة بجميع التذييلات المتوفرة
        if not footers:
            bot.edit_message_text(
                "⚠️ *لا توجد تذييلات متاحة*\n\n"
                "يمكنك إضافة تذييل جديد باستخدام زر 'إضافة تذييل'.",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=create_footers_keyboard(),
                parse_mode="Markdown"
            )
            return

        # إنشاء قائمة بالتذييلات
        footers_list = "📋 *قائمة التذييلات المتاحة*:\n\n"

        for key, footer in footers.items():
            footers_list += f"*{footer['name']}*\n"
            footers_list += f"النص: {footer['text']}\n"

            # تحويل أسماء الوسوم إلى أسماء مفهومة بالعربية
            tag_names = []
            for tag in footer["tags"]:
                arabic_name = available_id3_tags.get(tag, tag)
                tag_names.append(arabic_name)

            footers_list += f"الوسوم المطبقة: {', '.join(tag_names)}\n\n"

        # إنشاء لوحة مفاتيح للعودة
        markup = types.InlineKeyboardMarkup(row_width=1)
        btn_back = types.InlineKeyboardButton("العودة ↩️", callback_data="manage_footers")
        markup.add(btn_back)

        bot.edit_message_text(
            footers_list,
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=markup,
            parse_mode="Markdown"
        )

    elif action == "delete_footer":
        # عرض قائمة التذييلات لاختيار تذييل لحذفه
        if not footers:
            bot.edit_message_text(
                "⚠️ *لا توجد تذييلات متاحة للحذف*\n\n"
                "يمكنك إضافة تذييل جديد باستخدام زر 'إضافة تذييل'.",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=create_footers_keyboard(),
                parse_mode="Markdown"
            )
            return

        # إنشاء لوحة مفاتيح للتذييلات المتاحة للحذف
        markup = types.InlineKeyboardMarkup(row_width=1)

        for key, footer in footers.items():
            btn = types.InlineKeyboardButton(
                f"حذف: {footer['name']} 🗑️", 
                callback_data=f"delete_footer:{key}"
            )
            markup.add(btn)

        btn_back = types.InlineKeyboardButton("العودة ↩️", callback_data="manage_footers")
        markup.add(btn_back)

        bot.edit_message_text(
            "🗑️ *حذف تذييل*\n\n"
            "الرجاء اختيار التذييل الذي ترغب في حذفه:",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=markup,
            parse_mode="Markdown"
        )

    elif action.startswith("delete_footer:"):
        # حذف تذييل محدد
        footer_key = action.split(":", 1)[1]

        if footer_key in footers:
            footer_name = footers[footer_key]["name"]
            del footers[footer_key]

            bot.edit_message_text(
                f"✅ تم حذف التذييل *{footer_name}* بنجاح.\n\n"
                "يمكنك العودة إلى لوحة إدارة التذييلات:",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=create_footers_keyboard(),
                parse_mode="Markdown"
            )
        else:
            bot.edit_message_text(
                "⚠️ *التذييل غير موجود*\n\n"
                "تعذر العثور على التذييل المحدد.\n\n"
                "يمكنك العودة إلى لوحة إدارة التذييلات:",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=create_footers_keyboard(),
                parse_mode="Markdown"
            )

    elif action == "toggle_footer":
        # تفعيل أو تعطيل ميزة التذييل
        config["footer_enabled"] = not config["footer_enabled"]

        status = "✅ مفعّل" if config["footer_enabled"] else "❌ معطّل"

        bot.edit_message_text(
            f"📝 *إدارة التذييل*\n\n"
            f"تم {('تفعيل' if config['footer_enabled'] else 'تعطيل')} ميزة التذييل بنجاح.\n"
            f"الحالة الحالية: {status}\n\n"
            "يمكنك إدارة التذييلات من خلال الأزرار أدناه:",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=create_footers_keyboard(),
            parse_mode="Markdown"
        )

    # ==== إدارة حذف الروابط ====
    elif action == "manage_links":
        # عرض لوحة إدارة حذف الروابط
        status = "✅ مفعّل" if config["remove_links_enabled"] else "❌ معطّل"

        bot.edit_message_text(
            f"🔗 *إدارة حذف الروابط*\n\n"
            f"حالة ميزة حذف الروابط: {status}\n\n"
            "يمكنك التحكم في ميزة حذف الروابط من خلال الأزرار أدناه:",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=create_links_keyboard(),
            parse_mode="Markdown"
        )

    elif action == "toggle_links":
        # تفعيل أو تعطيل ميزة حذف الروابط
        config["remove_links_enabled"] = not config["remove_links_enabled"]

        status = "✅ مفعّل" if config["remove_links_enabled"] else "❌ معطّل"

        bot.edit_message_text(
            f"🔗 *إدارة حذف الروابط*\n\n"
            f"تم {('تفعيل' if config['remove_links_enabled'] else 'تعطيل')} ميزة حذف الروابط بنجاح.\n"
            f"الحالة الحالية: {status}\n\n"
            "يمكنك التحكم في ميزة حذف الروابط من خلال الأزرار أدناه:",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=create_links_keyboard(),
            parse_mode="Markdown"
        )

    # ==== إدارة صورة الألبوم ====
    elif action == "manage_album_cover":
        # عرض لوحة إدارة صورة الألبوم
        status = "✅ مفعّل" if config["album_cover_enabled"] else "❌ معطّل"

        bot.edit_message_text(
            f"🖼️ *إدارة صورة الألبوم*\n\n"
            f"حالة ميزة صورة الألبوم: {status}\n"
            f"صورة الألبوم: {('✅ تم تعيينها' if album_cover_path else '❌ غير معينة')}\n\n"
            "يمكنك إدارة صورة الألبوم من خلال الأزرار أدناه:",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=create_album_cover_keyboard(),
            parse_mode="Markdown"
        )

    elif action == "set_album_cover":
        # تعيين صورة الألبوم
        bot.edit_message_text(
            "🖼️ *تعيين صورة الألبوم*\n\n"
            "الرجاء إرسال صورة لاستخدامها كصورة ألبوم.\n"
            "يجب أن تكون الصورة بتنسيق JPG أو PNG.",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode="Markdown"
        )

        # تعيين حالة المستخدم
        user_states[user_id] = STATE_AWAITING_ALBUM_COVER

    elif action == "view_album_cover":
        # عرض صورة الألبوم الحالية
        if not album_cover_path:
            bot.edit_message_text(
                "⚠️ *لا توجد صورة ألبوم معينة*\n\n"
                "يمكنك تعيين صورة جديدة باستخدام زر 'تعيين صورة'.",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=create_album_cover_keyboard(),
                parse_mode="Markdown"
            )
            return

        try:
            # إرسال الصورة الحالية
            with open(album_cover_path, 'rb') as photo:
                bot.send_photo(
                    call.message.chat.id,
                    photo,
                    caption="🖼️ صورة الألبوم الحالية"
                )

            # إعادة عرض لوحة التحكم
            markup = types.InlineKeyboardMarkup(row_width=1)
            btn_back = types.InlineKeyboardButton("العودة ↩️", callback_data="manage_album_cover")
            markup.add(btn_back)

            bot.send_message(
                call.message.chat.id,
                "يمكنك العودة إلى لوحة إدارة صورة الألبوم:",
                reply_markup=markup
            )
        except Exception as e:
            bot.edit_message_text(
                f"⚠️ *خطأ في عرض الصورة*\n\n"
                f"تعذر عرض صورة الألبوم: {str(e)}\n\n"
                "يمكنك تعيين صورة جديدة باستخدام زر 'تعيين صورة'.",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=create_album_cover_keyboard(),
                parse_mode="Markdown"
            )

    elif action == "delete_album_cover":
        # حذف صورة الألبوم الحالية
        if not album_cover_path:
            bot.edit_message_text(
                "⚠️ *لا توجد صورة ألبوم لحذفها*\n\n"
                "يمكنك تعيين صورة جديدة باستخدام زر 'تعيين صورة'.",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=create_album_cover_keyboard(),
                parse_mode="Markdown"
            )
            return

        try:
            # حذف الملف إذا كان موجودًا
            if os.path.exists(album_cover_path):
                os.remove(album_cover_path)

            album_cover_path = None

            bot.edit_message_text(
                "✅ *تم حذف صورة الألبوم بنجاح*\n\n"
                "يمكنك تعيين صورة جديدة باستخدام زر 'تعيين صورة'.",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=create_album_cover_keyboard(),
                parse_mode="Markdown"
            )
        except Exception as e:
            bot.edit_message_text(
                f"⚠️ *خطأ في حذف الصورة*\n\n"
                f"تعذر حذف صورة الألبوم: {str(e)}\n\n"
                "يمكنك المحاولة مرة أخرى لاحقًا.",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=create_album_cover_keyboard(),
                parse_mode="Markdown"
            )

    elif action == "toggle_album_cover":
        # تفعيل أو تعطيل ميزة صورة الألبوم
        config["album_cover_enabled"] = not config["album_cover_enabled"]

        status = "✅ مفعّل" if config["album_cover_enabled"] else "❌ معطّل"

        bot.edit_message_text(
            f"🖼️ *إدارة صورة الألبوم*\n\n"
            f"تم {('تفعيل' if config['album_cover_enabled'] else 'تعطيل')} ميزة صورة الألبوم بنجاح.\n"
            f"الحالة الحالية: {status}\n\n"
            "يمكنك إدارة صورة الألبوم من خلال الأزرار أدناه:",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=create_album_cover_keyboard(),
            parse_mode="Markdown"
        )

    # ==== تأكيد حذف البيانات ====
    elif action == "confirm_reset":
        # حذف جميع البيانات
        reset_data()
        
        bot.edit_message_text(
            "✅ تم حذف جميع البيانات وإعادة تعيينها إلى القيم الافتراضية بنجاح.\n"
            "يمكنك الآن بدء إعداد البوت من جديد باستخدام /control",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id
        )
        
    elif action == "cancel_reset":
        # إلغاء عملية الحذف
        bot.edit_message_text(
            "❌ تم إلغاء عملية حذف البيانات.\n"
            "لم يتم إجراء أي تغييرات على البيانات المخزنة.",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id
        )

    # ==== تفعيل/تعطيل البوت ====
    elif action == "toggle_bot":
        # تفعيل أو تعطيل البوت بالكامل
        config["bot_enabled"] = not config["bot_enabled"]

        status = "✅ مفعّل" if config["bot_enabled"] else "❌ معطّل"

        bot.edit_message_text(
            f"🤖 *حالة البوت*\n\n"
            f"تم {('تفعيل' if config['bot_enabled'] else 'تعطيل')} البوت بنجاح.\n"
            f"الحالة الحالية: {status}\n\n"
            f"{'سيقوم البوت الآن بتنفيذ جميع عمليات التعديل على الملفات الصوتية.' if config['bot_enabled'] else 'لن يقوم البوت بتنفيذ أي عمليات تعديل على الملفات الصوتية. سيتم نقل الملفات فقط.'}\n\n"
            "يمكنك العودة إلى لوحة التحكم الرئيسية:",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=create_control_panel_keyboard(),
            parse_mode="Markdown"
        )

    elif action == "back_to_main":
        # العودة إلى لوحة التحكم الرئيسية
        bot.edit_message_text(
            "🎛 *لوحة تحكم البوت*\n\n"
            "يمكنك إدارة الإعدادات من خلال الأزرار أدناه:",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=create_control_panel_keyboard(),
            parse_mode="Markdown"
        )

    # ==== تبديل حالة اختيار الوسوم للاستبدال والتذييل ====
    elif action.startswith("toggle_tag:"):
        # تبديل حالة اختيار الوسم للاستبدال
        tag_key = action.split(":", 1)[1]

        # التحقق من وجود بيانات مؤقتة
        if user_id in temp_data and "tags" in temp_data[user_id]:
            # تبديل حالة الاختيار
            if tag_key in temp_data[user_id]["tags"]:
                temp_data[user_id]["tags"].remove(tag_key)
            else:
                temp_data[user_id]["tags"].append(tag_key)

            # تحديث لوحة المفاتيح
            markup = types.InlineKeyboardMarkup(row_width=2)

            # إضافة أزرار لكل وسم
            for tag_key_item, tag_name in available_id3_tags.items():
                # تحديد حالة الزر (محدد أو غير محدد)
                is_selected = tag_key_item in temp_data[user_id]["tags"]
                status = "✅" if is_selected else "⬜"

                btn = types.InlineKeyboardButton(
                    f"{tag_name} {status}",
                    callback_data=f"toggle_tag:{tag_key_item}"
                )
                markup.add(btn)

            # إضافة أزرار الحفظ والإلغاء
            btn_save = types.InlineKeyboardButton("حفظ قاعدة الاستبدال ✅", callback_data="save_replacement")
            btn_cancel = types.InlineKeyboardButton("إلغاء ❌", callback_data="cancel_replacement")
            markup.add(btn_save, btn_cancel)

            # تحديث الرسالة
            bot.edit_message_reply_markup(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=markup
            )

    elif action.startswith("toggle_footer_tag:"):
        # تبديل حالة اختيار الوسم للتذييل
        tag_key = action.split(":", 1)[1]

        # التحقق من وجود بيانات مؤقتة
        if user_id in temp_data and "tags" in temp_data[user_id]:
            # تبديل حالة الاختيار
            if tag_key in temp_data[user_id]["tags"]:
                temp_data[user_id]["tags"].remove(tag_key)
            else:
                temp_data[user_id]["tags"].append(tag_key)

            # تحديث لوحة المفاتيح
            markup = types.InlineKeyboardMarkup(row_width=2)

            # إضافة أزرار لكل وسم
            for tag_key_item, tag_name in available_id3_tags.items():
                # تحديد حالة الزر (محدد أو غير محدد)
                is_selected = tag_key_item in temp_data[user_id]["tags"]
                status = "✅" if is_selected else "⬜"

                btn = types.InlineKeyboardButton(
                    f"{tag_name} {status}",
                    callback_data=f"toggle_footer_tag:{tag_key_item}"
                )
                markup.add(btn)

            # إضافة أزرار الحفظ والإلغاء
            btn_save = types.InlineKeyboardButton("حفظ التذييل ✅", callback_data="save_footer")
            btn_cancel = types.InlineKeyboardButton("إلغاء ❌", callback_data="cancel_footer")
            markup.add(btn_save, btn_cancel)

            # تحديث الرسالة
            bot.edit_message_reply_markup(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=markup
            )

    # ==== حفظ وإلغاء الاستبدال والتذييل ====
    elif action == "save_replacement":
        # حفظ قاعدة الاستبدال الجديدة
        # التحقق من وجود بيانات مؤقتة
        if user_id in temp_data and "name" in temp_data[user_id] and "original" in temp_data[user_id] and "replacement" in temp_data[user_id]:
            # التحقق من اختيار وسم واحد على الأقل
            if "tags" in temp_data[user_id] and temp_data[user_id]["tags"]:
                # إنشاء معرف فريد للقاعدة الجديدة
                new_rule_id = str(len(replacements) + 1)
                while new_rule_id in replacements:
                    new_rule_id = str(int(new_rule_id) + 1)

                # إنشاء قاعدة الاستبدال الجديدة
                new_rule = {
                    "name": temp_data[user_id]["name"],
                    "original": temp_data[user_id]["original"],
                    "replacement": temp_data[user_id]["replacement"],
                    "tags": temp_data[user_id]["tags"]
                }

                # إضافة القاعدة إلى قواعد الاستبدال
                replacements[new_rule_id] = new_rule

                # تنظيف البيانات المؤقتة
                del temp_data[user_id]

                # إنشاء قائمة بأسماء الوسوم المحددة
                tag_names = []
                for tag in new_rule["tags"]:
                    arabic_name = available_id3_tags.get(tag, tag)
                    tag_names.append(arabic_name)

                # إرسال رسالة تأكيد
                bot.edit_message_text(
                    f"✅ تم إضافة قاعدة الاستبدال بنجاح.\n\n"
                    f"*الاسم*: {new_rule['name']}\n"
                    f"*النص الأصلي*: {new_rule['original']}\n"
                    f"*النص البديل*: {new_rule['replacement']}\n"
                    f"*الوسوم المطبقة*: {', '.join(tag_names)}\n\n"
                    "يمكنك العودة إلى لوحة إدارة الاستبدالات:",
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=create_replacements_keyboard(),
                    parse_mode="Markdown"
                )
            else:
                bot.answer_callback_query(call.id, "⚠️ الرجاء اختيار وسم واحد على الأقل.", show_alert=True)
        else:
            bot.answer_callback_query(call.id, "⚠️ البيانات غير مكتملة. الرجاء المحاولة مرة أخرى.", show_alert=True)

    elif action == "cancel_replacement":
        # إلغاء عملية إضافة قاعدة الاستبدال
        if user_id in temp_data:
            del temp_data[user_id]

        # إرسال رسالة تأكيد
        bot.edit_message_text(
            "❌ تم إلغاء عملية إضافة قاعدة الاستبدال.\n\n"
            "يمكنك العودة إلى لوحة إدارة الاستبدالات:",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=create_replacements_keyboard(),
            parse_mode="Markdown"
        )

    elif action == "save_footer":
        # حفظ التذييل الجديد
        # التحقق من وجود بيانات مؤقتة
        if user_id in temp_data and "name" in temp_data[user_id] and "text" in temp_data[user_id]:
            # التحقق من اختيار وسم واحد على الأقل
            if "tags" in temp_data[user_id] and temp_data[user_id]["tags"]:
                # إنشاء معرف فريد للتذييل الجديد
                new_footer_id = str(len(footers) + 1)
                while new_footer_id in footers:
                    new_footer_id = str(int(new_footer_id) + 1)

                # إنشاء التذييل الجديد
                new_footer = {
                    "name": temp_data[user_id]["name"],
                    "text": temp_data[user_id]["text"],
                    "tags": temp_data[user_id]["tags"]
                }

                # إضافة التذييل إلى قائمة التذييلات
                footers[new_footer_id] = new_footer

                # تنظيف البيانات المؤقتة
                del temp_data[user_id]

                # إنشاء قائمة بأسماء الوسوم المحددة
                tag_names = []
                for tag in new_footer["tags"]:
                    arabic_name = available_id3_tags.get(tag, tag)
                    tag_names.append(arabic_name)

                # إرسال رسالة تأكيد
                bot.edit_message_text(
                    f"✅ تم إضافة التذييل بنجاح.\n\n"
                    f"*الاسم*: {new_footer['name']}\n"
                    f"*النص*: {new_footer['text']}\n"
                    f"*الوسوم المطبقة*: {', '.join(tag_names)}\n\n"
                    "يمكنك العودة إلى لوحة إدارة التذييلات:",
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=create_footers_keyboard(),
                    parse_mode="Markdown"
                )
            else:
                bot.answer_callback_query(call.id, "⚠️ الرجاء اختيار وسم واحد على الأقل.", show_alert=True)
        else:
            bot.answer_callback_query(call.id, "⚠️ البيانات غير مكتملة. الرجاء المحاولة مرة أخرى.", show_alert=True)

    elif action == "cancel_footer":
        # إلغاء عملية إضافة التذييل
        if user_id in temp_data:
            del temp_data[user_id]

        # إرسال رسالة تأكيد
        bot.edit_message_text(
            "❌ تم إلغاء عملية إضافة التذييل.\n\n"
            "يمكنك العودة إلى لوحة إدارة التذييلات:",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=create_footers_keyboard(),
            parse_mode="Markdown"
        )

    # إخفاء مؤشر التحميل
    bot.answer_callback_query(call.id)


@bot.message_handler(func=lambda message: user_states.get(message.from_user.id) == STATE_AWAITING_SOURCE_CHANNEL)
def process_source_channel(message):
    """معالجة إدخال قناة المصدر"""
    global SOURCE_CHANNEL

    user_id = message.from_user.id

    # التحقق مما إذا كان المستخدم هو المشرف
    if user_id != ADMIN_ID:
        bot.reply_to(message, "⛔ هذا الإجراء متاح للمشرفين فقط.")
        return

    # تحديد معرف القناة من الرسالة أو الرسالة المعاد توجيهها
    channel_id = None

    if message.forward_from_chat:
        # إذا كانت الرسالة معاد توجيهها من قناة
        channel_id = f"@{message.forward_from_chat.username}" if message.forward_from_chat.username else str(message.forward_from_chat.id)
    elif message.text:
        # إذا كانت رسالة نصية
        channel_id = message.text.strip()

    if not channel_id:
        bot.reply_to(message, "⚠️ لم يتم تحديد قناة المصدر. يرجى إرسال معرف القناة أو إعادة توجيه رسالة منها.")
        return

    # حفظ القناة في قنوات المستخدم
    if user_id not in user_channels:
        user_channels[user_id] = []

    if channel_id not in user_channels[user_id]:
        user_channels[user_id].append(channel_id)

    # تعيين قناة المصدر
    # إضافة @ للمعرف إذا لم يكن يبدأ بها وليس معرف رقمي
    if not channel_id.startswith('@') and not channel_id.startswith('-100'):
        channel_id = f"@{channel_id}"

    SOURCE_CHANNEL = channel_id
    save_data()

    # إعادة تعيين حالة المستخدم
    user_states.pop(user_id, None)

    # إنشاء لوحة مفاتيح للوحة التحكم
    markup = create_control_panel_keyboard()

    bot.send_message(
        message.chat.id,
        f"✅ تم تعيين قناة المصدر بنجاح إلى: {SOURCE_CHANNEL}\n\n"
        "يمكنك متابعة إدارة الإعدادات من خلال لوحة التحكم:",
        reply_markup=markup,
        parse_mode="Markdown"
    )


@bot.message_handler(func=lambda message: user_states.get(message.from_user.id) == STATE_AWAITING_TARGET_CHANNEL)
def process_target_channel(message):
    """معالجة إدخال قناة الهدف"""
    global TARGET_CHANNEL

    user_id = message.from_user.id

    # التحقق مما إذا كان المستخدم هو المشرف
    if user_id != ADMIN_ID:
        bot.reply_to(message, "⛔ هذا الإجراء متاح للمشرفين فقط.")
        return

    # تحديد معرف القناة من الرسالة أو الرسالة المعاد توجيهها
    channel_id = None

    if message.forward_from_chat:
        # إذا كانت الرسالة معاد توجيهها من قناة
        channel_id = f"@{message.forward_from_chat.username}" if message.forward_from_chat.username else str(message.forward_from_chat.id)
    elif message.text:
        # إذا كانت رسالة نصية
        channel_id = message.text.strip()

    if not channel_id:
        bot.reply_to(message, "⚠️ لم يتم تحديد قناة الهدف. يرجى إرسال معرف القناة أو إعادة توجيه رسالة منها.")
        return

    # حفظ القناة في قنوات المستخدم
    if user_id not in user_channels:
        user_channels[user_id] = []

    if channel_id not in user_channels[user_id]:
        user_channels[user_id].append(channel_id)

    # تعيين قناة الهدف
    # إضافة @ للمعرف إذا لم يكن يبدأ بها وليس معرف رقمي
    if not channel_id.startswith('@') and not channel_id.startswith('-100'):
        channel_id = f"@{channel_id}"

    TARGET_CHANNEL = channel_id
    save_data()

    # إعادة تعيين حالة المستخدم
    user_states.pop(user_id, None)

    # إنشاء لوحة مفاتيح للوحة التحكم
    markup = create_control_panel_keyboard()

    bot.send_message(
        message.chat.id,
        f"✅ تم تعيين قناة الهدف بنجاح إلى: {TARGET_CHANNEL}\n\n"
        "يمكنك متابعة إدارة الإعدادات من خلال لوحة التحكم:",
        reply_markup=markup,
        parse_mode="Markdown"
    )


@bot.message_handler(func=lambda message: user_states.get(message.from_user.id) == STATE_AWAITING_REPLACEMENT_NAME)
def process_replacement_name(message):
    """معالجة إدخال اسم قاعدة الاستبدال"""
    user_id = message.from_user.id

    # التحقق مما إذا كان المستخدم هو المشرف
    if user_id != ADMIN_ID:
        bot.reply_to(message, "⛔ هذا الإجراء متاح للمشرفين فقط.")
        return

    if not message.text:
        bot.reply_to(message, "⚠️ الرجاء إدخال اسم صالح لقاعدة الاستبدال.")
        return

    # حفظ اسم القاعدة
    replacement_name = message.text.strip()
    temp_data[user_id] = {"name": replacement_name, "tags": []}

    # طلب النص الأصلي
    bot.reply_to(
        message,
        "تم حفظ اسم القاعدة. الآن الرجاء إرسال النص الأصلي الذي ترغب في استبداله:"
    )

    # تحديث حالة المستخدم
    user_states[user_id] = STATE_AWAITING_REPLACEMENT_ORIGINAL


@bot.message_handler(func=lambda message: user_states.get(message.from_user.id) == STATE_AWAITING_REPLACEMENT_ORIGINAL)
def process_replacement_original(message):
    """معالجة إدخال النص الأصلي للاستبدال"""
    user_id = message.from_user.id

    # التحقق مما إذا كان المستخدم هو المشرف
    if user_id != ADMIN_ID:
        bot.reply_to(message, "⛔ هذا الإجراء متاح للمشرفين فقط.")
        return

    if not message.text:
        bot.reply_to(message, "⚠️ الرجاء إدخال نص أصلي صالح للاستبدال.")
        return

    # حفظ النص الأصلي
    original_text = message.text.strip()
    temp_data[user_id]["original"] = original_text

    # طلب النص البديل
    bot.reply_to(
        message,
        "تم حفظ النص الأصلي. الآن الرجاء إرسال النص البديل الذي سيحل محله:"
    )

    # تحديث حالة المستخدم
    user_states[user_id] = STATE_AWAITING_REPLACEMENT_NEW


@bot.message_handler(func=lambda message: user_states.get(message.from_user.id) == STATE_AWAITING_REPLACEMENT_NEW)
def process_replacement_new(message):
    """معالجة إدخال النص البديل للاستبدال وإنشاء الأزرار لاختيار الوسوم"""
    user_id = message.from_user.id

    # التحقق مما إذا كان المستخدم هو المشرف
    if user_id != ADMIN_ID:
        bot.reply_to(message, "⛔ هذا الإجراء متاح للمشرفين فقط.")
        return

    if not message.text:
        bot.reply_to(message, "⚠️ الرجاء إدخال نص بديل صالح للاستبدال.")
        return

    # حفظ النص البديل
    replacement_text = message.text.strip()
    temp_data[user_id]["replacement"] = replacement_text

    # إنشاء لوحة مفاتيح لاختيار الوسوم التي سيتم تطبيق الاستبدال عليها
    markup = types.InlineKeyboardMarkup(row_width=2)

    # إضافة أزرار لكل وسم
    for tag_key, tag_name in available_id3_tags.items():
        # تحديد حالة الزر (محدد أو غير محدد)
        is_selected = tag_key in temp_data[user_id]["tags"]
        status = "✅" if is_selected else "⬜"

        btn = types.InlineKeyboardButton(
            f"{tag_name} {status}",
            callback_data=f"toggle_tag:{tag_key}"
        )
        markup.add(btn)

    # إضافة أزرار الحفظ والإلغاء
    btn_save = types.InlineKeyboardButton("حفظ قاعدة الاستبدال ✅", callback_data="save_replacement")
    btn_cancel = types.InlineKeyboardButton("إلغاء ❌", callback_data="cancel_replacement")
    markup.add(btn_save, btn_cancel)

    # إعادة تعيين حالة المستخدم
    user_states.pop(user_id, None)

    bot.send_message(
        message.chat.id,
        f"تم حفظ النص البديل: *{replacement_text}*\n\n"
        "الآن الرجاء اختيار الوسوم التي سيتم تطبيق الاستبدال عليها:",
        reply_markup=markup,
        parse_mode="Markdown"
    )


@bot.message_handler(func=lambda message: user_states.get(message.from_user.id) == STATE_AWAITING_FOOTER_NAME)
def process_footer_name(message):
    """معالجة إدخال اسم التذييل"""
    user_id = message.from_user.id

    # التحقق مما إذا كان المستخدم هو المشرف
    if user_id != ADMIN_ID:
        bot.reply_to(message, "⛔ هذا الإجراء متاح للمشرفين فقط.")
        return

    if not message.text:
        bot.reply_to(message, "⚠️ الرجاء إدخال اسم صالح للتذييل.")
        return

    # حفظ اسم التذييل
    footer_name = message.text.strip()
    temp_data[user_id] = {"name": footer_name, "tags": []}

    # طلب نص التذييل
    bot.reply_to(
        message,
        "تم حفظ اسم التذييل. الآن الرجاء إرسال نص التذييل الذي سيتم إضافته في نهاية الوسوم:"
    )

    # تحديث حالة المستخدم
    user_states[user_id] = STATE_AWAITING_FOOTER_TEXT


@bot.message_handler(func=lambda message: user_states.get(message.from_user.id) == STATE_AWAITING_FOOTER_TEXT)
def process_footer_text(message):
    """معالجة إدخال نص التذييل"""
    user_id = message.from_user.id

    # التحقق مما إذا كان المستخدم هو المشرف
    if user_id != ADMIN_ID:
        bot.reply_to(message, "⛔ هذا الإجراء متاح للمشرفين فقط.")
        return

    if not message.text:
        bot.reply_to(message, "⚠️ الرجاء إدخال نص صالح للتذييل.")
        return

    # حفظ نص التذييل
    footer_text = message.text.strip()
    temp_data[user_id]["text"] = footer_text

    # إنشاء لوحة مفاتيح لاختيار الوسوم التي سيتم تطبيق التذييل عليها
    markup = types.InlineKeyboardMarkup(row_width=2)

    # إضافة أزرار لكل وسم
    for tag_key, tag_name in available_id3_tags.items():
        # تحديد حالة الزر (محدد أو غير محدد)
        is_selected = tag_key in temp_data[user_id]["tags"]
        status = "✅" if is_selected else "⬜"

        btn = types.InlineKeyboardButton(
            f"{tag_name} {status}",
            callback_data=f"toggle_footer_tag:{tag_key}"
        )
        markup.add(btn)

    # إضافة أزرار الحفظ والإلغاء
    btn_save = types.InlineKeyboardButton("حفظ التذييل ✅", callback_data="save_footer")
    btn_cancel = types.InlineKeyboardButton("إلغاء ❌", callback_data="cancel_footer")
    markup.add(btn_save, btn_cancel)

    # إعادة تعيين حالة المستخدم
    user_states.pop(user_id, None)

    bot.send_message(
        message.chat.id,
        f"تم حفظ نص التذييل: *{footer_text}*\n\n"
        "الآن الرجاء اختيار الوسوم التي سيتم إضافة التذييل إليها:",
        reply_markup=markup,
        parse_mode="Markdown"
    )


@bot.message_handler(func=lambda message: user_states.get(message.from_user.id) == STATE_AWAITING_TEMPLATE_NAME)
def process_template_name(message):
    """معالجة إدخال اسم القالب الجديد"""
    user_id = message.from_user.id

    # التحقق مما إذا كان المستخدم هو المشرف
    if user_id != ADMIN_ID:
        bot.reply_to(message, "⛔ هذا الإجراء متاح للمشرفين فقط.")
        return

    if not message.text:
        bot.reply_to(message, "⚠️ الرجاء إدخال اسم صالح للقالب.")
        return

    # حفظ اسم القالب
    template_name = message.text.strip()

    # إنشاء قالب جديد
    if user_id in temp_data and temp_data[user_id]["type"] == "template":
        temp_data[user_id]["template"] = {
            "name": template_name,
            "artist": "فنان جديد",
            "album_artist": "فنان الألبوم الجديد",
            "album": "ألبوم جديد",
            "genre": "نوع جديد",
            "year": "2025",
            "publisher": "ناشر جديد",
            "copyright": "© 2025 جميع الحقوق محفوظة",
            "comment": "تعليق على الملف",
            "website": "https://example.com",
            "composer": "ملحن جديد",
            "lyrics": "كلمات الأغنية هنا",
            "description": "وصف للملف الصوتي"
        }

        # إنشاء نص تأكيد
        confirmation_text = "✅ تم إنشاء قالب جديد بالاسم: *" + template_name + "*\n\n"
        confirmation_text += "قم باختيار حقل لتعديله أو اضغط على زر 'حفظ القالب' للاحتفاظ بالقالب كما هو:\n\n"

        # إنشاء لوحة مفاتيح لاختيار الحقول للتعديل
        markup = types.InlineKeyboardMarkup(row_width=2)

        # إضافة أزرار لكل حقل
        for field_key, field_name in available_id3_tags.items():
            btn = types.InlineKeyboardButton(
                f"{field_name} ✏️",
                callback_data=f"new_template_field:{field_key}"
            )
            markup.add(btn)

        # إضافة زر الحفظ والإلغاء
        btn_save = types.InlineKeyboardButton("حفظ القالب ✅", callback_data="save_new_template")
        btn_cancel = types.InlineKeyboardButton("إلغاء ❌", callback_data="cancel_new_template")
        markup.add(btn_save, btn_cancel)

        # إعادة تعيين حالة المستخدم
        user_states.pop(user_id, None)

        bot.send_message(
            message.chat.id,
            confirmation_text,
            reply_markup=markup,
            parse_mode="Markdown"
        )
    else:
        bot.reply_to(message, "⚠️ حدث خطأ في العملية. الرجاء المحاولة مرة أخرى.")


@bot.message_handler(func=lambda message: user_states.get(message.from_user.id) == STATE_AWAITING_TEMPLATE_FIELD)
def process_template_field(message):
    """معالجة إدخال قيمة حقل للقالب"""
    user_id = message.from_user.id

    # التحقق مما إذا كان المستخدم هو المشرف
    if user_id != ADMIN_ID:
        bot.reply_to(message, "⛔ هذا الإجراء متاح للمشرفين فقط.")
        return

    # إذا أرسل المستخدم "-" أو "فارغ" أو "clear"، نجعل القيمة فارغة
    is_empty_request = False
    field_value = ""

    if message.text:
        text = message.text.strip()
        if text.lower() in ["-", "فارغ", "empty", "clear", "null", "none"]:
            # مؤشر لإفراغ الحقل
            is_empty_request = True
            field_value = ""
        else:
            field_value = text

    if user_id in temp_data:
        if temp_data[user_id]["type"] == "template_edit":
            # تعديل قالب موجود
            template_key = temp_data[user_id]["template_key"]
            field_key = temp_data[user_id]["field_key"]

            if template_key in templates:
                # تحديث قيمة الحقل
                templates[template_key][field_key] = field_value

                # الحصول على الاسم العربي للحقل
                field_name = available_id3_tags.get(field_key, field_key)

                # إرسال رسالة تأكيد
                markup = types.InlineKeyboardMarkup(row_width=1)
                btn_back = types.InlineKeyboardButton(
                    "العودة لتعديل القالب ↩️", 
                    callback_data=f"edit_template:{template_key}"
                )
                markup.add(btn_back)

                # تحديد نص الرسالة بناءً على ما إذا كانت القيمة فارغة
                if is_empty_request:
                    confirmation_msg = (
                        f"✅ تم إفراغ قيمة حقل {field_name} بنجاح.\n\n"
                        "الآن سيتم الاحتفاظ بالقيمة الأصلية للوسم في الملفات الصوتية."
                    )
                else:
                    confirmation_msg = (
                        f"✅ تم تحديث حقل {field_name} بنجاح.\n\n"
                        f"القيمة الجديدة: *{field_value}*"
                    )

                bot.send_message(
                    message.chat.id,
                    confirmation_msg,
                    reply_markup=markup,
                    parse_mode="Markdown"
                )
        elif temp_data[user_id]["type"] == "template" and "current_field" in temp_data[user_id] and temp_data[user_id]["current_field"]:
            # تعديل حقل لقالب جديد
            field_key = temp_data[user_id]["current_field"]
            temp_data[user_id]["template"][field_key] = field_value

            # الحصول على الاسم العربي للحقل
            field_name = available_id3_tags.get(field_key, field_key)

            # إنشاء لوحة مفاتيح لاختيار الحقول للتعديل
            markup = types.InlineKeyboardMarkup(row_width=2)

            # إضافة أزرار لكل حقل
            for field_key_item, field_name_item in available_id3_tags.items():
                btn = types.InlineKeyboardButton(
                    f"{field_name_item} ✏️",
                    callback_data=f"new_template_field:{field_key_item}"
                )
                markup.add(btn)

            # إضافة زر الحفظ والإلغاء
            btn_save = types.InlineKeyboardButton("حفظ القالب ✅", callback_data="save_new_template")
            btn_cancel = types.InlineKeyboardButton("إلغاء ❌", callback_data="cancel_new_template")
            markup.add(btn_save, btn_cancel)

            # تحديد نص الرسالة بناءً على ما إذا كانت القيمة فارغة
            if is_empty_request:
                confirmation_msg = (
                    f"✅ تم إفراغ قيمة حقل {field_name} بنجاح.\n\n"
                    "الآن سيتم الاحتفاظ بالقيمة الأصلية للوسم في الملفات الصوتية.\n\n"
                    "يمكنك مواصلة تعديل حقول القالب أو حفظه:"
                )
            else:
                confirmation_msg = (
                    f"✅ تم تحديث حقل {field_name} بنجاح.\n\n"
                    f"القيمة الجديدة: *{field_value}*\n\n"
                    "يمكنك مواصلة تعديل حقول القالب أو حفظه:"
                )

            bot.send_message(
                message.chat.id,
                confirmation_msg,
                reply_markup=markup,
                parse_mode="Markdown"
            )
        else:
            bot.reply_to(message, "⚠️ حدث خطأ في العملية. الرجاء المحاولة مرة أخرى.")
    else:
        bot.reply_to(message, "⚠️ لم يتم العثور على بيانات العملية. الرجاء بدء العملية من جديد.")

    # إعادة تعيين حالة المستخدم
    user_states.pop(user_id, None)


@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    """التعامل مع الصور المرسلة للبوت"""
    global album_cover_path

    user_id = message.from_user.id

    # فحص الحالة الانتظار
    if user_states.get(user_id) == STATE_AWAITING_ALBUM_COVER:
        # التحقق مما إذا كان المستخدم هو المشرف
        if user_id != ADMIN_ID:
            bot.reply_to(message, "⛔ هذا الإجراء متاح للمشرفين فقط.")
            return

        # تحميل الصورة بأفضل جودة
        file_id = message.photo[-1].file_id

        try:
            # تحميل الصورة من خادم تلجرام
            file_info = bot.get_file(file_id)
            file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_info.file_path}"

            # إنشاء دليل لحفظ الصور إذا لم يكن موجودًا
            if not os.path.exists("album_covers"):
                os.makedirs("album_covers")

            # تحديد مسار الملف واسمه
            album_cover_path = f"album_covers/album_cover_{int(time.time())}.jpg"

            # تحميل الصورة وحفظها
            response = requests.get(file_url)
            with open(album_cover_path, 'wb') as file:
                file.write(response.content)

            # تأكيد نجاح العملية
            bot.reply_to(
                message,
                f"✅ تم تعيين صورة الألبوم بنجاح!\n\n"
                f"المسار: {album_cover_path}"
            )

            # عرض لوحة تحكم صورة الألبوم
            markup = create_album_cover_keyboard()
            bot.send_message(
                message.chat.id,
                "🖼️ *إدارة صورة الألبوم*\n\n"
                "يمكنك إدارة صورة الألبوم من خلال الأزرار أدناه:",
                reply_markup=markup,
                parse_mode="Markdown"
            )

        except Exception as e:
            bot.reply_to(
                message, 
                f"⚠️ حدث خطأ أثناء تحميل صورة الألبوم: {str(e)}"
            )

        # إعادة تعيين حالة المستخدم
        user_states.pop(user_id, None)
    else:
        # في حالة عدم تحديد سياق معين، أخبر المستخدم أنك لا تعالج الصور بشكل عام
        bot.reply_to(message, 
            "هذا بوت لمعالجة الملفات الصوتية. لا يمكنني معالجة الصور إلا في سياق تعيين صورة للألبوم."
        )


@bot.message_handler(content_types=['audio'])
def handle_audio(message):
    """التعامل مع الملفات الصوتية المرسلة إلى البوت"""
    audio = message.audio
    user_id = message.from_user.id
    first_name = message.from_user.first_name

    logger.info(f"تم استلام ملف صوتي من {first_name} ({user_id}): {audio.file_name}")

    # إخبار المستخدم بأننا نعالج الملف
    processing_msg = bot.reply_to(message, 
        f"تم استلام الملف الصوتي: {audio.file_name}\n"
        "جاري معالجة الملف..."
    )

    try:
        # تحميل الملف الصوتي
        file_path = download_file(audio.file_id)

        if not file_path:
            bot.edit_message_text(
                "⚠️ حدث خطأ أثناء تحميل الملف الصوتي.",
                chat_id=message.chat.id,
                message_id=processing_msg.message_id
            )
            return

        # استخراج العنوان من وصف الرسالة أو اسم الملف
        title = message.caption if message.caption else os.path.splitext(audio.file_name)[0]

        # معالجة وسوم الملف الصوتي
        success = process_audio_tags(file_path, title)

        if success:
            # إعادة إرسال الملف مع الوسوم المعدلة
            with open(file_path, 'rb') as audio_file:
                current_template = templates[current_template_key]
                bot.send_audio(
                    message.chat.id,
                    audio_file,
                    caption=f"تم معالجة الملف الصوتي: {audio.file_name}",
                    title=title,
                    performer=current_template["artist"],
                )

            # إبلاغ المستخدم بالتعديلات التي تمت
            bot.edit_message_text(
                f"✅ تم معالجة الملف الصوتي بنجاح!\n"
                f"🎵 العنوان: {title}\n"
                f"👤 الفنان: {current_template['artist']}\n"
                f"💿 الألبوم: {current_template['album']}",
                chat_id=message.chat.id,
                message_id=processing_msg.message_id
            )

            # إذا كان المستخدم هو المشرف، إعادة نشر الملف إلى القناة المستهدفة
            if user_id == ADMIN_ID and TARGET_CHANNEL:
                try:
                    with open(file_path, 'rb') as audio_file:
                        bot.send_audio(
                            TARGET_CHANNEL,
                            audio_file,
                            caption=message.caption if message.caption else f"تم نشر الملف الصوتي: {title}",
                            title=title,
                            performer=current_template["artist"],
                        )
                    bot.send_message(
                        message.chat.id,
                        f"📢 تم إعادة نشر الملف الصوتي في القناة: {TARGET_CHANNEL}"
                    )
                except Exception as e:
                    logger.error(f"خطأ في إعادة نشر الملف الصوتي إلى القناة: {e}")
                    bot.send_message(
                        message.chat.id,
                        f"⚠️ حدث خطأ أثناء نشر الملف الصوتي في القناة: {str(e)}"
                    )
        else:
            bot.edit_message_text(
                "⚠️ حدث خطأ أثناء معالجة وسوم الملف الصوتي.",
                chat_id=message.chat.id,
                message_id=processing_msg.message_id
            )
    except Exception as e:
        logger.error(f"خطأ في معالجة الملف الصوتي: {e}")
        bot.edit_message_text(
            f"⚠️ حدث خطأ أثناء معالجة الملف الصوتي: {str(e)}",
            chat_id=message.chat.id,
            message_id=processing_msg.message_id
        )


@bot.message_handler(func=lambda message: True)
def echo_all(message):
    """الرد على جميع الرسائل الأخرى"""
    bot.reply_to(message, "هذا بوت لمعالجة الملفات الصوتية. أرسل /help للمساعدة.")


@bot.channel_post_handler(content_types=['audio'])
def handle_channel_audio(message):
    """معالجة الملفات الصوتية المرسلة في القناة"""
    try:
        if message.chat.username == SOURCE_CHANNEL.replace("@", "") or str(message.chat.id) == SOURCE_CHANNEL.replace("@", ""):
            logger.info(f"تم استلام ملف صوتي من القناة المصدر: {message.audio.file_name}")
            handle_audio(message)
    except Exception as e:
        logger.error(f"خطأ في معالجة ملف صوتي من القناة: {e}")

# وظائف حفظ واسترجاع البيانات
def reset_data():
    """إعادة تعيين جميع البيانات إلى القيم الافتراضية"""
    global SOURCE_CHANNEL, TARGET_CHANNEL, current_template_key, templates
    global replacements, footers, config, album_cover_path
    
    SOURCE_CHANNEL = ""
    TARGET_CHANNEL = ""
    current_template_key = "افتراضي"
    templates = {
        "افتراضي": {
            "name": "القالب الافتراضي",
            "artist": "$artist",
            "album_artist": "$album_artist",
            "album": "$album",
            "genre": "إنشاد",
            "year": "2025",
            "publisher": "الناشر الافتراضي",
            "copyright": "© 2025 جميع الحقوق محفوظة",
            "comment": "تم المعالجة بواسطة بوت معالجة الصوتيات",
            "website": "https://t.me/EchoAlMasirah",
            "composer": "ملحن افتراضي",
            "lyrics": "كلمات الأغنية الافتراضية",
            "description": "وصف للملف الصوتي"
        }
    }
    replacements = {}
    footers = {}
    config = {
        "bot_enabled": True,
        "replacement_enabled": True,
        "footer_enabled": True,
        "remove_links_enabled": True,
        "album_cover_enabled": True
    }
    album_cover_path = None
    
    # حذف ملف البيانات إذا كان موجوداً
    if os.path.exists('bot_data.json'):
        os.remove('bot_data.json')
    
    # حذف صور الألبوم
    if os.path.exists('album_covers'):
        for file in os.listdir('album_covers'):
            file_path = os.path.join('album_covers', file)
            try:
                os.remove(file_path)
            except Exception as e:
                logger.error(f"خطأ في حذف الملف {file_path}: {e}")
        os.rmdir('album_covers')
    
    logger.info("تم إعادة تعيين جميع البيانات إلى القيم الافتراضية")

def save_data():
    """حفظ جميع البيانات في قاعدة البيانات وملف JSON كنسخة احتياطية"""
    # حفظ في قاعدة البيانات
    save_settings_to_db()
    
    # حفظ نسخة احتياطية في ملف
    data = {
        'source_channel': SOURCE_CHANNEL,
        'target_channel': TARGET_CHANNEL,
        'current_template_key': current_template_key,
        'templates': templates,
        'replacements': replacements,
        'footers': footers,
        'config': config,
        'album_cover_path': album_cover_path
    }
    try:
        with open('bot_data.json', 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        logger.info("تم حفظ النسخة الاحتياطية بنجاح")
    except Exception as e:
        logger.error(f"خطأ في حفظ النسخة الاحتياطية: {e}")

def load_data():
    """استرجاع البيانات من قاعدة البيانات أو ملف JSON كنسخة احتياطية"""
    global SOURCE_CHANNEL, TARGET_CHANNEL, current_template_key, templates
    global replacements, footers, config, album_cover_path
    
    # محاولة تحميل من قاعدة البيانات أولاً
    db_data = load_settings_from_db()
    if db_data:
        SOURCE_CHANNEL = db_data.get('source_channel', '')
        TARGET_CHANNEL = db_data.get('target_channel', '')
        current_template_key = db_data.get('current_template_key', 'افتراضي')
        templates.update(db_data.get('templates', {}))
        replacements.update(db_data.get('replacements', {}))
        footers.update(db_data.get('footers', {}))
        config.update(db_data.get('config', {}))
        album_cover_path = db_data.get('album_cover_path')
        logger.info("تم تحميل البيانات من قاعدة البيانات بنجاح")
        return
    
    # إذا فشل التحميل من قاعدة البيانات، جرب تحميل النسخة الاحتياطية
    try:
        with open('bot_data.json', 'r', encoding='utf-8') as f:
            data = json.load(f)
            SOURCE_CHANNEL = data.get('source_channel', '')
            TARGET_CHANNEL = data.get('target_channel', '')
            current_template_key = data.get('current_template_key', 'افتراضي')
            templates.update(data.get('templates', {}))
            replacements.update(data.get('replacements', {}))
            footers.update(data.get('footers', {}))
            config.update(data.get('config', {}))
            album_cover_path = data.get('album_cover_path')
        logger.info("تم تحميل البيانات من النسخة الاحتياطية بنجاح")
    except FileNotFoundError:
        logger.info("لم يتم العثور على ملف البيانات - سيتم استخدام القيم الافتراضية")
    except Exception as e:
        logger.error(f"خطأ في تحميل البيانات: {e}")

# تعديل الوظائف الأساسية لحفظ البيانات بعد كل تغيير
def update_data(callback_query=None, success_message=None):
    """تحديث البيانات وإظهار رسالة نجاح اختيارية"""
    save_data()
    if callback_query and success_message:
        bot.answer_callback_query(callback_query.id, success_message, show_alert=True)

# بدء تشغيل البوت
if __name__ == "__main__":
    logger.info("بدء تشغيل البوت...")
    
    # تحميل البيانات المحفوظة
    load_data()
    
    while True:
        try:
            logger.info("محاولة تشغيل البوت...")
            bot.infinity_polling(allowed_updates=["message", "channel_post", "callback_query"], timeout=20)
        except Exception as e:
            logger.error(f"خطأ في تشغيل البوت: {e}")
            time.sleep(3)