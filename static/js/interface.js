(() => {
 const workspace=document.getElementById('exam-workspace');if(!workspace)return;
 const alert=document.getElementById('network-alert'),timer=document.getElementById('countdown');
 let end=performance.now()+Number(timer.dataset.remaining)*1000,closed=false,leaving=false;
 const forms=[...document.querySelectorAll('.answer-form')];
 const token=form=>form.querySelector('[name=csrfmiddlewaretoken]').value;
 const show=(form,text)=>form.querySelector('.save-status').textContent=text;
 const choice=form=>form.querySelector('input:checked')?.value||'';
 const key=()=>globalThis.crypto?.randomUUID?.() || Array.from(globalThis.crypto.getRandomValues(new Uint8Array(16)),b=>b.toString(16).padStart(2,'0')).join('');
 async function save(form){
  if(form.saving)return form.saving;
  form.saving=(async()=>{
   let conflicts=0;
   while(!closed && choice(form) && (choice(form)!==form.dataset.savedOption || form.pending)){
    if(!form.pending)form.pending={selected:choice(form),version:form.dataset.version,key:key()};
    const p=form.pending,data=new FormData(form);data.set('selected_option',p.selected);data.set('version',p.version);data.set('key',p.key);show(form,'Saving…');
    try{
     const response=await fetch(form.action,{method:'POST',body:data,headers:{'X-Requested-With':'XMLHttpRequest'},signal:AbortSignal.timeout(10000)});
     if(response.redirected)throw Error('Sign in again; answer not saved.');
     const result=await response.json();
     if(response.status===409 && result.error==='stale'){
      form.dataset.version=result.version;form.dataset.savedOption=result.selected;form.pending=null;
      if(++conflicts>2)throw Error('Another tab changed this answer. Retry your latest selection.');
      continue;
     }
     if(!response.ok||!result.saved)throw Error(result.error==='closed'?'Exam ended. This choice was not saved.':'Save failed. Check your network and retry.');
     form.dataset.version=result.version;form.dataset.savedOption=p.selected;form.pending=null;
     show(form,'Saved');
    }catch(e){show(form,e.message||'Connection lost. Retry to confirm saving.');throw e;}
   }
  })();
  try{return await form.saving;}finally{form.saving=null;}
 }
 forms.forEach(form=>{form.addEventListener('change',()=>save(form).catch(()=>{}));form.addEventListener('submit',e=>{e.preventDefault();save(form).catch(()=>{});});});
 async function access(){
  try{
   const r=await fetch(workspace.dataset.accessUrl,{cache:'no-store',headers:{'X-Requested-With':'XMLHttpRequest'},signal:AbortSignal.timeout(10000)});
   if(!r.ok||r.redirected)throw Error('Access unavailable. Reconnect to the approved network or sign in again. Timer continues.');
   const d=await r.json();if(d.closed){closed=true;leaving=true;location.href=d.result_url;return;}
   end=performance.now()+d.remaining*1000;workspace.hidden=false;alert.hidden=true;
  }catch(e){alert.textContent=e.message;alert.hidden=false;workspace.hidden=true;}
 }
 setInterval(()=>{const n=Math.max(0,Math.ceil((end-performance.now())/1000));timer.textContent=`${Math.floor(n/60)}:${String(n%60).padStart(2,'0')}`;},250);
 setInterval(access,5000);window.addEventListener('online',access);access();
 document.getElementById('exam-submit').addEventListener('submit',async e=>{
  e.preventDefault();const button=e.target.querySelector('button');button.disabled=true;
  try{await Promise.all(forms.map(save));if(confirm('Submit your saved answers? This cannot be undone.')){leaving=true;HTMLFormElement.prototype.submit.call(e.target);}}
  catch(_){alert.hidden=false;alert.textContent='Some answers are not acknowledged. Retry saving before submission; the deadline cannot be extended.';}
  finally{button.disabled=false;}
 });
 window.addEventListener('beforeunload',e=>{if(!leaving&&forms.some(f=>f.pending||choice(f)!==f.dataset.savedOption)){e.preventDefault();e.returnValue='';}});
})();
