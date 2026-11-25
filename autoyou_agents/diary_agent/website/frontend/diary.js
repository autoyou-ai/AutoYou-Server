import {renderMedia} from './media.js';

const ui = Object.fromEntries(['new','date','time','title','body','editor','entries','filter','media','status','remove','view','journal','tags','favorite','copy'].map(key => [key, document.getElementById(key)]));
const base = location.pathname.startsWith('/agent/diary_agent') ? '/agent/diary_agent' : '';
let entries = [], current, dirty = false, generation = 0;
async function request(path, options) {
    const response = await fetch(base + '/api/diary/entries' + path, {cache:'no-store',...options});
    if (response.status === 409) { ui.copy.hidden=false; throw new Error('This entry changed on another device. Download your draft before opening the latest version.'); }
    if (!response.ok) throw new Error('The journal could not complete this request (' + response.status + ').');
    return response.json();
}
function show(entry) {
    current = entry;
    ui.date.value = entry.date; ui.time.textContent = entry.time;
    ui.title.value = entry.title; ui.body.value = entry.body; ui.remove.hidden = !entry.revision;
    ui.journal.value=entry.journal || ''; ui.tags.value=(entry.tags || []).join(', '); ui.favorite.checked=Boolean(entry.favorite); ui.copy.hidden=true;
    renderMedia(ui.media, entry); dirty = false; list();
}
function newEntry() {
    const now = new Date(), local = new Date(now.getTime() - now.getTimezoneOffset()*60000);
    return {id:crypto.randomUUID(), date:local.toISOString().slice(0,10),time:now.toLocaleTimeString()+' ('+Intl.DateTimeFormat().resolvedOptions().timeZone+')',title:'',body:'',revision:''};
}
function list() {
    const query = ui.filter.value.toLocaleLowerCase();
    ui.entries.replaceChildren();
    const today=newEntry().date;
    for (const row of entries) {
        if (ui.view.value==='favorites' && !row.favorite) continue;
        if (ui.view.value==='anniversary' && (row.date===today || row.date.slice(5)!==today.slice(5))) continue;
        if (![row.date,row.title,row.preview,row.journal,...(row.tags || [])].some(value=>String(value || '').toLocaleLowerCase().includes(query))) continue;
        const button=document.createElement('button'), date=document.createElement('small'), title=document.createElement('strong');
        button.type='button'; button.setAttribute('aria-current',String(row.id===current?.id));
        date.textContent=row.date; title.textContent=row.title; button.append(date,title);
        button.onclick=async()=>{ if (dirty && !confirm('Discard this unsaved draft?')) return;
            const ticket=++generation;
            try { const {entry}=await request('/'+encodeURIComponent(row.id)); if(ticket===generation) { show(entry); ui.status.textContent=''; } }
            catch(e){ui.status.textContent=e.message;} };
        ui.entries.append(button);
    }
}
async function reload(){ entries=(await request('')).entries; list(); }
ui.filter.oninput=list; ui.view.onchange=list; ui.editor.oninput=()=>{ dirty=true; };
ui.new.onclick=()=>{ if(dirty && !confirm('Start a new entry and discard this unsaved draft?')) return; ++generation;show(newEntry());ui.status.textContent='';ui.title.focus(); };
function details() {
    return {journal:ui.journal.value.trim(),tags:[...new Set(ui.tags.value.split(',').map(t=>t.trim()).filter(Boolean))],
            favorite:ui.favorite.checked,media:current.media || [],captured_at:current.captured_at || '',time_zone:current.time_zone || ''};
}
ui.copy.onclick=()=>{
    const draft=JSON.stringify({date:ui.date.value,time:current.time,title:ui.title.value,body:ui.body.value,...details()},null,2);
    const url=URL.createObjectURL(new Blob([draft],{type:'application/json'}));
    const link=document.createElement('a'); link.href=url;link.download='Diary-'+ui.date.value+'.json';link.click();
    setTimeout(()=>URL.revokeObjectURL(url),1000);
};
ui.editor.onsubmit=async event=>{
    event.preventDefault();
    const {id,time,revision}=current, title=ui.title.value.trim(), date=ui.date.value, body=ui.body.value;
    const attrs=details();
    if(!title) return;
    const markdown=['<!-- autoyou-diary:'+id+' -->','### '+date,'','#### '+time,'','##### '+title,'',body,'<!-- /autoyou-diary:'+id+' -->'].join('\n');
    try { const saved=await request('',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id,markdown,details:attrs,base_revision:revision})});
        if(current.id===id) {current={id,date,time,title,body,...attrs,revision:saved.revision};dirty=ui.body.value!==body || ui.date.value!==date || ui.title.value.trim()!==title || JSON.stringify(details())!==JSON.stringify(attrs);ui.remove.hidden=false;ui.copy.hidden=true;renderMedia(ui.media,current);}
        await reload();ui.status.textContent=dirty?'Changes after your last save are pending.':'Saved.'; }
    catch(e){ui.status.textContent=e.message;}
};
ui.remove.onclick=async()=>{
    if(!current.revision || !confirm('Delete this diary entry and its attachments?')) return;
    try { await request('/'+encodeURIComponent(current.id)+'?base_revision='+current.revision,{method:'DELETE'}); ++generation;show(newEntry());await reload();ui.status.textContent='Entry deleted.'; }
    catch(e){ui.status.textContent=e.message;}
};
show(newEntry());reload().catch(e=>ui.status.textContent=e.message);
