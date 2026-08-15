# ==============================================================================
# yt-dlna: src/sync.py
# Lightweight media gateway, proxying streaming playlists to DLNA/UPnP clients
#
# Copyright (c) 2026 Fabian Schneider (@fabianswebworld) and contributors
# Licensed under the MIT License - see LICENSE file for details.
# SPDX-License-Identifier: MIT
# ==============================================================================

import os
import json
import urllib.parse
import threading
import utils
import proxy

# thread-safe locks for playlist processing and database file writing
_sync_lock = threading.Lock()
_file_lock = threading.Lock()
_currently_syncing = set()

def run_sync(targets=None):
    config = utils.load_config()
    base_url = utils.get_stream_base_url()
    url_pattern = utils.get_stream_pattern()
    precache_enabled = config.getboolean('sync', 'precache_cdn_urls', fallback=True)
    sync_interval = config.getint('sync', 'sync_interval', fallback=3600)
    
    # parse targets into a normalized lowercase list if provided
    target_list = []
    if targets:
        if isinstance(targets, str):
            targets = [targets]
        for t in targets:
            for item in t.split(','):
                cleaned = item.strip().lower()
                if cleaned:
                    target_list.append(cleaned)

    playlists = utils.get_playlists_config()

    for pl in playlists:
        if not pl.get('enabled', True):
            # skip syncing if playlist not enabled
            continue 

        folder_name = pl['title']
        service_name = pl['service']
        
        # smart target matching: skip playlist if targets specified and matches neither title nor service
        if target_list:
            match_title = folder_name.lower() in target_list
            match_service = service_name.lower() in target_list
            if not (match_title or match_service):
                continue

        # thread-safe check to prevent duplicate concurrent syncs for the same playlist
        folder_key = folder_name.lower()
        with _sync_lock:
            if folder_key in _currently_syncing:
                print(f"[Sync] Playlist '{folder_name}' is currently being synced. Skipping.")
                continue
            _currently_syncing.add(folder_key)

        raw_url = pl['url']
        limit_items = pl['limit_items']
        sort_by = pl['sort_by']
        srv_cfg = utils.get_service_config(service_name)
        synced_entry = {}

        try:
            # special handling of services that use the youtube extractor
            if srv_cfg['extractor'] == 'youtube' and not (raw_url.startswith('http://') or raw_url.startswith('https://')):
                playlist_url = f"https://www.youtube.com/playlist?list={raw_url}"
            else:
                playlist_url = raw_url

            print(f"[Sync] Indexing {folder_name} ({service_name})...")
            
            ydl_opts = {
                'extract_flat': True,
                'ies': [srv_cfg['extractor']]
            }

            # enable yt-dlp native reverse extraction using negative index slicing for tail items
            if sort_by in ('reverse', 'date', 'date_desc', 'newest'):
                ydl_opts['playlistreverse'] = True
                if limit_items > 0:
                    ydl_opts['playlist_items'] = f"-{limit_items}:"

            # pass approximate_date ONLY if date sorting is requested for youtube
            if sort_by in ('date', 'date_desc', 'date_asc', 'newest', 'oldest') and srv_cfg['extractor'] == 'youtube':
                ydl_opts['extractor_args'] = {'youtubetab': ['approximate_date']}

            # cap playlistend for standard forward extraction
            if limit_items > 0 and sort_by in ('none', 'default'):
                ydl_opts['playlistend'] = limit_items

            playlist = utils.extract_youtube_info(
                playlist_url, 
                extra_opts=ydl_opts, 
                use_cookies=srv_cfg['use_cookies_for_playlists'],
                cookie_path=srv_cfg['cookie_path']
            )
            video_list = []
            
            if 'entries' in playlist and playlist['entries']:
                # slice entries immediately if no custom sorting is requested
                if limit_items > 0 and sort_by in ('none', 'default'):
                    entries = playlist['entries'][:limit_items]
                else:
                    entries = playlist['entries']
                
                for entry in entries:
                    if entry:
                        # use extractor check to determine short ID vs full URL
                        if srv_cfg['extractor'] == 'youtube':
                            v_id = entry.get('id') or entry.get('url') or 'unknown'
                            web_url = f"https://www.youtube.com/watch?v={v_id}"
                        else:
                            web_url = entry.get('webpage_url') or entry.get('url')
                            if web_url and (web_url.startswith('http://') or web_url.startswith('https://')):
                                v_id = web_url
                            else:
                                v_id = entry.get('id') or 'unknown'

                        encoded_v_id = urllib.parse.quote(v_id, safe='')
                        
                        target_route = (url_pattern
                                        .replace('{service}', service_name)
                                        .replace('{video_id}', encoded_v_id))
                        if not target_route.startswith('/'):
                            target_route = '/' + target_route

                        raw_title = entry.get('title', 'Unknown Video')
                        clean_title = raw_title.replace('\\"', '"').replace('\\', '')
                        
                        channel_name = entry.get('uploader') or entry.get('channel') or entry.get('uploader_id') or ''
                        duration_sec = entry.get('duration')
                        upload_date = entry.get('upload_date') or entry.get('release_date') or entry.get('timestamp') or ''
                        pl_idx = entry.get('playlist_index') or entry.get('index')
                        
                        item_dict = {
                            "id": v_id,
                            "service": service_name,
                            "title": clean_title,
                            "channel": channel_name,
                            "duration": duration_sec,
                            "upload_date": upload_date,
                            "proxy_url": f"{base_url}{target_route}",
                            "web_url": web_url
                        }
                        if pl_idx is not None:
                            item_dict["pl_index"] = pl_idx

                        video_list.append(item_dict)

                # --- apply custom Python sorting if requested
                if sort_by in ('date', 'date_desc', 'newest'):
                    video_list.sort(key=lambda x: str(x.get('upload_date') or ''), reverse=True)
                elif sort_by in ('date_asc', 'oldest'):
                    video_list.sort(key=lambda x: str(x.get('upload_date') or '99999999'))
                elif sort_by in ('title', 'title_asc'):
                    video_list.sort(key=lambda x: str(x.get('title', '')).lower())
                elif sort_by == 'title_desc':
                    video_list.sort(key=lambda x: str(x.get('title', '')).lower(), reverse=True)
                elif sort_by in ('duration', 'duration_desc'):
                    video_list.sort(key=lambda x: int(x.get('duration') or 0), reverse=True)
                elif sort_by == 'duration_asc':
                    video_list.sort(key=lambda x: int(x.get('duration') or 0))

                # --- cap items AFTER sorting to guarantee top N items
                if limit_items > 0:
                    video_list = video_list[:limit_items]

                # --- proactively pre-cache CDN URLs if enabled and missing/expired,
                # --- or expiring within next sync interval
                if precache_enabled and video_list:
                    try:
                        v_ids = [item['id'] for item in video_list]
                        print(f"[Sync] Pre-caching {folder_name}...")
                        proxy.resolve_cdn_urls_batch(
                            v_ids, 
                            service_name=service_name, 
                            min_remaining_ttl=sync_interval
                        )
                    except Exception as e:
                        print(f"[Sync] Pre-cache warning for {folder_name}: {e}")
            
            synced_entry = {folder_name: video_list}

        except Exception as e:
            print(f"[Sync] Failed to sync {folder_name}: {e}")
            
            # inject explicit failure notification item into library on extraction error
            refresh_url = f"{base_url}/virtual_refresh_stream?playlist={urllib.parse.quote(folder_name)}"
            synced_entry = {folder_name: [{
                "id": "sync_failed_notice",
                "service": service_name,
                "title": "[Sync Failed: Check Cookie File / Login Settings]",
                "is_error": True,
                "proxy_url": refresh_url
            }]}

        finally:
            # always release folder from active syncing set when finished or on error
            with _sync_lock:
                _currently_syncing.discard(folder_key)

        # --- incremental atomic read-merge-write after each playlist completes
        if synced_entry:
            with _file_lock:
                disk_library = {}
                if os.path.exists(utils.JSON_PATH):
                    try:
                        with open(utils.JSON_PATH, "r", encoding="utf-8") as f:
                            disk_library = json.load(f)
                    except Exception:
                        disk_library = {}

                disk_library.update(synced_entry)

                # re-order dictionary keys to match exact section order in yt-dlna.conf
                ordered_library = {}
                for p in playlists:
                    f_name = p['title']
                    if f_name in disk_library:
                        ordered_library[f_name] = disk_library[f_name]

                os.makedirs(os.path.dirname(utils.JSON_PATH), exist_ok=True)
                with open(utils.JSON_PATH, "w", encoding="utf-8") as f:
                    json.dump(ordered_library, f, indent=4, ensure_ascii=False)

    print(f"[Sync] Playlists synchronized.")
    if precache_enabled:
        print(f"[Sync] CDN URL cache updated.")

def rename_playlist_data(old_name, new_name):
    """Renames a playlist key in playlists.json using the internal file lock."""
    with _file_lock:
        if not os.path.exists(utils.JSON_PATH):
            return
        try:
            with open(utils.JSON_PATH, 'r', encoding='utf-8') as f:
                content = f.read().strip()
                if not content: return
                library = json.loads(content)

            if old_name in library:
                # atomically swap keys
                library[new_name] = library.pop(old_name)
                
                with open(utils.JSON_PATH, 'w', encoding='utf-8') as f:
                    json.dump(library, f, indent=4, ensure_ascii=False)
                print(f"[Sync] Library entry renamed: '{old_name}' -> '{new_name}'")
        except Exception as e:
            print(f"[Sync] Error renaming library entry: {e}")
            raise e

if __name__ == "__main__":
    run_sync()
