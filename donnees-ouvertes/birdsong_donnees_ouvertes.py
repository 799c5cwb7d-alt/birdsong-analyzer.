#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Birdsong Analyzer — collecte et mesure des sons d'oiseaux en données ouvertes (France)
=====================================================================================

Sources (via GBIF, qui republie les deux jeux de données avec le lien de chaque son) :
  • iNaturalist — observations « recherche » d'oiseaux en France avec un son
  • xeno-canto  — enregistrements d'oiseaux faits en France

Fonctionnement par LOTS (pour ne jamais occuper plus que --lot-go sur le disque) :
  1. inventaire GBIF (métadonnées seulement, une fois puis mise à jour avec --maj-inventaire)
  2. téléchargement d'un lot de sons (20 Go maximum par défaut)
  3. mesure de chaque son avec LA MÊME CHAÎNE que Birdsong Analyzer (v64, méthode de mesure 46) :
     gain → 2 passe-haut → passe-bas → FFT 1024 Blackman (60 images/s) → réduction de bruit → suivi des cris
  4. intégration : birdsong-opendata.json (à charger dans l'onglet 🌍 Données ouvertes de l'appli)
     + tableaux CSV (par enregistrement, par espèce)
  5. suppression des sons du lot, puis lot suivant — jusqu'à ce que tout soit mesuré.
  Le travail reprend là où il s'est arrêté (Ctrl+C sans risque).

Respect des serveurs :
  • iNaturalist : au plus 4,5 Go par heure et 22 Go par 24 h (iNaturalist bloque définitivement au-delà de 5 Go/h ou 24 Go/jour)
  • xeno-canto : un téléchargement à la fois, au plus 1 par seconde
  • GBIF : une requête par seconde

Installation (une seule fois) :
  1. Python 3.9 ou plus récent : https://www.python.org/downloads/  (cocher « Add python to PATH »)
  2. Dans un terminal :  python -m pip install numpy scipy
  3. ffmpeg (pour lire les m4a/mp3) — Windows : winget install ffmpeg   ·   Mac : brew install ffmpeg

Utilisation :
  python birdsong_donnees_ouvertes.py --dossier D:\\birdsong-donnees --essai 10     (essai : 10 sons)
  python birdsong_donnees_ouvertes.py --dossier D:\\birdsong-donnees                (tout, par lots de 20 Go)
  python birdsong_donnees_ouvertes.py --dossier D:\\birdsong-donnees --copie-vers "C:\\...\\mon-depot-github\\donnees-ouvertes"

