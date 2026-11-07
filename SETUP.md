# Nohara Store Bot — पूरी SETUP गाइड (Render पर deploy)

## 1) फ़ाइलें कहाँ लगानी हैं

**Root फोल्डर** = `Nstore-main` के **अंदर** वाला वो फोल्डर जहाँ `package.json`, `index.html` और पुरानी `main.py` रखी है (GitHub repo की सबसे ऊपर वाली लिस्ट)। नीचे का ढाँचा हूबहू वैसा ही बनाएँ:

```
Nstore-main/                      <- ROOT फोल्डर
├── main.py                       <- REPLACE (पुरानी हटाकर नई)
├── requirements.txt              <- REPLACE
├── .env.example                  <- REPLACE
├── .python-version               <- NEW
├── render.yaml                   <- NEW
├── SETUP.md                      <- NEW (चाहें तो)
├── package.json                  <- न छुएँ
├── src/                          <- न छुएँ (React डेमो डैशबोर्ड)
└── nohara_bot/                   <- NEW फोल्डर (खुद बनाएँ)
    ├── config.py
    ├── botapi.py                 (रंगीन बटन)
    ├── i18n.py                   (18 भाषाएँ)
    ├── fonts.py
    ├── buttons.py                (बटन + रंग)
    ├── database.py
    ├── payments.py
    ├── webhooks.py
    ├── services/                 <- NEW सबफोल्डर
    │   └── core.py
    └── handlers/                 <- NEW सबफोल्डर
        ├── common.py
        ├── store.py
        ├── owner.py
        ├── registry.py
        └── master.py
```

**GitHub की वेबसाइट पर फोल्डर कैसे बनाएँ:** Add file → Create new file → नाम वाले खाने में सीधे `nohara_bot/config.py` टाइप करें (`/` लिखते ही फोल्डर बन जाता है) → कोड पेस्ट करें → Commit। बाकी फ़ाइलों के लिए भी यही तरीका, जैसे `nohara_bot/services/core.py` और `nohara_bot/handlers/store.py`। या फिर कंप्यूटर पर फोल्डर बनाकर पूरा फोल्डर drag-and-drop कर दें।

`__init__.py` की ज़रूरत **नहीं** है, Python 3.11 इनके बिना भी फोल्डर को पैकेज की तरह चलाता है।

⚠️ `.python-version` और `.env.example` dot (.) से शुरू होते हैं, इनका नाम ठीक वैसा ही रखें।
⚠️ असली `.env` फ़ाइल GitHub पर कभी push न करें। सारे secrets सिर्फ़ Render के Environment में डालें।
⚠️ पुरानी `main.py` के अलावा root में पड़ी पुरानी Python फ़ाइलें (अगर कोई हों) हटा दें, ताकि नाम न टकराएँ।

## 2) पहले ये 5 चीज़ें तैयार करें (इन्हें आगे `config.py` में भरना है)
| चीज़ | कहाँ से मिलेगी |
|---|---|
| `API_ID`, `API_HASH` | https://my.telegram.org → API development tools |
| `MASTER_BOT_TOKEN` | @BotFather → /newbot (ये आपका मुख्य "master" बॉट होगा) |
| `MONGO_URI` | MongoDB Atlas → free cluster → Database User बनाएँ → Network Access में `0.0.0.0/0` → Connect → Drivers से connection string |
| `ENCRYPTION_KEY` | कोई भी **लंबा रैंडम टेक्स्ट** (कम से कम 32, बेहतर 50 अक्षर: अंग्रेज़ी अक्षर + नंबर, बिना space)। मोबाइल पर आसान तरीका नीचे देखें |
| `ADMIN_IDS` | आपकी Telegram user ID (@userinfobot को /start करें) |

**मोबाइल से ENCRYPTION_KEY कैसे बनाएँ (कंप्यूटर नहीं चाहिए):**
- **तरीका 1 (सबसे आसान):** Chrome में किसी भी साइन-अप पेज के password वाले खाने पर टैप करें, "Suggest strong password" / "मज़बूत पासवर्ड सुझाएँ" दबाएँ। वो लंबा रैंडम टेक्स्ट दे देगा। उसे कॉपी करें। (iPhone पर भी Passwords ऐप में यही सुविधा है।)
- **तरीका 2:** खुद टाइप करें, जैसे 50 अक्षर अंग्रेज़ी अक्षर और नंबर बेतरतीब मिलाकर (बिना space), ज़्यादा लंबा हो तो और अच्छा।
- **तरीका 3 (क्लासिक key):** फ़ोन के ब्राउज़र में https://colab.research.google.com खोलें → New notebook → एक सेल में `from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())` लिखकर Run दबाएँ → नीचे आई 44 अक्षर की key कॉपी कर लें।
- ये key किसी को न बताएँ और इस चैट में भी न भेजें।

