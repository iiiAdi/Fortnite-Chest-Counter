/**
 * Chest Counter - friends leaderboard (Google Apps Script).
 * Each friend's app POSTs their season total here; opening the web app link shows the ranking live.
 *
 * Starting a new season: open this project in the script editor, pick the function
 * "newSeason" in the toolbar and press Run. Everyone's season total resets to 0
 * the next time their app syncs. (Only you, the owner, can do this.)
 */

// Change this to a long random string. It is the group key: it goes inside the leaderboard
// link you share with friends (<web app URL>?key=<SECRET>), so only people with the link can join.
const SECRET = 'CHANGE-ME';
const MAX_PLAYERS = 100;

// The name shown at the top of the leaderboard, e.g. 'Chapter 7 Season 4'.
// Leave '' to show a plain "Season 1", "Season 2"... counter instead.
// When a new Fortnite season starts: change this, run newSeason(), then publish a new version.
const SEASON_NAME = 'Chapter 7 Season 4';

// Optional: link to the app download (for example your GitHub release page).
// When set, the leaderboard page shows a "Download the app" button. Leave '' for no button.
const DOWNLOAD_URL = 'https://github.com/iiiAdi/Fortnite-Chest-Counter/releases/latest';

// Each friend's app sends its counts here.
function doPost(e) {
  const lock = LockService.getScriptLock();
  try {
    if (SECRET === 'CHANGE-ME') return out_({ ok: false, error: 'set SECRET first' });
    const body = JSON.parse(e.postData.contents);
    if (body.key !== SECRET) return out_({ ok: false, error: 'bad key' });

    const id = String(body.id || '');
    if (!/^[a-f0-9]{8,64}$/.test(id)) return out_({ ok: false, error: 'bad id' });
    const name = String(body.name || '').trim().substring(0, 24);
    if (!name) return out_({ ok: false, error: 'missing name' });

    lock.waitLock(10000);
    const props = PropertiesService.getScriptProperties();
    const seasonId = Number(props.getProperty('seasonId')) || 1;

    // The app sends the season it believes it is in. null = brand new app, it just adopts ours.
    // A different number means a new season started: tell the app to reset instead of saving.
    const sent = body.seasonId;
    if (sent !== null && sent !== undefined && Number(sent) !== seasonId) {
      return out_({ ok: true, reset: true, seasonId: seasonId });
    }

    const k = 'p_' + id;
    if (!props.getProperty(k) && countPlayers_(props) >= MAX_PLAYERS) {
      return out_({ ok: false, error: 'group is full' });
    }
    // "updated" = when the chest count last changed. "seen" = last time the app checked in
    // (every post, including the app's regular heartbeat), which drives the green online dot.
    const season = clamp_(body.season);
    const now = Date.now();
    let updated = now;
    const prev = props.getProperty(k);
    if (prev) {
      try {
        const old = JSON.parse(prev);
        if (old.season === season && old.updated) updated = old.updated;
      } catch (ignore) {}
    }
    props.setProperty(k, JSON.stringify({ name: name, season: season, updated: updated, seen: now }));
    return out_({ ok: true, reset: false, seasonId: seasonId });
  } catch (err) {
    return out_({ ok: false, error: String(err) });
  } finally {
    try { lock.releaseLock(); } catch (ignore) {}
  }
}

// Opening  <web app URL>?key=<SECRET>  shows the leaderboard.
function doGet(e) {
  const key = (e && e.parameter && e.parameter.key) || '';
  if (SECRET === 'CHANGE-ME') {
    return HtmlService.createHtmlOutput('<p>Open Code.gs and change SECRET first.</p>');
  }
  if (key !== SECRET) {
    return HtmlService.createHtmlOutput('<p>Wrong or missing key.</p>');
  }
  return HtmlService.createHtmlOutput(page_(key))
    .setTitle('Chest Leaderboard')
    .addMetaTag('viewport', 'width=device-width, initial-scale=1');
}

// Called by the page (via google.script.run) every few seconds.
function getData(key) {
  if (key !== SECRET) throw new Error('bad key');
  const props = PropertiesService.getScriptProperties();
  const all = props.getProperties();
  const players = [];
  Object.keys(all).forEach(function (k) {
    if (k.indexOf('p_') !== 0) return;
    try {
      const p = JSON.parse(all[k]);
      players.push({ name: p.name, season: p.season, updated: p.updated, seen: p.seen || p.updated });
    } catch (ignore) {}
  });
  players.sort(function (a, b) {
    return (b.season - a.season) || String(a.name).localeCompare(String(b.name));
  });
  return { seasonId: Number(props.getProperty('seasonId')) || 1, seasonName: SEASON_NAME, players: players, now: Date.now() };
}

