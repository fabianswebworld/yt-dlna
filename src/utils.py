# ==============================================================================
# yt-dlna: src/utils.py
# Lightweight media gateway, proxying streaming playlists to DLNA/UPnP clients
#
# Copyright (c) 2026 Fabian Schneider (@fabianswebworld) and contributors
# Licensed under the MIT License - see LICENSE file for details.
# SPDX-License-Identifier: MIT
# ==============================================================================

import os
import time
import socket
import json
import subprocess
import configparser
import yt_dlp

__version__ = "1.0.0"

# step up one level to application root where config and data folders reside
SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DIR = SCRIPT_DIR

CONFIG_FILE = os.path.join(CONFIG_DIR, 'yt-dlna.conf')
JSON_PATH = os.path.join(CONFIG_DIR, 'data', 'playlists.json')
CACHE_PATH = os.path.join(CONFIG_DIR, 'data', 'urlcache.json')

def load_config():
    config = configparser.ConfigParser()
    config.optionxform = str
    config.read(CONFIG_FILE)
    return config

def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 1))
        ip = s.getsockname()[0]
    except Exception:
        ip = '127.0.0.1'
    finally:
        s.close()
    return ip

def get_stream_base_url():
    config = load_config()
    base = config.get('dlna', 'stream_url_base', fallback='').strip()
    if not base:
        ip = get_local_ip()
        port = config.get('proxy', 'proxy_port', fallback='5000')
        base = f"http://{ip}:{port}"
    return base

def get_stream_pattern():
    config = load_config()
    pattern = config.get('dlna', 'stream_url_pattern', fallback='').strip()
    if not pattern:
        pattern = config.get('proxy', 'proxy_url_pattern', fallback='/play/{service}/{video_id}')
    return pattern

def format_duration_display(seconds):
    if not seconds or seconds == 'unknown':
        return ""
    try:
        sec = int(seconds)
        m, s = divmod(sec, 60)
        h, m = divmod(m, 60)
        if h > 0:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m}:{s:02d}"
    except Exception:
        return str(seconds)

def format_duration_dlna(seconds):
    if not seconds or seconds == 'unknown':
        return ""
    try:
        sec = int(seconds)
        m, s = divmod(sec, 60)
        h, m = divmod(m, 60)
        return f"{h:02d}:{m:02d}:{s:02d}.000"
    except Exception:
        return ""

def get_service_config(service_name='youtube'):
    config = load_config()
    service_section = f"services:{service_name}"
    
    # default fallbacks from [services] and [proxy]
    global_use_pl_cookies = config.getboolean('services', 'use_cookies_for_playlists', fallback=True)
    global_use_pb_cookies = config.getboolean('services', 'use_cookies_for_playback', fallback=False)
    global_cookie_path = config.get('services', 'cookie_path', fallback='data/cookies.txt').strip()
    global_fmt = config.get('services', 'format', fallback='b[ext=mp4][protocol^=http] / 18 / b / best').strip()
    global_title_fmt = config.get('services', 'title_format', fallback='{index}. {channel}: {title} ({duration})').strip()
    
    global_enable_cache = config.getboolean('proxy', 'enable_cache', fallback=True)
    global_default_ttl = config.getint('proxy', 'default_cache_ttl', fallback=14400)
    
    if service_section in config:
        sec = config[service_section]
        use_pl_cookies = config.getboolean(service_section, 'use_cookies_for_playlists', fallback=global_use_pl_cookies)
        use_pb_cookies = config.getboolean(service_section, 'use_cookies_for_playback', fallback=global_use_pb_cookies)
        cookie_path = sec.get('cookie_path', fallback=global_cookie_path).strip()
        enable_cache = config.getboolean(service_section, 'enable_cache', fallback=global_enable_cache)
        cache_ttl = config.getint(service_section, 'cache_ttl', fallback=global_default_ttl)
        extractor = sec.get('extractor', fallback=service_name).strip().lower()
        fmt = sec.get('format', fallback=global_fmt).strip()
        title_fmt = sec.get('title_format', fallback=global_title_fmt).strip()
    else:
        use_pl_cookies = global_use_pl_cookies
        use_pb_cookies = global_use_pb_cookies
        cookie_path = global_cookie_path
        enable_cache = global_enable_cache
        # guarantee youtube default service config with 18000 cache_ttl
        cache_ttl = 18000 if service_name == 'youtube' else global_default_ttl
        extractor = 'youtube' if service_name == 'youtube' else service_name.lower()
        fmt = global_fmt
        title_fmt = global_title_fmt
        
    return {
        'service': service_name,
        'extractor': extractor,
        'format': fmt,
        'use_cookies_for_playlists': use_pl_cookies,
        'use_cookies_for_playback': use_pb_cookies,
        'cookie_path': cookie_path,
        'enable_cache': enable_cache,
        'cache_ttl': cache_ttl,
        'title_format': title_fmt
    }

