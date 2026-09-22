# ==============================================================================
# yt-dlna: src/dashboard.py
# Lightweight media gateway, proxying streaming playlists to DLNA/UPnP clients
#
# Copyright (c) 2026 Fabian Schneider (@fabianswebworld) and contributors
# Licensed under the MIT License - see LICENSE file for details.
# SPDX-License-Identifier: MIT
# ==============================================================================

import os
import sys
import time
import json
import re
import threading
import subprocess
import urllib.parse
from flask import Flask, request, Response, jsonify, send_from_directory, stream_with_context
import flask.cli
import queue
import logging
import proxy
import utils
import sync

_LOG_SRC = __name__

# silence Flask development server warning banner
flask.cli.show_server_banner = lambda *args: None

# disable Flask/Werkzeug access logging (only show errors)
flask_log = logging.getLogger('werkzeug')
flask_log.setLevel(logging.ERROR)

app = Flask(__name__)

app.json.sort_keys = False 

# directory for web UI static dashboard assets
HTML_DIR = os.path.join(utils.CONFIG_DIR, 'assets', 'html')

@app.route('/', strict_slashes=False)
@app.route('/overview', strict_slashes=False)
@app.route('/status', strict_slashes=False)
@app.route('/home', strict_slashes=False)
@app.route('/playlists', strict_slashes=False)
@app.route('/playlists/online', strict_slashes=False)
@app.route('/playlists/custom', strict_slashes=False)
@app.route('/services', strict_slashes=False)
@app.route('/settings', strict_slashes=False)
@app.route('/raw-config', strict_slashes=False)
def index_routes():
    """Serves the main dashboard for all top-level UI paths."""
    if os.path.exists(os.path.join(HTML_DIR, 'index.html')):
        return send_from_directory(HTML_DIR, 'index.html')
    return "<h3>Error: yt-dlna Web UI assets not found.</h3>", 404

@app.route('/add', strict_slashes=False)
@app.route('/add-to', strict_slashes=False)
@app.route('/quick-add', strict_slashes=False)
@app.route('/add/<path:playlist_name>')
@app.route('/add-to/<path:playlist_name>')
@app.route('/quick-add/<path:playlist_name>')
def quick_add_page(playlist_name=None):
    """Serves the Quick-Add page."""
    if os.path.exists(os.path.join(HTML_DIR, 'quick-add.html')):
        return send_from_directory(HTML_DIR, 'quick-add.html')
    return "<h3>Error: yt-dlna Web UI asset (quick-add.html) not found.</h3>", 404

@app.route('/play-to', strict_slashes=False)
@app.route('/render', strict_slashes=False)
@app.route('/cast', strict_slashes=False)
@app.route('/play-to/<path:target_url>')
@app.route('/render/<path:target_url>')
@app.route('/cast/<path:target_url>')
def play_to_page(target_url=None):
    """Serves the Play-To controller interface."""
    if os.path.exists(os.path.join(HTML_DIR, 'play-to.html')):
        return send_from_directory(HTML_DIR, 'play-to.html')
    return "<h3>Error: yt-dlna Web UI asset (play-to.html) not found.</h3>", 404

@app.route('/playlist/<path:playlist_name>')
@app.route('/playlist/online/<path:playlist_name>')
@app.route('/playlist/custom/<path:playlist_name>')
def view_playlist(playlist_name):
    """Serves the playlist viewer."""
    if os.path.exists(os.path.join(HTML_DIR, 'view-playlist.html')):
        return send_from_directory(HTML_DIR, 'view-playlist.html')
    return "<h3>Error: yt-dlna Web UI asset (view-playlist.html) not found.</h3>", 404

@app.route('/playlists/custom/edit', strict_slashes=False)
def editor_page():
    """Serves the Custom Playlist editor."""
    if os.path.exists(os.path.join(HTML_DIR, 'editor.html')):
        return send_from_directory(HTML_DIR, 'editor.html')
    return "<h3>Error: yt-dlna Web UI asset (editor.html) not found.</h3>", 404

