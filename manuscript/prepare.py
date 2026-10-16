#!/usr/bin/env python3
"""Freeze an explicit local release, or regenerate tables/figures offline from evidence.json."""
import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path
import re

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')

def in_period(a, p):
    v = str(a['volume'])
    if not v.isdigit() or not p['volume_start'] <= int(v) <= p['volume_end']:
        return False
    if int(v) == p['volume_end'] and 'last_issue' in p:
        m = re.fullmatch(r'(\d+)(?:\s*[-–]\s*(\d+))?', str(a['issue']))
        return bool(m and 1 <= int(m[1]) <= int(m[2] or m[1]) <= p['last_issue'])
    return True

# Reviewed against the CURRENT model's keywords and all three representatives per topic.
# These are scope overlaps, never article-level validation labels.
MAPPING = {
 'Defence expenditure studies': ([1,4,9], 'broader', 'Original definition spans consequences, time-series properties and demand; current model separates growth, demand/alliances and debt.'),
 'Procurement processes and policies': ([3,8], 'partial', 'Industry/procurement and equipment-cost clusters overlap procurement, but include other questions.'),
 'Economics of alliances': ([4], 'narrower', 'Current topic also includes national expenditure demand, as its French spending representative demonstrates.'),
 'Conversion': ([3], 'partial', 'Industry restructuring overlaps this topic; conversion is not a separately recovered cluster.'),
 'Defence industries and industrial base': ([3], 'narrower', 'Current topic also includes procurement; industrial-base and contractor abstracts support the link.'),
 'Military manpower': ([5], 'narrower', 'Personnel topic also covers veterans and civilian outcomes.'),
 'Terrorism, guerrilla warfare, and insurgencies': ([0,2], 'partial', 'Historical composite category crosses current conflict and terrorism clusters.'),
 'Defence and growth': ([1], 'close', 'All three representatives explicitly analyse the spending-growth relationship.'),
 'Defence R&D, technological change, and weapon development': ([3,8], 'partial', 'R&D occurs in industrial-base research; quality and technical change also enter equipment costs.'),
 'Arms races': ([6], 'narrower', 'Current arms topic jointly covers trade and strategic competition.'),
 'Arms trade': ([6], 'narrower', 'Current arms topic jointly covers trade and strategic competition.'),
 'Disarmament and arms control': ([6], 'partial', 'Adjacent strategic-weapons scope; representative abstracts do not establish a dedicated disarmament cluster.'),
 'Military production function': ([3,8], 'partial', 'Production, scale and capability overlap industrial-base and equipment-cost work.'),
 'Defence budgets': ([4,9], 'partial', 'Expenditure demand and debt overlap budget choices without exhausting budget institutions.'),
 'Developing countries and defence': ([1], 'partial', 'Developing-country growth representative supports overlap; geography is not a model topic.'),
 'Defence and the economy': ([1], 'broader', 'Current cluster focuses on growth; the editorial category can cover other economic effects.'),
 'Terrorism': ([2], 'close', 'Recruitment, attack determinants and counterterror policies appear in the representatives.'),
 'Defence policies, planning and management': ([3,4,8], 'partial', 'Broad editorial category spans procurement, spending determinants and equipment choices.'),
 'Conflict and civil wars': ([0], 'narrower', 'Current topic includes peace and sanctions as well as conflict and civil war.'),
 'Peace and peacekeeping': ([0,4], 'partial', 'Peace overlaps conflict models and alliance spending, but no dedicated peacekeeping cluster is established.'),
 'Arms races, arms control and disarmament': ([6], 'partial', 'Trade/race representatives support partial strategic-weapons overlap, not full equivalence.'),
 'Defence R&D and technology': ([3,8], 'partial', 'Industrial R&D and equipment quality overlap these clusters without a separate R&D topic.'),
 'Fiscal effects of defence spending': ([9], 'broader', 'Debt is narrower than all fiscal effects, including taxes and spending substitution.'),
 'Military personnel and recruitment': ([5], 'narrower', 'Current topic extends recruitment and service to veterans and civilian outcomes.'),
 'Models of defence expenditures': ([4], 'close', 'Demand for spending is explicit in NATO and French-spending representatives; supersedes the archived growth-only mapping.'),
 'Arms trade and production': ([3,6], 'partial', 'Trade/race and industrial-base clusters divide this editorial category.'),
 'Security economics/issues': ([0,2,7], 'partial', 'Security is broader than geopolitical markets; sanctions and terrorism fall in other topics.'),
 'Company surveys': ([3], 'partial', 'Company-based industrial research overlaps the topic; publication format is not a separate cluster.'),
}

