# התקנה שלב אחרי שלב

בסוף ההתקנה הבוט רץ על שרת משלכם, עונה רק לכם בטלגרם, ושולח תזכורות גם כשהמחשב סגור.

בסיס חובה: טלגרם, מפתח של Claude ושרת. כל השאר בסעיף התוספות, ואפשר להוסיף אחר כך.

צריך מחשב עם `ssh` ו-`rsync` (Mac או Linux). המפתחות נשארים אצלכם ועל השרת, ולא נכנסים לקוד.

## 1. הקוד

1. בעמוד הריפו ב-GitHub לוחצים **Use this template**, ואז **Create a new repository**.
2. נותנים לריפו שם ובוחרים **Private**. שימו לב: במסך הזה ברירת המחדל היא Public.
3. מורידים את הריפו החדש למחשב:

```bash
git clone https://github.com/<המשתמש-שלכם>/<שם-הריפו>.git
cd <שם-הריפו>
```

**לא עושים Fork.** Fork של ריפו ציבורי הוא ציבורי, ואחרי שתתאימו את הבוט לעצמכם יהיו בקוד פרטים עליכם.

## 2. השרת

לכל בוט שרת משלו. ההעלאה כותבת לתיקייה `/docker/coach` בשרת, ואם כבר רץ שם בוט, הקוד שלו יידרס.

1. קונים VPS ב-Hostinger. במערכת ההפעלה, בלשונית **Applications**, בוחרים את **Docker and Traefik**. זו Ubuntu 24.04 שבה Docker ו-Traefik כבר מותקנים. Traefik נותן לבוט כתובת HTTPS, ובלעדיו המעקב לא ייפתח בטלגרם.
2. בחרתם **Docker** בלי Traefik? ב-hPanel, ב-**Docker Manager**, ב-**Catalog**, מתקינים את **Traefik**.
3. מעתיקים מעמוד ה-VPS את שם השרת. הוא נראה כך: `srv123456.hstgr.cloud`.
4. ב-VPS, בהגדרות, ב-**SSH keys**, מוסיפים את המפתח הציבורי מהמחשב שלכם. אם אין לכם מפתח:

```bash
ssh-keygen -t ed25519
```

```bash
cat ~/.ssh/id_ed25519.pub
```

מדביקים את השורה שהודפסה. בודקים שההתחברות עובדת:

```bash
ssh root@srv123456.hstgr.cloud 'docker ps'
```

ברשימה אמורה להופיע שורה עם `traefik`.

## 3. הבוט בטלגרם

1. בטלגרם פותחים שיחה עם **@BotFather**.
2. שולחים `/newbot`, או לוחצים **Create a New Bot** באפליקציה של BotFather. בוחרים שם, ושם משתמש שנגמר ב-`bot`.
3. שומרים את ה**טוקן** שחוזר, ולא משתפים אותו. באפליקציה מעתיקים אותו עם **Copy**.

## 4. מפתח של Claude