@app.route('/icon.png')
def serve_icon():
    """Serves the server logo directly from yt-dlna.conf config without duplication."""
    config = utils.load_config()
    icon_setting = config.get('dlna', 'icon', fallback='assets/yt-dlna.png').strip()
    if icon_setting:
        icon_path = icon_setting if os.path.isabs(icon_setting) else os.path.join(utils.CONFIG_DIR, icon_setting)
        if os.path.exists(icon_path):
            return send_from_directory(os.path.dirname(icon_path), os.path.basename(icon_path))
    return '', 404

@app.route('/<path:filename>')
def static_assets(filename):
    """Serves static asset files (CSS, JS, icons) from assets/html/."""
    return send_from_directory(HTML_DIR, filename)

@app.route('/api/status', methods=['GET'], strict_slashes=False)
def get_status():
    """Returns app status, version, playlist counts and sync times, stream statistics etc."""
    config = utils.load_config()
    library = utils.get_library()
    playlist_summary = []
    for name, data in library.items():
        if isinstance(data, dict):
            count = len(data.get('items', []))
            service = data.get('service', 'unknown')
            last_sync = data.get('last_sync', 0)
        else:
            count = len(data) if isinstance(data, list) else 0
            service = 'unknown'
            last_sync = 0

        playlist_summary.append({
            'title': name,
            'count': count,
            'service': service,
            'lastSync': last_sync
        })

    return jsonify({
        'version': utils.__version__,
        'status': 'online',
        'local_ip': utils.get_local_ip(),
        'dlna_port': config.getint('dlna', 'dlna_port', fallback=8200),
        'proxy_port': config.getint('proxy', 'proxy_port', fallback=5000),
        'dashboard_port': config.getint('dashboard', 'dashboard_port', fallback=5001),
        'cache_entries': utils.get_cache_count(),
        'playlists': playlist_summary,
        'stats': utils.STREAM_STATS
    })

@app.route('/api/sync', methods=['POST'])
def trigger_api_sync():
    """Triggers an immediate background sync for all or a specific playlist."""
    data = request.get_json(silent=True) or {}
    target = data.get('target')
    
    utils.log(_LOG_SRC, f"Sync triggered via Web UI for target: '{target or 'all'}'")
    threading.Thread(target=sync.run_sync, args=(target,), daemon=True).start()
    
    return jsonify({
        'status': 'success',
        'message': f"Sync started for '{target or 'all playlists'}'"
    })

@app.route('/api/reload', methods=['POST'])
def reload_configuration():
    """Forces an in-process reload of yt-dlna.conf configuration."""
    utils.load_config(force_reload=True)
    utils.log(_LOG_SRC, "In-process configuration reload triggered.")
    return jsonify({'status': 'success', 'message': 'Configuration reloaded successfully'})

@app.route('/api/restart', methods=['POST'])
def restart_daemon():
    """Triggers a service restart via process termination. Proper service setup is assumed."""
    utils.log(_LOG_SRC, "Daemon restart requested from Web UI...")
    
    def delayed_restart():
        time.sleep(1)
        os._exit(1)

    threading.Thread(target=delayed_restart, daemon=True).start()
    return jsonify({'status': 'success', 'message': 'Daemon restart initiated'})

