#!/usr/bin/env python3
"""Build docs/corpus-explorer.html: single-file corpus explorer.

Reads 4 frozen bundles, fail-closed verifies each embedded bundle_hash
recomputes via pacbench.frozen.bundle_hash, aborts on mismatch, and emits
one self-contained HTML file (inline CSS+JS, data inlined, no CDN/fetch).
Regenerate: /usr/local/bin/python3 scripts/build_corpus_explorer.py
Open: open docs/corpus-explorer.html (works from file://).
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pacbench.frozen import bundle_hash  # noqa: E402

FILES = ["runs/race-week-bundle.json", "runs/revoke-week-bundle.json",
         "runs/tiny-toy-bundle.json", "runs/tiny-pizza-bundle.json"]
FIELDS = ("source_id", "text", "channel", "sender", "thread",
          "written_at", "collected_at", "from_owner")
CHAT = ["imessage", "whatsapp", "text", "slack", "groupme", "hermes"]

TEMPLATE = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PACBench Corpus Explorer</title><style>
body{font-family:system-ui,sans-serif;max-width:1000px;margin:0 auto;padding:12px;background:#f4f4f5;color:#111}
header{background:#18181b;color:#fff;padding:10px 14px;border-radius:8px;margin-bottom:10px}
header b{font-size:17px}header .sub{font-size:12px;color:#d4d4d8}
#ctl{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:10px;align-items:center}
select,input,button{font-size:14px;padding:5px 8px;border-radius:6px;border:1px solid #bbb}
#days{display:flex;gap:4px;align-items:center}
#count{font-size:12px;color:#555;margin:6px 0}
.conv{border:1px solid #ddd;border-radius:12px;margin:10px 0;background:#fff;overflow:hidden}
.conv h3{margin:0;padding:7px 12px;background:#e4e4e7;font-size:13px}
.cwin{background:#fafafa;padding:8px 4px 12px}
.dm .cwin{background:#fff}
.daydiv{text-align:center;margin:10px 0 6px;font-size:11px;color:#666}
.daydiv span{background:#e4e4e7;border-radius:10px;padding:2px 10px}
.row{display:flex;gap:6px;margin:3px 10px;align-items:flex-end}
.row.me{justify-content:flex-end}
.row.joined{margin-top:-1px}
.av{width:26px;height:26px;border-radius:50%;flex:none;color:#fff;font-size:13px;font-weight:700;display:flex;align-items:center;justify-content:center}
.av.ghost{visibility:hidden}
.col{display:flex;flex-direction:column;max-width:72%}
.nm{font-size:11px;color:#555;margin:0 0 1px 2px}
.bub{padding:7px 11px;border-radius:16px;background:#e5e5ea;font-size:14px;overflow-wrap:anywhere}
.row.joined .bub{border-top-left-radius:6px}
.me .bub{background:#0a84ff;color:#fff;border-top-right-radius:16px}
.me.row.joined .bub{border-top-right-radius:6px}
.tm{font-size:10px;opacity:.6;margin-top:2px}
.bub details{font-size:11px;margin-top:2px;opacity:.85}
.mailapp{display:flex;min-height:180px}
.maillist{width:38%;border-right:1px solid #ddd;overflow:auto}
.mailitem{padding:7px 10px;border-bottom:1px solid #eee;cursor:pointer;font-size:13px}
.mailitem.active{background:#e8f0fe}
.mi-from{font-weight:600;font-size:12px}.mi-subj{color:#333;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.mi-date{font-size:11px;color:#888}
.mailread{flex:1;padding:10px 14px;font-size:14px;overflow-wrap:anywhere}
.mailread .mh{border-bottom:1px solid #eee;padding-bottom:6px;margin-bottom:8px;font-size:13px}
.mailread .mh div{margin:1px 0}.mailread .subj{font-size:16px;font-weight:700;margin-bottom:4px}
.mailread .body{white-space:pre-wrap}
.mailread details{font-size:11px;margin-top:8px;opacity:.8}
.mail{border-bottom:1px solid #eee;padding:8px 10px;background:#fff}
</style></head><body>
<header><b id="h-life"></b> <span class="sub" id="h-hash"></span>
<div class="sub" id="h-day"></div></header>
<div id="ctl">
<select id="life" title="life"></select>
<span id="days"><button id="prev">&larr;</button><select id="day" title="day"></select><button id="next">&rarr;</button></span>
<select id="chan" title="channel"></select>
<select id="thr" title="thread"></select>
<input id="q" placeholder="search text/sender" size="18">
</div><div id="count"></div><div id="view"></div>
<script>var DATA=__DATA__;
var CHAT=["imessage","whatsapp","text","slack","groupme","hermes"];
var GROUPCH=["whatsapp","slack","groupme"];
var S={life:null,day:null,chan:"all",thr:"all",q:""};
function esc(s){return String(s==null?"":s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;")}
function obs(){return DATA[S.life].obs}
function days(){var m={},r=[];obs().forEach(function(o){var d=(o.collected_at||"").slice(0,10);m[d]=(m[d]||0)+1});
Object.keys(m).sort().forEach(function(d){r.push([d,m[d]])});return r}
function meta(o){return "source_id: "+o.source_id+"<br>written_at: "+esc(o.written_at)+"<br>collected_at: "+esc(o.collected_at)}
function hue(s){var h=0,i;for(i=0;i<s.length;i++)h=(h*31+s.charCodeAt(i))%360;return h}
/* NOTE: corpus 0.4.0 will carry real email subjects/bodies; until then the
   Subject below is derived as the first line/clause of the stub text. */
function subjOf(t){var l=String(t==null?"":t).split("\\n")[0];var c=l.split(/[.!?;:]/)[0]||l;c=c.trim();return c.slice(0,90)||"(no subject)"}
function byId(sid){var r=null;obs().forEach(function(o){if(o.source_id===sid)r=o});return r}
function chatHTML(t,msgs){var ch=msgs[0].channel,se={},i;for(i=0;i<msgs.length;i++)if(!msgs[i].from_owner)se[msgs[i].sender]=1;
var grp=GROUPCH.indexOf(ch)>=0||Object.keys(se).length>1;
var h='<div class="conv '+(grp?"grp":"dm")+'"><h3>'+esc(t)+" &middot; "+esc(ch)+" ("+msgs.length+")</h3><div class='cwin'>";
var prev=null,pday=null;
msgs.forEach(function(o){var day=(o.written_at||"").slice(0,10),tm=(o.written_at||"").slice(11,16);
if(day!==pday){h+='<div class="daydiv"><span>'+esc(day)+"</span></div>";prev=null;pday=day}
var jn=(prev===o.sender);
if(o.from_owner){h+='<div class="row me'+(jn?" joined":"")+'"><div class="col"><div class="bub">'+esc(o.text)+'<div class="tm">'+esc(tm)+'</div><details><summary>provenance</summary>'+meta(o)+"</details></div></div></div>"}
else{var hh=hue(String(o.sender)),init=esc(String(o.sender||"?").slice(0,1).toUpperCase());
var av=jn?'<div class="av ghost"></div>':'<div class="av" style="background:hsl('+hh+',55%,45%)">'+init+"</div>";
var nm=(grp&&!jn)?'<div class="nm">'+esc(o.sender)+"</div>":"";
h+='<div class="row'+(jn?" joined":"")+'">'+av+'<div class="col">'+nm+'<div class="bub">'+esc(o.text)+'<div class="tm">'+esc(tm)+'</div><details><summary>provenance</summary>'+meta(o)+"</details></div></div></div>"}
prev=o.sender});
return h+"</div></div>"}
function readHTML(o){var from=o.from_owner?"me":o.sender,to=o.from_owner?o.thread:"me";
return '<div class="subj">'+esc(subjOf(o.text))+"</div><div class='mh'><div><b>From:</b> "+esc(from)+"</div><div><b>To:</b> "+esc(to)+"</div><div><b>Date:</b> "+esc(o.written_at)+"</div><div><b>Thread:</b> "+esc(o.thread)+"</div></div><div class='body'>"+esc(o.text)+"</div><details><summary>provenance &middot; "+esc(o.source_id)+"</summary>"+meta(o)+"</details>"}
function mailHTML(t,msgs){var h='<div class="conv"><h3>'+esc(t)+" &middot; email ("+msgs.length+")</h3><div class='mailapp'><div class='maillist'>";
msgs.forEach(function(o,i){h+='<div class="mailitem'+(i===0?" active":"")+'" data-sid="'+esc(o.source_id)+'"><div class="mi-from">'+esc(o.from_owner?"me":o.sender)+'</div><div class="mi-subj">'+esc(subjOf(o.text))+'</div><div class="mi-date">'+esc((o.written_at||"").slice(0,16).replace("T"," "))+"</div></div>"});
return h+"</div><div class='mailread'>"+readHTML(msgs[0])+"</div></div></div>"}
function render(){var L=DATA[S.life];
document.getElementById("h-life").textContent="life: "+S.life;
document.getElementById("h-hash").textContent="bundle "+L.hash.slice(0,12)+" ("+L.bundle+")";
document.getElementById("h-day").textContent="day "+S.day+" (collected_at date)";
var dl=document.getElementById("day"),ds=days(),cur=dl.value;
dl.innerHTML=ds.map(function(d){return '<option value="'+d[0]+'">'+d[0]+" ("+d[1]+")</option>"}).join("");
if(ds.some(function(d){return d[0]===cur}))dl.value=cur;else if(!ds.some(function(d){return d[0]===S.day})){S.day=ds[0][0];dl.value=S.day}else dl.value=S.day;
var ch=document.getElementById("chan");
if(!ch.options.length){var cs=["all"].concat(CHAT.concat(["email"]));ch.innerHTML=cs.map(function(c){return "<option>"+c+"</option>"}).join("")}
ch.value=S.chan;
var th=document.getElementById("thr"),threads={};obs().forEach(function(o){threads[o.thread]=1});
var tl=["all"].concat(Object.keys(threads).sort());
th.innerHTML=tl.map(function(t){return '<option value="'+esc(t)+'">'+esc(t)+"</option>"}).join("");
if(tl.indexOf(S.thr)<0)S.thr="all";th.value=S.thr;
var q=S.q.toLowerCase();
var list=obs().filter(function(o){return (o.collected_at||"").slice(0,10)===S.day
&&(S.chan==="all"||o.channel===S.chan)&&(S.thr==="all"||o.thread===S.thr)
&&(!q||(o.text||"").toLowerCase().indexOf(q)>=0||(o.sender||"").toLowerCase().indexOf(q)>=0)});
list.sort(function(a,b){return (a.written_at<b.written_at?-1:a.written_at>b.written_at?1:0)||(a.source_id<b.source_id?-1:1)});
document.getElementById("count").textContent=list.length+" shown";
var v=document.getElementById("view"),h="";
var chat=list.filter(function(o){return CHAT.indexOf(o.channel)>=0});
var mail=list.filter(function(o){return o.channel==="email"});
var other=list.filter(function(o){return CHAT.indexOf(o.channel)<0&&o.channel!=="email"});
var byT={};chat.forEach(function(o){(byT[o.thread]=byT[o.thread]||[]).push(o)});
Object.keys(byT).sort().forEach(function(t){h+=chatHTML(t,byT[t])});
var byM={};mail.forEach(function(o){(byM[o.thread]=byM[o.thread]||[]).push(o)});
Object.keys(byM).sort().forEach(function(t){h+=mailHTML(t,byM[t])});
other.forEach(function(o){h+='<div class="mail"><div class="mi-from">['+esc(o.channel)+"] "+esc(o.sender)+'</div><div>'+esc(o.text)+'</div><div class="mi-date">'+esc(o.written_at)+'</div><details><summary>'+esc(o.source_id)+"</summary>"+meta(o)+"</details></div>"});
v.innerHTML=h||"<p>No observations match.</p>";
Array.prototype.forEach.call(v.querySelectorAll(".mailitem"),function(el){el.onclick=function(){var box=el.parentNode.parentNode;
Array.prototype.forEach.call(box.querySelectorAll(".mailitem"),function(x){x.classList.remove("active")});
el.classList.add("active");box.querySelector(".mailread").innerHTML=readHTML(byId(el.getAttribute("data-sid")))}})}
function init(){var l=document.getElementById("life");
l.innerHTML=Object.keys(DATA).sort().map(function(k){return "<option>"+esc(k)+"</option>"}).join("");
S.life=l.value;S.day=days()[0][0];
l.onchange=function(){S.life=l.value;S.thr="all";S.day=days()[0][0];render()};
document.getElementById("day").onchange=function(e){S.day=e.target.value;render()};
document.getElementById("prev").onclick=function(){var d=days().map(function(x){return x[0]}),i=d.indexOf(S.day);if(i>0){S.day=d[i-1];render()}};
document.getElementById("next").onclick=function(){var d=days().map(function(x){return x[0]}),i=d.indexOf(S.day);if(i<d.length-1){S.day=d[i+1];render()}};
document.getElementById("chan").onchange=function(e){S.chan=e.target.value;render()};
document.getElementById("thr").onchange=function(e){S.thr=e.target.value;render()};
document.getElementById("q").oninput=function(e){S.q=e.target.value;render()};
render()}
init();</script></body></html>"""


def main() -> None:
    lives = {}
    for rel in FILES:
        raw = (ROOT / rel).read_text(encoding="utf-8")
        bundle = json.loads(raw)
        claimed = bundle.get("bundle_hash", "")
        if not claimed or bundle_hash(bundle) != claimed:
            raise SystemExit(f"ABORT hash mismatch: {rel} "
                             f"(claims {claimed[:12] or 'none'})")
        for lid, life in bundle["lives"].items():
            lives[lid] = {"bundle": rel.split("/")[-1], "hash": claimed,
                          "obs": [{k: o.get(k, "") for k in FIELDS}
                                  for o in life["observations"]]}
        print(f"verified {rel}: hash {claimed[:12]} "
              f"lives={len(bundle['lives'])}")
    out = ROOT / "docs" / "corpus-explorer.html"
    out.write_text(TEMPLATE.replace("__DATA__", json.dumps(lives)),
                   encoding="utf-8")
    n = sum(len(v["obs"]) for v in lives.values())
    print(f"wrote {out} ({out.stat().st_size} bytes, "
          f"{len(lives)} lives, {n} obs)")


if __name__ == "__main__":
    main()
