"use strict";
let csrf="", preview=null, order=null, sourceText=null, sales=false;
const el=id=>document.getElementById(id);
const show=(id,on=true)=>el(id).classList.toggle("hidden",!on);
function status(message){el("status").textContent=message;}
async function request(path,payload,method="POST"){
  const response=await fetch(path,{method,credentials:"same-origin",headers:{"Content-Type":"application/json","X-CSRF-Token":csrf},...(method==="POST"?{body:JSON.stringify(payload||{})}:{})});
  const data=await response.json();if(!response.ok)throw Error(data.detail||"Request failed");return data;
}
async function busy(button,work){button.disabled=true;status("");try{await work();}catch(error){status(error.message);}finally{button.disabled=false;}}
function renderReview(){
  const body=el("fields");body.replaceChildren();const coverage=preview.review.coverage;
  el("coverage").textContent=coverage.total_records+" records parsed · "+coverage.sampled_records+" sampled for inference · "+(coverage.sample_complete?"complete inference sample":"later records will be checked during validation");
  preview.review.fields.forEach(field=>{
    const row=document.createElement("tr"),target=document.createElement("td"),choice=document.createElement("td"),evidence=document.createElement("td");
    const name=document.createElement("strong");name.textContent=field.target.slice(2)+(field.required?" *":"");
    const contract=document.createElement("code");contract.textContent=field.target_contract_json;target.append(name,contract);
    const select=document.createElement("select");select.dataset.target=field.target.slice(2);select.required=true;
    const blank=document.createElement("option");blank.value="";blank.textContent="Choose a source";select.append(blank);
    for(const source of preview.source_fields){const option=document.createElement("option");option.value=source;option.textContent=source;select.append(option);}
    if(!field.required){const skip=document.createElement("option");skip.value="__skip__";skip.textContent="Explicitly omit this target";select.append(skip);}
    if(field.decision.sources.length)select.value=field.decision.sources[0].slice(2);
    select.addEventListener("change",()=>{el("approve-consent").checked=false;});choice.append(select);
    evidence.textContent=field.evidence.reason+". Match score "+field.match_score+". Alternatives: "+field.alternatives.map(a=>a.source_path.slice(2)).join(", ")+".";
    row.append(target,choice,evidence);body.append(row);
  });
  el("approve-consent").checked=false;show("review-panel");show("purchase-panel",false);el("review-panel").scrollIntoView({behavior:"smooth"});
}
function renderOrder(){
  show("purchase-panel");const paid=order.download_available;
  el("order-status").textContent=paid?"Payment verified. Save your artifact now.":"Order "+order.id+" · "+order.status+" · $9.00 USD, one-time.";
  show("download",paid);el("download").href="/api/orders/"+order.id+"/download";
  show("pay",!paid&&order.status!=="refunded");el("pay").disabled=!sales;
  if(!sales&&!paid)el("order-status").textContent+=" Purchasing is awaiting activation. No payment has been created.";
  show("refresh-payment",!paid&&order.status==="checkout");el("purchase-panel").scrollIntoView({behavior:"smooth"});
}
el("preview-form").addEventListener("submit",event=>{event.preventDefault();busy(el("preview-button"),async()=>{
  const file=el("source").files[0];if(!file&&sourceText===null)throw Error("Choose a source file or load the example.");
  if(file&&file.size>1048576)throw Error("Hosted sources must be at most 1 MiB.");
  const source=file?new TextDecoder("utf-8",{fatal:true}).decode(await file.arrayBuffer()):sourceText,format=file?file.name.split(".").pop().toLowerCase():"csv";
  preview=await request("/api/proposals",{source,format,target_json:el("target").value,vendor:el("vendor").value,domain:el("domain").value});renderReview();
});});
el("approve-form").addEventListener("submit",event=>{event.preventDefault();busy(event.submitter,async()=>{
  const decisions={};for(const select of el("fields").querySelectorAll("select")){if(!select.value)throw Error("Choose every source, or explicitly omit an optional target.");decisions[select.dataset.target]=select.value==="__skip__"?null:select.value;}
  order=await request("/api/proposals/"+preview.id+"/approve",{expected_fingerprint:preview.review.mapping_fingerprint,decisions,reviewer:el("reviewer").value,vendor:preview.vendor,domain:preview.domain,approve:el("approve-consent").checked});renderOrder();
});});
el("pay").addEventListener("click",()=>busy(el("pay"),async()=>{
  const result=await request("/api/orders/"+order.id+"/checkout");if(result.checkout_url)window.location.assign(result.checkout_url);else{order=await request("/api/orders/"+order.id,null,"GET");renderOrder();}
}));
el("refresh-payment").addEventListener("click",()=>busy(el("refresh-payment"),async()=>{order=await request("/api/orders/"+order.id+"/reconcile");renderOrder();}));
el("support-button").addEventListener("click",()=>{if(!order){status("Open your order in the browser that created it to request support.");return;}show("support-panel");el("support-panel").scrollIntoView({behavior:"smooth"});});
el("support-form").addEventListener("submit",event=>{event.preventDefault();busy(event.submitter,async()=>{const result=await request("/api/orders/"+order.id+"/support",{email:el("support-email").value,message:el("support-message").value});status("Request saved: "+result.id+". Keep this reference for merchant review.");});});
el("demo").addEventListener("click",()=>{
  sourceText="customer_id,first_name,credit_limit\n000401,Ada,123456789012.123456789\n000402,Grace,2500.05\n";el("source").value="";el("vendor").value="Synthetic example";el("domain").value="Customer imports";
  el("target").value=JSON.stringify({type:"object",additionalProperties:false,properties:{customer_id:{type:"string"},first_name:{type:"string"},credit_limit:{type:"number",minimum:0}},required:["customer_id","first_name","credit_limit"]},null,2);status("Example loaded. Confirm processing permission, then preview.");
  el("preview-form").scrollIntoView({behavior:"smooth"});
  if(csrf)request("/api/marketing/events",{event:"example_loaded"}).catch(()=>{});
});
(async()=>{try{const params=new URLSearchParams(location.search),campaign=params.get("campaign")||"direct";const session=await request("/api/session",{campaign});csrf=session.csrf;sales=session.sales_enabled;const identifier=params.get("order");if(identifier&&/^order_[0-9a-f]{32}$/.test(identifier)){order=await request("/api/orders/"+identifier+"/reconcile");renderOrder();}}catch(error){status(error.message);}})();