@app.route('/api/config/parsed', methods=['GET', 'POST'])
def handle_parsed_config():
    """Reads or updates structured configuration preserving INI comments."""
    if request.method == 'GET':
        config = utils.load_config()
        parsed_out = {}
        
        # export raw section dicts for form values
        for section in config.sections():
            parsed_out[section] = dict(config[section])
            
        # attach fully-resolved service configs computed natively by utils.py
        resolved_services = {}
        
        # global defaults, read config for pseudo-service 'global'
        resolved_services['global'] = utils.get_service_config('global')
        
        for section in config.sections():
            if section.startswith('services:'):
                s_name = section.replace('services:', '')
                resolved_services[s_name] = utils.get_service_config(s_name)
                
        parsed_out['resolved_services'] = resolved_services
        return jsonify({'status': 'success', 'config': parsed_out})

    elif request.method == 'POST':
        data = request.get_json(silent=True) or {}
        parsed_data = data.get('config')
        
        if not isinstance(parsed_data, dict):
            return jsonify({'status': 'error', 'message': 'Invalid parsed configuration format'}), 400

        try:
            # remove resolved_services section only needed by frontend
            parsed_data.pop('resolved_services', None)

            utils.update_config_from_dict(parsed_data)
            utils.log(_LOG_SRC, "yt-dlna.conf updated via web UI.", level=4)
            return jsonify({'status': 'success', 'message': 'Configuration updated successfully'})
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/config/single', methods=['GET', 'POST'])
def handle_single_config_key():
    """Generic API to get or set a single configuration key."""
    if request.method == 'GET':
        section = request.args.get('section')
        key = request.args.get('key')

        if not section or not key:
            return jsonify({'status': 'error', 'message': 'Missing section or key parameter'}), 400

        config = utils.load_config()
        
        if not config.has_section(section):
            return jsonify({'status': 'error', 'message': f'Section [{section}] not found'}), 404

        value = config.get(section, key, fallback=None)
        if value is None:
            return jsonify({'status': 'error', 'message': f'Key "{key}" not found in section [{section}]'}), 404

        return jsonify({
            'status': 'success',
            'section': section,
            'key': key,
            'value': value
        })

    elif request.method == 'POST':
        data = request.get_json(silent=True) or {}
        section = data.get('section')
        key = data.get('key')
        value = data.get('value')

        if not section or not key or value is None:
            return jsonify({'status': 'error', 'message': 'Missing parameters'}), 400

        try:
            utils.update_config_single_key(section, key, str(value))
            utils.log(_LOG_SRC, f"Config update: [{section}] {key} = {value}", level=4)
            return jsonify({'status': 'success', 'message': 'Configuration updated'})
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/config/raw', methods=['GET', 'POST'])
def handle_config():
    """Reads or updates the raw yt-dlna.conf configuration file."""
    if request.method == 'GET':
        if os.path.exists(utils.CONFIG_FILE):
            try:
                with open(utils.CONFIG_FILE, 'r', encoding='utf-8') as f:
                    content = f.read()
                return jsonify({'status': 'success', 'config': content})
            except Exception as e:
                return jsonify({'status': 'error', 'message': str(e)}), 500
        return jsonify({'status': 'error', 'message': 'Configuration file not found'}), 404

    elif request.method == 'POST':
        data = request.get_json(silent=True) or {}
        new_config_text = data.get('config')
        
        if not new_config_text:
            return jsonify({'status': 'error', 'message': 'No configuration text provided'}), 400

        try:
            with open(utils.CONFIG_FILE, 'w', encoding='utf-8') as f:
                f.write(new_config_text)
            utils.log(_LOG_SRC, "yt-dlna.conf updated via web UI (raw editor).", level=4)
            return jsonify({'status': 'success', 'message': 'Configuration saved successfully'})
            utils.load_config(force_reload=True)
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/logs', strict_slashes=False)
def api_logs():
    # returns the last 1000 lines as a JSON list
    return jsonify(utils.get_buffered_logs())

@app.route('/api/logs/stream', strict_slashes=False)
def stream_logs():
    def generate():
        q = utils.subscribe_logs()
        history = utils.get_buffered_logs()
        
        seen_ids = {item['id'] for item in history}
        
        try:
            for old_entry in history:
                yield f"data: {json.dumps(old_entry)}\n\n"
            
            while True:
                log_id, json_str = q.get()
                if log_id in seen_ids:
                    continue
                yield f"data: {json_str}\n\n"
        except GeneratorExit:
            pass
        finally:
            utils.unsubscribe_logs(q)

    return Response(stream_with_context(generate()), mimetype='text/event-stream')

