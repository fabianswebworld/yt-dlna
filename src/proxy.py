# ==============================================================================
# yt-dlna: src/proxy.py
# Lightweight media gateway, proxying streaming playlists to DLNA/UPnP clients
#
# Copyright (c) 2026 Fabian Schneider (@fabianswebworld) and contributors
# Licensed under the MIT License - see LICENSE file for details.
# SPDX-License-Identifier: MIT
# ==============================================================================

import os
import socket
import urllib.parse
import subprocess
import threading
import requests
from flask import Flask, redirect, request, Response, stream_with_context
import flask.cli
import logging
import utils

# silence Flask development server warning banner
flask.cli.show_server_banner = lambda *args: None

# disable Flask/Werkzeug access logging (only show errors)
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

config = utils.load_config()
app = Flask(__name__)

# helper to set up flask routes from patterns
def get_flask_path(key, default, is_static=False):
    pattern = config.get('proxy', key, fallback=default)
    if is_static:
        return pattern.replace('{video_id}', '<path:target_url>')
    return pattern.replace('{service}', '<service>').replace('{video_id}', '<path:video_id>')

# read configured routes
path_play     = get_flask_path('proxy_url_pattern', '/play/{service}/{video_id}')
path_redirect = get_flask_path('proxy_url_pattern_redirect', '/redirect/{service}/{video_id}')
path_proxy    = get_flask_path('proxy_url_pattern_proxy', '/proxy/{service}/{video_id}')
path_remux    = get_flask_path('proxy_url_pattern_remux', '/remux/{service}/{video_id}')

# read configured routes for static playlists
path_bounce   = get_flask_path('proxy_url_pattern_bounce', '/bounce/{video_id}', is_static=True)
path_reflect  = get_flask_path('proxy_url_pattern_reflect', '/reflect/{video_id}', is_static=True)
path_hit      = get_flask_path('proxy_url_pattern_hit', '/hit/{video_id}', is_static=True)

def log_ffmpeg_stderr(proc):
    """Background helper to log ffmpeg error output and close pipe safely."""
    try:
        while True:
            line = proc.stderr.readline()
            if not line:
                break
            decoded = line.decode('utf-8', errors='ignore').strip()
            if decoded:
                print(f"[FFmpeg] {decoded}")
    except Exception:
        pass
    finally:
        try:
            proc.stderr.close()
        except Exception:
            pass

def resolve_cdn_url(video_id, service_name='youtube', min_remaining_ttl=0, force_dash=False):
    """Helper to fetch URL or DASH formats from cache or extract fresh via yt-dlp."""
    video_id = urllib.parse.unquote(video_id)
    srv_cfg = utils.get_service_config(service_name)

    # overrides for /remux/ route
    enable_remux = True if force_dash else config.getboolean('proxy', 'enable_remux', fallback=False)
    remux_threshold = 0 if force_dash else config.getint('proxy', 'remux_threshold', fallback=0)

    cached_entry = utils.get_cached_url(video_id, service_name=service_name, min_remaining_ttl=min_remaining_ttl)
    if cached_entry:
        # if we specifically forced DASH but the cache is a single URL, re-resolve
        if not (force_dash and not cached_entry.get('is_dash')):
            print(f"[Proxy] Cache HIT for {service_name}:{video_id}")
            return cached_entry, True

    print(f"[Proxy] Cache MISS for {service_name}:{video_id}. Resolving via yt-dlp...")

    # check extractor name for special handling of yt video ids
    if video_id.startswith('http://') or video_id.startswith('https://'):
        video_url = video_id
    elif srv_cfg['extractor'] == 'youtube':
        video_url = f"https://www.youtube.com/watch?v={video_id}"
    else:
        video_url = video_id

    # construct effective format selector
    format_single = srv_cfg['format'].replace(' ', '')
    format_dash = srv_cfg.get('format_dash', '').strip().replace(' ', '')

    if enable_remux and format_dash:
        include_dash = True
        if remux_threshold <= 0:
            effective_format = f"{format_dash}/{format_single}"
        else:
            effective_format = f"({format_single})[height>={remux_threshold}]/{format_dash}/{format_single}"
    else:
        include_dash = False
        effective_format = format_single

    skips = ['hls']
    if not include_dash:
        skips.append('dash')

    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False,
        'ies': [srv_cfg['extractor']],
        'format': effective_format,
        'skip_download': True,
        'extractor_args': {
            'youtube': {
                'skip': skips
            }
        }
    }

    info = utils.extract_youtube_info(
        video_url, 
        extra_opts=ydl_opts, 
        use_cookies=srv_cfg['use_cookies_for_playback'],
        cookie_path=srv_cfg['cookie_path']
    )

    req_formats = info.get('requested_formats')
    if req_formats and len(req_formats) >= 2:
        v_url, a_url = None, None
        for fmt in req_formats:
            if fmt.get('vcodec') != 'none' and not v_url:
                v_url = fmt.get('url')
            elif fmt.get('acodec') != 'none' and not a_url:
                a_url = fmt.get('url')

        if v_url and a_url:
            entry = {
                'is_dash': True,
                'video_url': v_url,
                'audio_url': a_url
            }
            utils.set_cached_url(video_id, entry, service_name=service_name)
            return entry, False

    # fallback: single-file progressive MP4
    cdn_url = info.get('url')
    entry = {'url': cdn_url, 'is_dash': False}
    utils.set_cached_url(video_id, entry, service_name=service_name)
    return entry, False