⚠️ **ENCRYPTION_KEY एक बार बनाकर कहीं सुरक्षित लिख लें** (जैसे फ़ोन के Notes या password manager में)। खो दी या बदल दी तो सेव किए हुए clone टोकन पढ़े नहीं जा सकेंगे।

## config.py में वैल्यू कैसे भरें
`nohara_bot/config.py` खोलें। सबसे ऊपर `MY_VALUES = { ... }` मिलेगा। हर लाइन में दो `""` के **बीच** अपनी वैल्यू लिखें, बाकी कुछ न बदलें:

```python
"API_ID": "1234567",
"API_HASH": "आपका_api_hash",
"MASTER_BOT_TOKEN": "123456789:AAH...",
"MONGO_URI": "mongodb+srv://user:pass@cluster0.xxxx.mongodb.net/?retryWrites=true&w=majority",
"ENCRYPTION_KEY": "50 अक्षर का लंबा रैंडम टेक्स्ट",
"ADMIN_IDS": "आपकी_telegram_user_id",
```
- `MASTER_AUDIT_CHANNEL_ID` न हो तो `"0"` रहने दें; `APP_URL` खाली रहने दें।
- वैल्यू के दोनों तरफ `"` ज़रूर रहें, और बीच में space या Enter न आए।
- GitHub पर इस फ़ाइल को Edit (पेंसिल) करके सेव और Commit करें; Render अपने आप नया deploy कर देगा। आगे कुछ बदलना हो तो यहीं बदलें।
- ⚠️ **GitHub repo PRIVATE होना चाहिए।** इस फ़ाइल में आपके असली secrets हैं। Public repo में हों तो कोई भी आपका बॉट टोकन और DB पासवर्ड देखकर चुरा सकता है। फ़ाइल कहीं शेयर न करें, चैट में भी नहीं भेजें।
- अगर कभी कोई टोकन/पासवर्ड गलती से public हो जाए तो उसे तुरंत बदल दें (@BotFather → Revoke token, MongoDB में नया पासवर्ड)।

## 3) Render पर deploy (एक ही बार में)
1. सारी फ़ाइलें GitHub repo के root में डालकर push करें।
2. Render → **New +** → **Blueprint** → अपना repo चुनें। `render.yaml` से सेटिंग अपने आप भर जाएगी।
   *(अगर Blueprint की जगह Web Service बनाएँ, तो ये सेटिंग रखें: **Language = Python 3** (Node नहीं, क्योंकि root में package.json है), Build Command = `pip install -r requirements.txt`, Start Command = `python main.py`, Health Check Path = `/healthz`, Instance Type = Free)*
3. **Render के Environment में कुछ भरने की ज़रूरत नहीं।** सारी वैल्यू आप `nohara_bot/config.py` में सबसे ऊपर बने `MY_VALUES` वाले हिस्से में भरेंगे (नीचे "config.py में वैल्यू कैसे भरें" देखें)। सिर्फ़ `PYTHON_VERSION` = `3.11.9` चाहिए, जो Blueprint से या `.python-version` फ़ाइल से अपने आप आ जाता है। (Web Service हाथ से बना रहे हों तो Environment में बस यही एक `PYTHON_VERSION` डाल दें।)

4. Deploy दबाएँ। Logs में ये लाइनें आनी चाहिए: `Web server listening on port ...`, `Master bot started: @...`, `Platform ready`.
5. **Keep-alive (ज़रूरी):** Render Free 15 मिनट बाद सो जाता है। UptimeRobot (free) में `https://आपका-app.onrender.com/healthz` को हर 5 मिनट पर ping करने के लिए लगा दें।

## 4) पहली बार इस्तेमाल

### A) Master बॉट = आपका अपना स्टोर (प्रोडक्ट यहीं बेचें)
Master बॉट (जिसका टोकन आपने `MASTER_BOT_TOKEN` में डाला) अब आपका **अपना स्टोर** है। उसका owner वो पहली user ID है जो आपने `ADMIN_IDS` में लिखी (बाकी IDs भी owner की तरह चला सकती हैं)। उसमें ये करें:
1. एक **private channel** बनाएँ, master बॉट को **Admin** बनाएँ, फिर master बॉट में `/setdbchannel -100XXXXXXXXXX`
2. प्रोडक्ट: फ़ाइल भेजें और caption में लिखें `/addproduct Course Name | 9.99 | 10`
   (कीमत USD में; आख़िरी नंबर स्टॉक है, खाली छोड़ें तो unlimited)
3. पेमेंट: `/setgateway oxapay API_KEY` और/या `/setgateway fampay UPI_ID`
4. इमेज: फोटो भेजकर caption में `/setstartimage` (नीचे इमेज वाला हिस्सा देखें)
5. सारे कमांड: `/admin` खोलकर 📖 Command Guide

