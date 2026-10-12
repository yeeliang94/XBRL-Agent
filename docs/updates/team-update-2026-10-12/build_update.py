from pathlib import Path
import base64
import html
import json

P = Path(__file__).parent
prs = json.loads((P / 'prs.json').read_text())
merged = [r for r in prs if r['mergedAt'] and '2026-08-11T16:00:00Z' <= r['mergedAt'] <= '2026-10-12T15:59:59Z']
commits = [c for page in json.loads((P / 'commits.json').read_text()) for c in page]

def pr(n):
    return f'<a href="https://github.com/yeeliang94/XBRL-Agent/pull/{n}">PR #{n}</a>'

def commit(short):
    c = next(c for c in commits if c['sha'].startswith(short))
    return f'<a href="{c["html_url"]}">{short}</a>'

def picture(name, caption):
    data = base64.b64encode((P / name).read_bytes()).decode()
    return f'<figure><button class="screenshot" aria-label="Enlarge screenshot: {html.escape(caption, quote=True)}"><img src="data:image/jpeg;base64,{data}" alt="{html.escape(caption, quote=True)}"></button><figcaption>{caption}</figcaption></figure>'

chapters = [
    dict(nav='Documents', title='See what needs doing',
         lead='We now have a work queue for documents. You can see which ones are waiting for setup, which are being processed and which need review.',
         points=[('Find your place', 'The document list shows its current stage and the action available. You can upload several documents and switch between saved work from the sidebar.'), ('Confirm the filing details', 'Preparation finishes before filing setup opens. You then confirm the accounting standard, Company or Group filing, units, reporting periods and statement formats.'), ('Return to recent work', 'History follows the latest saved activity, including edits. Opening a document does not move it to the top.')],
         images=[('01-work-queue.jpg', 'Work queue: documents waiting for setup.')],
         extra=('02-filing-setup.jpg', 'Filing setup opens after preparation. This example uses a synthetic document.'),
         detail='Preparation has its own progress view before setup. The document can then move through extraction, checks and review, with the saved history available when you return.',
         sources=', '.join(pr(n) for n in [15,23,27,32]) + '; preparation: ' + commit('8ed3770')),
    dict(nav='Figures', title='Check a figure against the page it came from',
         lead='The figures worksheet keeps the original PDF alongside the extracted values. Select a figure to open its source page, then review the current and prior years together.',
         points=[('Keep the source in view', 'You can review the number without switching between separate windows.'), ('Check the relationships', 'The application checks whether related statements agree, such as closing cash against the balance sheet.'), ('See unfinished work', 'Saved figures remain available after an interrupted run. Missing or failed verification stays visible for review.')],
         images=[('03-figures-source.jpg', 'Cash-flow figures beside the source PDF.')],
         extra=('05-cross-checks.jpg', 'This saved example has eight passing checks. The figures still need source review before filing.'),
         detail='We corrected cash-flow sign handling and improved year mapping, numeric-note categories and share-class coverage. Filing-field rules keep values in the appropriate year, entity and destination. Runs that reach a limit can retain their saved figures, but incomplete source coverage or verification prevents the application from reporting an unqualified success.',
         sources=', '.join(pr(n) for n in [16,22,25,31,34]) + '; filing fields: ' + commit('3056a1e') + '; signs: ' + commit('46f2a11')),
    dict(nav='Notes', title='Review the note with its source beside it',
         lead='Notes now have a numbered source list. You can open a note or subsection, see where it has been placed and compare the copied content with the PDF.',
         points=[('Follow the note', 'The list on the left keeps the source notes and their subsections available while you review a filing field.'), ('Review the table', 'Formatting handles headings, totals, borders and emphasis. Visual checks compare the prepared note with the PDF.'), ('Keep your edits', 'Formatting protects notes that a person has taken over. Shared appearance settings apply to the review preview, Copy and mTool output.')],
         images=[('04-notes-source.jpg', 'Inventories note beside the source PDF.')],
         detail='We improved placement of complete notes and subsections across filing fields. Cleanup removes page banners only after validating the removal. Eligible table-copying defects can be corrected by copying the table again from the source page. Content problems and source defects that remain unresolved still need review.',
         sources=', '.join(pr(n) for n in [14,15,19,20,21,28,33,35])),
    dict(nav='Comparison', title='Compare the result with a file prepared by a person',
         lead='If someone has already filled an mTool file for the same document, you can compare it with the AI result. The worksheet puts both sets of figures in aligned columns and marks differences or missed inputs.',
         points=[('Use an existing file', 'Choose the completed mTool file and confirm whether its figures are in RM, thousands or millions.'), ('Go to the differences', 'The comparison separates matches, different values and inputs missed by AI. You can focus the worksheet on the items that need attention.'), ('Read the score in context', 'Agreement scores use the relevant input figures for that worksheet. Calculated totals are excluded from the score. Agreement with a human file still needs checking against the source.')],
         images=[('08-comparison-sample.jpg', 'Sample comparison preview. The 82.5% is illustrative; it is not an application accuracy result.')],
         extra=('07-human-file-upload.jpg', 'The local application asks for the completed file and the units used for its figures.'),
         detail='This replaces the older benchmark workflow. We improved handling of numeric text, input formulas, repeated totals, units and numeric-note categories. Worksheet scores use the displayed concepts, periods, categories and entities, and differences are marked at the relevant cells.',
         sources=commit('e2042bf') + '; ' + ', '.join(pr(n) for n in [11,14,24,34,35])),
    dict(nav='mTool', title='Prepare the mTool file with outstanding issues in view',
         lead='The application fills your mTool template using the filing fields and their destinations. If the run still needs review, the preparation dialog says that the output is a draft.',
         points=[('Choose the worksheets', 'You can select which sheets to fill and resolve ambiguous destinations before the application writes to them.'), ('Keep the filing checks', 'The preparation checks years, Company or Group scope, categories and protected formula cells. Unresolved mapping or readiness issues remain visible.'), ('Know what you are downloading', 'Preparation reports and receipts record the outcome. A draft with outstanding issues still needs review and final mTool validation.')],
         images=[('06-mtool-draft.jpg', 'An mTool draft with outstanding review items.')],
         detail='Figures are matched by meaning and exact destination. The same prepared notes output is used for review, Copy and mTool filling. These safeguards make the preparation outcome easier to inspect, but final Windows mTool rendering and validation remain outstanding in the recent PRs.',
         sources=commit('b510095') + '; ' + commit('0169fac') + '; ' + commit('84e9e0e') + '; ' + commit('d2e757e') + '; ' + pr(35)),
    dict(nav='Team guidance', title='Keep team instructions and progress available',
         lead='Administrators can save common instructions for figures and notes. Each run keeps the guidance it used, so later changes do not rewrite the instructions recorded for earlier work.',
         points=[('Use the same guidance', 'Shared instructions apply to extraction and review. Earlier runs retain their own copy.'), ('Follow the work', 'Activity shows preparation, extraction, review and formatting. Formatting progress names the worksheet or part, including queued work and failures.'), ('Investigate a problem', 'You can reopen saved activity and download a diagnostics package for that run. Credentials are redacted from the package.')],
         images=[],
         detail='Saved activity can be reopened after the run finishes. Run-specific diagnostics include traces and records that help investigate failures without reconstructing the whole run from memory.',
         sources=', '.join(pr(n) for n in [12,15,17,18,25,30,35])),
]

