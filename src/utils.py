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
import threading
import yt_dlp

__version__ = "1.1.0"

# step up one level to application root where config and data folders reside
SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DIR = SCRIPT_DIR
DATA_DIR = os.path.join(CONFIG_DIR, 'data')

CONFIG_FILE = os.path.join(CONFIG_DIR, 'yt-dlna.conf')
JSON_PATH = os.path.join(DATA_DIR, 'playlists.json')
CACHE_PATH = os.path.join(DATA_DIR, 'urlcache.json')

# thread-safe volatile playback statistics counter since daemon boot
_stats_lock = threading.Lock()
_cache_file_lock = threading.Lock()

STREAM_STATS = {
    'total_served': 0,
    'redirects': 0,
    'proxied': 0,
    'remuxed': 0
}

def xml_escape(text):
    """Utility to escape special characters for XML/HTML safe output."""
    if not text:
        return ""
    return (str(text)
            .replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;')
            .replace('"', '&quot;')
            .replace("'", '&apos;'))

def record_stream_event(event_type):
    """Thread-safely increments playback statistics counters."""
    with _stats_lock:
        STREAM_STATS['total_served'] += 1
        if event_type in STREAM_STATS:
            STREAM_STATS[event_type] += 1

def load_config():
    config = configparser.ConfigParser()
    config.optionxform = str
    config.read(CONFIG_FILE, encoding='utf-8')
    return config

def _update_config_file(updates_dict):
    """
    Config file writer: Performs non-destructive line-by-line updates to yt-dlna.conf.
    Preserves all comments, indentation, and formatting.
    updates_dict: { 'section_name': { 'key': 'value', ... }, ... }
    If a value in updates_dict is None, the key is deleted from the file.
    """
    if not os.path.exists(CONFIG_FILE): return
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    new_lines = []
    current_section = None
    seen_keys = set()
    processed_sections = set()

    for line in lines:
        stripped = line.strip()
        
        if stripped.startswith('[') and stripped.endswith(']'):
            # before leaving previous section, add missing (new) keys
            if current_section in updates_dict:
                for k, v in updates_dict[current_section].items():
                    # only add if key was never seen AND not marked for deletion
                    if k not in seen_keys and v is not None:
                        new_lines.append(f"{k} = {v}\n")
            
            current_section = stripped[1:-1].strip()
            processed_sections.add(current_section)
            seen_keys = set()
            new_lines.append(line)
            continue

        # if we are in a section targeted for updates
        if current_section in updates_dict and '=' in line and not stripped.startswith(('#', ';')):
            key_part, _ = line.split('=', 1)
            key = key_part.strip()
            
            if key in updates_dict[current_section]:
                seen_keys.add(key)
                new_val = updates_dict[current_section][key]
                
                if new_val is None:
                    continue

                indent = line[:len(line) - len(line.lstrip())]
                new_lines.append(f"{indent}{key} = {new_val}\n")
                continue

        new_lines.append(line)

    # handle end of file for the last section
    if current_section in updates_dict:
        for k, v in updates_dict[current_section].items():
            if k not in seen_keys and v is not None:
                new_lines.append(f"{k} = {v}\n")

    # add new sections
    for sec_name, key_dict in updates_dict.items():
        if sec_name not in processed_sections:
            new_lines.append(f"\n[{sec_name}]\n")
            for k, v in key_dict.items():
                if v is not None: # Don't create a section just to delete a key
                    new_lines.append(f"{k} = {v}\n")

    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        f.writelines(new_lines)
    load_config()

def update_config_from_dict(parsed_data):
    """Updates the config using a nested dictionary structure."""
    updates = {}
    for sec, keys in parsed_data.items():
        if isinstance(keys, dict):
            updates[sec] = {k: (str(v) if v is not None else None) for k, v in keys.items()}
    
    _update_config_file(updates)

def update_config_single_key(section, key, value):
    """Updates or adds a single key in a specific section."""
    val = str(value) if value is not None else None
    _update_config_file({section: {key: val}})

