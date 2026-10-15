#!/usr/bin/env python3
"""Check manuscript outputs after prepare.py and Quarto rendering (requires Poppler)."""
import csv
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import subprocess

HERE=Path(__file__).resolve().parent
class Document(HTMLParser):
    def __init__(self):
        super().__init__();self.ids=set();self.images=[];self.links=[];self.text=[]
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if 'id' in a:self.ids.add(a['id'])
        if tag=='img':self.images.append(a)
        if tag=='a':self.links.append(a.get('href',''))
    def handle_data(self,data):self.text.append(data)

def validate():
    for f in ['manuscript.qmd','references.bib','manuscript.html','manuscript.pdf','data/evidence.json','data/provenance.json','data/crosswalk.csv','generated/statistics.json']:
        assert (HERE/f).is_file(),f'Missing {f}'
    source=(HERE/'manuscript.qmd').read_text();bib=(HERE/'references.bib').read_text()
    keys=set(re.findall(r'@\w+\{([^,]+),',bib))
    cited=set(re.findall(r'@([a-z]+\d{4})',source))
    assert keys==cited and len(keys)==10,(keys,cited)
    doc=Document();doc.feed((HERE/'manuscript.html').read_text())
    assert all('ref-'+k in doc.ids for k in keys)
    assert all(x in doc.ids for x in ['tbl-topics','tbl-periods','tbl-comparison','fig-trends','fig-network','fig-overlap'])
    assert len(doc.images)==3 and all(i.get('src','').startswith('data:image/') and i.get('alt') for i in doc.images)
    assert all(h[1:] in doc.ids for h in doc.links if h.startswith('#') and len(h)>1)
    assert 'Unable to resolve' not in (HERE/'manuscript.html').read_text()
    text=subprocess.check_output(['pdftotext',str(HERE/'manuscript.pdf'),'-'],text=True)
    assert '??' not in text and not re.search(r'@(fig-|tbl-|[a-z]+\d{4})',text)
    assert all(f'Figure {n}:' in text for n in [1,2,3])
    assert all(f'Table {n}:' in text for n in [1,2,3])
    assert all(s in text for s in ['Hartley','Sandler','Arce','Kollias','Gaibulloev','Solomon','Delahais','Thyrard'])
    assert '3,097' in text and '1,152' in text and '60.4%' in text
    with (HERE/'data/crosswalk.csv').open() as f: rows=list(csv.DictReader(f))
    assert len(rows)==55 and {x['period'] for x in rows}=={'first','second','third'}
    pagecount=int(re.search(r'Pages:\s+(\d+)',subprocess.check_output(['pdfinfo',str(HERE/'manuscript.pdf')],text=True))[1])
    result={'status':'passed','bibliography_entries':len(keys),'figures':3,'tables':3,'crosswalk_rows':len(rows),'pdf_pages':pagecount,
            'checks':['All citation keys resolve','All HTML internal links resolve','All images embedded with alternative text','All PDF cross-references resolve','Required statistics and sources present','Crosswalk contains all three periods'],
            'sha256':{f:hashlib.sha256((HERE/f).read_bytes()).hexdigest() for f in ['manuscript.qmd','references.bib','manuscript.html','manuscript.pdf']}}
    (HERE/'generated/validation.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
if __name__=='__main__':validate()
