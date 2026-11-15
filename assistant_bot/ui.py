"""English copy for Telegram screens and controls. Catalog content is separate."""

from html import escape


def button(label: str, callback: str) -> dict:
    return {"text": label, "callback_data": callback}


def link(label: str, url: str) -> dict:
    return {"text": label, "url": url}


def html_text(value) -> str:
    return escape(str(value), quote=False)

ENGLISH = {
    "План на неделю": "Weekly plan",
    "Составить план на неделю": "Make a weekly plan",
    "15 минут": "15 minutes",
    "30 минут": "30 minutes",
    "60 минут": "60 minutes",
    "Предложение плана на неделю": "Your proposed weekly plan",
    "План на паузе": "Plan paused",
    "План отменён": "Plan cancelled",
    "План завершён": "Plan complete",
    "Цель": "Goal",
    "Выполнено {done} из {total} действий.": "{done} of {total} actions complete.",
    "День {day}": "Day {day}",
    "Ожидает": "To do",
    "Проверьте результат": "Check the result",
    "Нужна доработка": "Needs more work",
    "Отложено": "Deferred",
    "Выполнено": "Complete",
    "Материалов хватило на часть плана. Остальное можно уточнить отдельным вопросом.": "The available material supports part of this plan. You can ask about the remaining details.",
    "Начать план": "Start plan",
    "Отказаться": "Decline",
    "Продолжить план": "Resume plan",
    "Отменить план": "Cancel plan",
    "Приостановить план": "Pause plan",
    "Ближайшее действие": "Next action",
    "Открыть действие": "Open action",
    "Продолжить действие": "Resume action",
    "Начать действие": "Start action",
    "Посмотреть новый план": "See the new plan",
    "К плану": "Back to plan",
    "Зачем": "Why it matters",
    "Что сделать": "What to do",
    "Примерно {minutes} мин.": "About {minutes} min.",
    "Как получилось?": "How did it go?",
    "Получилось": "It worked",
    "Получилось частично": "It partly worked",
    "Не получилось": "It didn't work",
    "Отложить": "Do later",
    "Доработка": "Adjustment",
    "Доработать действие": "Try a smaller version",
    "Следующее действие": "Next action",
    "Ваш результат": "Your result",
    "Подходящий материал": "Suggested material",
    "Все действия плана пройдены.": "You've completed all the actions in your plan.",
    "Подходящего нового материала пока нет.": "I couldn't find another relevant resource yet.",
    "Можно составить недельный план по последнему совету.": "You can turn your latest suggestion into a weekly plan.",
    "Задайте вопрос о бизнесе, чтобы составить план на неделю.": "Ask a business question to make a weekly plan.",
    "Этот совет уже недоступен. Откройте план или задайте новый вопрос.": "This suggestion is no longer available. Open your plan or ask another question.",
    "Сколько времени вы готовы выделять в день? Можно ввести свой ответ.": "How much time can you spend each day? You can type your own answer.",
    "Пока не знаю": "I'm not sure yet",
    "Для плана пока мало подходящих материалов.": "I don't have enough relevant material to make a plan yet.",
    "Сначала зафиксируйте, каких данных не хватает. Используйте один доступный показатель или соберите недостающее значение.": "List the information you're missing first. Use one metric you can access or collect the missing information.",
    "Возьмите один пример из действия и проверьте его за один короткий подход.": "Pick one example and work through it in a short session.",
    "Сузьте действие до одного примера. Сверьте его с критерием выполнения и запишите, чего ещё не хватает.": "Try one example, compare it with the completion criterion, and write down what's still missing.",
    "Коротко опишите результат. Можно указать общие числа, например число заявок.": "Briefly describe what happened. You can include totals such as the number of leads.",
    "Что помешало? Напишите коротко, чтобы подобрать доработку.": "What got in the way? A brief note can help me suggest a smaller step.",
    "Напишите только короткий итог и общие числа, без личных данных.": "Enter a short summary and aggregate figures only. Leave out personal information.",
    "Сначала запишите результат или пропустите этот вопрос.": "Record the result or skip this question first.",
    "Укажите разумное время в день, например «30 минут».": "Enter how much time you have per day, for example “30 minutes”.",
    "Эта кнопка относится к предыдущему плану.": "That button belongs to an earlier plan.",
    "Эта кнопка относится к предыдущему действию.": "That button belongs to an earlier action.",
    "Действие отложено. Вернитесь к нему, когда будет удобно.": "Action deferred. Return to it when you have time.",
    "Предложение плана закрыто.": "Plan suggestion dismissed.",
    "У вас уже есть план или задача. Заменить их новым планом?": "You have a plan or task in progress. Replace it with the new plan?",
    "Заменить и начать": "Replace and start",
    "Заменить план": "Replace plan",
    "Начать эту задачу вместо текущего плана?": "Start this task in place of your current plan?",
    "Напоминания по действию выключены. После продолжения их можно поставить снова.": "Action reminders are off. You can set them again when you resume.",
    "Эта кнопка относится к предыдущему напоминанию.": "That button belongs to an earlier reminder.",
    "У этого действия уже есть напоминание.": "This action already has a reminder.",
    "Настройка напоминания уже открыта. Продолжите её или введите /cancel.": "Reminder setup is already open. Continue it or enter /cancel.",
    "Открыть итог": "View result",
    "Опишите задачу без персональных сведений.": "Describe your task without personal details.",
    "Сначала откройте новое предложение и подтвердите замену.": "Open the new plan and confirm the replacement again.",
    "Записать результат": "Record result",
    "Уберите ФИО, адреса и личные документы из сообщения. Опишите бизнес-задачу без персональных данных.": "Remove names, addresses and personal documents. Describe the business question without personal data.",
    "Сейчас много запросов. Дождитесь ответа на предыдущие сообщения и попробуйте ещё раз.": "There are too many requests. Wait for your previous replies and try again.",
    "Задать вопрос": "Ask a question",
    "Моя задача": "My task",
    "Напоминания": "Reminders",
    "Мой бизнес": "My business",
    "Выбрать сферу": "Choose an industry",
    "Выберите сферу бизнеса.": "Choose your industry.",
    "Убрать сферу": "Clear industry",
    "Помощь": "Help",
    "Что я умею": "What I can do",
    "Язык": "Language",
    "Назад в меню": "Back to menu",
    "Планирую открыть": "Planning to open",
    "Уже работаю": "Already operating",
    "Расширяюсь": "Expanding",
    "Пропустить": "Skip",
    "Очистить профиль": "Clear business profile",
    "Выберите этап бизнеса. Сферу можно указать в следующем вопросе.": "Choose your business stage. You can mention your industry in your next question.",
    "Выберите этап бизнеса или расскажите подробнее.": "Choose your business stage or tell me more about your question.",
    "Какой результат нужен сейчас? Например, проверить спрос, привлечь клиентов или разобраться в учёте.": "What are you trying to achieve? For example, test demand, find customers, or sort out your bookkeeping.",
    "Этап бизнеса сохранён. Напишите, с чем помочь.": "Business stage saved. What can I help you with?",
    "Профиль бизнеса очищен.": "Business profile cleared.",
    "Пока пусто": "Nothing saved yet",
    "готовится к запуску": "preparing to launch",
    "уже работает": "already operating",
    "развивается": "growing",
    "Кофейня": "Coffee shop",
    "Кафе": "Café",
    "Общепит": "Food service",
    "Розница": "Retail",
    "Маркетплейсы": "Marketplaces",
    "Услуги": "Services",
    "Студия": "Studio",
    "число заказов": "order count",
    "число звонков": "call count",
    "число броней": "booking count",
    "число заявок": "lead count",
    "число визитов": "visit count",
    "Демонстрационный каталог. Источники учебные.": "Demo catalog. These sources are examples.",
    "В доступных материалах «Альфа-Курса» я не нашёл подтверждённого ответа. Попробуйте уточнить задачу, например указать сферу бизнеса или что уже пробовали.": "I couldn't find a supported answer in the Alfa Course materials. Try mentioning your business sector or what you've already tried.",
    "В найденных материалах недостаточно сведений для уверенного ответа.": "The sources I found don't provide enough information for a reliable answer.",
    "Уточните, какой результат хотите получить.": "What would you like to achieve?",
    "Сейчас доступна подборка материалов; консультация модели для этого чата не активна.": "I can show relevant materials. AI consultation is currently unavailable in this chat.",
    "Внешняя модель": "External AI",
    "Дать согласие": "Allow external AI",
    "Продолжить без модели": "Continue without AI",
    "Отозвать согласие": "Withdraw consent",
    "Настройка согласия доступна в публичном режиме.": "Consent settings are available in public mode.",
    "Настройки согласия временно недоступны.": "Consent settings are temporarily unavailable.",
    "Кнопка устарела. Откройте настройки через /privacy.": "This button has expired. Open settings with /privacy.",
    "Согласие сохранено. Следующий подходящий вопрос можно отправить модели.": "Consent saved. Your next eligible question can be sent to the model.",
    "Внешняя модель отключена для этого чата. Поиск, план и напоминания доступны.": "External AI is off for this chat. Search, plans and reminders are still available.",
    "Для консультации {provider} получает ваш вопрос и относящиеся к нему уточнения, этап, сферу и цель бизнеса, описание препятствия и доступные фрагменты «Альфа-Курса». Модель использует их, чтобы ответить по источникам. Согласие можно отозвать через /privacy.": "To answer from sources, {provider} receives your question and relevant clarifications, business stage, sector and goal, a description of any obstacle, and available Alfa Course excerpts. You can withdraw consent with /privacy.",
    "Общий лимит консультаций исчерпан. Ниже материалы и шаг из локального каталога.": "The overall AI consultation limit has been reached. Here are materials and a next step from the local catalogue.",
    "Ваш лимит консультаций исчерпан. Ниже материалы и шаг из локального каталога.": "Your AI consultation limit has been reached. Here are materials and a next step from the local catalogue.",
    "Недавно была консультация. Пока можно воспользоваться материалами и шагом из локального каталога.": "There was a recent AI consultation. For now, you can use these materials and a next step from the local catalogue.",
    "Все места для консультаций заняты. Ниже материалы и шаг из локального каталога.": "All AI consultation slots are busy. Here are materials and a next step from the local catalogue.",
    "Сейчас доступен ответ из локального каталога.": "Here is an answer from the local catalogue.",
    "Внешняя консультация сейчас недоступна. Ниже материалы и шаг из локального каталога.": "External AI is currently unavailable. Here are materials and a next step from the local catalogue.",
    "Сейчас много запросов. Дождитесь ответа на предыдущие сообщения и попробуйте ещё раз.": "The bot is busy. Wait for your earlier messages to finish and try again.",
    "Не удалось подготовить ответ. Попробуйте ещё раз; подходящие материалы доступны ниже.": "I couldn't prepare an answer. You can try again or open the relevant materials below.",
    "Учёл контекст": "Business context",
    "Уточнение к шагу": "What's in the way",
    "Что нашёл в «Альфа-Курсе»": "What I found in Alfa Course",
    "По вашей задаче": "About your question",
    "Источники": "Sources",
    "Источник": "Source",
    "Что сделать дальше": "What to do next",
    "Как проверить результат": "How to check the result",
    "Источник может описывать правила за прошлый период.": "The source may describe rules for an earlier period.",
    "Поддерживаются типовые вопросы на английском. Материалы курса и локальные советы могут быть на русском.": "Common business questions are supported in English. Course materials and offline advice may be in Russian.",
    "Открыть источник": "View source",
    "Открыть материал": "Open material",
    "Подробнее о курсе": "View course",
    "Бесплатно": "Free",
    "Материал": "Material",
    "Подходит к текущему этапу бизнеса.": "Matches your current business stage.",
    "Есть материал для вашей сферы.": "Relevant to your business sector.",
    "Подходит к теме запроса.": "Relevant to your question.",
    "Начать задачу": "Start task",
    "Задайте вопрос, чтобы выбрать первый шаг.": "Ask a question to find your first step.",
    "Совет": "Suggested",
    "Последний совет": "Latest suggestion",
    "К этому совету можно вернуться через «Моя задача».": "You can find this suggestion again under My task.",
    "Откройте «Моя задача» → «Последний совет», чтобы вернуться к этому совету.": "Open My task → Latest suggestion to find this advice again.",
    "Нажмите «Начать задачу», чтобы поставить напоминание или отметить выполнение.": "Tap Start task to set a reminder or mark it done.",
    "В работе": "In progress",
    "Выполнен": "Done",
    "Нужна помощь": "Needs attention",
    "Сделано": "Done",
    "Не получается": "I'm stuck",
    "Напомнить": "Remind me",
    "У вас уже есть шаг в работе. Заменить его новым?": "You already have a step in progress. Replace it with this one?",
    "Заменить задачу": "Replace task",
    "Оставить как есть": "Keep this task",
    "Это действие относится к предыдущему шагу.": "That button belongs to an earlier step.",
    "Шаг выполнен. Можно задать новый вопрос или открыть исходный материал.": "Step completed. Ask another question or open the source.",
    "Что помешало выполнить шаг? Напишите одним сообщением, и я попробую найти материал под это препятствие.": "What got in the way? Tell me in one message and I'll look for relevant material.",
    "Действие отменено.": "Canceled.",
    "О чём напомнить?": "What should I remind you about?",
    "Учёба": "Learning",
    "Работа": "Work",
    "Когда напомнить? Часовой пояс — <b>{zone}</b>. Введите дату и время, например <code>{example}</code>.": "When should I remind you? Your time zone is <b>{zone}</b>. Enter a date and time, for example <code>{example}</code>.",
    "Через минуту": "In one minute",
    "Завтра в 19:00": "Tomorrow at 19:00",
    "Часовой пояс": "Time zone",
    "Отмена": "Cancel",
    "Москва": "Moscow",
    "Екатеринбург": "Yekaterinburg",
    "Новосибирск": "Novosibirsk",
    "Владивосток": "Vladivostok",
    "Это время уже прошло. Укажите будущее время.": "That time has passed. Choose a time in the future.",
    "Как часто напоминать?": "How often should I remind you?",
    "один раз": "once",
    "ежедневно": "daily",
    "еженедельно": "weekly",
    "Поменять время": "Change time",
    "Введите короткое название.": "Enter a short title.",
    "Проверьте дату и время. Формат ДД.ММ.ГГГГ ЧЧ:ММ.": "Check the date and time. Use DD.MM.YYYY HH:MM.",
    "Выберите вариант кнопкой или введите /cancel.": "Tap an option or type /cancel.",
    "Активных напоминаний нет.": "You have no active reminders.",
    "Добавить напоминание": "Add a reminder",
    "Мои напоминания": "My reminders",
    "Напоминание №{id}": "Reminder #{id}",
    "К напоминаниям": "View reminders",
    "Изменить частоту №{id}": "Change frequency #{id}",
    "Отключить №{id}": "Turn off #{id}",
    "Эта настройка уже закрыта. Откройте /remind, чтобы создать напоминание.": "This setup is closed. Use /remind to create a reminder.",
    "Создание отменено.": "Reminder setup canceled.",
    "Введите название. Например, «Дочитать материал».": "Enter a title, for example “Finish reading the article”.",
    "Введите название. Например, «Отправить отчёт».": "Enter a title, for example “Send the report”.",
    "Выберите часовой пояс.": "Choose a time zone.",
    "Подтвердите напоминание": "Confirm reminder",
    "Сохранить": "Save",
    "Это время уже прошло. Укажите новое.": "That time has passed. Choose a new time.",
    "Напоминание №{id} создано. Ближайшая отправка — {date}.": "Reminder #{id} created. Next reminder: {date}.",
    "Напоминание отключено.": "Reminder turned off.",
    "Напоминание уже отключено.": "This reminder is already off.",
    "Напоминание уже завершено или отключено.": "This reminder has ended or been turned off.",
    "Как часто напоминать?\n«{title}»": "How often should I remind you?\n“{title}”",
    "Частота обновлена — {frequency}. Ближайшая отправка — {date}.": "Frequency updated to {frequency}. Next reminder: {date}.",
    "Напоминание": "Reminder",
    "Ответ формируется…": "Preparing your answer…",
    "Я не распознал команду. Доступные команды — в /help.": "I don't recognize that command. Use /help to see what's available.",
}