def rename_config_section(old_section, new_section):
    """Renames section header while preserving all other file content."""
    if not os.path.exists(CONFIG_FILE):
        return

    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    old_h = f"[{old_section}]"
    new_h = f"[{new_section}]"
    found = False

    new_lines = []
    for line in lines:
        if line.strip() == old_h:
            indent = line[:len(line) - len(line.lstrip())]
            new_lines.append(f"{indent}{new_h}\n")
            found = True
        else:
            new_lines.append(line)

    if not found:
        raise Exception(f"Section {old_h} not found in config file.")

    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        f.writelines(new_lines)
    
    load_config()

def reorder_config_sections(section_prefix, new_order_names):
    """
    Reorders config sections while keeping comments in place.
    """
    if not os.path.exists(CONFIG_FILE):
        return

    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    target_prefix = section_prefix if section_prefix.endswith(':') else f"{section_prefix}:"
    
    # index of the first target section header, lines (comments) above it never move
    anchor_idx = -1
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(f"[{target_prefix}") and stripped.endswith(']'):
            anchor_idx = i
            break
            
    if anchor_idx == -1:
        return

    # header_lines contains everything including the general documentation
    header_lines = lines[:anchor_idx]
    # moving_lines contains the sections we actually want to shuffle
    moving_lines = lines[anchor_idx:]

    # parse moving_lines into blocks
    blocks = []
    current_section = None
    current_block_lines = []
    comment_buffer = []

    for line in moving_lines:
        stripped = line.strip()
        if stripped.startswith('[') and stripped.endswith(']'):
            if current_section:
                blocks.append((current_section, current_block_lines))
            current_section = stripped[1:-1].strip()
            current_block_lines = comment_buffer + [line]
            comment_buffer = []
        elif stripped.startswith(('#', ';')) or not stripped:
            comment_buffer.append(line)
        else:
            current_block_lines.extend(comment_buffer)
            comment_buffer = []
            current_block_lines.append(line)

    if current_section:
        blocks.append((current_section, current_block_lines + comment_buffer))

    # reorder logic
    target_blocks_map = {}
    other_blocks = []
    for name, lines_list in blocks:
        if name.startswith(target_prefix):
            display_name = name.replace(target_prefix, '')
            target_blocks_map[display_name] = lines_list
        else:
            other_blocks.append((name, lines_list))

    # reassemble
    new_lines = []
    new_lines.extend(header_lines)
    
    for name in new_order_names:
        if name in target_blocks_map:
            new_lines.extend(target_blocks_map[name])
            del target_blocks_map[name]
    
    for name in target_blocks_map:
        new_lines.extend(target_blocks_map[name])
    for _, lines_list in other_blocks:
        new_lines.extend(lines_list)

    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        f.writelines(new_lines)

    load_config()

def delete_config_section(section_name):
    """Removes a section and its keys from the INI, preserving all other sections and comments."""
    if not os.path.exists(CONFIG_FILE):
        return

    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    new_lines = []
    skip = False
    target_header = f"[{section_name}]"

    for line in lines:
        stripped = line.strip()
        # if we hit the target section, start skipping
        if stripped == target_header:
            skip = True
            continue
        # if we are skipping and hit a NEW section, stop skipping
        if skip and stripped.startswith('[') and stripped.endswith(']'):
            skip = False
        
        if not skip:
            new_lines.append(line)

    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        f.writelines(new_lines)
    
    load_config()

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

def get_secure_path(relative_path, check_exists=False):
    """
    Resolves a relative path within the data/ directory.
    Returns (absolute_path, error_message).
    """
    # deny absolute paths from web UI
    if os.path.isabs(relative_path):
        return None, "Absolute paths are not permitted."

    # block directory traversals
    jail = os.path.abspath(DATA_DIR)
    target = os.path.abspath(os.path.join(CONFIG_DIR, relative_path))

    try:
        if os.path.commonpath([jail, target]) != jail:
            return None, "Access Denied: Path must be within 'data/'."
    except ValueError:
        return None, "Invalid path location."

    if check_exists and os.path.exists(target):
        return None, f"File '{os.path.basename(target)}' already exists."

    return target, None

# --- service configuration helpers ---

