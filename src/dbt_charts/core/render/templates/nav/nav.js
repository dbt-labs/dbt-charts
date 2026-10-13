(function(){
function syncInset(){
var n=document.querySelector('.dbt-nav[data-dbt-content-left]');
var s=document.querySelector('.dbt-charts-svg-container>svg');
if(!n||!s)return;
var i=parseFloat(n.getAttribute('data-dbt-content-left'));
var v=s.viewBox&&s.viewBox.baseVal;
var w=v&&v.width?v.width:parseFloat(s.getAttribute('width'));
var r=s.getBoundingClientRect();
if(!isFinite(i)||!isFinite(w)||w<=0||!r.width)return;
var p=Math.max(0,r.left+(i*r.width/w));
n.style.setProperty('--dbt-nav-page-inset',p.toFixed(2)+'px');
}
function queueSync(){window.requestAnimationFrame?window.requestAnimationFrame(syncInset):syncInset();}
function opts(m){return Array.prototype.slice.call(m.querySelectorAll('[data-dbt-nav-menu-option]'));}
function close(except){document.querySelectorAll('.dbt-nav-file-menu.dbt-nav-menu-open').forEach(function(m){if(m!==except){m.classList.remove('dbt-nav-menu-open');var t=document.querySelector('[data-dbt-nav-menu-id="'+m.id+'"]');if(t)t.setAttribute('aria-expanded','false');}});}
function open(t,m){close(m);m.classList.add('dbt-nav-menu-open');t.setAttribute('aria-expanded','true');}
function closeOne(t,m){m.classList.remove('dbt-nav-menu-open');t.setAttribute('aria-expanded','false');}
function focusOpt(m,idx){var o=opts(m);if(!o.length)return;o[(idx+o.length)%o.length].focus();}
document.querySelectorAll('.dbt-nav-file-trigger').forEach(function(t){
var m=document.getElementById(t.getAttribute('data-dbt-nav-menu-id'));
if(!m)return;
t.addEventListener('click',function(e){e.preventDefault();if(m.classList.contains('dbt-nav-menu-open'))closeOne(t,m);else open(t,m);});
t.addEventListener('keydown',function(e){if(e.key==='Enter'||e.key===' '||e.key==='ArrowDown'){e.preventDefault();open(t,m);var o=opts(m);var i=o.findIndex(function(x){return x.getAttribute('aria-selected')==='true';});focusOpt(m,i<0?0:i);}if(e.key==='Escape'){e.preventDefault();closeOne(t,m);t.focus();}});
m.addEventListener('keydown',function(e){var o=opts(m);var i=o.indexOf(document.activeElement);if(e.key==='Escape'){e.preventDefault();closeOne(t,m);t.focus();}else if(e.key==='ArrowDown'){e.preventDefault();focusOpt(m,i+1);}else if(e.key==='ArrowUp'){e.preventDefault();focusOpt(m,i-1);}else if(e.key==='Home'){e.preventDefault();focusOpt(m,0);}else if(e.key==='End'){e.preventDefault();focusOpt(m,o.length-1);}else if(e.key===' '){e.preventDefault();if(document.activeElement)document.activeElement.click();}});
opts(m).forEach(function(o){o.addEventListener('click',function(){closeOne(t,m);});});});
document.addEventListener('click',function(e){if(!e.target.closest('.dbt-nav-file')&&!e.target.closest('.dbt-nav-file-menu'))close();});
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',queueSync);else queueSync();
window.addEventListener('resize',queueSync);
})();
document.querySelectorAll('select.dbt-nav-download').forEach(function(s){s.addEventListener('change',function(){if(s.value){window.location.href=window.location.pathname.replace(/\/$/,'')+'.'+s.value;s.value='';}});});
(function(){var K='dbt-view',R=document.documentElement;
function chrome(){var n=document.querySelector('.dbt-nav');if(n)R.style.setProperty('--dbt-view-chrome',n.getBoundingClientRect().height+'px');}
function apply(v){R.classList.remove('dbt-view-fit-height','dbt-view-actual');if(v!=='fit-width')R.classList.add('dbt-view-'+v);chrome();}
var saved=null;try{saved=window.localStorage.getItem(K);}catch(e){}
apply(saved||'fit-width');
// The nav script is emitted ahead of the board, so everything touching the SVG waits
// for the document — the same reason syncInset() above defers.
function init(){
// The artifact carries responsive-embedding hints inline (boards.py) so a bare .svg
// behaves when dropped into a page. Inline style outranks every stylesheet rule, so
// left alone they pin the board to height:auto and no view mode can resize it. This
// host owns presentation, so drop the two that conflict and let page.css govern; the
// artifact itself is untouched, and no golden changes.
var svg=document.querySelector('.dbt-charts-svg-container>svg');
if(svg){svg.style.removeProperty('height');svg.style.removeProperty('max-width');}
chrome();
var s=document.querySelector('select.dbt-nav-view');
if(!s)return;
if(saved)s.value=saved;
s.addEventListener('change',function(){apply(s.value);try{window.localStorage.setItem(K,s.value);}catch(e){}
// syncInset() positions the nav off the rendered SVG rect, which every mode changes.
window.dispatchEvent(new Event('resize'));});}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init);else init();
})();
(function(){var R=document.documentElement;
function clear(){R.classList.remove('dbt-page-loading');document.removeEventListener('pointerdown',clear,true);document.removeEventListener('keydown',clear,true);}
// On window, so a handler that claims the click first (the file menu trigger,
// variables.js's in-page links) wins via defaultPrevented.
window.addEventListener('click',function(e){
if(e.defaultPrevented||e.button!==0||e.metaKey||e.ctrlKey||e.shiftKey||e.altKey)return;
var a=e.target.closest('a');
if(!a||a.hasAttribute('download'))return;
var t=a.getAttribute('target');
if(t&&t!=='_self')return;
var h=a.getAttribute('href')||a.getAttribute('xlink:href');
if(!h)return;
var u=new URL(h,window.location.href);
if(u.origin!==window.location.origin)return;
if(u.hash&&u.pathname===window.location.pathname&&u.search===window.location.search)return;
R.classList.add('dbt-page-loading');
// A navigation that never commits (Escape, Stop, a 204) fires no pageshow; the next
// interaction proves this document is still here.
document.addEventListener('pointerdown',clear,true);
document.addEventListener('keydown',clear,true);});
// The back/forward cache restores the page exactly as it was left.
window.addEventListener('pageshow',clear);
})();
