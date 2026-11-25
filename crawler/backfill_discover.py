import argparse, json, re, time
from collections import Counter
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser
import requests
from bs4 import BeautifulSoup
from backfill_history import identity, ROOT, DATA_DIR, parse_history, record_key, export_csv, write_google_sheets

VERSION = 'V5.4.2.4'
BASE = 'https://conenmua.vn'
UA = 'iPhonePriceTracker/1.0'
SHOPS = {'thế giới di động':'TGDD','cellphones':'CPS','viettel store':'VTS','fpt shop':'FPT','hoàng hà mobile':'HHM','di động việt':'DDV','shopdunk':'SDK'}
MODEL = r'iphone\s+(?:\d{1,2}(?:e|\s+(?:pro max|pro|plus|mini|air|duo))?|air|se(?:\s+\d{4})?)'
BAD = r'\b(?:cũ|likenew|like new|đổi trả|thu cũ|trưng bày|quốc tế|lock|refurbished|99%|98%|95%)\b'

def discover(html, model, targets):
    soup = BeautifulSoup(html,'html.parser')
    found = {}
    rejected = Counter()
    for a in soup.select('li a[href^="/san-pham/"]'):
        li = a.find_parent('li'); text = li.get_text(' ',strip=True)
        ident = identity(a.get_text(' ',strip=True))
        if not ident or ident[0]!=model:
            rejected['identity'] += 1; continue
        if re.search(BAD,text.lower()):
            rejected['condition'] += 1
            print('SKIP CONDITION', a.get_text(' ',strip=True), '|', text[:300]); continue
        shops = [v for k,v in SHOPS.items() if 'bán trên '+k in text.lower()]
        if len(shops)!=1:
            rejected['retailer'] += 1; continue
        if (*ident,shops[0]) not in targets:
            print('HISTORY ONLY', *ident, shops[0])
        path = a['href']; m = re.fullmatch(r'/san-pham/(\d+)/[\w-]+',path)
        if m: found[m[1]] = {'id':m[1],'url':BASE+path,'model':ident[0],'storage':ident[1],'retailer':shops[0],'file':'backfill_'+m[1]+'.html'}
    print('LINKS:', len(soup.select('a[href*="/san-pham/"]')), 'REJECTED:', dict(rejected))
    return list(found.values())

class Fetcher:
    def __init__(self):
        self.s = requests.Session(); self.s.headers['User-Agent']=UA
        r=self.s.get(BASE+'/robots.txt',timeout=30,allow_redirects=False)
        if r.status_code!=200: raise RuntimeError('STOP: robots unavailable')
        self.rp=RobotFileParser(); self.rp.parse(r.text.splitlines())
        rate=self.rp.request_rate(UA); self.delay=max(5,self.rp.crawl_delay(UA) or 0,rate.seconds/rate.requests if rate and rate.requests else 0)
    def get(self,url):
        if urlsplit(url).netloc!='conenmua.vn' or not self.rp.can_fetch(UA,url): raise RuntimeError('STOP: robots or host restriction '+url)
        time.sleep(self.delay); r=self.s.get(url,timeout=30,allow_redirects=False)
        if r.status_code==404: return None
        if r.status_code!=200: raise RuntimeError('STOP HTTP '+str(r.status_code)+' '+url)
        return r.text

def main():
    p=argparse.ArgumentParser(); p.add_argument('--listing-html', type=Path); p.add_argument('--model'); p.add_argument('--limit',type=int,default=10); p.add_argument('--days',type=int,default=90); p.add_argument('--write',action='store_true'); p.add_argument('--permission-confirmed',action='store_true'); a=p.parse_args()
    if not 1<=a.days<=90 or a.limit<1: p.error('invalid days/limit')
    if not a.permission_confirmed: p.error('Confirm source permits collection before running: --permission-confirmed')
    data=json.loads((ROOT/'docs/data.json').read_text(encoding='utf-8'))
    targets=set()
    for r in data['current']:
        ident=identity(str(r.get('model',''))+' '+str(r.get('storage','')))
        if ident and r.get('retailer') in SHOPS.values(): targets.add((*ident,r['retailer']))
    models=sorted({x[0] for x in targets if not a.model or x[0]==a.model.lower().strip()})
    if not models: raise RuntimeError('NO MATCHING MODELS')
    if a.listing_html and (len(models)!=1 or a.write): p.error('--listing-html requires one model and preview')
    print('VERSION:', VERSION)
    print('TARGETS:', sorted(x for x in targets if x[0] in models))
    DATA_DIR.mkdir(parents=True,exist_ok=True); fetch=Fetcher(); candidates=[]
    for model in models:
        url=BASE+'/gia/'+model.replace(' ','-')+'?edition=official'
        html=a.listing_html.read_text(encoding='utf-8-sig') if a.listing_html else fetch.get(url)
        if html is None: print('NOT FOUND',model); continue
        (DATA_DIR / ('listing_'+model.replace(' ','-')+'.html')).write_text(html,encoding='utf-8')
        print('LISTING:',url,'SIZE:',len(html),'TITLE:',BeautifulSoup(html,'html.parser').title)
        rows=discover(html,model,targets); candidates.extend(rows); print('DISCOVERED',model,len(rows))
    groups={c['id']:c for c in candidates}
    selected=list(groups.values())[:a.limit]; records=[]
    for c in selected:
        html=fetch.get(c['url'])
        if html is None: print('NOT FOUND',c['url']); continue
        (DATA_DIR/c['file']).write_text(html,encoding='utf-8')
        try: rows=parse_history(c['retailer'],c,a.days)
        except ValueError as e: print('SKIP',c['id'],e); continue
        records.extend(rows); print('HISTORY',c['retailer'],c['model'],c['storage'],len(rows))
    records=list({record_key(r):r for r in records}.values())
    print('CANDIDATES:',len(candidates),'GROUPS:',len(groups),'ATTEMPTED:',len(selected),'RECORDS:',len(records))
    if not records: raise RuntimeError('NO VALID HISTORY: inspect TARGETS, LISTING and REJECTED above')
    export_csv(records)
    if a.write: write_google_sheets(records)
    print('BACKFILL SUCCESS')

if __name__=='__main__': main()