def get_service_config(service_name='youtube'):
    config = load_config()
    service_section = f"services:{service_name}"
    
    # default fallbacks from [services] and [proxy]
    global_use_pl_cookies = config.getboolean('services', 'use_cookies_for_playlists', fallback=True)
    global_use_pb_cookies = config.getboolean('services', 'use_cookies_for_playback', fallback=False)
    global_cookie_path = config.get('services', 'cookie_path', fallback='data/cookies.txt').strip()
    global_fmt = config.get('services', 'format', fallback='b[ext=mp4][protocol^=http]/18/b/best').strip()
    global_fmt_dash = config.get('services', 'format_dash', fallback='').strip()
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
        fmt_dash = sec.get('format_dash', fallback=global_fmt_dash).strip()
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
        fmt_dash = global_fmt_dash
        title_fmt = global_title_fmt
        
    return {
        'service': service_name,
        'extractor': extractor,
        'format': fmt,
        'format_dash': fmt_dash,
        'use_cookies_for_playlists': use_pl_cookies,
        'use_cookies_for_playback': use_pb_cookies,
        'cookie_path': cookie_path,
        'enable_cache': enable_cache,
        'cache_ttl': cache_ttl,
        'title_format': title_fmt
    }

# --- playlist configuration helpers ---

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
            is_enabled = config.getboolean(section, 'enabled', fallback=True)
            if url:
                playlists.append({
                    'title': title,
                    'service': service,
                    'url': url,
                    'limit_items': limit_items,
                    'sort_by': sort_by,
                    'enabled': is_enabled
                })
    return playlists

def get_custom_playlists_registry():
    """Returns a list of custom playlist definitions from yt-dlna.conf."""
    cfg = load_config()
    registry = []
    for section in cfg.sections():
        if section.startswith('custom_playlists:'):
            name = section.replace('custom_playlists:', '').strip()
            registry.append({
                'name': name,
                'enabled': cfg.getboolean(section, 'enabled', fallback=True),
                'file': cfg.get(section, 'playlist_file', fallback='').strip()
            })
    return registry

# --- playlist item formatting helper ---

def format_item_title(entry, enum_idx=1):
    """Formats display title for a video entry using the service's configured title_format template."""
    if not isinstance(entry, dict):
        return 'Video'

    raw_title = entry.get('title', 'Video')
    raw_channel = entry.get('channel', '')
    duration_sec = entry.get('duration')
    service_name = entry.get('service', 'youtube')

    srv_cfg = get_service_config(service_name)
    fmt_template = srv_cfg['title_format']

    # determine playlist index (use pl_index from json if available, else loop counter)
    idx_num = entry.get('pl_index')
    if idx_num is None:
        idx_num = enum_idx
    idx_str = f"{idx_num:02d}"

    disp_duration = format_duration_display(duration_sec)

    try:
        formatted_title = fmt_template.format(
            index=idx_str,
            channel=raw_channel,
            title=raw_title,
            duration=disp_duration
        ).replace('()', '').replace('[]', '').strip()
    except Exception:
        formatted_title = f"{idx_str}. {raw_title}"

    return formatted_title

# --- CDN URL cache helpers ---

def get_cached_url(video_id, service_name='youtube', min_remaining_ttl=0):
    srv_cfg = get_service_config(service_name)
    if not srv_cfg['enable_cache']:
        return None
    
    ttl = srv_cfg['cache_ttl']
    
    if not os.path.exists(CACHE_PATH):
        return None
        
    with _cache_file_lock:
        try:
            with open(CACHE_PATH, 'r', encoding='utf-8') as f:
                cache = json.load(f)
            
            entry = cache.get(video_id)
            if entry:
                cached_time = entry.get('timestamp', 0)
                # ttl == 0 indicates infinite cache
                if ttl == 0:
                    return entry
                    
                remaining_ttl = ttl - (time.time() - cached_time)
                if remaining_ttl > min_remaining_ttl:
                    return entry
        except Exception:
            pass
    return None

def set_cached_url(video_id, entry_data, service_name='youtube'):
    srv_cfg = get_service_config(service_name)
    if not srv_cfg['enable_cache']:
        return

    with _cache_file_lock:
        cache = {}
        if os.path.exists(CACHE_PATH):
            try:
                with open(CACHE_PATH, 'r', encoding='utf-8') as f:
                    cache = json.load(f)
            except Exception:
                cache = {}

        if isinstance(entry_data, dict):
            cache_entry = dict(entry_data)
            cache_entry['timestamp'] = time.time()
            cache_entry['service'] = service_name
        else:
            cache_entry = {
                'url': entry_data,
                'timestamp': time.time(),
                'service': service_name
            }

        cache[video_id] = cache_entry

        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        try:
            with open(CACHE_PATH, 'w', encoding='utf-8') as f:
                json.dump(cache, f, indent=2)
        except Exception as e:
            print(f"[Cache] Error writing cache file: {e}")

