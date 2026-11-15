"""Task-level regressions with independent, synthetic public material fixtures."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from assistant_bot.advice import compose_advice
from assistant_bot.business_intents import business_topic
from assistant_bot.knowledge import Catalog
from assistant_bot.weekly_actions import topic_for
from assistant_bot.weekly_plan import build_plan
from assistant_bot.weekly_plan import adjustment_for


class CoreScenarioTests(unittest.TestCase):
    def catalog(self, rows):
        temporary = TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / 'catalog.jsonl'
        data = [{'format':1,'mode':'case'}]
        prefixes={'article':'articles','document':'documents','glossary':'glossary','podcast':'podcasts'}
        data.extend({'title':title,'url':f'https://kurs.alfabank.ru/{prefixes[kind]}/core-test-{index}/',
                     'kind':kind,'price':0,'content_scope':scope,
                     'passages':[{'text':text}]} for index,(title,text,kind,scope) in enumerate(rows))
        path.write_text('\n'.join(json.dumps(x,ensure_ascii=False) for x in data),encoding='utf-8')
        return Catalog(path)

    def test_ads_are_not_job_ads_and_hiring_is_not_marketing(self):
        catalog = self.catalog([
            ('Как отследить эффективность рекламы','CPL — стоимость заявки. Расходы на рекламу разделите на число заявок.','article','public_text'),
            ('Как привлечь сотрудников','Укажите требования к кандидатам. Разместить вакансию на ресурсе для поиска работы.','article','public_text'),
            ('Как привлечь клиентов','Разместите рекламные объявления и собирайте заявки.','article','public_text'),
        ])
        for q in ('Как сравнить два рекламных объявления по стоимости заявки?',
                  'У двух объявлений разные затраты и число обращений. Как выбрать более выгодное?'):
            self.assertEqual(catalog.search(q).evidence[0].title,'Как отследить эффективность рекламы')
            local=compose_advice(q,catalog.search(q).evidence[0],{})
            self.assertTrue(local.local_complete)
            self.assertIn('не делите на ноль',local.answer)
        for q in ('Как найти первого сотрудника и выбрать канал поиска?', 'Нужно нанять человека. Как начать подбор?'):
            self.assertEqual(catalog.search(q).evidence[0].title,'Как привлечь сотрудников')
            self.assertEqual(topic_for(q),'hiring')
        self.assertNotEqual(business_topic('Как оформить трудовой договор с сотрудником при найме?'),'hiring')
        with_numbers='Как сравнить два рекламных объявления по стоимости заявки? Есть 6000 рублей и 20 заявок.'
        self.assertFalse(compose_advice(with_numbers,catalog.search(with_numbers).evidence[0],{}).local_complete)

    def test_cashflow_uses_readable_instructions_instead_of_podcast_description(self):
        catalog = self.catalog([
            ('Кассовый разрыв','Прибыль есть, денег нет. В подкасте рассказываем о кассовом разрыве.','podcast','description'),
            ('Как составить платёжный календарь','Платёжный календарь содержит даты платежей, суммы. Можно получать информацию о балансе и составлять прогноз. Важно регулярно обновлять календарь.','article','public_text'),
        ])
        q='Прибыль есть, но денег на платёж поставщику нет. Как проверить кассовый разрыв?'
        found=catalog.search(q)
        self.assertEqual(found.evidence[0].content_scope,'public_text')
        self.assertIn('календарь',found.evidence[0].title)
        plan=build_plan(catalog,q,{},minutes=20)
        self.assertEqual([x['artifact'] for x in plan['steps']],
                         ['payment_calendar_inputs','cash_balance_forecast','calendar_update_rule'])
        self.check_plan(plan)

    def check_plan(self,plan):
        artifacts=set();days=[]
        for step in plan['steps']:
            self.assertNotIn(step['artifact'],artifacts)
            self.assertTrue(set(step['requires'])<=artifacts)
            artifacts.add(step['artifact'])
            self.assertTrue(step['source']['excerpt'])
            self.assertTrue(step['completion'])
            self.assertTrue(all(0<s['minutes']<=20 for s in step['sessions']))
            days.extend(s['day'] for s in step['sessions'])
        self.assertEqual(days,sorted(set(days)))
        self.assertLessEqual(len(days),7)

    def test_service_plan_prepares_reply_without_pretending_it_is_published(self):
        catalog=self.catalog([
            ('Как отвечать на негативные отзывы','Конструктивная критика связана с реальными ошибками. Показать эмпатию. Извинитесь. Когда решите ситуацию, можно попросить дополнить отзыв.','article','public_text')])
        plan=build_plan(catalog,'Как ответить на негативный отзыв о долгом ожидании?',{},minutes=20)
        self.assertEqual(len(plan['steps']),3)
        self.check_plan(plan)
        draft=next(s for s in plan['steps'] if s['artifact']=='review_reply_draft')
        self.assertIn('черновик',draft['completion'])
        self.assertIn('Публикация в этот шаг не входит',draft['instruction'])
        self.assertNotIn('опубликован',draft['completion'])

    def test_new_product_test_is_not_a_franchise_or_a_sales_guarantee(self):
        catalog=self.catalog([
            ('Customer Development: как проверить спрос на продукт','Сформулируйте гипотезу. Создайте минимальную версию продукта или прототип и покажите его тем же людям. Скорректируете предположения по наблюдениям.','article','public_text'),
            ('Как выбрать франшизу кофейни','Откройте новую кофейню по франшизе.','article','public_text')])
        q='Хочу протестировать новинку в меню кофейни. Какие данные собрать?'
        self.assertIn('Customer Development',catalog.search(q).evidence[0].title)
        plan=build_plan(catalog,q,{},minutes=20)
        self.assertEqual([s['artifact'] for s in plan['steps']],
                         ['product_test_hypothesis','product_test_prototype','product_test_observation'])
        self.check_plan(plan)
        self.assertIn('ограничением выборки',plan['steps'][-1]['completion'])

    def test_template_card_does_not_become_a_read_document_or_a_weekly_plan(self):
        catalog=self.catalog([
            ('Соглашение о неразглашении','Описание шаблона соглашения о неразглашении.','document','document_description'),
            ('NDA','NDA — соглашение о неразглашении.','glossary','public_text')])
        q='Есть образец соглашения о неразглашении? Перечисли заполненные условия файла.'
        found=catalog.search(q)
        self.assertTrue(any(m.kind=='document' for m in found.recommendations))
        self.assertEqual(found.evidence[0].content_scope,'document_description')
        advice=compose_advice(q,found.evidence[0],{})
        self.assertTrue(advice.local_complete)
        self.assertIn('описание',advice.answer)
        self.assertFalse(build_plan(catalog,q,{},minutes=20)['steps'])

    def test_explicit_profit_goal_and_margin_cases_are_understood(self):
        catalog=self.catalog([('Точка безубыточности','Точка безубыточности = постоянные затраты / маржинальность.','article','public_text')])
        q='Нужна выручка в ноль при постоянных расходах 100000 рублей и доле маржи 0,4.'
        found=catalog.search(q)
        self.assertTrue(found.evidence)
        answer=compose_advice(q,found.evidence[0],{})
        self.assertTrue(answer.local_complete)
        self.assertIn('250 000',answer.answer)
        self.assertEqual(topic_for('Затраты на рекламу и продажи есть. Как проверить возврат вложений?'),'romi')
        self.assertEqual(topic_for('Как проверить интерес к услуге до запуска?'),'demand')

    def test_kpi_plan_uses_general_role_instructions_not_the_sales_example(self):
        catalog=self.catalog([
            ('Показатели эффективности менеджеров по продажам','Для менеджеров в примере порог премии 80–85%.','article','public_text'),
            ('KPI','KPI помогают оценить, как сотрудники достигают бизнес-целей. Для каждой задачи выберите 1–2 измеримых ключевых показателя. Убедитесь, что цели понятны и сотрудники считают их достижимыми.','glossary','public_text')])
        q='В статье о продавцах порог премии 80–85%. Можно ли назначить его всем ролям?'
        plan=build_plan(catalog,q,{},minutes=20)
        self.assertEqual(len(plan['steps']),3)
        self.assertEqual({s['source']['title'] for s in plan['steps']},{'KPI'})

    def test_partial_lead_cost_result_requires_the_missing_spend_not_zero(self):
        answer=adjustment_for({'subject':'cost_per_lead','completion':'Есть расходы и заявки.'},
                              'Получилось частично: заявки есть, но не найдены расходы.')
        self.assertIn('той же кампании',answer)
        self.assertIn('нулём',answer)
