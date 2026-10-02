"""Compare parsed layout of the original pdf2docx (PyMuPDF) with pdf2docx_pdfium.

Development-only tool. The original pdf2docx + PyMuPDF must be importable from
ORIG_PYTHONPATH (e.g. ``pip install --target /tmp/orig pdf2docx pymupdf``).

Usage::

    ORIG_PYTHONPATH=/tmp/orig python tools/compare_with_pdf2docx.py path/to/*.pdf
    V=-v ...   # print first differences per file
"""
import sys, json, subprocess, os, glob
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ORIG = os.environ.get('ORIG_PYTHONPATH', '/tmp/orig')
code=r'''
import sys,json,logging; logging.disable(logging.CRITICAL)
mod=sys.argv[1]; pdf=sys.argv[2]
C=__import__(mod).Converter
cv=C(pdf)
try:
    cv.parse(**cv.default_settings)
except Exception as e:
    print(json.dumps({'error':repr(e)})); sys.exit(0)
res=[]
def walk_blocks(blocks, depth, out):
    for b in blocks:
        t=b.get('type')
        if t in (0,):
            imgs=0; uris=[]
            txt=''
            for l in b.get('lines',[]):
                for s in l.get('spans',[]):
                    if 'image' in s: imgs+=1
                    else:
                        txt+=s.get('text','')
                        for st in s.get('style',[]):
                            if st.get('uri'): uris.append(st['uri'])
            out.append(('T',depth,txt.strip(),imgs,tuple(uris),[round(x) for x in b['bbox']]))
        elif t in (2,3):
            out.append(('TAB%d'%t,depth,len(b['rows']),max([len(r['cells']) for r in b['rows']] or [0]),[round(x) for x in b['bbox']]))
            for r in b['rows']:
                for c in r['cells']:
                    if c and c.get('blocks'): walk_blocks(c['blocks'],depth+1,out)
        else:
            out.append(('?',t))
for p in cv.store()['pages']:
    out=[]
    for sec in p.get('sections',[]):
        for col in sec.get('columns',[]):
            walk_blocks(col.get('blocks',[]),0,out)
    out.append(('FLOAT',len(p.get('floats',[]))))
    res.append(out)
print(json.dumps(res,ensure_ascii=False))
'''
def run(mod,pdf,env=None,cwd=None):
    r=subprocess.run(['python','-c',code,mod,pdf],capture_output=True,text=True,env=env,cwd=cwd,timeout=600)
    try: return json.loads(r.stdout.strip().splitlines()[-1])
    except Exception: return {'error':r.stderr[-1500:]}
files=sys.argv[1:]
summary=[]
for pdf in files:
    A=run('pdf2docx',pdf,env={**os.environ,'PYTHONPATH':ORIG})
    B=run('pdf2docx_pdfium',pdf,cwd=REPO)
    name=os.path.basename(pdf)
    if isinstance(A,dict) or isinstance(B,dict):
        print(name,'ERROR', 'orig:',str(A)[:300] if isinstance(A,dict) else 'ok','| new:',str(B)[:1500] if isinstance(B,dict) else 'ok'); summary.append((name,'ERR')); continue
    tot=same_txt=same_struct=0; diffs=[]
    for pi,(x,y) in enumerate(zip(A,B)):
        sx=[e[:2]+e[2:4] if e[0].startswith('TAB') else e[:2] for e in x]; sy=[e[:2]+e[2:4] if e[0].startswith('TAB') else e[:2] for e in y]
        tx=' '.join(e[2] for e in x if e[0]=='T'); ty=' '.join(e[2] for e in y if e[0]=='T')
        tot+=1; same_struct+= sx==sy; same_txt+= tx.replace(' ','')==ty.replace(' ','')
        if sx!=sy or tx!=ty: diffs.append((pi,sx,sy,tx[:300],ty[:300]))
    ok = (same_struct==tot and same_txt==tot)
    print(f'{name}: pages={len(A)}/{len(B)} struct_same={same_struct}/{tot} text_same(nospace)={same_txt}/{tot}')
    summary.append((name,ok))
    if '-v' in os.environ.get('V',''):
        for d in diffs[:2]: print('   DIFF page',d[0],'\n    orig struct',d[1][:12],'\n    new  struct',d[2][:12],'\n    orig text',d[3],'\n    new  text',d[4])
print('ALL OK:',sum(1 for s in summary if s[1] is True),'/',len(summary))