def get_playlists_config():
    config = load_config()
    default_service = config.get('playlists', 'default_service', fallback='youtube').strip().lower()
    global_limit = config.getint('playlists', 'limit_items', fallback=0)
    global_sort = config.get('playlists', 'sort_by', fallback='none').strip().lower()
    playlists = []
    
    for section in config.sections():
        if section.startswith('playlists:'):
            title = section.split('playlists:', 1)[1].strip()
            sec = config[section]
            service = sec.get('service', fallback=default_service).strip().lower()
            url = sec.get('url', fallback='').strip()
            limit_items = sec.getint('limit_items', fallback=global_limit)
            sort_by = sec.get('sort_by', fallback=global_sort).strip().lower()
            if url:
                playlists.append({
                    'title': title,
                    'service': service,
                    'url': url,
                    'limit_items': limit_items,
                    'sort_by': sort_by
                })
    return playlists

# --- CDN URL cache helpers ---

def get_cached_url(video_id, service_name='youtube', min_remaining_ttl=0):
    srv_cfg = get_service_config(service_name)
    if not srv_cfg['enable_cache']:
        return None
    
    ttl = srv_cfg['cache_ttl']
    
    if not os.path.exists(CACHE_PATH):
        return None
        
    try:
        with open(CACHE_PATH, 'r', encoding='utf-8') as f:
            cache = json.load(f)
        
        entry = cache.get(video_id)
        if entry:
            cached_time = entry.get('timestamp', 0)
            # ttl == 0 indicates infinite cache
            if ttl == 0:
                return entry.get('url')
                
            remaining_ttl = ttl - (time.time() - cached_time)
            if remaining_ttl > min_remaining_ttl:
                return entry.get('url')
    except Exception:
        pass
    return None

def set_cached_url(video_id, url, service_name='youtube'):
    srv_cfg = get_service_config(service_name)
    if not srv_cfg['enable_cache']:
        return

    cache = {}
    if os.path.exists(CACHE_PATH):
        try:
            with open(CACHE_PATH, 'r', encoding='utf-8') as f:
                cache = json.load(f)
        except Exception:
            cache = {}

    cache[video_id] = {
        'url': url,
        'timestamp': time.time(),
        'service': service_name
    }

    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    try:
        with open(CACHE_PATH, 'w', encoding='utf-8') as f:
            json.dump(cache, f, indent=2)
    except Exception as e:
        print(f"[Cache] Error writing cache file: {e}")

def invalidate_cached_url(video_id):
    if not os.path.exists(CACHE_PATH):
        return
    try:
        with open(CACHE_PATH, 'r', encoding='utf-8') as f:
            cache = json.load(f)
        if video_id in cache:
            del cache[video_id]
            with open(CACHE_PATH, 'w', encoding='utf-8') as f:
                json.dump(cache, f, indent=2)
    except Exception:
        pass

# --- yt extraction helper ---

def extract_youtube_info(url, extra_opts=None, use_cookies=True, cookie_path=None):
    config = load_config()
    mode = config.get('yt-dlp', 'mode', fallback='import').strip().lower()
    
    if not cookie_path:
        cookie_path = config.get('services', 'cookie_path', fallback='data/cookies.txt').strip()
        
    if cookie_path and not os.path.isabs(cookie_path):
        cookie_path = os.path.join(CONFIG_DIR, cookie_path)
        
    has_cookies = use_cookies and bool(cookie_path) and os.path.exists(cookie_path)
    active_cookie_file = cookie_path if has_cookies else None

    if mode == 'import':
        ydl_opts = {
            'quiet': True,
            'cookiefile': active_cookie_file
        }
        if extra_opts:
            ydl_opts.update(extra_opts)
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(url, download=False)
    else:
        exec_path = config.get('yt-dlp', 'executable_path', fallback='yt-dlp')
        cmd = [exec_path, url, '--dump-json']
        if active_cookie_file:
            cmd.extend(['--cookies', active_cookie_file])
        if extra_opts and extra_opts.get('extract_flat'):
            cmd.append('--flat-playlist')
        if extra_opts and extra_opts.get('playlistreverse'):
            cmd.append('--playlist-reverse')
        if extra_opts and extra_opts.get('playlistend') and extra_opts['playlistend'] > 0:
            cmd.extend(['--playlist-end', str(extra_opts['playlistend'])])
        if extra_opts and 'format' in extra_opts:
            cmd.extend(['-f', extra_opts['format']])
        if extra_opts and 'ies' in extra_opts:
            ie_regexes = [f"(?i){ie}.*" for ie in extra_opts['ies']]
            cmd.extend(['--use-extractors', ','.join(ie_regexes)])
            
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode != 0:
            raise Exception(f"CLI Error: {result.stderr}")
            
        # parse multi-line NDJSON stdout from CLI exec mode
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        if not lines:
            return {}
        if len(lines) == 1:
            return json.loads(lines[0])
            
        # if multi-line output, check if first line is playlist container or aggregate entries
        try:
            first_obj = json.loads(lines[0])
            if isinstance(first_obj, dict) and 'entries' in first_obj:
                return first_obj
        except Exception:
            pass
            
        parsed_entries = []
        for line in lines:
            try:
                parsed_entries.append(json.loads(line))
            except Exception:
                pass
        return {'entries': parsed_entries}