def freeze(release):
    if (HERE/'data/evidence.json').exists():
        raise SystemExit('Frozen evidence already exists; use a separate manuscript directory for another release.')
    t = json.loads((release/'topics.json').read_text())
    c = json.loads((release/'citations.json').read_text())
    corpus = json.loads((release/'corpus.json').read_text())
    ref = json.loads((ROOT/'data/retrospectives.json').read_text())
    assignments = {d['handle']:d for d in t['documents']}
    papers = []
    for a in corpus:
        papers.append({**{k:a[k] for k in ['handle','title','authors','year','volume','issue','doi']},
                       'topic': assignments.get(a['handle'],{}).get('topic')})
    files = [release/f for f in ['topics.json','citations.json','corpus.json','model/definition.json']]
    files += [ROOT/'data/retrospectives.json', *sorted((ROOT/'docs').glob('*.pdf')), *sorted((ROOT/'docs').glob('*.epub'))]
    files += [ROOT/'app'/f for f in ['topics.py','update_topics.py','citations.py','citation_data.py']]
    provenance = {'release':str(release.relative_to(ROOT)), 'frozen_date':'2026-10-15',
                  'files':{str(p.relative_to(ROOT)):sha(p) for p in files}}
    evidence = {'provenance':provenance, 'papers':papers,
                'topics':t['topics'], 'topic_metadata':t['metadata'],
                'topic_report':{k:v for k,v in t['report'].items() if k not in ['trials','finalists','representative_abstracts']},
                'model_definition':json.loads((release/'model/definition.json').read_text()),
                'retrospectives':ref['periods'],
                'citations':{k:c[k] for k in ['metadata','nodes','edges','communities','flows','coverage','network','baseline','sensitivity','comparison']}}
    dump(HERE/'data/evidence.json',evidence)
    dump(HERE/'data/provenance.json',{**provenance,'evidence_sha256':sha(HERE/'data/evidence.json')})

def table(path, headers, rows, caption, ident):
    lines=['| '+' | '.join(headers)+' |','|'+'|'.join(['---']*len(headers))+'|']
    lines += ['| '+' | '.join(map(str,row))+' |' for row in rows]
    lines += ['',f': {caption} {{#{ident}}}','']
    path.write_text('\n'.join(lines))

