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

_LOG_SRC = __name__

# silence Flask development server warning banner
flask.cli.show_server_banner = lambda *args: None

# disable Flask/Werkzeug access logging (only show errors)
flask_log = logging.getLogger('werkzeug')
flask_log.setLevel(logging.ERROR)

config = utils.load_config()
app = Flask(__name__)

# helper to set up flask routes from patterns
def get_flask_path(key, default, is_static=False):
    pattern = config.get('proxy', key, fallback=default)
    if is_static:
        return pattern.replace('{video_id}', '<path:target_url>')
    return pattern.replace('{service}', '<service>').replace('{video_id}', '<path:video_id>')

# read configured routes
path_play      = get_flask_path('proxy_url_pattern', '/play/{service}/{video_id}')
path_redirect  = get_flask_path('proxy_url_pattern_redirect', '/redirect/{service}/{video_id}')
path_proxy     = get_flask_path('proxy_url_pattern_proxy', '/proxy/{service}/{video_id}')
path_remux     = get_flask_path('proxy_url_pattern_remux', '/remux/{service}/{video_id}')
path_remux_mp4 = get_flask_path('proxy_url_pattern_remux_mp4', '/remux/mp4/{service}/{video_id}')
path_remux_ts  = get_flask_path('proxy_url_pattern_remux_ts', '/remux/ts/{service}/{video_id}')

# read configured routes for static playlists
path_bounce    = get_flask_path('proxy_url_pattern_bounce', '/bounce/{video_id}', is_static=True)
path_reflect   = get_flask_path('proxy_url_pattern_reflect', '/reflect/{video_id}', is_static=True)
path_hit       = get_flask_path('proxy_url_pattern_hit', '/hit/{video_id}', is_static=True)

def log_ffmpeg_stderr(proc):
    """Background helper to log ffmpeg error output and close pipe safely."""
    try:
        while True:
            line = proc.stderr.readline()
            if not line:
                break
            decoded = line.decode('utf-8', errors='ignore').strip()
            if decoded:
                utils.log(_LOG_SRC, f"{decoded}", "ffmpeg", level=4)
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
    config = utils.load_config()
    srv_cfg = utils.get_service_config(service_name)

    # overrides for /remux/ route
    enable_remux = True if force_dash else config.getboolean('proxy', 'enable_remux', fallback=False)
    remux_threshold = 0 if force_dash else config.getint('proxy', 'remux_threshold', fallback=0)

    cached_entry = utils.get_cached_url(video_id, service_name=service_name, min_remaining_ttl=min_remaining_ttl)
    if cached_entry:
        # if we specifically forced DASH but the cache is a single URL, re-resolve
        if not (force_dash and not cached_entry.get('is_dash')):
            utils.log(_LOG_SRC, f"Cache HIT for {service_name}:{utils.short(video_id)}", level=-3, type='S')
            utils.log(_LOG_SRC, f"Cache HIT for {service_name}:{video_id}", level=4, type='S')
            return cached_entry, True

    utils.log(_LOG_SRC, f"Cache MISS for {service_name}:{utils.short(video_id)}. Resolving via yt-dlp...", level=-3)
    utils.log(_LOG_SRC, f"Cache MISS for {service_name}:{video_id}. Resolving via yt-dlp...", level=4)

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
        utils.log(_LOG_SRC, "Batch resolve: Nothing to do for this collection.")
        return results

    utils.log(_LOG_SRC, f"Batch resolving {len(missing_ids)} missing/expired CDN URL(s) for service '{service_name}'...")

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
        missing_id_set = set(missing_ids)

        for info in extracted_infos:
            if not isinstance(info, dict):
                continue

            info_id = info.get('id')
            web_url = info.get('webpage_url') or info.get('original_url') or ''

            # direct match on short ID for youtube
            matched_id = None
            if info_id and info_id in missing_id_set:
                matched_id = info_id
            # match on full webpage URL (other extractors)
            elif web_url and web_url in missing_id_set:
                matched_id = web_url
            else:
                # fallback search (e.g. if orig_id contained the video ID)
                for mid in missing_ids:
                    if (info_id and (mid == info_id or info_id in mid)) or (web_url and mid in web_url):
                        matched_id = mid
                        break

            target_id = matched_id or info_id
            if not target_id:
                continue

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
                    results[target_id] = entry
                    utils.set_cached_url(target_id, entry, service_name=service_name)
                    continue

            # single-file fallback
            cdn_url = info.get('url')
            if cdn_url:
                entry = {'url': cdn_url, 'is_dash': False}
                results[target_id] = entry
                utils.set_cached_url(target_id, entry, service_name=service_name)

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

