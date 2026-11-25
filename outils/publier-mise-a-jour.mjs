#!/usr/bin/env node
// Birdsong Analyzer — publier une mise à jour de l'appli dans index.html (page de connexion chiffrée).
// Ne demande AUCUN mot de passe : la version est chiffrée pour la clé de publication (partie publique dans index.html).
//
//   node publier-mise-a-jour.mjs <appli.html> <index.html> [--code BSA1:…]
//
// --code : seulement la première fois, avec le code donné par activer-mises-a-jour.html (index.html à l'ancien format).
// index.html est réécrit sur place : mêmes profils, mêmes mots de passe, même apparence ; seule l'appli change.
import fs from 'node:fs';
import zlib from 'node:zlib';
import { webcrypto as C } from 'node:crypto';
const LOADER = "(function () {\n  var USERS = __USERS__, ITER = __ITER__;\n  /* Publication des mises à jour : l'appli est chiffrée pour une « clé de publication » dont la partie privée est\n     elle-même chiffrée par la clé de l'appli (débloquée par le mot de passe de chaque profil). On peut donc publier\n     une nouvelle version sans connaître aucun mot de passe ; profils, mots de passe et « rester connecté » ne changent pas. */\n  var REL = __REL__;\n  var $ = function (id) { return document.getElementById(id); };\n  var dec = function (s) { var b = atob(s), a = new Uint8Array(b.length); for (var i = 0; i < b.length; i++) a[i] = b.charCodeAt(i); return a; };\n  var enc = function (a) { var s = ''; for (var i = 0; i < a.length; i++) s += String.fromCharCode(a[i]); return btoa(s); };\n  var get = function (st, k) { try { return st.getItem(k); } catch (e) { return null; } };\n  var put = function (st, k, v) { try { st.setItem(k, v); } catch (e) {} };\n  Object.keys(USERS).forEach(function (n) { var o = document.createElement('option'); o.textContent = n; $('u').appendChild(o); });\n  var last = get(localStorage, 'bsa-last'); if (last && USERS[last]) $('u').value = last;\n  // appli installée sur l'écran d'accueil : « rester connecté » est coché d'avance (pour l'ouvrir sans réseau ni mot de passe)\n  try { if (navigator.standalone || matchMedia('(display-mode: standalone)').matches) $('k').checked = true; } catch (e) {}\n  if ('serviceWorker' in navigator && (location.protocol === 'https:' || location.hostname === 'localhost')) navigator.serviceWorker.register('./sw.js').catch(function () {});\n\n  async function releaseKey(raw) {\n    var S = crypto.subtle, P256 = { name: 'ECDH', namedCurve: 'P-256' };\n    var k = await S.importKey('raw', raw, 'AES-GCM', false, ['decrypt']);\n    var jwk = JSON.parse(new TextDecoder().decode(await S.decrypt({ name: 'AES-GCM', iv: dec(REL.iv) }, k, dec(REL.w))));\n    var priv = await S.importKey('jwk', jwk, P256, false, ['deriveBits']);\n    var epub = await S.importKey('raw', dec(REL.epub), P256, false, []);\n    var bits = await S.deriveBits({ name: 'ECDH', public: epub }, priv, 256);\n    var hk = await S.importKey('raw', bits, 'HKDF', false, ['deriveKey']);\n    return S.deriveKey({ name: 'HKDF', hash: 'SHA-256', salt: dec(REL.salt), info: new TextEncoder().encode('birdsong-analyzer-release') },\n                       hk, { name: 'AES-GCM', length: 256 }, false, ['decrypt']);\n  }\n  async function openApp(raw, user) {\n    var key = await releaseKey(raw);\n    var gz = await crypto.subtle.decrypt({ name: 'AES-GCM', iv: dec(REL.piv) }, key, dec(document.getElementById('payload').textContent.trim()));\n    var html = await new Response(new Blob([gz]).stream().pipeThrough(new DecompressionStream('gzip'))).text();\n    put(sessionStorage, 'bsa-user', user); put(sessionStorage, 'bsa-key', enc(raw));\n    await window.__libs;\n    document.open(); document.write(html); document.close();\n  }\n  async function unlock(user, pass) {\n    var U = USERS[user], te = new TextEncoder(), salt = dec(U.s), un = te.encode(user.toLowerCase());\n    var full = new Uint8Array(salt.length + un.length); full.set(salt); full.set(un, salt.length);\n    var base = await crypto.subtle.importKey('raw', te.encode(pass), 'PBKDF2', false, ['deriveKey']);\n    var kk = await crypto.subtle.deriveKey({ name: 'PBKDF2', salt: full, iterations: ITER, hash: 'SHA-256' }, base, { name: 'AES-GCM', length: 256 }, false, ['decrypt']);\n    return new Uint8Array(await crypto.subtle.decrypt({ name: 'AES-GCM', iv: dec(U.i) }, kk, dec(U.w)));\n  }\n  $('f').addEventListener('submit', async function (e) {\n    e.preventDefault();\n    var user = $('u').value, pass = $('p').value.trim(), keep = $('k').checked;\n    $('b').disabled = true; $('msg').textContent = 'Vérification…';\n    var raw;\n    try { raw = await unlock(user, pass); }\n    catch (err) { $('b').disabled = false; $('msg').textContent = 'Mot de passe incorrect pour ce profil.'; $('p').select(); return; }\n    put(localStorage, 'bsa-last', user);\n    if (keep) { put(localStorage, 'bsa-key', enc(raw)); put(localStorage, 'bsa-user', user); }\n    try { await openApp(raw, user); }\n    catch (err) { $('b').disabled = false; $('msg').textContent = 'Ouverture impossible : navigateur trop ancien (utilise Chrome, Edge, Firefox ou Safari à jour).'; }\n  });\n  if (!window.crypto || !crypto.subtle || !window.DecompressionStream) { $('msg').textContent = 'Ce navigateur est trop ancien pour ouvrir l’appli. Utilise Chrome, Edge, Firefox ou Safari à jour.'; $('b').disabled = true; return; }\n  // session déjà ouverte (rechargement de la page) ou « rester connecté »\n  var k = get(sessionStorage, 'bsa-key'), u = get(sessionStorage, 'bsa-user');\n  if (!k) { k = get(localStorage, 'bsa-key'); u = get(localStorage, 'bsa-user'); }\n  if (k && u && USERS[u]) {\n    $('f').style.visibility = 'hidden';\n    openApp(dec(k), u).catch(function () { try { sessionStorage.removeItem('bsa-key'); localStorage.removeItem('bsa-key'); } catch (e) {} $('f').style.visibility = ''; });\n  }\n})();\n";
const S = C.subtle, P256 = { name: 'ECDH', namedCurve: 'P-256' };
const b64 = a => Buffer.from(a).toString('base64'), unb64 = s => new Uint8Array(Buffer.from(s, 'base64'));
const args = process.argv.slice(2);
const ci = args.indexOf('--code'), code = ci >= 0 ? args.splice(ci, 2)[1] : null;
const [appPath, idxPath] = args;
if (!appPath || !idxPath) { console.error('usage : node publier-mise-a-jour.mjs <appli.html> <index.html> [--code BSA1:…]'); process.exit(1); }
const app = fs.readFileSync(appPath, 'utf8'), idx = fs.readFileSync(idxPath, 'utf8');
if (!/^<!DOCTYPE html>/i.test(app.trim()) || /id="payload"/.test(app)) throw new Error("appli.html n'est pas la page de l'appli (en clair)");
const mU = idx.match(/var USERS = (\{.*?\}), ITER = (\d+)/s);
if (!mU) throw new Error('profils introuvables dans index.html');
const USERS = JSON.parse(mU[1]), ITER = +mU[2];
let pubB64, iv, w;
const mR = idx.match(/var REL = (\{.*?\});/s);
if (code) {
  const m = code.trim().match(/^BSA1:([A-Za-z0-9+/=]+):([A-Za-z0-9+/=]+):([A-Za-z0-9+/=]+)$/);
  if (!m) throw new Error('code d’activation illisible');
  [, pubB64, iv, w] = m;
} else if (mR) {
  const R = JSON.parse(mR[1]); pubB64 = R.pub; iv = R.iv; w = R.w;
} else throw new Error('index.html est à l’ancien format : il faut le code d’activation (--code)');
const gpub = await S.importKey('raw', unb64(pubB64), P256, true, []);
const eph = await S.generateKey(P256, true, ['deriveBits']);
const bits = await S.deriveBits({ name: 'ECDH', public: gpub }, eph.privateKey, 256);
const salt = C.getRandomValues(new Uint8Array(16)), piv = C.getRandomValues(new Uint8Array(12));
const hk = await S.importKey('raw', bits, 'HKDF', false, ['deriveKey']);
const key = await S.deriveKey({ name: 'HKDF', hash: 'SHA-256', salt, info: new TextEncoder().encode('birdsong-analyzer-release') }, hk, { name: 'AES-GCM', length: 256 }, false, ['encrypt']);
const gz = zlib.gzipSync(Buffer.from(app, 'utf8'), { level: 9 });
const ct = new Uint8Array(await S.encrypt({ name: 'AES-GCM', iv: piv }, key, gz));
const epub = new Uint8Array(await S.exportKey('raw', eph.publicKey));
const ver = (app.match(/<title>[^<]*?(v\d+)<\/title>/) || [, ''])[1];
const REL = { v: ver, pub: pubB64, iv, w, epub: b64(epub), salt: b64(salt), piv: b64(piv) };
// gabarit : tout ce qui précède l'appli chiffrée (apparence, formulaire) et le bloc des bibliothèques sont repris tels quels
const iP = idx.indexOf('<script type="application/octet-stream" id="payload">');
const head = idx.slice(0, iP);
const afterPayload = idx.slice(idx.indexOf('</script>', iP) + '</script>'.length);
const libs = afterPayload.match(/<script>\s*\/\* Les bibliothèques[\s\S]*?<\/script>/);
if (iP < 0 || !libs) throw new Error('structure de index.html inattendue');
const tpl = LOADER;
const loader = tpl.replace('__USERS__', JSON.stringify(USERS)).replace('__ITER__', String(ITER)).replace('__REL__', JSON.stringify(REL));
let ctB64 = b64(ct).replace(/(.{76})/g, '$1\n');
const out = head + '<script type="application/octet-stream" id="payload">' + ctB64 + '</script>\n' + libs[0] + '\n<script>\n' + loader + '</script>\n</body>\n</html>\n';
fs.writeFileSync(idxPath, out);
console.log(`index.html mis à jour : appli ${ver || ''} (${Math.round(app.length / 1024)} Ko → ${Math.round(ct.length / 1024)} Ko chiffrés), ${Object.keys(USERS).length} profils inchangés.`);