def resolve_cdn_urls_batch(video_ids, service_name='youtube', min_remaining_ttl=0):
    """Batch resolves missing or expired CDN URLs for a list of video IDs in a single pass."""
    if not video_ids:
        return {}

    srv_cfg = utils.get_service_config(service_name)
    enable_remux = config.getboolean('proxy', 'enable_remux', fallback=False)
    remux_threshold = config.getint('proxy', 'remux_threshold', fallback=0)

    missing_ids = []
    missing_urls = []
    results = {}

    # filter video_ids to only those missing or expiring in urlcache.json
    for raw_id in video_ids:
        v_id = urllib.parse.unquote(raw_id)
        cached = utils.get_cached_url(v_id, service_name=service_name, min_remaining_ttl=min_remaining_ttl)
        if cached:
            results[v_id] = cached
        else:
            missing_ids.append(v_id)
            if v_id.startswith('http://') or v_id.startswith('https://'):
                missing_urls.append(v_id)
            elif srv_cfg['extractor'] == 'youtube':
                missing_urls.append(f"https://www.youtube.com/watch?v={v_id}")
            else:
                missing_urls.append(v_id)

    if not missing_ids:
        print(f"[Proxy] Batch resolve: Nothing to do for this collection.")
        return results

    print(f"[Proxy] Batch resolving {len(missing_ids)} missing/expired CDN URL(s) for service '{service_name}'...")

    # construct effective format selector for batch extraction
    format_single = srv_cfg['format'].replace(' ', '')
    format_dash = srv_cfg.get('format_dash', '').strip().replace(' ', '')

    if enable_remux and format_dash:
        include_dash = True
        if remux_threshold <= 0:
            effective_format = f"{format_dash}/{format_single}"
        else:
            effective_format = f"({format_single})[height>={remux_threshold}]/{format_dash}/{format_single}"
    else:
        include_dash = False
        effective_format = format_single

    skips = ['hls']
    if not include_dash:
        skips.append('dash')

    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False,
        'ies': [srv_cfg['extractor']],
        'format': effective_format,
        'skip_download': True,
        'extractor_args': {
            'youtube': {
                'skip': skips
            }
        }
    }

    # single pass extraction for all missing URLs
    extracted_infos = utils.extract_youtube_info(
        missing_urls, 
        extra_opts=ydl_opts, 
        use_cookies=srv_cfg['use_cookies_for_playback'],
        cookie_path=srv_cfg['cookie_path']
    )

    if isinstance(extracted_infos, list):
        for orig_id, info in zip(missing_ids, extracted_infos):
            if isinstance(info, dict):
                req_formats = info.get('requested_formats')
                if req_formats and len(req_formats) >= 2:
                    v_url, a_url = None, None
                    for fmt in req_formats:
                        if fmt.get('vcodec') != 'none' and not v_url:
                            v_url = fmt.get('url')
                        elif fmt.get('acodec') != 'none' and not a_url:
                            a_url = fmt.get('url')

                    if v_url and a_url:
                        entry = {
                            'is_dash': True,
                            'video_url': v_url,
                            'audio_url': a_url
                        }
                        results[orig_id] = entry
                        utils.set_cached_url(orig_id, entry, service_name=service_name)
                        continue

                cdn_url = info.get('url')
                if cdn_url:
                    entry = {'url': cdn_url, 'is_dash': False}
                    results[orig_id] = entry
                    utils.set_cached_url(orig_id, entry, service_name=service_name)

    return results