@app.route(path_remux_mp4)
def route_remux_mp4(service, video_id):
    return _stream_internal(service, video_id, mode_override='remux', target_format_override='mp4')

@app.route(path_remux_ts)
def route_remux_ts(service, video_id):
    return _stream_internal(service, video_id, mode_override='remux', target_format_override='ts')

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

def _stream_internal(service, video_id, mode_override=None, target_format_override=None):
    """Master handler for all resolving routes."""
    config = utils.load_config()
    global_mode = config.get('proxy', 'mode', fallback='redirect').strip().lower()
    
    # check if we are forcing remux
    force_remux = (mode_override == 'remux')
    # determine the effective operating mode
    mode = mode_override if mode_override and mode_override != 'remux' else global_mode

    try:
        cached_entry, is_cached = resolve_cdn_url(video_id, service_name=service, force_dash=force_remux)

        # --- REMUX: If entry is DASH, stream via ffmpeg in-memory ---
        if isinstance(cached_entry, dict) and cached_entry.get('is_dash'):
            return _serve_remux_implementation(cached_entry, f"{service}:{video_id}", target_format_override=target_format_override)

        # extract direct CDN URL
        cdn_url = cached_entry.get('url') if isinstance(cached_entry, dict) else cached_entry

        # --- REDIRECT MODE: 302 Redirect (ultra lightweight for old hardware) ---
        if mode == 'redirect':
            utils.log(_LOG_SRC, f"302 Redirecting {service}:{utils.short(video_id)} to CDN...", level=-3)
            utils.log(_LOG_SRC, f"302 Redirecting {service}:{video_id} to CDN...", level=4)
            utils.record_stream_event('redirects')
            return redirect(cdn_url, code=302)

        # --- PROXY MODE: active proxying of bytes (fallback for TVs that don't follow 302) ---
        http_headers = cached_entry.get('http_headers') if isinstance(cached_entry, dict) else None
        return _serve_proxy_implementation(cdn_url, f"{service}:{video_id}", is_cached, service)

    except Exception as e:
        utils.log(_LOG_SRC, f"{e} (mode: {mode})", level=1, type='E')
        return f"Proxy Error ({mode} mode): {str(e)}", 500

def _static_internal(target_url, mode):
    """Master handler for all non-resolving (custom playlist) routes."""
    url = urllib.parse.unquote(target_url)
    
    if mode == 'bounce':
        utils.log(_LOG_SRC, f"Bouncing client to static URL: {utils.short(url)}", level=-3)
        utils.log(_LOG_SRC, f"Bouncing client to static URL: {url}", level=4)
        utils.record_stream_event('redirects')
        return redirect(url, code=302)
        
    elif mode == 'reflect':
        utils.log(_LOG_SRC, f"Reflecting static URL to client: {utils.short(url)}", level=-3)
        utils.log(_LOG_SRC, f"Reflecting static URL to client: {url}", level=4)
        return _serve_proxy_implementation(url, f"static:{url}")
        
    elif mode == 'hit':
        utils.log(_LOG_SRC, f"Hitting static URL: {utils.short(url)}", level=-3)
        utils.log(_LOG_SRC, f"Hitting static URL: {url}", level=4)
        try:
            requests.get(url, timeout=5)
        except Exception as e:
            utils.log(_LOG_SRC, f"Hit failed: {e}", level=1, type='E')
        return _serve_dummy_file()