@app.route('/api/upload-cookies', methods=['POST'])
def upload_cookies():
    """Uploads a Netscape cookie .txt file or text content into the data/ directory (write-only)."""
    raw_service = request.form.get('service', 'youtube').strip().lower()
    service_name = re.sub(r'[^a-z0-9_-]', '', raw_service) or 'youtube'
    
    # check if the frontend provided a specific target filename from the input field
    custom_filename = request.form.get('filename', '').strip()
    if custom_filename:
        rel_filename = custom_filename
    else:
        # Default generator logic
        rel_filename = os.path.join('data', f"cookies-{service_name}.txt" if service_name != 'global' else "cookies.txt")
    
    # validate path, only allow inside data/ folder
    target_path, error = utils.get_secure_path(rel_filename, check_exists=False)
    if error:
        return jsonify({'status': 'error', 'message': f"Security Block: {error}"}), 403

    try:
        os.makedirs(os.path.dirname(target_path), exist_ok=True)

        # handle file upload
        if 'file' in request.files:
            file = request.files['file']
            if file.filename == '':
                return jsonify({'status': 'error', 'message': 'No file selected'}), 400
            file.save(target_path)
            utils.log(_LOG_SRC, f"Cookie file created via Web UI: {rel_filename}", level=4)
            return jsonify({
                'status': 'success', 
                'message': f"Saved to {rel_filename}",
                'path': rel_filename
            })

        # handle pasted raw cookie string
        cookie_text = request.form.get('cookie_text')
        if cookie_text:
            with open(target_path, 'w', encoding='utf-8') as f:
                f.write(cookie_text)
            utils.log(_LOG_SRC, f"Cookie text saved via Web UI: {rel_filename}", level=4)
            return jsonify({
                'status': 'success', 
                'message': f"Saved to {rel_filename}",
                'path': rel_filename
            })

    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

    return jsonify({'status': 'error', 'message': 'No data provided'}), 400

@app.route('/api/playlist/view-data')
def get_playlist_view_data():
    """Returns structured playlist items and metadata for the view playlist page."""
    name = request.args.get('name', '').strip()
    pl_type = request.args.get('type', '').strip().lower()
    if not name:
        return jsonify({'status': 'error', 'message': 'Missing playlist name'}), 400

    config = utils.load_config()
    base_url = utils.get_stream_base_url()
    redirect_pattern = config.get('proxy', 'proxy_url_pattern_redirect', fallback='/redirect/{service}/{video_id}')

    # auto-detect type if not provided
    if not pl_type:
        custom_reg = utils.get_custom_playlists_registry()
        pl_type = 'custom' if any(r['name'] == name for r in custom_reg) else 'online'

    if pl_type == 'custom':
        registry = utils.get_custom_playlists_registry()
        reg_entry = next((r for r in registry if r['name'] == name), None)
        if not reg_entry:
            return jsonify({'status': 'error', 'message': 'Custom playlist not found'}), 404

        file_path = os.path.join(utils.CONFIG_DIR, reg_entry['file'])
        if not os.path.exists(file_path):
            return jsonify({'status': 'error', 'message': 'Custom playlist file missing'}), 404

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read().strip()
                cpl_data = json.loads(content) if content else {}
        except Exception:
            cpl_data = {}

        root_mode = (cpl_data.get('mode') if isinstance(cpl_data, dict) else None) or 'default'
        children = cpl_data if isinstance(cpl_data, list) else cpl_data.get('children', [])

        return jsonify({
            'status': 'success',
            'name': name,
            'type': 'custom',
            'root_mode': root_mode,
            'children': children,
            'base_url': base_url
        })

    else:
        library = utils.get_library()
        raw_data = library.get(name, {})
        if isinstance(raw_data, dict):
            raw_items = raw_data.get('items', [])
            pl_service = raw_data.get('service', 'auto')
        else:
            raw_items = raw_data if isinstance(raw_data, list) else []
            pl_service = 'auto'

        formatted_items = []
        for idx, item in enumerate(raw_items, 1):
            item_id = str(item.get('id', ''))
            service = str(pl_service or item.get('service', 'auto'))
            v_id_encoded = urllib.parse.quote(item_id, safe='')

            disp_title = utils.format_item_title(item, enum_idx=idx)
            proxy_url = item.get('proxy_url', '#')
            web_url = item.get('web_url', '#')
            if web_url == '#' and item_id.startswith('http'):
                web_url = item_id

            download_link = f"{base_url}{redirect_pattern.replace('{service}', service).replace('{video_id}', v_id_encoded)}"

            formatted_items.append({
                'id': item_id,
                'title': disp_title,
                'service': service,
                'proxy_url': proxy_url,
                'download_url': download_link,
                'web_url': web_url
            })

        return jsonify({
            'status': 'success',
            'name': name,
            'type': 'online',
            'service': pl_service,
            'items': formatted_items,
            'base_url': base_url
        })

