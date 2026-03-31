import sys

if len(sys.argv) < 2:
    print("\n📱 Простой способ получить Chat ID:\n")
    print("1️⃣ Найди в Telegram бота: @userinfobot")
    print("2️⃣ Нажми START")
    print("3️⃣ Бот сразу покажет твой Chat ID\n")
    print("Или используй альтернативный способ:\n")
    print("1️⃣ Найди в Telegram бота: @RawDataBot")
    print("2️⃣ Отправь ему /start")
    print("3️⃣ Бот покажет полную информацию, включая Chat ID\n")
    print("💡 Затем обнови .env файл с полученным Chat ID")
else:
    bot_token = sys.argv[1]
    print(f"\n🔍 Проверяю токен бота...\n")
    print(f"✅ Инструкции для получения Chat ID:\n")
    print(f"1. Напиши своему боту ЛЮБОЕ сообщение")
    print(f"2. Открой в браузере:")
    print(f"   https://api.telegram.org/bot{bot_token}/getUpdates\n")
    print(f"3. Найди строку: \"chat\":{{\"id\":12345678}}")
    print(f"4. Число 12345678 - это твой Chat ID\n")
