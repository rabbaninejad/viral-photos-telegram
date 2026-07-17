# راه‌اندازی خودکار

این ریپازیتوری هر ۳۰ دقیقه یک عکس وایرال جدید (بدون تکرار) از Unsplash (و در صورت وجود، Pexels) به ربات تلگرام ارسال می‌کند.

## سکرت‌های لازم
در Settings → Secrets and variables → Actions:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `UNSPLASH_ACCESS_KEY`
- `PEXELS_API_KEY` (اختیاری)

## جلوگیری از تکرار
فایل `sent_ids.json` شناسه‌ی هر عکس ارسال‌شده را نگه می‌دارد و بعد از هر ارسال خودش commit می‌شود.