short_points = [
    ["Upload several documents, switch between saved work and return to the ones edited most recently.", "Preparation now finishes before setup opens. Confirm the filing details, then continue to extraction and review."],
    ["Cross-checks show whether related statements agree.", "We've improved cash-flow signs, year mapping and share-class coverage. Interrupted runs keep their saved figures, with incomplete checks left visible."],
    ["Table formatting now handles headings, totals, borders and emphasis more consistently.", "The same appearance settings apply to review, Copy and mTool output. Formatting protects notes you've taken over."],
    ["Choose the file and confirm its units.", "Scores use the relevant input figures and exclude calculated totals. Agreement with the human file still needs checking against the PDF."],
    ["Choose the worksheets to fill and resolve unclear destinations. Preparation checks the years, entity, categories and protected formula cells.", "Outstanding issues stay visible in the preparation report. A run that needs review produces a draft warning."],
    ["", "Activity shows progress, queued work and failures by worksheet or part. Saved activity and a run-specific diagnostics package help investigate problems."],
]
for chapter, paragraphs in zip(chapters, short_points):
    chapter['detail'] += ' ' + ' '.join(body for title, body in chapter['points'])
    chapter['points'] = [('', ' '.join(paragraphs))]


css = '''
:root{--ink:#172b37;--muted:#586976;--paper:#fff;--ground:#f3f5f5;--edge:#dce3e5;--accent:#aa462c;--tint:#f9eee8}*{box-sizing:border-box}html{scroll-behavior:smooth;scroll-padding-top:95px}body{margin:0;background:var(--ground);color:var(--ink);font:17px/1.65 Arial,sans-serif}a{color:#265a70;text-underline-offset:3px}button{font:inherit;cursor:pointer}a:focus-visible,button:focus-visible,summary:focus-visible{outline:3px solid #265a70;outline-offset:4px}.skip{position:absolute;left:16px;top:-80px}.skip:focus{top:8px;z-index:10;background:white;padding:8px}.masthead{max-width:1120px;margin:auto;padding:65px 35px 45px}.eyebrow{font-size:13px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}h1{font-size:clamp(34px,4.5vw,57px);line-height:1.1;letter-spacing:-.035em;max-width:850px;margin:20px 0 25px}h2{font-size:clamp(27px,3vw,38px);line-height:1.2;letter-spacing:-.025em;margin:10px 0 18px}h3{font-size:18px;line-height:1.35;margin:0 0 8px}p{margin:0 0 16px}.intro{font-size:21px;max-width:820px}.meta{font-size:14px;color:var(--muted);margin-top:25px}.chapter-nav{position:sticky;top:0;z-index:3;background:#fffffff5;border-block:1px solid var(--edge);backdrop-filter:blur(8px)}.nav-inner{max-width:1120px;margin:auto;display:flex;overflow-x:auto;gap:6px;padding:12px 26px}.chapter-nav a{white-space:nowrap;text-decoration:none;color:var(--muted);padding:8px 12px;border-radius:5px;font-size:14px}.chapter-nav a[aria-current=true]{background:var(--ink);color:white}.chapter-nav a:hover{background:var(--tint);color:var(--ink)}main{max-width:1120px;margin:auto;padding:30px 24px 55px}.chapter{background:var(--paper);border:1px solid var(--edge);border-radius:9px;margin:0 0 28px;padding:36px}.chapter-head{max-width:840px}.lead{font-size:19px}.step-number{font-size:13px;color:var(--accent);letter-spacing:.07em;text-transform:uppercase}figure{margin:25px 0}.screenshot{display:block;width:100%;padding:0;border:1px solid var(--edge);border-radius:6px;overflow:hidden;background:var(--ground)}.screenshot img{width:100%;height:auto;display:block}figcaption{font-size:14px;color:var(--muted);margin-top:10px}figcaption span{white-space:nowrap}.explanations{display:grid;grid-template-columns:1fr;gap:25px;margin:24px 0}.explanation{border-top:2px solid var(--edge);padding-top:17px}.explanations p{font-size:16px;margin:0}.read-more{border-top:1px solid var(--edge);padding-top:16px;margin-top:20px;font-size:15px}summary{cursor:pointer;color:#265a70}details[open]>summary{margin-bottom:15px}.sources{font-size:13px;color:var(--muted);margin-top:17px;margin-bottom:0}.appendix{padding:30px 36px;font-size:15px}.appendix h2{font-size:26px}.table-wrap{overflow:auto}table{border-collapse:collapse;width:100%;font-size:13px}td,th{padding:10px;text-align:left;border-bottom:1px solid var(--edge)}th:first-child,td:first-child{white-space:nowrap}.status{padding:18px 22px;border-left:3px solid var(--accent);background:var(--tint);margin:20px 0}.status p:last-child{margin:0}dialog{width:96vw;max-width:1700px;max-height:95vh;border:0;border-radius:8px;padding:15px;background:white}dialog::backdrop{background:#142630cc}.dialog-head{display:flex;align-items:center;justify-content:space-between;gap:20px;margin-bottom:12px}.dialog-head p{font-size:14px;margin:0}.dialog-head button{border:1px solid var(--edge);background:white;border-radius:4px;padding:6px 15px}.zoom-image{display:block;max-width:100%;height:auto;margin:auto}.zoom-scroll{overflow:auto;max-height:80vh}.footer{color:var(--muted);font-size:13px;padding:10px 35px}@media(max-width:760px){.masthead{padding:38px 24px 28px}.intro{font-size:18px}.nav-inner{padding:8px 12px}.chapter-nav a{padding:8px;font-size:13px}main{padding:18px 12px}.chapter,.appendix{padding:25px 20px}.explanations{grid-template-columns:1fr;gap:19px}.lead{font-size:17px}figcaption span{white-space:normal}}@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}}@media print{body{background:white;font-size:11pt}.chapter-nav,.skip,dialog,figcaption span{display:none}.masthead{padding:15px}main{padding:0}.chapter{border:0;border-radius:0;break-before:page;padding:15px}.chapter:first-child{break-before:auto}figure{break-inside:avoid}.explanations{gap:15px}.read-more{display:block}.read-more>summary{display:none}h1{font-size:30pt}h2{font-size:24pt}}
'''
parts = [f'<!doctype html><html lang="en-GB"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>XBRL Agent: development explained</title><style>{css}</style></head><body><a class="skip" href="#main">Skip to the walkthrough</a>',
 '<header class="masthead"><div class="eyebrow">Team development update · 12 August to 12 October 2026</div><h1>What we\'ve changed in XBRL Agent</h1><p class="intro">We\'ve made it easier to follow a document through the application and check what it produces. You can see where work has reached, review figures and notes beside the source PDF, and compare the result with a file prepared by a person.</p><p class="meta">27 merged pull requests · Human review and final mTool validation remain required.</p></header>',
 '<nav class="chapter-nav" aria-label="Walkthrough chapters"><div class="nav-inner">' + ''.join(f'<a href="#theme{i}">{i}. {c["nav"]}</a>' for i,c in enumerate(chapters,1)) + '</div></nav><main id="main">']