Licences : les sons restent la propriété de leurs auteurs (CC BY, CC BY-NC, CC BY-NC-SA…). Le script ne garde
que des MESURES ; le tableau par enregistrement conserve l'auteur, la licence et le lien de chaque son pour les citer.
"""

import argparse
import csv
import hashlib
import json
import math
import os
import random
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone

try:
    import numpy as np
    from scipy.signal import lfilter
except ImportError:
    print("Il manque numpy ou scipy. Installe-les avec :  python -m pip install numpy scipy")
    sys.exit(1)

VERSION = "1.2"
MEAS_VER = 46                       # version de la méthode de mesure de Birdsong Analyzer reproduite ici

GBIF = "https://api.gbif.org/v1"
DATASETS = {
    "inat": {"key": "50c9509d-22c7-4a22-a47d-8c48425ef4a7", "nom": "iNaturalist", "extra": {"mediaType": "Sound"}},
    "xc":   {"key": "b1047888-ae52-4179-9dd5-5448ea342a24", "nom": "xeno-canto", "extra": {}},
}
AVES_KEY = 212

INAT_GO_HEURE = 4.5                 # marges sous les limites d'iNaturalist (5 Go/h, 24 Go/jour)
INAT_GO_JOUR = 22.0
GO = 1024 ** 3


# ═══════════════════════════════════════════════════════════════════════════════════════════════
#  1. MESURE — portage fidèle de la chaîne de Birdsong Analyzer v64 (refAnalyzeBuffer, nrStep,
#     trackStep, summarizeCall, nonBirdReason, refSelectCalls)
# ═══════════════════════════════════════════════════════════════════════════════════════════════

REF_N = 1024
REF_MAX_S = 300
NR_GAIN, NR_CALIB_S, NR_BURST_ON, NR_BURST_OFF, NR_MIN_SNR = 1.6, 1.0, 0.50, 0.30, 14
NR_MARGIN_DB, NRF_EXTRA_DB = 7, 3
CALL_GAP_END_S, CALL_NOTE_GAP_S, CALL_MIN_JUMP, CALL_JUMP_HZ_PER_S, CALL_SHORT_MAG, CALL_WIN_PAD_S = 0.09, 0.05, 700, 90000, 60, 0.012
MT_MAX_TRACKS, MT_MAX_RIDGES, MT_REL_DB, MT_BPD = 6, 8, 25, 255 / 70
REF_SHAPES = ['up', 'flatup', 'down', 'flat', 'arch', 'valley', 'short']

# fenêtre de Blackman (α = 0,16, périodique) — identique à REF_WIN (Float32Array)
_I = np.arange(REF_N)
REF_WIN = (0.42 - 0.5 * np.cos(2 * np.pi * _I / REF_N) + 0.08 * np.cos(4 * np.pi * _I / REF_N)).astype(np.float32)


def jsround(x):
    """Math.round de JavaScript (arrondi vers +∞ pour .5)."""
    return int(math.floor(x + 0.5))


# ── filtres biquad selon la spécification Web Audio (BiquadFilterNode) ──
def biquad(kind, f0, q, sr, gain_db=0.0):
    w0 = 2 * math.pi * f0 / sr
    cw, sw = math.cos(w0), math.sin(w0)
    if kind in ('lowpass', 'highpass'):
        alpha = sw / (2 * 10 ** (q / 20))          # Web Audio : Q en dB pour passe-bas / passe-haut
        if kind == 'lowpass':
            b = [(1 - cw) / 2, 1 - cw, (1 - cw) / 2]
        else:
            b = [(1 + cw) / 2, -(1 + cw), (1 + cw) / 2]
        a = [1 + alpha, -2 * cw, 1 - alpha]
    elif kind == 'peaking':
        A = 10 ** (gain_db / 40)
        alpha = sw / (2 * q)
        b = [1 + alpha * A, -2 * cw, 1 - alpha * A]
        a = [1 + alpha / A, -2 * cw, 1 - alpha / A]
    else:
        raise ValueError(kind)
    return np.array(b) / a[0], np.array(a) / a[0]


def chaine_filtres(x, sr, gain, hp, lp):
    """gain → passe-haut ×2 (Q 0,7) → passe-bas (Q 0,5) → égaliseurs (0 dB quand la réduction de bruit est active)."""
    y = x.astype(np.float64) * gain
    for kind, f, q in (('highpass', hp, 0.7), ('highpass', hp, 0.7), ('lowpass', lp, 0.5)):
        b, a = biquad(kind, f, q, sr)
        y = lfilter(b, a, y)
    return y.astype(np.float32)


def spectres_octets(x, ends, min_db=-100.0, max_db=-30.0):
    """Émulation d'AnalyserNode (refSpectrumBytes) pour plusieurs fins de trame : |X|/N → dB → octet."""
    k = 255.0 / (max_db - min_db)
    out = np.zeros((len(ends), REF_N // 2), dtype=np.uint8)
    for s in range(0, len(ends), 2048):
        e = ends[s:s + 2048]
        idx = (e[:, None] - REF_N) + _I[None, :]
        fr = x[idx] * REF_WIN[None, :]                      # produit stocké en float32, comme dans l'appli
        X = np.fft.rfft(fr.astype(np.float64), axis=1)[:, :REF_N // 2]
        m = np.abs(X) / REF_N
        with np.errstate(divide='ignore'):
            db = 20 * np.log10(m)
        v = np.floor(k * (db - min_db))
        v[~(m > 0)] = 0
        out[s:s + len(e)] = np.clip(v, 0, 255).astype(np.uint8)
    return out


class EtatNR:
    def __init__(self, n):
        self.F = np.zeros(n, np.float32)
        self.S = np.zeros(n, np.float32)
        self.t = 0.0
        self.inited = False
        self.calibrating = True
        self.burst = False


def nr_step(st, margin_db, raw, dt, bpd, b0, b1):
    """Réduction de bruit adaptative (nrStep) → spectre nettoyé (octets) et état de rafale."""
    dt = max(0.004, min(dt, 0.05))
    r = raw.astype(np.float64)
    F = r.copy() if not st.inited else st.F.astype(np.float64)
    warm = st.t < NR_CALIB_S
    marginU, evU = margin_db * bpd, 9 * bpd
    ex = r - F
    if warm:
        f = F + ex * 0.2
        S32 = np.zeros_like(st.S)
    else:
        aDown = 1 - math.exp(-dt / 1.0)
        aUp = 1 - math.exp(-dt / 1.5)
        aStuck = 1 - math.exp(-dt / 2)
        neg = ex < 0
        mid = (~neg) & (ex < evU)
        hi = ~(neg | mid)
        f = F.copy()
        f[neg] += ex[neg] * aDown
        f[mid] += ex[mid] * aUp
        S32 = st.S.copy()
        S32[neg | mid] = 0
        S32[hi] = (st.S[hi].astype(np.float64) + dt).astype(np.float32)
        stuck = hi & (S32 > 3)
        f[stuck] += ex[stuck] * aStuck
    st.F = f.astype(np.float32)
    st.S = S32
    E = (r - f).astype(np.float32).astype(np.float64)
    a = np.empty_like(E); c = np.empty_like(E)
    a[1:] = E[:-1]; a[0] = E[0]
    c[:-1] = E[1:]; c[-1] = E[-1]
    med = np.maximum(np.minimum(a, E), np.minimum(np.maximum(a, E), c))
    v = (med - marginU) * NR_GAIN
    pos = v > 0
    out = np.zeros(len(raw), np.uint8)
    out[pos] = np.trunc(np.minimum(v[pos], 255)).astype(np.uint8)
    span = max(1, b1 - b0)
    covered = int(np.count_nonzero(pos[b0:b1]))
    st.inited = True
    st.t += dt
    st.calibrating = st.t < NR_CALIB_S
    cov = covered / span
    if not st.burst and cov > NR_BURST_ON and not st.calibrating:
        st.burst = True
    elif st.burst and cov < NR_BURST_OFF:
        st.burst = False
    return out, st.burst


def find_ridges(C, b0, b1, min_val, max_n):
    hi = min(b1, len(C) - 1)
    lo = max(1, b0)
    if hi <= lo:
        return []
    seg = C[lo:hi]
    if seg.max() < min_val:
        return []
    seg = seg.astype(np.int64)
    left = C[lo - 1:hi - 1].astype(np.int64)
    right = C[lo + 1:hi + 1].astype(np.int64)
    idx = np.nonzero((seg >= min_val) & (seg >= left) & (seg > right))[0]
    if not len(idx):
        return []
    found = []
    for j in idx:
        v, a, c = int(seg[j]), int(left[j]), int(right[j])
        d = a - 2 * v + c
        off = 0.5 * (a - c) / d if d != 0 else 0.0
        found.append((lo + j + max(-0.5, min(0.5, off)), v))
    found.sort(key=lambda p: -p[1])                 # tri stable, comme Array.prototype.sort
    kept = []
    for p in found:
        if all(abs(k[0] - p[0]) > 3 for k in kept):
            kept.append(p)
        if len(kept) >= max_n:
            break
    return kept


def _mean(arr):
    return sum(arr) / (len(arr) or 1)


def summarize_call(a):
    allp = a['pts']
    if len(allp) >= 5:
        keep = []
        for i, p in enumerate(allp):
            w = sorted(q['f'] for q in allp[max(0, i - 2):i + 3])
            med = w[len(w) // 2]
            if abs(p['f'] - med) <= max(250, 0.12 * med):
                keep.append(p)
        if len(keep) >= 2:
            allp = keep
    BPD = 255 / 70
    mTop = max(p['mag'] for p in allp)
    strong = [p for p in allp if p['mag'] >= mTop - 12 * BPD]
    body = [p for p in allp if p['mag'] >= mTop - 20 * BPD]
    pts = strong if len(strong) >= 2 else allp
    span = body if len(body) >= 1 else allp
    n = len(pts)
    fs = [p['f'] for p in pts]
    mags = [p['mag'] for p in pts]
    srt = sorted(fs)

    def q(pp):
        return srt[min(n - 1, max(0, jsround(pp * (n - 1))))]

    peak = 0
    for i in range(1, n):
        if mags[i] > mags[peak]:
            peak = i
    k = max(1, jsround(n * 0.25))
    fStart = _mean(fs[:k])
    fEnd = _mean(fs[n - k:])
    mi = n // 2
    fMid = _mean(fs[max(0, mi - k // 2):mi + math.ceil(k / 2)])
    fMean = _mean(fs)
    shape = 'short'
    if n >= 3:
        d = fEnd - fStart
        rng = q(0.95) - q(0.05)
        tol = 0.07 * fMean
        if abs(d) < tol and rng < 0.12 * fMean:
            shape = 'flat'
        elif fMid > max(fStart, fEnd) + 0.05 * fMean:
            shape = 'arch'
        elif fMid < min(fStart, fEnd) - 0.05 * fMean:
            shape = 'valley'
        elif d > tol:
            shape = 'up'
        elif d < -tol:
            shape = 'down'
        else:
            shape = 'flat'
        if shape == 'up' and n >= 5:
            lead = 0
            while lead < n and abs(fs[lead] - fStart) < 0.04 * fMean:
                lead += 1
            if lead / n >= 0.3:
                shape = 'flatup'
    harm = 1 if sum(1 for p in allp if p['h2']) >= max(2, 0.4 * len(allp)) else 0
    fsd = math.sqrt(sum((v - fMean) ** 2 for v in fs) / max(1, n)) / max(1, fMean)
    return {'t0': span[0]['t'], 't1': span[-1]['t'], 'dur': (span[-1]['t'] - span[0]['t']) + CALL_WIN_PAD_S,
            'n': len(allp), 'fStd': fsd, 'fPeak': fs[peak], 'fMed': q(0.5), 'fMin': q(0.05), 'fMax': q(0.95),
            'fStart': fStart, 'fEnd': fEnd, 'fMean': fMean, 'shape': shape, 'notes': a['notes'], 'harm': harm,
            'mag': max(mags), 'magMean': _mean(mags)}


def _is_harm(f, g):
    k = f / g
    return (1.94 < k < 2.06) or (2.91 < k < 3.09)


def track_step(tr, t, C, hz, b0, b1, burst, min_val):
    done = []
    ridges = [] if burst else find_ridges(C, b0, b1, min_val, MT_MAX_RIDGES)
    if ridges:
        top = ridges[0][1]
        ridges = [r for r in ridges if r[1] >= top - MT_REL_DB * MT_BPD]

    def close(a):
        if len(a['pts']) >= 2 or a['pts'][0]['mag'] >= CALL_SHORT_MAG:
            done.append(summarize_call(a))

    if any(t < a['lastT'] - 0.05 for a in tr['tracks']):
        for a in tr['tracks']:
            close(a)
        tr['tracks'] = []
        tr['pendings'] = []
    tr['pendings'] = [p for p in tr['pendings'] if t >= p['t'] - 0.05]

    # 1) chaque piste reprend la crête la plus proche
    pairs = []
    for ai, a in enumerate(tr['tracks']):
        max_jump = min(1800, max(CALL_MIN_JUMP, CALL_JUMP_HZ_PER_S * max(0.005, min(0.02, t - a['lastT']))))
        n = len(a['pts'])
        pv = a['pts'][n - 2] if n >= 2 else None
        slope = max(-60000, min(60000, (a['lastF'] - pv['f']) / (a['lastT'] - pv['t']))) if (pv and a['lastT'] > pv['t']) else 0
        pred = a['lastF'] + slope * (t - a['lastT']) * 0.7
        for ri, r in enumerate(ridges):
            f = r[0] * hz
            d0 = abs(f - a['lastF'])
            d1 = abs(f - pred)
            if d0 <= CALL_MIN_JUMP or (n >= 2 and d1 <= 900 and d0 <= max_jump + 700):
                pairs.append((min(d0 * 1.3, d1), ai, ri))
    pairs.sort(key=lambda p: p[0])
    usedA, usedR, usedR_order = set(), set(), []
    for _, ai, ri in pairs:
        if ai in usedA or ri in usedR:
            continue
        usedA.add(ai); usedR.add(ri); usedR_order.append(ri)
        a = tr['tracks'][ai]
        f = ridges[ri][0] * hz
        if t - a['lastT'] > CALL_NOTE_GAP_S:
            a['notes'] += 1
        pt = {'t': t, 'f': f, 'mag': ridges[ri][1], 'h2': 0}
        a['pts'].append(pt); a['lastT'] = t; a['lastF'] = f; a['newPt'] = pt
    # 2) harmoniques des crêtes suivies
    followed = [ridges[i] for i in usedR_order]
    for ri, r in enumerate(ridges):
        if ri in usedR:
            continue
        f = r[0] * hz
        if any(_is_harm(f, g[0] * hz) and r[1] <= g[1] + 3 * MT_BPD for g in followed):
            usedR.add(ri); usedR_order.append(ri)
            for a in tr['tracks']:
                if (a.get('newPt') and abs(f / a['lastF'] - 2) < 0.15) or (a.get('newPt') and abs(f / a['lastF'] - 3) < 0.2):
                    a['newPt']['h2'] = 1
    # 3) pistes terminées
    keep = []
    for a in tr['tracks']:
        if t - a['lastT'] > CALL_GAP_END_S or burst:
            close(a)
        else:
            keep.append(a)
    tr['tracks'] = keep
    # 4) images isolées
    newp = []
    for p in tr['pendings']:
        if t - p['t'] < 0.06:
            newp.append(p)
            continue
        if p['mag'] >= CALL_SHORT_MAG:
            done.append(summarize_call({'pts': [p['pt']], 'notes': 1}))
    tr['pendings'] = newp
    # 5) crêtes libres
    taken = [ridges[i][0] * hz for i in usedR_order]
    for ri, r in enumerate(ridges):
        if ri in usedR:
            continue
        f = r[0] * hz
        if any(abs(f - g) < max(250, 0.08 * g) for g in taken):
            continue
        if any(gi != ri and g[1] >= r[1] - 3 * MT_BPD and _is_harm(f, g[0] * hz) for gi, g in enumerate(ridges)):
            continue
        bi, bd = -1, float('inf')
        for pi, p in enumerate(tr['pendings']):
            d = abs(f - p['f'])
            if t - p['t'] < 0.06 and t > p['t'] and d <= max(CALL_MIN_JUMP, CALL_JUMP_HZ_PER_S * (t - p['t'])) and d < bd:
                bd, bi = d, pi
        if bi >= 0 and len(tr['tracks']) < MT_MAX_TRACKS:
            p = tr['pendings'].pop(bi)
            pt = {'t': t, 'f': f, 'mag': r[1], 'h2': 0}
            tr['tracks'].append({'pts': [p['pt'], pt], 'lastT': t, 'lastF': f, 'notes': 1, 'newPt': pt})
        elif len(tr['pendings']) < MT_MAX_TRACKS and not any(p['t'] == t and abs(p['f'] - f) < 200 for p in tr['pendings']):
            tr['pendings'].append({'t': t, 'f': f, 'mag': r[1], 'pt': {'t': t, 'f': f, 'mag': r[1], 'h2': 0}})
    for a in tr['tracks']:
        if a.get('newPt') and a['newPt']['t'] != t:
            a['newPt'] = None
    return done


def non_bird_reason(c):
    rel = (c['fMax'] - c['fMin']) / max(1, c['fMean'])
    if c['dur'] >= 3 and rel < 0.04:
        return 'tonalité continue'
    if c['fPeak'] < 1500 and c['dur'] > 1.2 and c['shape'] in ('up', 'down'):
        return 'sirène ou voix'
    if c['dur'] > 8:
        return 'son trop long'
    if c['fStd'] < 0.0025 and c['dur'] >= 0.25 and c['n'] >= 10 and c['shape'] == 'flat':
        return 'son artificiel'
    return None


def select_calls(calls):
    """refSelectCalls : cris plausibles et nets (≥ 55 % du 90e centile des niveaux)."""
    ok = [c for c in calls if not non_bird_reason(c)]
    if not ok:
        return []
    mags = sorted(c['mag'] for c in ok)
    m90 = mags[int(math.floor(0.9 * (len(mags) - 1)))]
    return [c for c in ok if c['mag'] >= 0.55 * m90]


def analyser_signal(x, sr, gain=12.0, hp=800.0, lp=12000.0, max_s=REF_MAX_S):
    """refAnalyzeBuffer : signal mono (float32) → liste des cris résumés (avant sélection)."""
    n_total = len(x)
    ln = min(n_total, int(math.floor(max_s * sr)))
    if ln < REF_N:
        return []
    lp = min(lp, sr / 2 - 100)
    y = chaine_filtres(x[:ln], sr, gain, hp, lp)
    hop = max(1, jsround(sr / 60))
    dt = hop / sr
    hz2 = (sr / 2) / (REF_N / 2)
    fb0 = max(1, int(math.floor(hp / hz2)))
    fb1 = min(REF_N // 2 - 1, int(math.floor(lp / hz2)))
    bpd = 255 / 70
    ends = np.arange(REF_N, ln + 1, hop)
    spec = spectres_octets(y, ends)
    st = EtatNR(REF_N // 2)
    margin = NR_MARGIN_DB + NRF_EXTRA_DB
    # calibrage du bruit sur le début du fichier (bouclé si le fichier est très court)
    e, guard = REF_N, 0
    while st.t < NR_CALIB_S + dt and guard < 100000:
        if e > ln:
            e = REF_N
        nr_step(st, margin, spec[(e - REF_N) // hop], dt, bpd, fb0, fb1)
        e += hop
        guard += 1
    tr = {'tracks': [], 'pendings': []}
    calls = []
    for i, e in enumerate(ends):
        clean, burst = nr_step(st, margin, spec[i], dt, bpd, fb0, fb1)
        calls.extend(track_step(tr, e / sr, clean, hz2, fb0, fb1, burst, NR_MIN_SNR))
    zero = np.zeros(REF_N // 2, np.uint8)
    for k in range(1, 21):
        calls.extend(track_step(tr, ln / sr + k * dt, zero, hz2, fb0, fb1, False, NR_MIN_SNR))
    return calls


def ligne_cri(c):
    return [jsround(c['fPeak']), jsround(c['fMin']), jsround(c['fMax']), jsround(c['dur'] * 1000),
            max(0, REF_SHAPES.index(c['shape'])), int(c['notes'])]


# ── lecture des fichiers audio (ffmpeg) — comme l'appli : WAV = 1re voie à sa fréquence d'origine ;
#    autres formats = décodés à 48 kHz (fréquence d'un navigateur) puis voies moyennées ──
def sonder(ffprobe, path):
    out = subprocess.run([ffprobe, '-v', 'error', '-select_streams', 'a:0', '-show_entries',
                          'stream=codec_name,sample_rate,channels:format=duration,format_name', '-of', 'json', path],
                         capture_output=True, timeout=120)
    if out.returncode != 0:
        raise RuntimeError('fichier illisible')
    info = json.loads(out.stdout.decode('utf-8', 'replace') or '{}')
    s = (info.get('streams') or [{}])[0]
    fmt = info.get('format') or {}
    return {'codec': s.get('codec_name', ''), 'sr': int(s.get('sample_rate') or 0), 'ch': int(s.get('channels') or 1),
            'duree': float(fmt.get('duration') or 0), 'conteneur': fmt.get('format_name', '')}


def decoder(ffmpeg, path, info, sr_navigateur, max_s):
    wav_pcm = 'wav' in info['conteneur'] and info['codec'].startswith('pcm_')
    sr = info['sr'] if (wav_pcm and info['sr']) else sr_navigateur
    ch = max(1, info['ch'])
    cmd = [ffmpeg, '-v', 'error', '-nostdin', '-t', str(max_s), '-i', path, '-vn', '-f', 'f32le', '-acodec', 'pcm_f32le', '-ar', str(sr), '-']
    out = subprocess.run(cmd, capture_output=True, timeout=600)
    if out.returncode != 0 and not out.stdout:
        raise RuntimeError('décodage impossible')
    x = np.frombuffer(out.stdout, dtype='<f4')
    if ch > 1:
        x = x[: (len(x) // ch) * ch].reshape(-1, ch)
        x = x[:, 0] if wav_pcm else x.mean(axis=1)
    return np.ascontiguousarray(x, dtype=np.float32), sr


def _ignorer_ctrl_c():
    signal.signal(signal.SIGINT, signal.SIG_IGN)


def mesurer_fichier(tache):
    """Exécuté dans un processus séparé : un fichier → mesures."""
    path, p = tache['fichier'], tache['params']
    try:
        info = sonder(p['ffprobe'], path)
        x, sr = decoder(p['ffmpeg'], path, info, p['sr_navigateur'], p['max_s'])
        if len(x) < REF_N:
            return {'url': tache['url'], 'ok': False, 'erreur': 'son trop court ou vide'}
        calls = analyser_signal(x, sr, p['gain'], p['hp'], p['lp'], p['max_s'])
        sel = select_calls(calls)
        lignes = [ligne_cri(c) for c in sel]
        med = [float(v) for v in np.median(np.array(lignes, dtype=float), axis=0)[[0, 3, 1, 2, 5]]] if lignes else [None] * 5
        return {'url': tache['url'], 'sid': tache['sid'], 'ok': True, 'duree': info['duree'] or len(x) / sr, 'sr': sr,
                'detectes': len(calls), 'lignes': lignes, 'med': med}
    except subprocess.TimeoutExpired:
        return {'url': tache['url'], 'ok': False, 'erreur': 'délai dépassé'}
    except Exception as e:  # noqa
        return {'url': tache['url'], 'ok': False, 'erreur': str(e)[:200] or e.__class__.__name__}


# ═══════════════════════════════════════════════════════════════════════════════════════════════
#  2. INVENTAIRE GBIF, ÉTAT (SQLite), TÉLÉCHARGEMENTS PAR LOTS
# ═══════════════════════════════════════════════════════════════════════════════════════════════

SCHEMA_TABLES = """
CREATE TABLE IF NOT EXISTS sons (
  url TEXT PRIMARY KEY, source TEXT, gbif_id TEXT, catalogue TEXT, fiche TEXT,
  espece TEXT, species_key INTEGER, nom_en TEXT, type TEXT, sexe TEXT,
  date TEXT, annee INTEGER, mois INTEGER, jour INTEGER, heure INTEGER, lat REAL, lon REAL, region TEXT, departement TEXT,
  auteur TEXT, licence TEXT, format TEXT, duree_annoncee REAL, note_xc TEXT,
  ordre REAL, statut TEXT DEFAULT 'attente', fichier TEXT, octets INTEGER, essais INTEGER DEFAULT 0, erreur TEXT,
  duree REAL, sr INTEGER, detectes INTEGER, retenus INTEGER, lot INTEGER, maj TEXT);
CREATE TABLE IF NOT EXISTS especes (species_key INTEGER PRIMARY KEY, espece TEXT, nom_fr TEXT);
CREATE TABLE IF NOT EXISTS debit (ts REAL, source TEXT, octets INTEGER);
CREATE TABLE IF NOT EXISTS etat (k TEXT PRIMARY KEY, v TEXT);
"""
SCHEMA_INDEX = """
CREATE TABLE IF NOT EXISTS cris (sid INTEGER, fpic INTEGER, fmin INTEGER, fmax INTEGER, dur_ms INTEGER, forme INTEGER, notes INTEGER);
CREATE INDEX IF NOT EXISTS i_statut ON sons(statut, source, ordre);
CREATE INDEX IF NOT EXISTS i_espece ON sons(espece);
CREATE INDEX IF NOT EXISTS i_cris_sid ON cris(sid);
"""
MEDIANES = ('fpic_med', 'dur_med', 'fmin_med', 'fmax_med', 'notes_med')

ARRET = threading.Event()
VERROU_DB = threading.Lock()


def log(msg, fichier=None):
    ligne = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(ligne, flush=True)
    if fichier:
        try:
            with open(fichier, 'a', encoding='utf-8') as f:
                f.write(ligne + '\n')
        except OSError:
            pass


def ouvrir_db(path):
    db = sqlite3.connect(path, timeout=60, check_same_thread=False)
    db.execute('PRAGMA journal_mode=WAL')
    db.executescript(SCHEMA_TABLES)
    migrer(db)
    db.executescript(SCHEMA_INDEX)
    return db


def migrer(db):
    """Met à niveau une base créée par la version 1.0 du script, sans rien perdre de ce qui est déjà mesuré."""
    cols = {r[1] for r in db.execute('PRAGMA table_info(sons)')}
    for c in MEDIANES:
        if c not in cols:
            db.execute(f'ALTER TABLE sons ADD COLUMN {c} REAL')
    ccols = {r[1] for r in db.execute('PRAGMA table_info(cris)')}
    if 'url' in ccols:                                  # 1.0 : cris rattachés à l'adresse du son → numéro du son
        db.executescript("""
          DROP INDEX IF EXISTS i_cris_url;
          CREATE TABLE cris_n (sid INTEGER, fpic INTEGER, fmin INTEGER, fmax INTEGER, dur_ms INTEGER, forme INTEGER, notes INTEGER);
          INSERT INTO cris_n SELECT s.rowid, c.fpic, c.fmin, c.fmax, c.dur_ms, c.forme, c.notes FROM cris c JOIN sons s ON s.url = c.url;
          DROP TABLE cris;
          ALTER TABLE cris_n RENAME TO cris;""")
    db.commit()
    # médianes par enregistrement manquantes (sons mesurés par la 1.0)
    if 'sid' in {r[1] for r in db.execute('PRAGMA table_info(cris)')}:
        manq = [r[0] for r in db.execute("SELECT rowid FROM sons WHERE statut='mesure' AND retenus > 0 AND fpic_med IS NULL")]
        for sid in manq:
            rows = db.execute('SELECT fpic, dur_ms, fmin, fmax, notes FROM cris WHERE sid=?', (sid,)).fetchall()
            if rows:
                m = np.median(np.array(rows, dtype=float), axis=0)
                db.execute('UPDATE sons SET fpic_med=?, dur_med=?, fmin_med=?, fmax_med=?, notes_med=? WHERE rowid=?',
                           [float(v) for v in m] + [sid])
        db.commit()


def get_json(url, ua, essais=5):
    for k in range(essais):
        if ARRET.is_set():
            raise KeyboardInterrupt
        try:
            req = urllib.request.Request(url, headers={'User-Agent': ua, 'Accept': 'application/json'})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and k < essais - 1:
                time.sleep(min(120, 5 * 2 ** k))
                continue
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if k < essais - 1:
                time.sleep(min(120, 5 * 2 ** k))
                continue
            raise


def heure_de(rec):
    for champ in ('eventTime', 'eventDate'):
        v = rec.get(champ) or ''
        m = re.search(r'(?:^|T)(\d{1,2}):(\d{2})', v)
        if m:
            h = int(m.group(1))
            if 0 <= h <= 23:
                return h
    return None


def extraire_sons(rec, source):
    """Une occurrence GBIF → une ligne par fichier son."""
    gadm = rec.get('gadm') or {}
    region = rec.get('stateProvince') or (gadm.get('level1') or {}).get('name')
    dep = (gadm.get('level2') or {}).get('name')
    rating = None
    for ext in (rec.get('extensions') or {}).values():
        for it in ext or []:
            if it.get('http://purl.org/dc/elements/1.1/type') == 'Sound' and it.get('http://ns.adobe.com/xap/1.0/Rating'):
                rating = it.get('http://ns.adobe.com/xap/1.0/Rating')
    out = []
    for m in rec.get('media') or []:
        if m.get('type') != 'Sound' or not m.get('identifier'):
            continue
        dur = None
        mm = re.match(r'^\s*(\d+(?:\.\d+)?)\s*s\s*$', m.get('description') or '')
        if mm:
            dur = float(mm.group(1))
        out.append({
            'url': m['identifier'], 'source': source, 'gbif_id': str(rec.get('gbifID') or rec.get('key') or ''),
            'catalogue': rec.get('catalogNumber'), 'fiche': rec.get('references') or rec.get('occurrenceID'),
            'espece': rec.get('species'), 'species_key': rec.get('speciesKey'), 'nom_en': rec.get('vernacularName'),
            'type': rec.get('behavior'), 'sexe': rec.get('sex'),
            'date': (rec.get('eventDate') or '')[:10], 'annee': rec.get('year'), 'mois': rec.get('month'), 'jour': rec.get('day'),
            'heure': heure_de(rec), 'lat': rec.get('decimalLatitude'), 'lon': rec.get('decimalLongitude'),
            'region': region, 'departement': dep,
            'auteur': m.get('rightsHolder') or m.get('creator') or rec.get('recordedBy') or rec.get('rightsHolder'),
            'licence': licence_courte(m.get('license') or rec.get('license')), 'format': m.get('format'),
            'duree_annoncee': dur, 'note_xc': rating,
        })
    return out


def licence_courte(u):
    if not u:
        return ''
    s = u.lower()
    if 'publicdomain' in s or 'cc0' in s:
        return 'CC0'
    m = re.search(r'licenses/([a-z\-]+)/', s)
    if m:
        return 'CC ' + m.group(1).upper()
    return u


def inventaire(db, args, flog):
    ua = args.ua
    total_new = 0
    for source in args.sources:
        ds = DATASETS[source]
        base = {'datasetKey': ds['key'], 'country': 'FR', 'taxonKey': AVES_KEY}
        base.update(ds['extra'])
        q = dict(base, limit=0)
        n = get_json(f"{GBIF}/occurrence/search?{urllib.parse.urlencode(q)}", ua)['count']
        log(f"Inventaire {ds['nom']} : {n} observations à parcourir", flog)
        tranches = [None]
        if n > 95000:                                   # GBIF limite la pagination : découpage par année
            fac = get_json(f"{GBIF}/occurrence/search?{urllib.parse.urlencode(dict(base, limit=0, facet='year', facetLimit=200))}", ua)
            tranches = sorted(int(c['name']) for c in fac['facets'][0]['counts'])
        vus = 0
        for an in tranches:
            off = 0
            while not ARRET.is_set():
                qq = dict(base, limit=300, offset=off)
                if an is not None:
                    qq['year'] = an
                page = get_json(f"{GBIF}/occurrence/search?{urllib.parse.urlencode(qq)}", ua)
                lignes = []
                for rec in page.get('results', []):
                    lignes.extend(extraire_sons(rec, source))
                with VERROU_DB:
                    for l in lignes:
                        l['ordre'] = random.random()
                        cur = db.execute(
                            'INSERT OR IGNORE INTO sons (' + ','.join(l.keys()) + ',maj) VALUES (' + ','.join('?' * len(l)) + ',?)',
                            list(l.values()) + [datetime.now(timezone.utc).isoformat()])
                        total_new += cur.rowcount
                    db.commit()
                vus += len(page.get('results', []))
                off += page.get('limit') or len(page.get('results', [])) or 300
                print(f"\r  {ds['nom']} : {vus}/{n} observations lues   ", end='', flush=True)
                time.sleep(1.0)
                if page.get('endOfRecords') or not page.get('results'):
                    break
        print()
    if ARRET.is_set():
        return
    # ordre de passage : chaque espèce est servie à tour de rôle (les premiers lots couvrent toutes les espèces)
    with VERROU_DB:
        rows = db.execute("SELECT url, espece FROM sons WHERE statut='attente' ORDER BY ordre").fetchall()
        rang = {}
        maj = []
        for url, esp in rows:
            r = rang.get(esp, 0)
            rang[esp] = r + 1
            maj.append((r + random.random() * 0.5, url))
        db.executemany('UPDATE sons SET ordre=? WHERE url=?', maj)
        db.commit()
    noms_francais(db, args, flog)
    if ARRET.is_set():
        return
    with VERROU_DB:
        db.execute("INSERT OR REPLACE INTO etat VALUES ('inventaire', ?)", (datetime.now(timezone.utc).isoformat(),))
        db.commit()
    log(f"Inventaire terminé : {total_new} nouveaux sons ajoutés.", flog)


def noms_francais(db, args, flog):
    keys = [r[0] for r in db.execute(
        'SELECT DISTINCT species_key FROM sons WHERE species_key IS NOT NULL AND species_key NOT IN (SELECT species_key FROM especes)')]
    if keys:
        log(f"Noms français de {len(keys)} espèces (GBIF)…", flog)
    for i, k in enumerate(keys):
        if ARRET.is_set():
            return
        esp = db.execute('SELECT espece FROM sons WHERE species_key=? LIMIT 1', (k,)).fetchone()[0]
        nom = None
        try:
            res = get_json(f"{GBIF}/species/{k}/vernacularNames?limit=200", args.ua).get('results', [])
            fr = [r for r in res if (r.get('language') or '').lower() in ('fra', 'fre', 'fr')]
            pref = [r for r in fr if re.search(r'taxref|inpn|muséum|museum', (r.get('source') or ''), re.I)]
            choix = (pref or fr)
            if choix:
                cnt = {}
                for r in choix:
                    v = r['vernacularName'].strip()
                    v = v[:1].upper() + v[1:]
                    cnt[v] = cnt.get(v, 0) + 1
                nom = max(cnt.items(), key=lambda kv: kv[1])[0]
        except Exception:
            pass
        with VERROU_DB:
            db.execute('INSERT OR REPLACE INTO especes VALUES (?,?,?)', (k, esp, nom))
            db.commit()
        print(f"\r  noms : {i + 1}/{len(keys)}", end='', flush=True)
        time.sleep(1.0)
    if keys:
        print()


class Debit:
    """Comptage glissant des octets téléchargés chez iNaturalist (persistant d'une session à l'autre)."""

    def __init__(self, db):
        self.db = db

    def ajouter(self, source, octets):
        with VERROU_DB:
            self.db.execute('INSERT INTO debit VALUES (?,?,?)', (time.time(), source, octets))
            self.db.execute('DELETE FROM debit WHERE ts < ?', (time.time() - 2 * 86400,))
            self.db.commit()

    def attente_inat(self):
        """Secondes à attendre avant le prochain téléchargement iNaturalist (0 = maintenant)."""
        now = time.time()
        with VERROU_DB:
            h = self.db.execute("SELECT ts, octets FROM debit WHERE source='inat' AND ts > ? ORDER BY ts", (now - 3600,)).fetchall()
            d = self.db.execute("SELECT ts, octets FROM debit WHERE source='inat' AND ts > ? ORDER BY ts", (now - 86400,)).fetchall()
        w = 0.0
        for rows, lim, fen in ((h, INAT_GO_HEURE * GO, 3600), (d, INAT_GO_JOUR * GO, 86400)):
            tot = sum(o for _, o in rows)
            if tot >= lim:
                for ts, o in rows:              # attendre que les plus anciens sortent de la fenêtre
                    tot -= o
                    if tot < lim:
                        w = max(w, ts + fen - now + 1)
                        break
        return w


MAX_FLUX = 200 * 1024 ** 2          # au-delà, seul le début d'un WAV/MP3/FLAC est utile (≈ 5 min mesurées)


def telecharger(url, dest, ua, timeout=90, plafond=None):
    """Télécharge url → dest (fichier .part puis renommé). Renvoie le nombre d'octets.
    plafond : pour les formats lisibles en flux, on s'arrête après ce nombre d'octets (le début suffit)."""
    tmp = dest + '.part'
    req = urllib.request.Request(url, headers={'User-Agent': ua})
    n = 0
    with urllib.request.urlopen(req, timeout=timeout) as r, open(tmp, 'wb') as f:
        while True:
            if ARRET.is_set():
                raise KeyboardInterrupt
            b = r.read(1 << 16)
            if not b:
                break
            f.write(b)
            n += len(b)
            if plafond and n >= plafond:
                break
    os.replace(tmp, dest)
    return n


def extension(url, fmt):
    m = re.search(r'\.([a-z0-9]{2,5})(?:\?|$)', url.lower())
    if m:
        return m.group(1)
    return {'audio/mp4': 'm4a', 'audio/mpeg': 'mp3', 'audio/vnd.wave': 'wav', 'audio/wav': 'wav', 'audio/x-wav': 'wav',
            'audio/ogg': 'ogg', 'audio/flac': 'flac'}.get((fmt or '').lower(), 'audio')


class Lot:
    def __init__(self, num, max_octets, max_fichiers):
        self.num = num
        self.max_octets = max_octets
        self.max_fichiers = max_fichiers
        self.octets = 0
        self.fichiers = 0
        self.lock = threading.Lock()
        self.fin = {}                                   # source → True quand le fil a terminé

    def plein(self):
        with self.lock:
            return self.octets >= self.max_octets or (self.max_fichiers and self.fichiers >= self.max_fichiers)


def fil_telechargement(source, db, lot, args, debit, flog, dossier_sons):
    ua = args.ua
    refus = 0
    dernier = 0.0
    try:
        while not ARRET.is_set() and not lot.plein():
            with VERROU_DB:
                row = db.execute("SELECT url, format, espece FROM sons WHERE statut='attente' AND source=? ORDER BY ordre LIMIT 1",
                                 (source,)).fetchone()
            if not row:
                return
            url, fmt, esp = row
            if source == 'inat':
                w = debit.attente_inat()
                if w > 0:
                    autres_finis = all(lot.fin.get(s) for s in args.sources if s != source)
                    if autres_finis and (lot.octets > 0 or w > 600):
                        log(f"iNaturalist : limite de débit atteinte (reprise possible dans {int(w // 60)} min) — le lot est clos pour être mesuré.", flog)
                        return
                    time.sleep(min(w, 30))
                    continue
            pause = 1.0 - (time.time() - dernier)          # au plus un téléchargement par seconde et par source
            if pause > 0:
                time.sleep(pause)
            dernier = time.time()
            nom = f"{source}_{hashlib.sha1(url.encode()).hexdigest()[:16]}.{extension(url, fmt)}"
            dest = os.path.join(dossier_sons, nom)
            try:
                ext = dest.rsplit('.', 1)[-1]
                n = telecharger(url, dest, ua, plafond=MAX_FLUX if ext in ('wav', 'wave', 'mp3', 'flac') else None)
                refus = 0
                with VERROU_DB:
                    db.execute("UPDATE sons SET statut='telecharge', fichier=?, octets=?, lot=?, maj=? WHERE url=?",
                               (dest, n, lot.num, datetime.now(timezone.utc).isoformat(), url))
                    db.commit()
                if source == 'inat':
                    debit.ajouter('inat', n)
                with lot.lock:
                    lot.octets += n
                    lot.fichiers += 1
                    tot, nf = lot.octets, lot.fichiers
                if nf % 25 == 0:
                    log(f"  lot {lot.num} : {nf} sons · {tot / GO:.2f} Go", flog)
            except KeyboardInterrupt:
                return
            except urllib.error.HTTPError as e:
                code = e.code
                statut = 'introuvable' if code in (404, 410) else ('refuse' if code in (401, 403) else 'attente')
                with VERROU_DB:
                    db.execute("UPDATE sons SET statut=?, erreur=?, essais=essais+1 WHERE url=?", (statut, f'HTTP {code}', url))
                    db.execute("UPDATE sons SET statut='erreur' WHERE url=? AND essais>=3 AND statut='attente'", (url,))
                    db.commit()
                if code in (401, 403):
                    refus += 1
                    if refus >= 20:
                        log(f"{DATASETS[source]['nom']} refuse les téléchargements (HTTP {code}, 20 fois de suite) : source mise en pause pour ce lancement.", flog)
                        return
                if code == 429:
                    time.sleep(int(e.headers.get('Retry-After') or 60))
            except Exception as e:  # noqa
                with VERROU_DB:
                    db.execute("UPDATE sons SET essais=essais+1, erreur=? WHERE url=?", (str(e)[:200], url))
                    db.execute("UPDATE sons SET statut='erreur' WHERE url=? AND essais>=3", (url,))
                    db.commit()
                time.sleep(5)
            finally:
                if os.path.exists(dest + '.part'):
                    try:
                        os.remove(dest + '.part')
                    except OSError:
                        pass
    finally:
        lot.fin[source] = True


def mesurer_lot(db, args, flog):
    with VERROU_DB:
        rows = db.execute("SELECT rowid, url, fichier FROM sons WHERE statut='telecharge'").fetchall()
    if not rows:
        return 0
    params = {'ffmpeg': args.ffmpeg, 'ffprobe': args.ffprobe, 'sr_navigateur': args.sr_navigateur, 'max_s': args.max_s,
              'gain': args.gain, 'hp': args.passe_haut, 'lp': args.passe_bas}
    log(f"Mesure de {len(rows)} sons avec {args.processus} processus…", flog)
    t0 = time.time()
    fait = 0
    with ProcessPoolExecutor(max_workers=args.processus, initializer=_ignorer_ctrl_c) as ex:
        futs = [ex.submit(mesurer_fichier, {'sid': sid, 'url': u, 'fichier': f, 'params': params})
                for sid, u, f in rows if f and os.path.exists(f)]
        manquants = [u for sid, u, f in rows if not (f and os.path.exists(f))]
        with VERROU_DB:
            for u in manquants:
                db.execute("UPDATE sons SET statut='attente', fichier=NULL WHERE url=?", (u,))
            db.commit()
        for fu in as_completed(futs):
            r = fu.result()
            with VERROU_DB:
                if r['ok']:
                    db.execute('DELETE FROM cris WHERE sid=?', (r['sid'],))
                    db.executemany('INSERT INTO cris VALUES (?,?,?,?,?,?,?)', [[r['sid']] + l for l in r['lignes']])
                    db.execute("UPDATE sons SET statut='mesure', duree=?, sr=?, detectes=?, retenus=?, erreur=NULL, maj=?, "
                               "fpic_med=?, dur_med=?, fmin_med=?, fmax_med=?, notes_med=? WHERE rowid=?",
                               [r['duree'], r['sr'], r['detectes'], len(r['lignes']), datetime.now(timezone.utc).isoformat()] + r['med'] + [r['sid']])
                else:
                    db.execute("UPDATE sons SET statut='erreur', erreur=? WHERE url=?", (r['erreur'], r['url']))
                db.commit()
            fait += 1
            if fait % 50 == 0 or fait == len(futs):
                el = time.time() - t0
                print(f"\r  mesurés : {fait}/{len(futs)} · {el / 60:.1f} min", end='', flush=True)
            if ARRET.is_set():
                ex.shutdown(cancel_futures=True)
                break
    print()
    return fait


def supprimer_sons(db, flog):
    with VERROU_DB:
        rows = db.execute("SELECT url, fichier FROM sons WHERE fichier IS NOT NULL AND statut IN ('mesure','erreur')").fetchall()
    n = 0
    for url, f in rows:
        try:
            if f and os.path.exists(f):
                os.remove(f)
                n += 1
        except OSError:
            pass
    with VERROU_DB:
        db.executemany("UPDATE sons SET fichier=NULL WHERE url=?", [(u,) for u, _ in rows])
        db.commit()
    log(f"{n} fichiers son supprimés.", flog)


# ═══════════════════════════════════════════════════════════════════════════════════════════════
#  3. INTÉGRATION — birdsong-opendata.json (pour l'appli) + CSV
# ═══════════════════════════════════════════════════════════════════════════════════════════════

def categorie(behavior):
    b = (behavior or '').lower()
    if not b:
        return 'non précisé', 'day'
    vol = 'flight' in b or 'nocturnal' in b
    chant = re.search(r'\bsong\b|subsong|chant', b) is not None
    cri = re.search(r'\bcalls?\b|cri', b) is not None
    if vol and not chant:
        return 'cri de vol', 'both'
    if chant and not cri:
        return 'chant', 'day'
    if cri and not chant:
        return 'cri', 'day'
    if chant and cri:
        return 'chant et cris', 'day'
    return 'autre', 'day'


def q_list(a, ps):
    if not len(a):
        return [None] * len(ps)
    return [float(np.quantile(a, p)) for p in ps]


def exporter(db, args, flog):
    rnd = random.Random(46)
    with VERROU_DB:
        noms = {r[0]: r[1] for r in db.execute('SELECT espece, nom_fr FROM especes WHERE espece IS NOT NULL')}
        sons = db.execute("SELECT source, espece, type, mois, heure, licence, auteur, statut, region, species_key "
                          "FROM sons WHERE espece IS NOT NULL").fetchall()
    especes = {}
    for source, esp, typ, mois, heure, lic, aut, statut, reg, skey in sons:
        e = especes.get(esp)
        if e is None:
            e = especes[esp] = {'la': esp, 'fr': noms.get(esp), 'key': skey, 'rec': 0, 'meas': 0, 'month': [0] * 12, 'hour': [0] * 24,
                                'src': {}, 'lic': {}, 'cats': {}, '_aut': set(), 'reg': {}}
        e['rec'] += 1
        if statut == 'mesure':
            e['meas'] += 1
        if mois and 1 <= mois <= 12:
            e['month'][mois - 1] += 1
        if heure is not None and 0 <= heure <= 23:
            e['hour'][heure] += 1
        e['src'][source] = e['src'].get(source, 0) + 1
        if lic:
            e['lic'][lic] = e['lic'].get(lic, 0) + 1
        c, _ = categorie(typ)
        e['cats'][c] = e['cats'].get(c, 0) + 1
        if aut:
            e['_aut'].add(aut)
        if reg:
            e['reg'][reg] = e['reg'].get(reg, 0) + 1
    del sons
    groups = {}
    for esp in sorted(especes):
        e = especes[esp]
        if not e['meas']:
            continue
        with VERROU_DB:
            rows = db.execute("SELECT c.sid, s.type, c.fpic, c.fmin, c.fmax, c.dur_ms, c.forme, c.notes "
                              "FROM sons s JOIN cris c ON c.sid = s.rowid WHERE s.espece = ? ORDER BY c.sid, c.rowid", (esp,)).fetchall()
        if not rows:
            continue
        # références : au plus --cris-par-enr cris par enregistrement, puis 300 par groupe espèce|type (comme REF_CAP)
        par_groupe = {}
        for sid, typ, *row in rows:
            par_groupe.setdefault(categorie(typ), {}).setdefault(sid, []).append(row)
        for (cat, ctx), enr in par_groupe.items():
            pool = []
            for sid in sorted(enr):
                rr = enr[sid]
                pool.extend(rr if len(rr) <= args.cris_par_enr else rnd.sample(rr, args.cris_par_enr))
            c = pool if len(pool) <= 300 else rnd.sample(pool, 300)
            groups[f"od|{esp}|{cat}"] = {'la': esp, 'fr': noms.get(esp), 'cat': cat, 'ctx': ctx,
                                         'seen': sum(len(v) for v in enr.values()), 'nfiles': len(enr), 'c': c}
        a = np.array([r[2:] for r in rows], dtype=float)
        del rows, par_groupe
        fp, fmn, fmx, du, fo, no = a.T
        e['calls'] = int(len(a))
        e['fPeak'] = [round(v) for v in q_list(fp, [0.1, 0.25, 0.5, 0.75, 0.9])]
        e['fMin'] = round(float(np.median(fmn)))
        e['fMax'] = round(float(np.median(fmx)))
        e['ext'] = [round(float(np.quantile(fmn, 0.05))), round(float(np.quantile(fmx, 0.95)))]
        e['dur'] = [round(v) for v in q_list(du, [0.1, 0.5, 0.9])]
        e['notes'] = [round(v, 1) for v in q_list(no, [0.5, 0.9])]
        e['shapes'] = [int(np.sum(fo == i)) for i in range(len(REF_SHAPES))]
        e['hist'] = np.histogram(np.clip(fp, 0, 15999), bins=40, range=(0, 16000))[0].astype(int).tolist()
    for e in especes.values():
        e['authors'] = len(e.pop('_aut'))
        e['reg'] = dict(sorted(e['reg'].items(), key=lambda kv: -kv[1])[:8])
    with VERROU_DB:
        st = dict(db.execute('SELECT statut, COUNT(*) FROM sons GROUP BY statut').fetchall())
        src = dict(db.execute('SELECT source, COUNT(*) FROM sons GROUP BY source').fetchall())
        srcm = dict(db.execute("SELECT source, COUNT(*) FROM sons WHERE statut='mesure' GROUP BY source").fetchall())
        lots = db.execute("SELECT v FROM etat WHERE k='lots'").fetchone()
    out = {
        'type': 'birdsong-opendata', 'v': 1, 'mver': MEAS_VER, 'script': VERSION,
        'updated': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'zone': 'France', 'sources': {s: DATASETS[s]['nom'] for s in DATASETS},
        'params': {'gain': args.gain, 'hp': args.passe_haut, 'lp': args.passe_bas, 'srNav': args.sr_navigateur, 'maxS': args.max_s,
                   'crisParEnr': args.cris_par_enr},
        'inventory': {'total': sum(st.values()), 'measured': st.get('mesure', 0), 'waiting': st.get('attente', 0) + st.get('telecharge', 0),
                      'errors': st.get('erreur', 0) + st.get('introuvable', 0) + st.get('refuse', 0),
                      'bySource': src, 'measuredBySource': srcm, 'lots': int(lots[0]) if lots else 0},
        'species': especes,
        'groups': groups,
    }
    dest = os.path.join(args.dossier, 'birdsong-opendata.json')
    tmp = dest + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, separators=(',', ':'))
    os.replace(tmp, dest)
    exporter_csv(db, args, especes)
    if args.copie_vers:
        try:
            os.makedirs(args.copie_vers, exist_ok=True)
            shutil.copy2(dest, os.path.join(args.copie_vers, 'birdsong-opendata.json'))
            if args.git:
                envoyer_github(args.copie_vers, out['inventory']['measured'], flog)
        except OSError as e:
            log(f"Copie vers {args.copie_vers} impossible : {e}", flog)
    log(f"Résultats intégrés : {out['inventory']['measured']} sons mesurés sur {out['inventory']['total']} · "
        f"{len(especes)} espèces · {sum(len(g['c']) for g in groups.values())} cris de référence → {dest}", flog)


def envoyer_github(dossier, n_mesures, flog):
    """--git : enregistre birdsong-opendata.json dans le dépôt (clone git) et l'envoie sur GitHub.
    Seul ce fichier est concerné ; un échec (pas de réseau, pas d'accès) n'arrête pas la collecte."""
    if not shutil.which('git'):
        log("git introuvable : résultats copiés dans le dossier de l'appli, mais pas envoyés sur GitHub.", flog)
        return

    def g(*a, t=120):
        return subprocess.run(['git', *a], capture_output=True, text=True, timeout=t)
    try:
        top = g('-C', dossier, 'rev-parse', '--show-toplevel')
        if top.returncode:
            log(f"{dossier} n'est pas dans un clone git du dépôt : résultats non envoyés sur GitHub.", flog)
            return
        root = top.stdout.strip()
        rel = os.path.relpath(os.path.join(dossier, 'birdsong-opendata.json'), root).replace(os.sep, '/')
        ident = [] if g('-C', root, 'config', 'user.email').stdout.strip() else \
            ['-c', 'user.name=Birdsong Analyzer (collecte)', '-c', 'user.email=birdsong@localhost']
        g('-C', root, 'add', '--', rel)
        if g('-C', root, 'diff', '--cached', '--quiet', '--', rel).returncode == 0:
            return                                          # rien de nouveau
        c = g(*ident, '-C', root, 'commit', '-q', '-m', f'Données ouvertes : {n_mesures} sons mesurés', '--', rel)
        if c.returncode:
            log(f"Enregistrement git impossible : {(c.stderr or c.stdout).strip()[:200]}", flog)
            return
        g('-C', root, 'pull', '--rebase', '--autostash', '-q', t=180)
        p = g('-C', root, 'push', '-q', t=600)
        log('Résultats envoyés sur GitHub.' if p.returncode == 0 else
            f"Envoi sur GitHub impossible pour l'instant ({(p.stderr or '').strip()[:160]}) : nouvel essai au prochain lot.", flog)
    except Exception as e:  # noqa
        log(f"Envoi sur GitHub impossible : {e}", flog)


def fnum(v, nd=0):
    if v is None:
        return ''
    s = f"{v:.{nd}f}" if isinstance(v, float) else str(v)
    return s.replace('.', ',')


def exporter_csv(db, args, especes):
    """Tableaux au format Excel français (séparateur « ; », virgule décimale)."""
    p1 = os.path.join(args.dossier, 'mesures-par-enregistrement.csv')
    tmp = p1 + '.tmp'
    with VERROU_DB:
        cur = db.execute("""
          SELECT s.source, s.gbif_id, s.catalogue, s.fiche, s.url, s.espece, e.nom_fr, s.type, s.sexe, s.date, s.heure,
                 s.lat, s.lon, s.region, s.departement, s.auteur, s.licence, s.duree, s.sr, s.detectes, s.retenus,
                 s.fpic_med, s.dur_med, s.fmin_med, s.fmax_med, s.notes_med
          FROM sons s LEFT JOIN especes e ON e.species_key = s.species_key WHERE s.statut='mesure' ORDER BY s.espece, s.rowid""")
        with open(tmp, 'w', encoding='utf-8-sig', newline='') as f:
            w = csv.writer(f, delimiter=';')
            w.writerow(['source', 'id_gbif', 'catalogue', 'fiche', 'url_son', 'espece', 'nom_francais', 'type', 'sexe', 'date', 'heure',
                        'latitude', 'longitude', 'region', 'departement', 'auteur', 'licence', 'duree_s', 'frequence_echantillonnage',
                        'sons_detectes', 'sons_retenus', 'fpic_mediane_hz', 'duree_mediane_ms', 'fmin_mediane_hz', 'fmax_mediane_hz', 'notes_mediane'])
            for r in cur:
                w.writerow(list(r[:11]) + [fnum(r[11], 5) if r[11] is not None else '', fnum(r[12], 5) if r[12] is not None else ''] +
                           list(r[13:17]) + [fnum(r[17], 1) if r[17] is not None else '', r[18], r[19], r[20]] +
                           [fnum(v, 1 if i == 4 else 0) if v is not None else '' for i, v in enumerate(r[21:26])])
    os.replace(tmp, p1)
    p2 = os.path.join(args.dossier, 'resume-par-espece.csv')
    tmp = p2 + '.tmp'
    with open(tmp, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f, delimiter=';')
        w.writerow(['espece', 'nom_francais', 'enregistrements', 'mesures', 'cris_retenus', 'fpic_p10', 'fpic_mediane', 'fpic_p90',
                    'fmin_p05', 'fmax_p95', 'duree_p10_ms', 'duree_mediane_ms', 'duree_p90_ms', 'notes_mediane', 'auteurs',
                    'inaturalist', 'xeno_canto', 'mois_max', 'heure_max'])
        for esp in sorted(especes, key=lambda k: (especes[k].get('fr') or k)):
            e = especes[esp]
            fpk = e.get('fPeak') or [None] * 5
            du = e.get('dur') or [None] * 3
            ext = e.get('ext') or [None, None]
            mm = int(np.argmax(e['month'])) + 1 if sum(e['month']) else ''
            hh = int(np.argmax(e['hour'])) if sum(e['hour']) else ''
            w.writerow([esp, e.get('fr') or '', e['rec'], e['meas'], e.get('calls', 0), fpk[0], fpk[2], fpk[4], ext[0], ext[1],
                        du[0], du[1], du[2], fnum((e.get('notes') or [None])[0], 1) if e.get('notes') else '', e['authors'],
                        e['src'].get('inat', 0), e['src'].get('xc', 0), mm, hh])
    os.replace(tmp, p2)


# ═══════════════════════════════════════════════════════════════════════════════════════════════
#  4. PROGRAMME PRINCIPAL
# ═══════════════════════════════════════════════════════════════════════════════════════════════

def bilan(db):
    st = dict(db.execute('SELECT statut, COUNT(*) FROM sons GROUP BY statut').fetchall())
    return st


def main():
    ap = argparse.ArgumentParser(description="Collecte et mesure des sons d'oiseaux en données ouvertes (France) pour Birdsong Analyzer.")
    ap.add_argument('--dossier', required=True, help='dossier de travail (base, sons du lot en cours, résultats)')
    ap.add_argument('--lot-go', type=float, default=20.0, help='taille maximale d’un lot de sons sur le disque, en Go (20)')
    ap.add_argument('--essai', type=int, default=0, help='essai : un seul petit lot de N sons, puis arrêt')
    ap.add_argument('--lots', type=int, default=0, help='nombre maximal de lots pour ce lancement (0 = jusqu’à la fin)')
    ap.add_argument('--sources', default='inat,xc', help='inat, xc ou inat,xc')
    ap.add_argument('--maj-inventaire', action='store_true', help='relire GBIF pour ajouter les nouveaux sons publiés')
    ap.add_argument('--exporter', action='store_true', help='seulement régénérer les résultats à partir de ce qui est déjà mesuré')
    ap.add_argument('--copie-vers', default='', help='dossier où copier birdsong-opendata.json après chaque lot (ex. ton dépôt GitHub)')
    ap.add_argument('--git', action='store_true', help='après chaque lot, envoyer birdsong-opendata.json sur GitHub (avec --copie-vers dans le clone du dépôt)')
    ap.add_argument('--processus', type=int, default=max(1, (os.cpu_count() or 2) - 1), help='processus de mesure en parallèle')
    ap.add_argument('--contact', default='', help='ton e-mail, ajouté à l’identification du script auprès des serveurs (recommandé)')
    ap.add_argument('--gain', type=float, default=12.0, help='gain (curseur « Gain » de l’appli, 12)')
    ap.add_argument('--passe-haut', type=float, default=800.0, help='passe-haut en Hz (curseur « Filtre » de l’appli, 800)')
    ap.add_argument('--passe-bas', type=float, default=12000.0, help='passe-bas en Hz (12000)')
    ap.add_argument('--sr-navigateur', type=int, default=48000, help='fréquence de décodage des sons compressés (48000)')
    ap.add_argument('--max-s', type=float, default=REF_MAX_S, help='secondes mesurées au début de chaque son (300, comme l’appli)')
    ap.add_argument('--cris-par-enr', type=int, default=10, help='cris au plus par enregistrement dans les références (10)')
    ap.add_argument('--ffmpeg', default=shutil.which('ffmpeg') or 'ffmpeg')
    ap.add_argument('--ffprobe', default=shutil.which('ffprobe') or 'ffprobe')
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(errors='replace')
    except Exception:
        pass
    args.sources = [s.strip() for s in args.sources.split(',') if s.strip() in DATASETS]
    args.ua = f"BirdsongAnalyzer-donnees-ouvertes/{VERSION} (recherche etudiante; {args.contact or 'sans contact'})"
    os.makedirs(args.dossier, exist_ok=True)
    dossier_sons = os.path.join(args.dossier, 'sons-lot-en-cours')
    os.makedirs(dossier_sons, exist_ok=True)
    flog = os.path.join(args.dossier, 'journal.txt')
    db = ouvrir_db(os.path.join(args.dossier, 'etat.sqlite'))

    def stop(*_):
        if ARRET.is_set():
            sys.exit(1)
        print('\nArrêt demandé : fin propre en cours (Ctrl+C encore une fois pour forcer)…', flush=True)
        ARRET.set()
    signal.signal(signal.SIGINT, stop)

    if args.exporter:
        exporter(db, args, flog)
        return
    for exe in (args.ffmpeg, args.ffprobe):
        if not shutil.which(exe) and not os.path.exists(exe):
            print(f"ffmpeg introuvable ({exe}). Windows : winget install ffmpeg · Mac : brew install ffmpeg — puis rouvre le terminal.")
            sys.exit(1)

    log(f"Birdsong Analyzer — données ouvertes v{VERSION} · dossier {args.dossier}", flog)
    inv_ok = db.execute("SELECT v FROM etat WHERE k='inventaire'").fetchone()
    if not inv_ok or args.maj_inventaire:
        try:
            inventaire(db, args, flog)
        except (urllib.error.URLError, OSError, ValueError) as e:
            log(f"GBIF injoignable pour l'inventaire ({getattr(e, 'reason', e)}). Vérifie la connexion puis relance : "
                f"ce qui est déjà inventorié et mesuré est gardé.", flog)
            if not inv_ok and not db.execute("SELECT COUNT(*) FROM sons WHERE statut='attente'").fetchone()[0]:
                return
    if ARRET.is_set():
        return
    st = bilan(db)
    log(f"État : {st.get('mesure', 0)} mesurés · {st.get('attente', 0)} en attente · {st.get('telecharge', 0)} téléchargés non mesurés · "
        f"{st.get('erreur', 0) + st.get('introuvable', 0) + st.get('refuse', 0)} en erreur", flog)

    # un lot resté en cours (arrêt précédent) est d'abord mesuré
    if st.get('telecharge', 0):
        log('Reprise : mesure des sons déjà téléchargés.', flog)
        mesurer_lot(db, args, flog)
        exporter(db, args, flog)
        supprimer_sons(db, flog)

    debit = Debit(db)
    nb_lots = 0
    while not ARRET.is_set():
        if args.lots and nb_lots >= args.lots:
            break
        attente = db.execute("SELECT COUNT(*) FROM sons WHERE statut='attente' AND source IN (%s)" %
                             ','.join('?' * len(args.sources)), args.sources).fetchone()[0]
        if not attente:
            log('Tout est mesuré. Relance avec --maj-inventaire de temps en temps pour ajouter les nouveaux sons.', flog)
            break
        libre = shutil.disk_usage(args.dossier).free
        max_oct = min(args.lot_go * GO, libre - 2 * GO)
        if max_oct < min(args.lot_go * GO, 0.2 * GO):
            log('Pas assez de place libre sur le disque (il faut au moins 2,2 Go).', flog)
            break
        num = int((db.execute("SELECT v FROM etat WHERE k='lots'").fetchone() or [0])[0]) + 1
        lot = Lot(num, max_oct, args.essai)
        log(f"══ Lot {num} : téléchargement (jusqu'à {max_oct / GO:.3g} Go) — {attente} sons restent à mesurer", flog)
        fils = [threading.Thread(target=fil_telechargement, args=(s, db, lot, args, debit, flog, dossier_sons), daemon=True)
                for s in args.sources]
        for t in fils:
            t.start()
        while any(t.is_alive() for t in fils):
            time.sleep(0.5)
        log(f"Lot {num} téléchargé : {lot.fichiers} sons · {lot.octets / GO:.2f} Go", flog)
        if ARRET.is_set():
            log('Arrêt : les sons déjà téléchargés seront mesurés au prochain lancement.', flog)
            break
        if lot.fichiers == 0:
            w = debit.attente_inat()
            if w > 0 and not ARRET.is_set():
                log(f"Attente de la limite iNaturalist : {int(w // 60) + 1} min…", flog)
                ARRET.wait(w)
                continue
            break
        mesurer_lot(db, args, flog)
        with VERROU_DB:
            db.execute("INSERT OR REPLACE INTO etat VALUES ('lots', ?)", (str(num),))
            db.commit()
        exporter(db, args, flog)
        supprimer_sons(db, flog)
        nb_lots += 1
        if args.essai:
            log("Essai terminé. Ouvre birdsong-opendata.json dans l'onglet 🌍 Données ouvertes de l'appli pour vérifier.", flog)
            break
    st = bilan(db)
    log(f"Fin : {st.get('mesure', 0)} mesurés · {st.get('attente', 0)} en attente.", flog)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nArrêté. Relance la même commande pour reprendre là où tu t’es arrêté.')