# --- Flask route implementations ---

@app.route(path_play)
def route_play(service, video_id):
    return _stream_internal(service, video_id)

@app.route(path_redirect)
def route_redirect(service, video_id):
    return _stream_internal(service, video_id, mode_override='redirect')

@app.route(path_proxy)
def route_proxy(service, video_id):
    return _stream_internal(service, video_id, mode_override='proxy')

@app.route(path_remux)
def route_remux(service, video_id):
    return _stream_internal(service, video_id, mode_override='remux')

@app.route(path_bounce)
def route_bounce(target_url):
    return _static_internal(target_url, 'bounce')

@app.route(path_reflect)
def route_reflect(target_url):
    return _static_internal(target_url, 'reflect')

@app.route(path_hit)
def route_hit(target_url):
    return _static_internal(target_url, 'hit')

# --- internal handlers ---

def _stream_internal(service, video_id, mode_override=None):
    """Master handler for all resolving routes."""
    global_mode = config.get('proxy', 'mode', fallback='redirect').strip().lower()
    
    # check if we are forcing remux
    force_remux = (mode_override == 'remux')
    # determine the effective operating mode
    mode = mode_override if mode_override and mode_override != 'remux' else global_mode

    try:
        cached_entry, is_cached = resolve_cdn_url(video_id, service_name=service, force_dash=force_remux)

        # --- REMUX: If entry is DASH, stream via ffmpeg -c copy in-memory ---
        if isinstance(cached_entry, dict) and cached_entry.get('is_dash'):
            return _serve_remux_implementation(cached_entry, f"{service}:{video_id}")

        # extract direct CDN URL
        cdn_url = cached_entry.get('url') if isinstance(cached_entry, dict) else cached_entry

        # --- REDIRECT MODE: 302 Redirect (ultra lightweight for old hardware) ---
        if mode == 'redirect':
            print(f"[Proxy] 302 Redirecting {service}:{video_id} to CDN...")
            utils.record_stream_event('redirects')
            return redirect(cdn_url, code=302)

        # --- PROXY MODE: active proxying of bytes (fallback for TVs that don't follow 302) ---
        return _serve_proxy_implementation(cdn_url, f"{service}:{video_id}", is_cached, service)

    except Exception as e:
        print(f"[Proxy] Error ({mode} mode): {e}")
        return f"Proxy Error ({mode} mode): {str(e)}", 500

def _static_internal(target_url, mode):
    """Master handler for all non-resolving (custom playlist) routes."""
    url = urllib.parse.unquote(target_url)
    
    if mode == 'bounce':
        print(f"[Proxy] Bouncing client to static URL: {url}")
        utils.record_stream_event('redirects')
        return redirect(url, code=302)
        
    elif mode == 'reflect':
        print(f"[Proxy] Reflecting static URL to client: {url}")
        return _serve_proxy_implementation(url, f"static:{url}")
        
    elif mode == 'hit':
        print(f"[Proxy] Hitting static URL: {url}")
        try:
            requests.get(url, timeout=5)
        except Exception as e:
            print(f"[Proxy] Hit failed: {e}")
        return _serve_dummy_file()

# --- Implementation of proxy modes ---

def _serve_proxy_implementation(cdn_url, identifier, is_cached=False, service=None):
    print(f"[Proxy] Tunneling stream bytes for {identifier}...")
    utils.record_stream_event('proxied')
    
    req_headers = {}
    if 'Range' in request.headers:
        req_headers['Range'] = request.headers['Range']

    upstream_res = requests.get(cdn_url, headers=req_headers, stream=True)
    final_mime = upstream_res.headers.get('Content-Type', 'video/mp4')

    # If cached link expired early (403/410), purge cache & retry once with fresh URL
    if is_cached and upstream_res.status_code in (403, 404, 410) and service:
        print(f"[Proxy] Cached URL expired for {identifier} ({upstream_res.status_code}). Refreshing...")
        utils.invalidate_cached_url(identifier.split(':')[-1])
        cached_entry, _ = resolve_cdn_url(identifier.split(':')[-1], service_name=service)
        cdn_url = cached_entry.get('url') if isinstance(cached_entry, dict) else cached_entry
        upstream_res = requests.get(cdn_url, headers=req_headers, stream=True)

    response_headers = {
        'Content-Type': final_mime,
        'Accept-Ranges': 'bytes',
        'transferMode.dlna.org': 'Streaming',
        'contentFeatures.dlna.org': 'DLNA.ORG_OP=01;DLNA.ORG_CI=0;DLNA.ORG_FLAGS=01700000000000000000000000000000'
    }

    if 'Content-Range' in upstream_res.headers:
        response_headers['Content-Range'] = upstream_res.headers['Content-Range']
    if 'Content-Length' in upstream_res.headers:
        response_headers['Content-Length'] = upstream_res.headers['Content-Length']

    return Response(
        stream_with_context(upstream_res.iter_content(chunk_size=64 * 1024)),
        status=upstream_res.status_code,
        headers=response_headers
    )