for i,c in enumerate(chapters,1):
    parts.append(f'<section class="chapter" id="theme{i}" aria-labelledby="title{i}"><header class="chapter-head"><div class="step-number">{i:02d} / {len(chapters):02d}</div><h2 id="title{i}">{c["title"]}</h2><p class="lead">{c["lead"]}</p></header>')
    parts.extend(picture(*x) for x in c['images'])
    parts.append('<div class="explanations">'+''.join(f'<p>{body}</p>' for title,body in c['points'])+'</div>')
    extra = picture(*c['extra']) if c.get('extra') else ''
    parts.append(f'<details class="read-more"><summary>Details and sources</summary><p>{c["detail"]}</p>{extra}<p class="sources">{c["sources"]}</p></details></section>')
parts.append('<section class="chapter appendix"><h2>Where development stands</h2><div class="status"><p>The changes in this update are merged into the main branch. Rollout on team machines and final SSM acceptance have not been confirmed by this review.</p><p>We have not measured an overall accuracy rate or time saving for this update. Windows mTool rendering and final validation remain outstanding in the recent pull requests.</p></div><details class="read-more"><summary>About the screenshots</summary><p>Seven screenshots were captured from the local application on 12 October. It was already running, so some controls may be behind the latest merged code. The work queue contains synthetic drafts, and the review screens show an existing saved financial-statement example.</p><p>The comparison image is an earlier implementation preview with sample data. Its 82.5% score is illustrative.</p></details><details class="read-more"><summary>Checks reported in the latest pull request</summary><p>PR #35 reports a successful production build, 6,348 passing backend tests with two fixture-related failures, and 1,594 passing frontend tests with two existing responsive-layout assertion failures. These are the PR\'s recorded results. We did not rerun application tests for this update.</p></details><details class="read-more"><summary>Changes left out of the walkthrough</summary><p>CLBG support was briefly added and then removed. Retired repeat-run, benchmark and scanned-PDF transcript controls are also excluded. MFRS/MPERS and Company/Group support predate this period; this update describes the improvements made to those workflows.</p></details><details class="read-more"><summary>All 27 merged pull requests</summary><div class="table-wrap"><table><thead><tr><th>PR</th><th>Merged (UTC)</th><th>Change</th></tr></thead><tbody>')
parts.extend(f'<tr><td>{pr(r["number"])}</td><td>{r["mergedAt"][:10]}</td><td>{html.escape(r["title"])}</td></tr>' for r in sorted(merged,key=lambda r:r['number']))
parts.append('</tbody></table></div></details></section></main><footer class="footer">Revised 13 October 2026</footer><dialog id="image-dialog" aria-labelledby="image-caption"><div class="dialog-head"><p id="image-caption"></p><button id="close-image" type="button">Close</button></div><div class="zoom-scroll"><img class="zoom-image" id="zoom-image" alt=""></div></dialog>')
parts.append('''<script>
const dialog=document.getElementById('image-dialog');
const zoom=document.getElementById('zoom-image');
const caption=document.getElementById('image-caption');
document.querySelectorAll('.screenshot').forEach(button=>button.addEventListener('click',()=>{const image=button.querySelector('img');zoom.src=image.src;zoom.alt=image.alt;caption.textContent=image.alt;dialog.showModal();}));
document.getElementById('close-image').addEventListener('click',()=>dialog.close());
dialog.addEventListener('click',event=>{if(event.target===dialog)dialog.close();});
const links=[...document.querySelectorAll('.chapter-nav a')];
function updateNav(){let current=1;document.querySelectorAll('.chapter[id]').forEach((section,index)=>{if(section.getBoundingClientRect().top<155)current=index+1;});links.forEach((link,index)=>{if(index+1===current)link.setAttribute('aria-current','true');else link.removeAttribute('aria-current');});}
window.addEventListener('scroll',updateNav,{passive:true});updateNav();
</script></body></html>''')
result=''.join(parts)
(P / 'team-update.html').write_text(result)
text=['XBRL Agent development update: 12 August to 12 October 2026', '', "We've made it easier to follow a document through the application and check what it produces.", '']
for c in chapters:
    text.extend([c['title'], c['lead'], *[body for title,body in c['points']], ''])
text.extend(['The changes are merged. Human review and final mTool validation are still required. Rollout on team machines and final SSM acceptance have not been confirmed. No overall accuracy or time-saving figure has been measured for this update.'])
(P / 'team-message.txt').write_text('\n'.join(text))
assert len(merged)==27 and len(commits)==243
assert result.count('data:image/jpeg;base64,')==8
print('Rebuilt screenshot explainer: six chapters, eight embedded images, all 27 PR links.')