def _handle_rename_logic(old_sec, new_sec):
    if not old_sec or not new_sec: return jsonify({'status': 'error', 'message': 'Missing names'}), 400
    try:
        utils.rename_config_section(old_sec, new_sec)
        return jsonify({'status': 'success'})
    except Exception as e: return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/playlists/online/rename', methods=['POST'])
def rename_online():
    d = request.get_json()
    old_name, new_name = d.get('old'), d.get('new')
    if not old_name or not new_name:
        return jsonify({'status': 'error', 'message': 'Names missing'}), 400
    
    try:
        # rename the section in yt-dlna.conf
        utils.rename_config_section(f"playlists:{old_name}", f"playlists:{new_name}")
        # rename the data in playlists.json
        sync.rename_playlist_data(old_name, new_name)
        
        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/playlists/custom/rename', methods=['POST'])
def rename_custom():
    d = request.get_json()
    return _handle_rename_logic(f"custom_playlists:{d.get('old')}", f"custom_playlists:{d.get('new')}")

@app.route('/api/services/rename', methods=['POST'])
def rename_service():
    d = request.get_json()
    return _handle_rename_logic(f"services:{d.get('old')}", f"services:{d.get('new')}")

@app.route('/api/config/rename', methods=['POST'])
def rename_generic():
    d = request.get_json()
    return _handle_rename_logic(d.get('old'), d.get('new'))

@app.route('/api/playlists/custom', methods=['GET'], strict_slashes=False)
def get_custom_playlists():
    """Returns the list of custom playlists from the config registry."""
    try:
        # Calls the function we added to utils.py
        registry = utils.get_custom_playlists_registry()
        return jsonify({'status': 'success', 'registry': registry})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/playlists/custom/add', methods=['POST'])
def add_custom_playlist():
    """Registers a new custom playlist and creates the JSON file with standard root structure."""
    d = request.get_json() or {}
    name = d.get('name', '').strip()
    file_rel_path = d.get('file', '').strip()
    mode = d.get('mode', 'bounce').strip() or 'bounce'

    if not name or not file_rel_path:
        return jsonify({'status': 'error', 'message': 'Name and File Path are required'}), 400

    # validate path and check for existing file (overwrite lock)
    full_path, error = utils.get_secure_path(file_rel_path, check_exists=True)
    if error:
        return jsonify({'status': 'error', 'message': f"Creation Blocked: {error}"}), 403

    try:
        # create physical file
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        # initialize with standard root folder schema
        utils.replace_save_json(full_path, {"children": []}, indent=4)
        
        # add to yt-dlna.conf registry
        sec = f"custom_playlists:{name}"
        utils.update_config_single_key(sec, 'playlist_file', file_rel_path)
        utils.update_config_single_key(sec, 'enabled', 'yes')
        
        utils.log(_LOG_SRC, f"Custom Playlist file '{name}' created at {file_rel_path}.")
        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/playlists/online/delete', methods=['POST'])
def delete_online_playlist():
    name = request.get_json().get('name')
    if not name: return jsonify({'status': 'error', 'message': 'Name missing'}), 400
    
    utils.delete_config_section(f"playlists:{name}")
    utils.log(_LOG_SRC, f"Online playlist '{name}' deleted.")
    return jsonify({'status': 'success'})

