(function(root){
'use strict';
const norm=x=>String(x??'').trim().toLowerCase();
const key=r=>[r.retailer,r.sku,r.scope||(norm(r.color)==='unknown'||!r.color?'PRODUCT':'COLOR'),r.url].map(norm).join('|');
const valid=r=>r.retailer&&r.sku&&r.url&&(!r.source||norm(r.source)==='crawler')&&['in_stock','instock','available'].includes(norm(r.stock))&&Number.isFinite(Number(r.price))&&Number(r.price)>0&&Number.isFinite(Date.parse(r.timestamp));
function rank(current,history,now=Date.now()){
 const start=now-86400000, groups=new Map(), result=[];
 for(const r of history){if(!valid(r))continue; const t=Date.parse(r.timestamp);if(t<start-14400000||t>now)continue;const k=key(r);if(!groups.has(k))groups.set(k,[]);groups.get(k).push(r);}
 for(const r of current){if(!valid(r))continue;const t=Date.parse(r.timestamp);if(t>now||t<now-14400000)continue;
 const rows=(groups.get(key(r))||[]).filter(x=>Date.parse(x.timestamp)<t).sort((a,b)=>Date.parse(a.timestamp)-Date.parse(b.timestamp));
 const base=rows.filter(x=>Date.parse(x.timestamp)<=start).pop();if(!base)continue;
 const old=Number(base.price),price=Number(r.price),delta=price-old;if(!delta)continue;
 result.push({row:r,base,old,price,delta,pct:delta/old*100});}
 return result.sort((a,b)=>Math.abs(b.delta)-Math.abs(a.delta));
}
root.iPhoneMovers={rank};
})(typeof window==='undefined'?globalThis:window);