1. נכנסים ל-[console.anthropic.com](https://console.anthropic.com) ומוסיפים אמצעי תשלום. הכתובת מעבירה היום ל-platform.claude.com.
2. ב-**API Keys** לוחצים **Create key**. אם נפתח חלון על identity federation, בוחרים **Continue with an API key**. בטופס נותנים למפתח שם ובודקים את **Expires**. ברירת המחדל היא 30 יום, ואחריהם המפתח מפסיק לעבוד והבוט מפסיק לענות. בוחרים **Never**, או **Custom** עם תאריך רחוק ותזכורת להחליף. שומרים את המפתח.
3. מגדירים תקרה חודשית: **Spending**, אחר כך **Spend limits**, אחר כך **Adjust limit**. התשלום לפי שימוש, והבוט רושם הערכה של העלות היומית.

## 5. העלאה ראשונה

מתיקיית הריפו במחשב (במקום `srv123456.hstgr.cloud` כותבים את שם השרת שלכם):

```bash
bash bot/deploy/deploy.sh root@srv123456.hstgr.cloud --first srv123456.hstgr.cloud
```

הפקודה מעתיקה את הקוד, מזהה את הגדרות Traefik ויוצרת על השרת קובץ הגדרות. היא תעצור ותגיד שחסרים מפתחות, וזה תקין.

## 6. המפתחות

מתחברים לשרת ופותחים את קובץ ההגדרות:

```bash
ssh root@srv123456.hstgr.cloud
```

```bash
nano /docker/coach/.env
```

ממלאים:

- `TELEGRAM_TOKEN`: הטוקן מ-BotFather.
- `ANTHROPIC_API_KEY`: המפתח של Claude.
- `GYM_NAME`: שם חדר הכושר שלכם, כמו שיופיע בתוכנית וביומן.

שומרים עם `Ctrl+O`, `Enter`, ויוצאים עם `Ctrl+X`. אחר כך `exit`, ומהמחשב מריצים שוב:

```bash
bash bot/deploy/deploy.sh root@srv123456.hstgr.cloud
```

## 7. רק אתם

1. בטלגרם שולחים לבוט `/start`. הוא עונה עם מספר המשתמש שלכם.
2. מכניסים את המספר לשורה `OWNER_ID` באותו קובץ `.env`.
3. מריצים שוב את פקודת ההעלאה מסעיף 6.

מעכשיו הבוט עונה רק לכם. אדם אחר שישלח לו `/start` יקבל רק את מספר המשתמש של עצמו.

## 8. השאלון

שולחים שוב `/start`. נפתח כפתור לשאלון של 9 שאלות. ממנו הבוט מעריך כמה אתם שורפים ומציע יעדים, ומחליף אותם רק אחרי שתאשרו.

התוכנית שעולה עם ההתקנה היא נקודת התחלה: 5 אימונים בשבוע ותבנית ארוחות (`bot/starter`). לשנות אותן אומרים לבוט, למשל "אני מתאמן רק בימים א׳, ג׳ ו-ה׳".

## תוספות

### יומן Google

בלי היומן הבוט לא מזיז אימונים ולא מתכנן סביב פגישות.

הבוט מתחבר דרך חשבון שירות (service account): כתובת מייל משלו ב-Google, שאתם משתפים איתה את היומן. הוא רואה ומשנה רק את היומן ששיתפתם.

1. נכנסים ל-[Cloud Shell](https://shell.cloud.google.com) עם חשבון Google של היומן. הוא חינמי. בפעם הראשונה לוחצים **Continue**, ובחלון **Authorize Cloud Shell** לוחצים **Authorize**.
2. מדביקים את הפקודות האלה ולוחצים Enter:

```bash
P=coach-$(date +%s)
gcloud projects create $P --name="Coach bot"
gcloud config set project $P
gcloud services enable calendar-json.googleapis.com iam.googleapis.com
gcloud iam service-accounts create coach-bot --display-name="Coach bot"
sleep 15
gcloud iam service-accounts keys create google_service_account.json --iam-account=coach-bot@$P.iam.gserviceaccount.com
cloudshell download google_service_account.json
echo "Share your calendar with: coach-bot@$P.iam.gserviceaccount.com"
echo "GOOGLE_CALENDAR_ID=$(gcloud config get-value account 2>/dev/null)"
```

נפתח חלון **Download File**, ולוחצים **Download**. הקובץ `google_service_account.json` יורד למחשב, ובטרמינל מודפסות שתי שורות: הכתובת של הבוט, ושורה `GOOGLE_CALENDAR_ID=` עם הכתובת שלכם. אחרי שהקובץ ירד, מוחקים את העותק שנשאר ב-Cloud Shell:

```bash
rm google_service_account.json
```

3. ב-[Google Calendar](https://calendar.google.com) במחשב: ⚙️ **Settings**, ותחת **Settings for my calendars** בוחרים את היומן. ב-**Shared with** לוחצים **Add people and groups**, ומדביקים את הכתובת של הבוט. ב-**Permissions** בוחרים **Make changes and see all event details**, ולוחצים **Send**.
4. מתיקיית הריפו במחשב מעבירים את הקובץ:

```bash
mv ~/Downloads/google_service_account.json bot/
```

5. מוסיפים ל-`/docker/coach/.env` את השורה `GOOGLE_CALENDAR_ID=` שהודפסה, ומריצים שוב את פקודת ההעלאה. היא מעתיקה את הקובץ לשרת. בסוף ההעלאה, בשורות מהלוג, אמורה להופיע `calendar connected`. אם מופיעה `calendar error`, השורה אומרת מה לבדוק.

שני דברים שכדאי לדעת:

- הקובץ הוא מפתח שלא פג. הוא לא נכנס לגיט, ושמור רק אצלכם ועל השרת. אם הוא דלף, מוחקים את השיתוף ביומן, והמפתח כבר לא רואה כלום.
- התראות ביומן שייכות לכל משתמש בנפרד, אז הבוט לא קובע התראה ביומן שלכם. אימון שהוא יוצר מקבל את ההתראות הרגילות של היומן, ותזכורת היציאה מגיעה בטלגרם. באירוע עצמו, כיוצר, תופיע הכתובת של הבוט.

#### חשבון Google של עבודה או לימודים

ארגון יכול לחסום מפתחות של חשבון שירות, ואז השלב השני נכשל. במקרה כזה מתחברים בהרשאה רגילה (OAuth):

1. ב-[console.cloud.google.com](https://console.cloud.google.com) יוצרים פרויקט ומפעילים את **Google Calendar API**.
2. ב-**OAuth consent screen** בוחרים External, ואז מעבירים את ה-Publishing status ל-**In production**. אחרת ההרשאה פגה כל 7 ימים.
3. ב-**Credentials** יוצרים **OAuth client ID** מסוג **Desktop app**, ומורידים את קובץ ה-JSON.
4. שומרים אותו בשם `bot/credentials.json`, ומריצים:

```bash
cd bot && python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt && .venv/bin/python scripts/auth_google.py
```

הפקודה צריכה Python 3.12 במחשב. Google יזהיר שהאפליקציה לא מאומתת. זו האפליקציה שלכם, אז לוחצים Advanced ואז Continue.

5. הפקודה נכנסת לתיקייה `bot`, אז חוזרים לתיקיית הריפו עם `cd ..` ומריצים שוב את פקודת ההעלאה. היא מעתיקה את ההרשאה לשרת.

הבוט מזהה אימון לפי אירוע שהשם שלו כולל "חדר כושר". הוא מזיז רק את האירוע הזה, ויוצר אותו כשהופכים יום ליום אימון. אירועים אחרים הוא לא משנה.

### הודעות קוליות

מכניסים ל-`.env` מפתח של ElevenLabs בשורה `ELEVENLABS_API_KEY`.

### המלצות מ-Wolt

ב-`.env` כותבים `WOLT_ENABLED=1`. אפשר להוסיף גם `HOME_ADDRESS` (מאיפה לחפש כשלא שיתפתם מיקום) ו-`FOOD_AVOID_WORDS` (מילים שמסתירות מנות).

זה קורא מהכתובות הציבוריות של אתר Wolt, לא מ-API רשמי, אז זה עלול להפסיק לעבוד, ובודקים בעצמכם שזה מתאים לתנאי השימוש שלהם. הבוט לא מזמין, רק ממליץ.

### זמני נסיעה

זמני נסיעה חיים מ-Waze מגיעים מהשירות [waze-eta](https://github.com/BlinkyBill182/waze-eta), שרץ על אותו שרת. ההתקנה שלו ב-README שלו, ובסופה ממלאים כאן ב-`.env` את `ETA_URL` ואת `ETA_KEY`. הוא לא רשמי: ל-Waze אין API ציבורי, אז הוא עלול להפסיק לעבוד.

בלי השירות הבוט משתמש בזמנים ששמרתם לכל מקום, וברירת המחדל היא 20 דקות.

### העלאה אוטומטית

כל דחיפה ל-`main` מריצה את הבדיקות ומעלה לשרת. מגדירים פעם אחת (צריך את [GitHub CLI](https://cli.github.com)):

```bash
bash bot/deploy/setup-github-deploy.sh root@srv123456.hstgr.cloud <המשתמש-שלכם>/<שם-הריפו>
```

עד שמגדירים, הבדיקות רצות ושלב ההעלאה מדלג עם אזהרה.

## גיבוי

הבוט מעתיק את מסד הנתונים כל לילה ב-03:30 ל-`/docker/coach/data/backups`, ושומר 14 עותקים. הם על אותו שרת.

לעותק מחוץ לשרת יש `bot/scripts/export.py`. הוא כולל משקל, אוכל, אימונים ושיחות, אז שומרים אותו רק במקום פרטי.

## אם משהו לא עובד

```bash
ssh root@srv123456.hstgr.cloud 'cd /docker/coach && docker compose logs --tail 50'
```
