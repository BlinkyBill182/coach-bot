"""What describes one person's routine in the code. Edit this file for your own bot, or set the values in .env.

The coaching knowledge is in .claude/skills/muscle-coach. Targets, the week, the exercise plan and the meal
template live in the database, and the bot changes them in Telegram.
"""
from __future__ import annotations

import os

GYM_NAME = os.environ.get("GYM_NAME") or "חדר הכושר"
# When the planner may put a workout. Python weekday() (Monday=0) -> (open, close) in minutes after midnight.
GYM_HOURS = {
    0: (360, 1380), 1: (360, 1380), 2: (360, 1380), 3: (360, 1380),
    4: (360, 1020), 5: (600, 1380), 6: (360, 1380),
}
# Where the Wolt search starts when there is no recent shared location. Empty: the bot asks for a place.
HOME_ADDRESS = os.environ.get("HOME_ADDRESS") or ""
# Words that hide a dish in Wolt menus, comma separated, e.g. FOOD_AVOID_WORDS=בקר,פיצה,burger. Empty: nothing is hidden.
FOOD_AVOID_WORDS = [w.strip() for w in (os.environ.get("FOOD_AVOID_WORDS") or "").split(",") if w.strip()]
# Cooking days, Python weekday() -> what to cook. Sent the evening before and shown in the day's plan.
PREP = {
    6: "כ-40 דקות: עוף ותפוחי אדמה בתבנית אחת, אורז ל-3 ימים, ביצים קשות, ירקות חתוכים.",
    2: "כ-25 דקות: עוף טחון או הודו טחון, ברוקולי וקישואים, אורז חדש.",
}
# The week's shopping, sent the evening before this weekday. Keep it in step with the skill's "Weekly shopping".
SHOP_FOR_WEEKDAY = 6  # Sunday
SHOPPING = [
    "חלבון: חזה עוף 1.2 ק״ג, עוף או הודו טחון 500 ג׳, 3 פילה סלמון, 30 ביצים, 7 יוגורט חלבון, קוטג׳.",
    "פחמימות: אורז 700 ג׳, תפוחי אדמה ובטטות 1.5 ק״ג, שיבולת שועל 500 ג׳, 2 חבילות פריכיות.",
    "פירות: 8 בננות, 6 תפוחים, פירות יער קפואים, תמרים, 4 אבוקדו.",
    "עוד: דבש, שמן זית, טחינה גולמית, אגוזים 200 ג׳, ירקות, עשבי תיבול, תבלינים.",
]
