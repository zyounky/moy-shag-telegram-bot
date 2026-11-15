"""Shared task recognition for retrieval and local planning."""
import re
from .answer_policy import staff_retention_intent

SEARCHES = {
    "romi": "ROMI",
    "ad_measure": "Как отследить эффективность рекламы",
    "opening": "Как составить бизнес-план пошаговая инструкция",
    "repeat": "Как увеличить повторные продажи",
    "retention": "Почему сотрудники увольняются",
    "cashflow": "Платёжный календарь",
    "service": "Как отвечать на негативные отзывы",
    "hiring": "Как привлечь сотрудников",
    "product_test": "Customer Development",
    "demand": "Customer Development",
    "ad_compare": "CPL",
    "kpi": "KPI",
}


def business_topic(text):
    text = re.sub(r"\s+", " ", text.casefold().replace("ё", "е"))
    if (re.match(r"^(?:что такое|что значит|что означает|определение|расшифруй|what is)\b", text)
            and not re.search(r"(?:[.!?]\s*|\bи\s+)как\b|\band how\b", text)):
        return ""
    if staff_retention_intent(text):
        return "retention"
    if re.search(r"бизнес[ -]?план", text):
        return "opening"
    if re.search(r"реклам|кампани", text) and re.search(r"окупаем|окупит|возврат вложен|\bromi\b", text):
        return "romi"
    if (re.search(r"повторн\w*.{0,15}(?:покуп|продаж|заказ)|возвраща\w*", text)
            and re.search(r"клиент|покупател|покуп|продаж|заказ", text)):
        return "repeat"
    if re.search(r"кассов\w* разрыв|прибыл\w*.{0,65}(?:денег нет|не хватает денег)|денег.{0,35}(?:платеж|поставщик)", text):
        return "cashflow"
    if re.search(r"отзыв|жалоб|пожаловал", text) and re.search(r"негатив|плох|жалоб|пожаловал|очеред|ожидани", text):
        return "service"
    if (re.search(r"найти|наня\w*|нанима\w*|найм|подбор|поиск|искать|подобра", text)
            and re.search(r"сотрудни|кандидат|работник|персонал|человек|людей", text)
            and not re.search(r"оформ|трудов\w* договор|штраф|налог|увол", text)):
        return "hiring"
    if re.search(r"тест|провер|спрос|прототип", text) and re.search(r"нов\w* (?:напит|позиц|товар|продукт|услуг)|новинк|нов\w*.{0,15}меню", text):
        return "product_test"
    if re.search(r"(?:провер|измер|оцен|тест)\w*.{0,35}(?:спрос|иде[юя]|интерес|потребност)|custdev|customer development", text):
        return "demand"
    if (re.search(r"реклам|объявлен", text) and re.search(r"сравн|выгод|стоимост|затрат", text)
            and re.search(r"заяв|обращен|\bcpl\b", text) and not re.search(r"ваканси|кандидат", text)):
        return "ad_compare"
    if re.search(r"реклам|кампани", text) and re.search(r"оцен|измер|провер|отслед", text) and re.search(r"эффективност|результат|эффект", text):
        return "ad_measure"
    if re.search(r"\bkpi\b|\bкпэ\b|порог преми|ключев\w* показател", text) and re.search(r"сотрудни|персонал|роль|ролям|кладовщик|всем", text):
        return "kpi"
    return ""


def applicable_business_source(topic, title, question):
    title = re.sub(r"\s+", " ", title.casefold().replace("ё", "е"))
    if topic == "hiring":
        return bool(re.search(r"сотрудни|персонал|найм|подбор|рекрут|\bhr\b", title)
                    and not re.search(r"уволь|ответственност|оформ|отпуск", title))
    if topic == "service" and "маркетплейс" in title and not re.search(r"маркетплейс|ozon|wildberries", question, re.I):
        return False
    return True