@app.route('/api/playlists/<any(online, custom):pl_type>/reorder', methods=['POST'])
def reorder_playlists(pl_type):
    """Reorders playlist sections."""
    data = request.get_json() or {}
    new_order = data.get('order', [])
    
    if not new_order:
        return jsonify({'status': 'error', 'message': 'No order provided'}), 400
        
    try:
        prefix = 'playlists' if pl_type == 'online' else 'custom_playlists'
        
        utils.reorder_config_sections(prefix, new_order)
        utils.log(_LOG_SRC, f"{pl_type.capitalize()} playlists reordered.")
        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/playlists/custom/delete', methods=['POST'])
def delete_custom_playlist():
    """Removes custom playlist from registry and deletes its file (if inside data/)."""
    d = request.get_json() or {}
    name = d.get('name')
    if not name:
        return jsonify({'status': 'error', 'message': 'Name missing'}), 400
    
    config = utils.load_config()
    section = f"custom_playlists:{name}"
    file_rel_path = config.get(section, 'playlist_file', fallback=None)
    if file_rel_path:
        full_path, error = utils.get_secure_path(file_rel_path, check_exists=False)

        if error:
            # block the deletion attempt if it points outside data/ directory
            utils.log(_LOG_SRC, f"Deletion of '{file_rel_path}' blocked: {error}", level=2, type='W')
            return jsonify({'status': 'error', 'message': f"Access Denied: {error}"}), 403

        if os.path.exists(full_path):
            os.remove(full_path)
            utils.log(_LOG_SRC, f"Custom Playlist file '{file_rel_path}' deleted.")

    utils.delete_config_section(section)
    return jsonify({'status': 'success'})