Master के मेन्यू में एक **🧬 "Create Your Own Bot"** बटन भी है। कोई भी उसे दबाकर बता सकता है कि अपना बॉट कैसे बनाए: @BotFather से टोकन लेकर master बॉट में `/clone BOT_TOKEN` भेजे। (`/myclones` और `/deleteclone BOT_ID` भी वहीं चलते हैं।)

### Admin Panel (`/admin`) और फोल्डर
**Master बॉट और हर clone बॉट, दोनों में** owner `/admin` भेजे (या मेन्यू का ⚙️ बटन दबाए) तो बटनों वाला **Admin Panel** खुलता है। इसमें ओवरव्यू (प्रोडक्ट, यूज़र, ऑर्डर), सेटअप चेकलिस्ट (✅/❌) और ये सेक्शन हैं:
📦 Products (देखना और 🗑 हटाना, कन्फर्म के साथ) · 🗂 Folders · 💳 Payments (OxaPay/UPI चालू-बंद बटन) · 🖼 Images (कौन सी सेट है, हटाना) · 📡 Channels · 📢 Force-Sub · 🎨 Appearance (फ़ॉन्ट बटन से बदलें) · 👛 Wallet & Users · 📖 Command Guide · और master में 🧬 Clone Bots (सारे clone की लिस्ट)।
जिन चीज़ों में टाइप करना पड़ता है (नाम, API key), वहाँ पैनल में ठीक वही कमांड लिखा मिलता है।

**फोल्डर (कैटेगरी):** प्रोडक्ट को फोल्डर में रखें, ग्राहक पहले फोल्डर देखेगा, फिर उसके अंदर के प्रोडक्ट।
- बनाएँ: `/addfolder Courses`
- प्रोडक्ट सीधे फोल्डर में: फ़ाइल भेजकर caption में `/addproduct Python Course | 9.99 | 10 | Courses` (फोल्डर न हो तो अपने आप बन जाता है)
- पुराना प्रोडक्ट हिलाएँ: `/setfolder PRODUCT_ID FOLDER_ID` (फोल्डर से निकालने के लिए `none`)
- `/folders` लिस्ट देखें, `/delfolder FOLDER_ID` से हटाएँ (प्रोडक्ट बने रहते हैं और "Other products" में दिखते हैं)
- कोई फोल्डर न हो तो पहले की तरह सीधी प्रोडक्ट लिस्ट दिखती है।

### कमांड मेन्यू अपने आप बनता है
बॉट में "/" दबाने पर दिखने वाली कमांड की लिस्ट **अपने आप** सेट हो जाती है, @BotFather में `/setcommands` करने की ज़रूरत नहीं।
- **हर यूज़र** को: `/start`, `/changelang`, `/help` (हिंदी वालों को हिंदी विवरण के साथ)।
- **सिर्फ़ बॉट के owner** को अपनी चैट में सारे एडमिन कमांड भी दिखते हैं (`/addproduct`, `/setgateway`, `/setstartimage` ...)।
- **Master बॉट** में सबको `/clone`, `/myclones`, `/deleteclone` भी दिखते हैं।
- ये नया बॉट clone होते ही, हर Render restart पर (पुराने clone भी ठीक हो जाते हैं), और owner के `/start` भेजने पर अपने आप लगता है। Telegram किसी का निजी मेन्यू तभी लेता है जब उसने बॉट खोला हो, इसलिए owner `/start` एक बार ज़रूर भेजे।

### B) किसी का clone किया हुआ बॉट
Clone का owner अपने बॉट में वही कमांड चलाता है (`/setdbchannel`, `/addproduct`, `/setgateway`, इमेज वाले कमांड, `/help`)। पहले उसे उस बॉट को `/start` करना होगा। उसके मेन्यू में Clone बटन नहीं दिखता, सिर्फ़ master में दिखता है।

## 5) इमेज (start / banner) फ़ीचर
हर स्क्रीन पर आप अपनी इमेज लगा सकते हैं (master और clone, दोनों में)। DB channel सेट होना चाहिए। **फोटो (या GIF/वीडियो) भेजें और caption में कमांड लिखें**, या किसी फोटो पर reply करके कमांड भेजें:

| कमांड | कहाँ दिखेगी |
|---|---|
| `/setstartimage` | `/start` वेलकम स्क्रीन |
| `/setcatalogimage` | प्रोडक्ट लिस्ट और प्रोडक्ट पेज |
| `/setwalletimage` | वॉलेट स्क्रीन |
| `/setstockimage` | जब प्रोडक्ट का स्टॉक खत्म हो |
| `/setproductimage` | एक प्रोडक्ट का पेज (सेट न हो तो कैटलॉग वाली इमेज दिखती है) |
| `/setoxapayimage` | Crypto (OxaPay) पेमेंट स्क्रीन (सेट न हो तो वॉलेट वाली इमेज) |
| `/setfampayimage` | UPI / FamPay पेमेंट स्क्रीन (सेट न हो तो वॉलेट वाली इमेज) |