// Run this from the script editor to start a new season. Wipes everyone's season total.
function newSeason() {
  const lock = LockService.getScriptLock();
  lock.waitLock(10000);
  try {
    const props = PropertiesService.getScriptProperties();
    const seasonId = (Number(props.getProperty('seasonId')) || 1) + 1;
    props.getKeys().forEach(function (k) {
      if (k.indexOf('p_') === 0) props.deleteProperty(k);
    });
    props.setProperty('seasonId', String(seasonId));
  } finally {
    lock.releaseLock();
  }
}

function countPlayers_(props) {
  return props.getKeys().filter(function (k) { return k.indexOf('p_') === 0; }).length;
}

function clamp_(v) {
  return Math.max(0, Math.min(1000000, Math.floor(Number(v) || 0)));
}

function out_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}

function esc_(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function page_(key) {
  const dl = DOWNLOAD_URL
    ? '<a class="dl" href="' + esc_(DOWNLOAD_URL) + '" target="_blank" rel="noopener">Download the app</a>'
    : '';
  return '<!DOCTYPE html><html><head><meta charset="utf-8">' +
  '<style>' +
  'body{margin:0;background:#101418;color:#fff;font-family:Segoe UI,Roboto,Arial,sans-serif;padding:16px;max-width:560px;margin:0 auto;}' +
  'h1{font-size:20px;margin:4px 0 2px;}' +
  '#season{font-size:14px;color:#9aa4ad;margin-bottom:14px;}' +
  '.row{display:flex;align-items:center;background:#1a2027;border-radius:12px;padding:12px 14px;margin-bottom:8px;}' +
  '.row.top{background:#1f2a24;}' +
  '.rank{width:38px;font-size:20px;text-align:center;color:#9aa4ad;}' +
  '.who{flex:1;display:flex;flex-direction:column;min-width:0;padding:0 8px;}' +
  '.name{font-size:17px;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}' +
  '.dot{display:inline-block;width:9px;height:9px;border-radius:50%;background:#444c55;margin-right:7px;}' +
  '.dot.on{background:#3ddc84;box-shadow:0 0 6px #3ddc84;}' +
  '.sub{font-size:12px;color:#7b858e;}' +
  '.num{font-size:28px;font-weight:700;color:#7CFC9A;}' +
  '#msg{text-align:center;color:#7b858e;font-size:14px;margin-top:14px;}' +
  '.dl{display:block;text-align:center;background:#7CFC9A;color:#101418;font-weight:700;text-decoration:none;border-radius:12px;padding:12px;margin-top:16px;}' +
  '</style></head><body>' +
  '<h1>Chest Leaderboard</h1><div id="season">Loading...</div>' +
  '<div id="rows"></div><div id="msg"></div>' + dl +
  '<script>' +
  'var KEY=' + JSON.stringify(key) + ';var last=null,fetchedAt=0;' +
  'function age(ms){return (last.now-ms)+(Date.now()-fetchedAt);}' +
  'function agoText(a){var s=Math.max(0,Math.round(a/1000));' +
  'if(s<60)return "just now";var m=Math.round(s/60);if(m<60)return m+" min ago";' +
  'var h=Math.round(m/60);if(h<48)return h+" h ago";return Math.round(h/24)+" days ago";}' +
  'function mk(tag,cls,text){var el=document.createElement(tag);el.className=cls;el.textContent=text;return el;}' +
  'function render(){if(!last)return;' +
  'document.getElementById("season").textContent=last.seasonName||("Season "+last.seasonId);' +
  'var box=document.getElementById("rows");box.textContent="";' +
  'if(!last.players.length){document.getElementById("msg").textContent="No players yet";return;}' +
  'document.getElementById("msg").textContent="";' +
  'var medals=["\\uD83E\\uDD47","\\uD83E\\uDD48","\\uD83E\\uDD49"];' +
  'last.players.forEach(function(p,i){' +
  'var online=age(p.seen)<180000;' +
  'var row=mk("div","row"+(i<3?" top":""),"");' +
  'row.appendChild(mk("span","rank",medals[i]||String(i+1)));' +
  'var who=mk("span","who","");var nm=mk("span","name","");' +
  'nm.appendChild(mk("span","dot"+(online?" on":""),""));nm.appendChild(document.createTextNode(p.name));' +
  'who.appendChild(nm);' +
  'who.appendChild(mk("span","sub",(online?"Online \\u00b7 ":"")+"counted "+agoText(age(p.updated))));' +
  'row.appendChild(who);row.appendChild(mk("span","num",String(p.season)));box.appendChild(row);});}' +
  'function onData(d){last=d;fetchedAt=Date.now();render();}' +
  'function onFail(){document.getElementById("msg").textContent="Could not reach the server";}' +
  'function poll(){if(document.hidden)return;' +
  'google.script.run.withSuccessHandler(onData).withFailureHandler(onFail).getData(KEY);}' +
  'poll();setInterval(poll,15000);setInterval(render,1000);' +
  'document.addEventListener("visibilitychange",poll);' +
  '</script></body></html>';
}