def invalidate_cached_url(video_id):
    if not os.path.exists(CACHE_PATH):
        return
    with _cache_file_lock:
        try:
            with open(CACHE_PATH, 'r', encoding='utf-8') as f:
                cache = json.load(f)
            if video_id in cache:
                del cache[video_id]
                with open(CACHE_PATH, 'w', encoding='utf-8') as f:
                    json.dump(cache, f, indent=2)
        except Exception:
            pass

# --- yt-dlp extraction helper ---

def extract_youtube_info(url, extra_opts=None, use_cookies=True, cookie_path=None):
    """Extracts metadata or stream info for a single URL or a list of URLs in batch."""
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

        # --- debug output ---
        # print(f"[yt-dlp] yt-dlp module call for: {url}")
        # print(f"[yt-dlp] Effective Options: {ydl_opts}")
            
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            if isinstance(url, list):
                results = []
                for u in url:
                    try:
                        results.append(ydl.extract_info(u, download=False))
                    except Exception as e:
                        print(f"[Cache] Batch item extraction failed for {u}: {e}")
                return results
            else:
                return ydl.extract_info(url, download=False)
    else:
        exec_path = config.get('yt-dlp', 'executable_path', fallback='yt-dlp')
        cmd = [exec_path, '--dump-json']
        stdin_input = None
        
        if isinstance(url, list):
            cmd.extend(['--batch-file', '-'])
            stdin_input = '\n'.join(url)
        else:
            cmd.append(url)
            
        if active_cookie_file:
            cmd.extend(['--cookies', active_cookie_file])
            
        if extra_opts:
            if extra_opts.get('extract_flat'):
                cmd.append('--flat-playlist')
            if extra_opts.get('playlistreverse'):
                cmd.append('--playlist-reverse')
            if extra_opts.get('playlist_items'):
                cmd.extend(['--playlist-items', str(extra_opts['playlist_items'])])
            if extra_opts.get('playlistend') and extra_opts['playlistend'] > 0:
                cmd.extend(['--playlist-end', str(extra_opts['playlistend'])])
            if 'format' in extra_opts:
                cmd.extend(['-f', extra_opts['format']])
            if 'ies' in extra_opts:
                ie_regexes = [f"(?i){ie}.*" for ie in extra_opts['ies']]
                cmd.extend(['--use-extractors', ','.join(ie_regexes)])

            # translate extractor_args dict to CLI format: --extractor-args "ie:key=val;key2=val"
            if extra_opts and 'extractor_args' in extra_opts:
                for extractor, args in extra_opts['extractor_args'].items():
                    arg_strings = []
                    for arg_key, arg_val in args.items():
                        val_str = ','.join(arg_val) if isinstance(arg_val, list) else str(arg_val)
                        arg_strings.append(f"{arg_key}={val_str}")
                    if arg_strings:
                        cmd.extend(['--extractor-args', f"{extractor}:{';'.join(arg_strings)}"])

        # --- debug output ---
        # print(f"[yt-dlp] CLI call command: {' '.join(cmd)}")
        # if stdin_input:
        #    print(f"[yt-dlp] CLI stdin (batch): {stdin_input[:200]}...")
            
        result = subprocess.run(
            cmd, 
            input=stdin_input, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE, 
            text=True
        )
        if result.returncode != 0:
            raise Exception(f"CLI Error: {result.stderr}")
            
        # parse multi-line NDJSON stdout from CLI exec mode
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        if not lines:
            return [] if isinstance(url, list) else {}
            
        if isinstance(url, list):
            parsed_items = []
            for line in lines:
                try:
                    parsed_items.append(json.loads(line))
                except Exception:
                    pass
            return parsed_items
        else:
            if len(lines) == 1:
                return json.loads(lines[0])
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