def text(language, phrase, **values):
    message = ENGLISH.get(phrase, phrase) if language == "en" else phrase
    return message.format(**values) if values else message


SCREENS = {
    "ru": {
        "welcome": "<b>Мой шаг · помощник «Альфа-Курса»</b>\n"
                   "Напишите, с чем нужна помощь в бизнесе. Я подберу материал и предложу один следующий шаг.",
        "example": "Например, «Как оценить эффективность рекламы кофейни?»",
        "ask": "Напишите вопрос о бизнесе. Добавьте, что уже пробовали или какого результата хотите достичь.",
        "help": "<b>Помощь и команды</b>\n"
                "/start или /menu — главное меню\n"
                "/step — открыть мою задачу\n"
                "/plan — открыть план на неделю\n"
                "/profile — настройки моего бизнеса\n"
                "/remind — создать напоминание\n"
                "/reminders — открыть напоминания\n"
                "/cancel — отменить текущий ввод\n"
                "/language — сменить язык\n"
                "/about — возможности бота\n"
                "/help — эта справка",
        "about": "<b>Что умеет «Мой шаг»</b>\n"
                 "• Находит материалы «Альфа-Курса» по вашему вопросу и показывает источник.\n"
                 "• Учитывает этап и сферу бизнеса, предлагает один шаг и по возможности критерий результата.\n"
                 "• Помогает вернуться к шагу, если вы застряли.\n"
                 "• Напоминает о выбранном шаге, обучении или бизнес-событии.\n"
                 "• Помогает составить план на неделю и проверить итог действий.",
        "current_step": "<b>В работе</b>\n{step}",
    },
    "en": {
        "welcome": "<b>My Next Step · Alfa Course assistant</b>\n"
                   "Tell me what you need help with. I'll look for relevant course material and suggest one next step.",
        "example": "For example, “How do I measure ad performance for my coffee shop?”",
        "ask": "Ask a business question. Mention what you've tried or what you want to achieve.",
        "help": "<b>Help and commands</b>\n"
                "/start or /menu — main menu\n"
                "/step — open your task\n"
                "/plan — open your weekly plan\n"
                "/profile — your business details\n"
                "/remind — create a reminder\n"
                "/reminders — view reminders\n"
                "/cancel — cancel the current input\n"
                "/language — change language\n"
                "/about — what I can do\n"
                "/help — this help screen",
        "about": "<b>What My Next Step can do</b>\n"
                 "• Find relevant Alfa Course material and show the source.\n"
                 "• Use your business stage and sector to suggest a next step and, when possible, a way to check the result.\n"
                 "• Help you get back on track when you're stuck.\n"
                 "• Remind you about your next step, learning, or a business event.\n"
                 "• Help you make a weekly plan and review what worked.",
        "current_step": "<b>In progress</b>\n{step}",
    },
}