def _serve_remux_implementation(cached_entry, identifier):
    v_url = cached_entry.get('video_url')
    a_url = cached_entry.get('audio_url')
    
    target_fmt = config.get('proxy', 'remux_target_format', fallback='ts').strip().lower()
    print(f"[Proxy] Remuxing DASH to {target_fmt.upper()} for {identifier} on the fly via ffmpeg...")
    utils.record_stream_event('remuxed')
    
    ffmpeg_path = config.get('ffmpeg', 'executable_path', fallback='/usr/bin/ffmpeg').strip()
    add_opts_str = config.get('ffmpeg', 'add_opts', fallback='').strip()

    if target_fmt == 'ts':
        # MPEG transport stream
        cmd = [
            ffmpeg_path,
            '-loglevel', 'warning',
            '-probesize', '524288',
            '-analyzeduration', '1000000',
            '-reconnect', '1',
            '-reconnect_at_eof', '1',
            '-reconnect_streamed', '1',
            '-thread_queue_size', '8192',
            '-i', v_url,
            '-thread_queue_size', '8192',
            '-i', a_url,
            '-map', '0:v:0',
            '-map', '1:a:0',
            '-c', 'copy',
            '-f', 'mpegts',
            '-fflags', '+genpts+igndts',
            '-max_interleave_delta', '100M',
            '-avoid_negative_ts', 'make_zero',
            '-pcr_period', '20',
        ]
        mime = 'video/mpeg'
        dlna_pn = 'AVC_TS_HD_EU'
        chunk_size = 188 * 348
    else:
        # fragmented MP4
        cmd = [
            ffmpeg_path,
            '-loglevel', 'warning',
            '-probesize', '524288',
            '-analyzeduration', '1000000',
            '-i', v_url,
            '-i', a_url,
            '-map', '0:v:0',
            '-map', '1:a:0',
            '-c', 'copy',
            '-f', 'mp4',
            '-movflags', 'frag_keyframe+empty_moov+default_base_moof+omit_tfhd_offset',
            '-frag_duration', '2000000',
        ]
        mime = 'video/mp4'
        dlna_pn = None
        chunk_size = 64 * 1024
    
    if add_opts_str:
        cmd.extend(add_opts_str.split())
    cmd.append('pipe:1')

    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    threading.Thread(target=log_ffmpeg_stderr, args=(proc,), daemon=True).start()

    def generate_remux():
        try:
            while True:
                chunk = proc.stdout.read(chunk_size)
                if not chunk: break
                yield chunk
        except Exception: pass
        finally:
            try:
                proc.stdout.close()
                proc.stderr.close()
                proc.terminate()
                proc.wait(timeout=1)
            except: pass

    pn_string = f"DLNA.ORG_PN={dlna_pn};" if dlna_pn else ""
    response_headers = {
        'Content-Type': mime,
        'Accept-Ranges': 'bytes',
        'transferMode.dlna.org': 'Streaming',
        'contentFeatures.dlna.org': f'{pn_string}DLNA.ORG_OP=01;DLNA.ORG_CI=0;DLNA.ORG_FLAGS=01700000000000000000000000000000'
    }
    return Response(stream_with_context(generate_remux()), status=200, headers=response_headers)

def _serve_dummy_file():
    """Helper to serve the dummy silence file for 'hit' triggers."""
    dummy_path = os.path.join(utils.CONFIG_DIR, "assets", "dummy.mp3")
    if os.path.exists(dummy_path):
        with open(dummy_path, 'rb') as f:
            data = f.read()
        return Response(data, mimetype="audio/mpeg")
    else:
        return Response(status=204)

def start_proxy():
    bind_ip = config.get('proxy', 'proxy_ip', fallback='0.0.0.0')
    port = config.getint('proxy', 'proxy_port', fallback=5000)
    app.run(host=bind_ip, port=port, threaded=True)

if __name__ == '__main__':
    start_proxy()
