"""Offline retrieval and plan checks; does not call a model or Telegram."""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from assistant_bot.advice import catalog_context, compose_advice
from assistant_bot.knowledge import Catalog
from assistant_bot.weekly_plan import build_plan

SOURCES={
 'K01':r'инфлюенс|romi','K02':r'эффективност.*реклам','K03':r'повторн.*продаж',
 'K04':r'отзыв','K05':r'ресторан в цифрах|безубыточ|финансовый анализ',
 'K06':r'плат[её]жный календар','K07':r'как составить бизнес-план',
 'K08':r'customer development','K09':r'customer development',
 'K10':r'привлечь сотрудник','K11':r'почему сотрудники.*увольня',
 'K12':r'эффективност.*менеджер','K14':r'соглашение.*неразглашени',
 'K15':r'эффективност.*реклам',
}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog',type=Path,required=True)
    parser.add_argument('--report',type=Path)
    args=parser.parse_args();catalog=Catalog(args.catalog)
    cases=json.loads((ROOT/'evaluation/core15.json').read_text(encoding='utf-8'))
    records=[]
    for case in cases:
        for variant in ('question','paraphrase'):
            q=case[variant];result=catalog.search(q,catalog_context(case['profile']))
            plan=build_plan(catalog,q,case['profile'],obstacle=case.get('obstacle',''),minutes=case['minutes'])
            checks={}
            if case.get('expected')=='clarification':
                checks.update(clarification=result.needs_clarification,no_invented_plan=not plan['steps'])
            else:
                checks['source']=bool(result.evidence and re.search(SOURCES[case['id']],result.evidence[0].title,re.I))
                if case.get('expected')=='scope_limit':
                    checks.update(description_only=bool(result.evidence and result.evidence[0].content_scope in {'description','document_description'}),
                                  document_card=any(m.kind=='document' for m in result.recommendations),no_invented_plan=not plan['steps'])
                else:
                    checks.update(topic=plan.get('topic')==case['topic'],substantive_steps=3<=len(plan['steps'])<=5)
                    artifacts=set();days=[];ordered=True;grounded=True;time_ok=True
                    for step in plan['steps']:
                        ordered &= set(step['requires'])<=artifacts and step['artifact'] not in artifacts
                        artifacts.add(step['artifact'])
                        grounded &= bool(step['source'].get('excerpt'))
                        for session in step['sessions']:
                            days.append(session['day']);time_ok &= 0<session['minutes']<=case['minutes']
                    checks.update(dependencies=ordered,sourced_steps=grounded,time_bounds=time_ok and len(days)<=7,
                                  days=days==sorted(set(days)))
            passed=all(checks.values())
            records.append({'id':case['id'],'variant':variant,'passed':passed,'checks':checks,
                            'titles':[e.title for e in result.evidence],
                            'step_artifacts':[s['artifact'] for s in plan['steps']]})
            if not passed:print(case['id'],variant,{k:v for k,v in checks.items() if not v})
    summary={'passed':sum(r['passed'] for r in records),'total':len(records),'model_calls':0,
             'meaning':'Offline source relevance and plan invariants; not generated-answer factual accuracy'}
    if args.report:args.report.write_text(json.dumps({'summary':summary,'records':records},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary));return 0 if summary['passed']==len(records) else 1

if __name__=='__main__':raise SystemExit(main())