# --- Implementation of proxy modes ---

def _serve_proxy_implementation(cdn_url, identifier, is_cached=False, service=None):
    utils.log(_LOG_SRC, f"Tunneling stream bytes for {utils.short(identifier)}...", level=-3)
    utils.log(_LOG_SRC, f"Tunneling stream bytes for {identifier}...", level=4)
    utils.record_stream_event('proxied')
    
    req_headers = {}
    if 'Range' in request.headers:
        req_headers['Range'] = request.headers['Range']

    upstream_res = requests.get(cdn_url, headers=req_headers, stream=True)
    final_mime = upstream_res.headers.get('Content-Type', 'video/mp4')

    # If cached link expired early (403/410), purge cache & retry once with fresh URL
    if is_cached and upstream_res.status_code in (403, 404, 410) and service:
        utils.log(_LOG_SRC, f"Cached URL expired for {utils.short(identifier)} ({upstream_res.status_code}). Refreshing...", level=-3)
        utils.log(_LOG_SRC, f"Cached URL expired for {identifier} ({upstream_res.status_code}). Refreshing...", level=4)
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

def _serve_remux_implementation(cached_entry, identifier, target_format_override=None):
    config = utils.load_config()
    v_url = cached_entry.get('video_url')
    a_url = cached_entry.get('audio_url')

    target_fmt = (target_format_override or config.get('proxy', 'remux_target_format', fallback='ts')).strip().lower()
    utils.log(_LOG_SRC, f"Remuxing DASH to {target_fmt.upper()} for {utils.short(identifier)} on the fly via ffmpeg...", level=-3)
    utils.log(_LOG_SRC, f"Remuxing DASH to {target_fmt.upper()} for {identifier} on the fly via ffmpeg...", level=4)
    utils.record_stream_event('remuxed')

    ffmpeg_path = config.get('ffmpeg', 'executable_path', fallback='/usr/bin/ffmpeg').strip()
    add_opts_str = config.get('ffmpeg', 'add_opts', fallback='').strip()
    ffmpeg_loglevel = 'debug' if utils.get_verbosity() >= 5 else 'warning'

    # determine command based on format
    if target_fmt == 'ts':
        # MPEG transport stream
        cmd = [
            ffmpeg_path,
            '-loglevel', ffmpeg_loglevel,
            '-probesize', '524288',
            '-analyzeduration', '1000000',
            '-reconnect', '1',
            '-reconnect_streamed', '1',
            '-reconnect_delay_max', '30',
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
            '-loglevel', ffmpeg_loglevel,
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

    # --- debug output ---
    utils.log(_LOG_SRC, f"Calling command: {' '.join(cmd)}", "ffmpeg", level=5, type='D')

    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    threading.Thread(target=log_ffmpeg_stderr, args=(proc,), daemon=True).start()

    def generate_remux():
        try:
            while True:
                chunk = proc.stdout.read(chunk_size)
                if not chunk:
                    break
                yield chunk
        except (ConnectionResetError, BrokenPipeError):
            utils.log(_LOG_SRC, f"Client disconnected for {identifier}", level=4)
        except Exception as e:
            utils.log(_LOG_SRC, f"Remux error: {e}", level=1, type='E')
        finally:
            # process cleanup logic
            if proc.poll() is None:
                utils.log(_LOG_SRC, f"Cleaning up FFmpeg process for {identifier}...", "ffmpeg")
                try:
                    proc.stdout.close()
                    proc.stderr.close()
                    proc.terminate()
                    try:
                        proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        utils.log(_LOG_SRC, f"FFmpeg process not gone after timeout, sending SIGKILL...", "ffmpeg", level=2, type='W')
                        proc.kill()
                        proc.wait()
                except Exception as e:
                    utils.log(_LOG_SRC, f"Error during cleanup: {e}", "ffmpeg", level=1, type='E')

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