def build():
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection
    import numpy as np
    e = json.loads((HERE/'data/evidence.json').read_text())
    provenance=json.loads((HERE/'data/provenance.json').read_text())
    assert sha(HERE/'data/evidence.json')==provenance['evidence_sha256'], 'Frozen evidence changed'
    papers=e['papers']; topics=e['topics']; c=e['citations']; meta=e['topic_metadata']
    counts=Counter(a['topic'] for a in papers)
    assert len(papers)==meta['article_count']==1221
    assert len({a['handle'] for a in papers})==len(papers)
    assert counts[None]==meta['missing_abstract_count']==69
    assert sum(counts.values())-counts[None]==meta['abstract_count']==1152
    assert counts[-1]==82 and all(counts[t['id']]==t['count'] for t in topics)
    assert c['metadata']['corpus_fingerprint']==meta['corpus_fingerprint']
    assert c['comparison']['topic_run_id']==meta['run_id']
    nodes={n['handle']:n for n in c['nodes']}
    assert len(nodes)==1143 and len(c['edges'])==3097
    assert len({(a['source'],a['target']) for a in c['edges']})==len(c['edges'])
    assert all(x['source'] in nodes and x['target'] in nodes for x in c['edges'])
    incoming=Counter(x['target'] for x in c['edges']);outgoing=Counter(x['source'] for x in c['edges'])
    assert all(n['in_degree']==incoming[h] and n['out_degree']==outgoing[h] for h,n in nodes.items())
    assert sum(n['degree']==0 for n in nodes.values())==188
    assert len(c['communities'])==36 and sum(g['count'] for g in c['communities'])==955
    assert all(sum(x['count'] for x in row['cells'])==row['count'] for row in c['comparison']['rows'])
    by_handle={a['handle']:a for a in papers}
    for row in c['comparison']['rows']:
        members=[n for n in nodes.values() if n['community']==row['community']]
        assert len(members)==row['count']
        actual=Counter('missing' if by_handle[n['handle']]['topic'] is None else str(by_handle[n['handle']]['topic']) for n in members)
        assert all(actual[cell['id']]==cell['count'] for cell in row['cells'])
    assert all(sum(n['community']==g['id'] for n in nodes.values())==g['count'] for g in c['communities'])

    gen=HERE/'generated'; figs=HERE/'figures'
    table(gen/'topics.qmd',['Topic','Abstracts','Share (%)'],
          [[t['label'],t['count'],f"{100*t['count']/1152:.1f}"] for t in topics]+[['No topic assigned',82,f'{100*82/1152:.1f}']],
          'How the 1,152 available abstracts are divided among topics. Percentages include abstracts with no assigned topic, but exclude the 69 records without abstracts.', 'tbl-topics')
    period_stats=[]; crosswalk=[]
    for p in e['retrospectives']:
        assert sum(x['count'] for x in p['categories'])==p['classified_total']
        selected=[a for a in papers if in_period(a,p)]; cnt=Counter(a['topic'] for a in selected)
        n=len(selected)-cnt[None]
        period_stats.append({'id':p['id'],'catalogue':len(selected),'abstracts':n,'missing':cnt[None],
                             'unassigned':cnt[-1],'counts':{str(k):v for k,v in cnt.items()}})
        for cat in p['categories']:
            ids,relation,note=MAPPING.get(cat['name'],([], 'not_resolved','No distinct current cluster; editorial format, geographical scope or conceptual category differs from subject clustering.'))
            crosswalk.append({'period':p['id'],'editorial_category':cat['name'],'editorial_count':cat['count'],
              'editorial_denominator':p['classified_total'],'editorial_share':round(100*cat['count']/p['classified_total'],3),
              'current_topic_ids':';'.join(map(str,ids)),'current_topic_labels':'; '.join(topics[i]['label'] for i in ids),
              'relationship_editorial_to_model':relation,'rationale':note,'model_union_count':sum(cnt[i] for i in ids) if ids else '',
              'model_denominator':n,'model_union_share':round(100*sum(cnt[i] for i in ids)/n,3) if ids else '',
              'source_doi':p['source']['doi'],'source_locator':p['source']['locator']})
    comparison_rows=[]
    selected_categories=[('Growth',1,['Defence and growth','Defence and the economy','Defence and the economy']),
                         ('Terrorism',2,['Terrorism, guerrilla warfare, and insurgencies','Terrorism','Terrorism']),
                         ('Conflict',0,[None,'Conflict and civil wars','Conflict and civil wars']),
                         ('Alliances',4,['Economics of alliances']*3),
                         ('Industry',3,['Defence industries and industrial base']*3),
                         ('Personnel',5,['Military manpower','Military personnel and recruitment','Military personnel and recruitment'])]
    for label,topic,names in selected_categories:
        cells=[]
        for p,stats,name in zip(e['retrospectives'],period_stats,names):
            editorial=next((x['count'] for x in p['categories'] if x['name']==name),'—')
            cells.append(f"{editorial} / {stats['counts'].get(str(topic),0)}")
        comparison_rows.append([label,*cells])
    table(gen/'comparison.qmd',['Theme','First decade','Second decade','Third decade'],comparison_rows,
          'Selected subjects: each cell shows the review count / model count. The reviews classify 207, 331 and 389 papers; the model uses 45, 330 and 381 abstracts. First-decade coverage is incomplete. A dash means the review has no separate category. The subjects overlap but differ: the first review combines terrorism with guerrilla warfare and armed rebellions; model conflict includes peace, alliances includes reasons for defence spending, industry includes purchasing, and personnel includes veterans. Matching totals do not prove that the same papers were selected.', 'tbl-comparison')
    with (HERE/'data/crosswalk.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(crosswalk[0]));w.writeheader();w.writerows(crosswalk)
    table(gen/'periods.qmd',['Review period','Review count','Catalogue','Abstracts','No topic'],
          [[p['label']+(' (partial)' if p['partial'] else ''),p['classified_total'],s['catalogue'],s['abstracts'],s['unassigned']] for p,s in zip(e['retrospectives'],period_stats)],
          'Papers available for each historical comparison. Review count is the number classified by the editors, excluding separately listed book reviews; catalogue is all available records; the abstracts column counts records with a summary; no topic counts summaries the model left unassigned. The first review covers volumes 1-10 through volume 10, issue 2, but the catalogue starts at volume 8. The second covers volumes 11-20 and the third volumes 21-30.', 'tbl-periods')
    short=['Conflict / peace','Spending / growth','Terrorism','Industry / purchasing','Spending / alliances','Military staff / veterans','Arms trade / races','Political risk / markets','Equipment costs','Spending / debt']
    # Counts and percentages are computed directly from the retained paper assignments.
    bins=[(1997,1999),(2000,2004),(2005,2009),(2010,2014),(2015,2019),(2020,2024),(2025,2026)]
    trend=[]
    for start,end in bins:
        members=[a for a in papers if start<=int(a['year'])<=end and a['topic'] is not None]
        cnt=Counter(a['topic'] for a in members)
        trend.append({'start':start,'end':end,'abstracts':len(members),'counts':dict(cnt)})
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':9,'axes.spines.top':False,'axes.spines.right':False,'pdf.fonttype':42})
    colors=['#167D9A','#D17838','#4A8956','#8A5BA5','#AF555E','#627D32','#537AB4','#9A7941','#52928B','#A45C87','#6E729E','#9B6861']
    fig,axs=plt.subplots(5,2,figsize=(7.4,7.1),sharex=True,sharey=True,layout='constrained')
    for i,ax in enumerate(axs.flat):
        vals=[100*b['counts'].get(i,0)/b['abstracts'] for b in trend]
        ax.plot(range(len(bins)),vals,color=colors[i],marker='o',linewidth=1.6,markersize=3.5)
        ax.set_title(short[i],loc='left',fontsize=9,fontweight='bold');ax.set_ylim(0,40);ax.set_yticks([0,20,40]);ax.grid(axis='y',alpha=.18)
        ax.set_xticks(range(len(bins)),['1997-99*','2000-04','2005-09','2010-14','2015-19','2020-24','2025-26*'],rotation=45,ha='right',fontsize=7)
    fig.supylabel('Share of abstracts in period (%)',fontsize=10)
    fig.savefig(figs/'topic-trends.png',dpi=240);fig.savefig(figs/'topic-trends.pdf');plt.close(fig)
    # Preserve release coordinates; do not recompute layout or communities.
    fig,ax=plt.subplots(figsize=(7.4,6.2))
    segments=[[(nodes[x['source']]['x'],nodes[x['source']]['y']),(nodes[x['target']]['x'],nodes[x['target']]['y'])] for x in c['edges']]
    ax.add_collection(LineCollection(segments,colors='#89939D',linewidths=.25,alpha=.25,rasterized=True))
    for i in list(range(12))+[99,-1]:
        members=[n for n in nodes.values() if (n['community']==i if i!=99 else n['community']>=12)]
        ax.scatter([n['x'] for n in members],[n['y'] for n in members],s=[5+1.4*n['in_degree'] for n in members],
                   c=colors[i] if 0<=i<12 else ('#A9ADB2' if i==99 else '#D2D5D9'),alpha=.8,linewidths=.2,edgecolors='white',rasterized=True)
    ax.autoscale();ax.set_aspect('equal');ax.axis('off')
    labels=['Growth','Industry / purchasing','Terrorism','Why governments spend','Arms / technology','Risk / resources','Debt / budgets','Recruitment','Civil war costs','Intervention / negotiation','Greek-Turkish rivalry','Aircraft / price rises']
    from matplotlib.lines import Line2D
    handles=[Line2D([0],[0],marker='o',linestyle='',color=colors[i],markersize=5,label=f'{i+1}. {s}') for i,s in enumerate(labels)]
    handles += [Line2D([0],[0],marker='o',linestyle='',color='#A9ADB2',markersize=5,label='Other communities'),Line2D([0],[0],marker='o',linestyle='',color='#D2D5D9',markersize=5,label='No recorded links')]
    fig.legend(handles=handles,loc='lower center',ncol=3,fontsize=7.5,frameon=False)
    fig.subplots_adjust(left=.02,right=.98,top=.99,bottom=.22)
    fig.savefig(figs/'citation-network.png',dpi=260);fig.savefig(figs/'citation-network.pdf');plt.close(fig)
    # Community rows use all eligible members as denominator, including missing abstracts.
    rows={x['community']:x for x in c['comparison']['rows']}
    selected=[rows[i] for i in range(12)]
    other=[x for k,x in rows.items() if k>=12]
    aggregate={'count':sum(x['count'] for x in other),'cells':[{'count':sum(x['cells'][j]['count'] for x in other)} for j in range(12)]}
    selected += [aggregate,rows[-1]]
    matrix=np.array([[100*z['count']/row['count'] for z in row['cells']] for row in selected])
    fig,ax=plt.subplots(figsize=(8.2,6.5),layout='constrained')
    im=ax.imshow(matrix,vmin=0,vmax=100,cmap='Blues',aspect='auto')
    ax.set_yticks(range(14),[f'{i+1}. {s} ({selected[i]["count"]} papers)' for i,s in enumerate(labels)]+[f'Other 24 communities ({aggregate["count"]} papers)','No recorded links (188 papers)'],fontsize=8)
    ax.set_xticks(range(12),short+['No topic assigned','No abstract'],rotation=55,ha='right',fontsize=8)
    for i in range(14):
        for j in range(12):
            if matrix[i,j]>=10:ax.text(j,i,f'{matrix[i,j]:.0f}',ha='center',va='center',fontsize=7,color='white' if matrix[i,j]>55 else '#223344')
    cb=fig.colorbar(im,ax=ax,shrink=.6,pad=.02);cb.set_label('Share of papers in the row (%)',fontsize=8)
    fig.savefig(figs/'topic-community.png',dpi=240);fig.savefig(figs/'topic-community.pdf');plt.close(fig)
    stats={'periods':period_stats,'trends':trend,'within_community_edges':sum(x['count'] for x in c['flows'] if x['source']==x['target']),
           'reference_coverage':{k:sum(x[k] for x in c['coverage']) for k in ['eligible','with_references','missing_reference_deposit','unresolved_identities']},
           'communities':[{'id':g['id'],'label':g['label'],'count':g['count']} for g in c['communities']],
           'topic_counts':dict(counts),'checks':'All frozen-evidence integrity, count, edge, degree and denominator checks passed.'}
    dump(gen/'statistics.json',stats)
    print(json.dumps(stats['periods'],indent=2));print(stats['checks'])

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--freeze',type=Path,help='Explicit release directory; only allowed when evidence.json is absent')
    args=parser.parse_args()
    if args.freeze:freeze(args.freeze.resolve())
    build()