@app.route('/api/playlists/custom/data', methods=['GET', 'POST'])
def handle_custom_playlist_data():
    """Reads or overwrites the actual JSON content of a custom playlist file."""
    name = request.args.get('name')
    if not name:
        return jsonify({'status': 'error', 'message': 'Playlist name required'}), 400

    # look up file path
    config = utils.load_config()
    section = f"custom_playlists:{name}"
    file_rel_path = config.get(section, 'playlist_file', fallback=None)
    
    if not file_rel_path:
        return jsonify({'status': 'error', 'message': 'Playlist not found in registry'}), 404

    full_path, error = utils.get_secure_path(file_rel_path)
    if error:
        return jsonify({'status': 'error', 'message': error}), 403

    if request.method == 'GET':
        if not os.path.exists(full_path):
            return jsonify([])
        with open(full_path, 'r', encoding='utf-8') as f:
            return f.read()

    elif request.method == 'POST':
        new_data = request.get_json()
        try:
            utils.replace_save_json(full_path, new_data, indent=4)
            return jsonify({'status': 'success'})
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/playlists/custom/item/add', methods=['POST'])
def add_custom_playlist_item():
    """Quickly appends an item to a custom playlist and triggers instant background pre-resolve."""
    d = request.get_json() or {}
    playlist_name = d.get('playlist', '').strip()
    url = d.get('url', '').strip()
    name = d.get('name', '').strip()
    service = d.get('service', 'auto').strip() or 'auto'
    mode = d.get('mode', '').strip()  # empty string means 'inherit'

    if not playlist_name or not url:
        return jsonify({'status': 'error', 'message': 'Playlist and URL are required'}), 400

    config = utils.load_config()
    section = f"custom_playlists:{playlist_name}"
    file_rel_path = config.get(section, 'playlist_file', fallback=None)
    if not file_rel_path:
        return jsonify({'status': 'error', 'message': f"Playlist '{playlist_name}' not found"}), 404

    full_path, error = utils.get_secure_path(file_rel_path)
    if error:
        return jsonify({'status': 'error', 'message': error}), 403

    try:
        data = {}
        if os.path.exists(full_path):
            with open(full_path, 'r', encoding='utf-8') as f:
                content = f.read().strip()
                data = json.loads(content) if content else {}

        if isinstance(data, list):
            data = {"name": playlist_name, "type": "folder", "children": data}
        elif not isinstance(data, dict):
            data = {"name": playlist_name, "type": "folder", "children": []}

        if "children" not in data:
            data["children"] = []

        # if playlist is marked as Watch Later (precache=yes) and root mode is unset or 'bounce',
        # promote root mode to 'default' so all inheriting items resolve properly
        is_precache = config.getboolean(section, 'precache', fallback=False)
        if is_precache and data.get('mode') in (None, '', 'bounce'):
            data['mode'] = 'default'

        item_name = name if name else url
        new_item = {
            'name': item_name,
            'url': url,
            'service': service
        }

        if mode:
            new_item['mode'] = mode

        data["children"].append(new_item)
        utils.replace_save_json(full_path, data, indent=4)
        utils.log(_LOG_SRC, f"Added item '{item_name}' to Custom Playlist '{playlist_name}'.", level=4)

        effective_mode = mode if mode else data.get('mode', 'bounce')
        resolving_modes = {'default', 'redirect', 'proxy', 'remux', 'remux_mp4', 'remux_ts'}

        if effective_mode in resolving_modes:
            def background_resolve_and_title():
                try:
                    utils.log(_LOG_SRC, f"Instant background pre-resolve for '{utils.short(url)}'...", "cache")
                    entry, _ = proxy.resolve_cdn_url(url, service_name=service)

                    if not name and entry:
                        srv_cfg = utils.get_service_config(service)
                        norm_url = proxy._normalize_video_url(url, srv_cfg['extractor'])
                        info = utils.extract_youtube_info(norm_url, extra_opts={'extract_flat': True})
                        fetched_title = info.get('title') if isinstance(info, dict) else None
                        if fetched_title:
                            with utils._file_lock:
                                with open(full_path, 'r', encoding='utf-8') as rf:
                                    cur_data = json.load(rf)
                                items_list = cur_data.get('children', []) if isinstance(cur_data, dict) else cur_data
                                for it in items_list:
                                    if it.get('url') == url and it.get('name') == url:
                                        it['name'] = fetched_title
                                        break
                                utils.replace_save_json(full_path, cur_data, indent=4)
                            utils.log(_LOG_SRC, f"Auto-updated title for '{utils.short(url)}': '{fetched_title}'", level=4)
                except Exception as ex:
                    utils.log(_LOG_SRC, f"Background resolve error for '{utils.short(url)}': {ex}", "cache", level=1, type='E')

            threading.Thread(target=background_resolve_and_title, daemon=True).start()

        return jsonify({'status': 'success', 'message': 'Item added successfully'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/playlists/custom/item/delete', methods=['POST'])
def delete_custom_playlist_item():
    """Deletes an item by index from a custom playlist."""
    d = request.get_json() or {}
    playlist_name = d.get('playlist', '').strip()
    index = d.get('index')

    if not playlist_name or index is None:
        return jsonify({'status': 'error', 'message': 'Playlist and Index are required'}), 400

    config = utils.load_config()
    section = f"custom_playlists:{playlist_name}"
    file_rel_path = config.get(section, 'playlist_file', fallback=None)
    if not file_rel_path:
        return jsonify({'status': 'error', 'message': 'Playlist not found'}), 404

    full_path, error = utils.get_secure_path(file_rel_path)
    if error:
        return jsonify({'status': 'error', 'message': error}), 403

    try:
        with open(full_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        items = data.get('children', []) if isinstance(data, dict) else data
        idx = int(index)
        if 0 <= idx < len(items):
            removed = items.pop(idx)
            utils.replace_save_json(full_path, data, indent=4)
            utils.log(_LOG_SRC, f"Removed item '{removed.get('name')}' from '{playlist_name}'.", level=4)
            return jsonify({'status': 'success'})
        return jsonify({'status': 'error', 'message': 'Index out of range'}), 400
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/services/delete', methods=['POST'])
def delete_service():
    name = request.get_json().get('name')
    if not name: return jsonify({'status': 'error', 'message': 'Name missing'}), 400
    
    utils.delete_config_section(f"services:{name}")
    utils.log(_LOG_SRC, f"Service profile '{name}' deleted.")
    return jsonify({'status': 'success'})

@app.route('/api/cache/purge', methods=['POST'])
def purge_cache_api():
    """Purges the resolved CDN URL cache."""
    success = utils.purge_cdn_cache()
    if success:
        return jsonify({'status': 'success', 'message': 'CDN URL cache purged successfully'})
    return jsonify({'status': 'error', 'message': 'Failed to purge CDN URL cache'}), 500

@app.route('/api/library/purge', methods=['POST'])
def purge_library_api():
    """Resets the indexed playlist library."""
    success = utils.purge_playlist_library()
    if success:
        return jsonify({'status': 'success', 'message': 'Playlist library reset successfully'})
    return jsonify({'status': 'error', 'message': 'Failed to reset playlist library'}), 500

@app.route('/api/renderers', strict_slashes=False)
def get_renderers_api():
    """Returns discovered UPnP MediaRenderers and currently configured default."""
    import dlna_server
    config = utils.load_config()
    cfg_default = config.get('dlna', 'default_renderer', fallback='').strip()
    renderers = dlna_server.load_renderers()
    renderer_list = []
    for udn_key, r_info in renderers.items():
        item = dict(r_info)
        item['udn'] = udn_key
        renderer_list.append(item)

    return jsonify({
        'status': 'success',
        'default_renderer': cfg_default,
        'renderers': renderer_list
    })

@app.route('/api/renderers/scan', methods=['POST'])
def trigger_renderer_scan_api():
    """Triggers an immediate active SSDP M-SEARCH scan for MediaRenderers."""
    import dlna_server
    threading.Thread(target=dlna_server.scan_for_renderers, daemon=True).start()
    return jsonify({'status': 'success', 'message': 'Renderer scan initiated'})

@app.route('/api/renderers/default', methods=['POST'])
def set_default_renderer_api():
    """Updates the default_renderer setting in yt-dlna.conf."""
    data = request.get_json(silent=True) or {}
    target_renderer = data.get('renderer', '').strip()
    
    try:
        utils.update_config_single_key('dlna', 'default_renderer', target_renderer)
        utils.log(_LOG_SRC, f"Default renderer updated in config: '{target_renderer}'", level=4)
        return jsonify({'status': 'success', 'default_renderer': target_renderer})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/play-to', methods=['POST'])
def trigger_play_to_api():
    """Initiates playback of a video stream on a target UPnP MediaRenderer."""
    import dlna_server
    data = request.get_json(silent=True) or {}
    video_url = data.get('url', '').strip()
    renderer_key = data.get('renderer', 'default-renderer').strip() or 'default-renderer'
    service = data.get('service', 'auto').strip() or 'auto'
    mode = data.get('mode', 'default').strip() or 'default'

    if not video_url:
        return jsonify({'status': 'error', 'message': 'Video URL or ID is required'}), 400

    try:
        utils.log(_LOG_SRC, f"Play-To requested for '{utils.short(video_url)}' on renderer '{renderer_key}' (mode: {mode}, service: {service})")
        res = dlna_server.play_to_renderer(renderer_key, video_url, service=service, mode=mode)
        return jsonify({
            'status': 'success',
            'message': f"Playing on '{res.get('renderer')}'",
            'data': res
        })
    except Exception as e:
        utils.log(_LOG_SRC, f"Play-To failed: {e}", level=1, type='E')
        return jsonify({'status': 'error', 'message': str(e)}), 500

def start_web_server():
    """Launches the Web UI Flask server in a background thread on configured port."""
    config = utils.load_config()
    bind_ip = config.get('dashboard', 'dashboard_ip', fallback='0.0.0.0')
    port = config.getint('dashboard', 'dashboard_port', fallback=5001)
    app.run(host=bind_ip, port=port, threaded=True)

if __name__ == '__main__':
    start_web_server()