वही काम इन तरीकों से भी: `/setbanner start`, `/setimage catalog` (नाम: start, catalog, wallet, stock)।
हटाने के लिए: `/delstartimage`, `/delcatalogimage`, `/delwalletimage`, `/delstockimage`, `/delproductimage`, `/deloxapayimage`, `/delfampayimage` या `/delbanner NAME`।

**तैयार इमेज** `nohara_images` फोल्डर में हैं (`banner_start.png`, `banner_catalog.png`, `banner_product.png`, `banner_wallet.png`, `banner_stock.png`, `banner_oxapay.png`, `banner_fampay.png`, और `logo.png`)। हर इमेज को अपने बॉट की चैट में **Photo** (File नहीं) के रूप में भेजें और caption में ऊपर वाला कमांड लिखें, जैसे `banner_start.png` पर `/setstartimage`।
`/setwelcome आपका टेक्स्ट` से वेलकम का लिखा संदेश बदलें। इमेज के नीचे का टेक्स्ट 1000 अक्षरों तक होना चाहिए, वरना बिना इमेज के टेक्स्ट भेजा जाता है।

## 6) रंगीन बटन और प्रीमियम UI (Telegram के असली colored buttons)
बटन इमोजी से नहीं, Telegram के **आधिकारिक style** से रंगीन हैं: `primary` नीला, `success` हरा, `danger` लाल। मेन्यू के सामान्य बटन (वॉलेट, भाषा, होम, बैक) अब **हरे** हैं, मुख्य बटन नीले और cancel/स्टॉक खत्म वाले लाल। हर स्क्रीन पर आइकन, डिवाइडर लाइन, नाम के साथ अभिवादन और ट्रस्ट लाइन है। ये मैसेज Telegram के Bot API से जाते हैं (`botapi.py`), बाकी सब Hydrogram से।
- रंग देखने के लिए आपके Telegram ऐप का 2026 की शुरुआत के बाद का वर्ज़न होना चाहिए; पुराने ऐप में बटन सामान्य दिखते हैं।
- अगर Telegram कभी style रिजेक्ट करे तो बॉट अपने आप बिना रंग के भेज देता है, बॉट नहीं रुकता।
- बंद करने के लिए Render में `BUTTON_COLORS=false`।

## 7) पेमेंट कैसे काम करते हैं
- **Wallet:** owner `/addmoney USER_ID 5` से बैलेंस जोड़ता है। खरीद पर बैलेंस और स्टॉक एक साथ सुरक्षित तरीके से कटते हैं।
- **OxaPay (crypto):** बॉट खुद invoice बनाता है और callback URL अपने आप लगाता है। पहले `OXAPAY_SANDBOX=true` डालकर टेस्ट करें, फिर हटा दें।
- **UPI / FamPay:** खरीदार UPI से भुगतान करके UTR/स्क्रीनशॉट बॉट को भेजता है; owner के पास **Approve / Reject** बटन आता है।
- डिलीवरी फेल हो तो खरीदार के पैसे अपने आप उसके wallet में लौट जाते हैं।

## 8) अगर कुछ न चले
| लक्षण | वजह / उपाय |
|---|---|
| Deploy पर `[CONFIG ERROR]` | बताया गया environment variable Render में नहीं भरा |
| Build पर TgCrypto/pip error | Python 3.11.9 नहीं लगा: `PYTHON_VERSION` और `.python-version` जाँचें |
| `/setdbchannel` पर ❌ | बॉट को उस channel में Admin (post permission के साथ) बनाएँ |
| फ़ाइल डिलीवर नहीं होती | बॉट DB channel में Admin है या नहीं देखें; owner को ⚠️ मैसेज आता है |
| Owner को UPI proof नहीं मिलता | Owner ने अपने store बॉट को कभी `/start` नहीं किया |
| Crypto बटन नहीं दिखता | `/setgateway oxapay KEY` करें; `RENDER_EXTERNAL_URL` अपने आप होता है |
| Restart के बाद clone नहीं उठा | `ENCRYPTION_KEY` बदल गई है |

## 9) ईमानदार नोट्स
- Master backup mirror हटा दिया है; master channel में सिर्फ़ metadata का audit log जाता है।
- भाषाओं में मुख्य 16 स्ट्रिंग्स 18 भाषाओं में पूरी हैं; नए संदेश English और Hindi में हैं, बाकी में English दिखेगा।
- ये कोड असली Telegram/OxaPay पर चलाकर टेस्ट नहीं हुआ है। पहली बार एक टेस्ट बॉट और OxaPay sandbox से पूरा फ्लो ज़रूर आज़माएँ।
