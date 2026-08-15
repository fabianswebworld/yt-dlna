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
from flask import Flask, request, jsonify, send_from_directory
import flask.cli
import logging
import utils
import sync

# silence Flask development server warning banner
flask.cli.show_server_banner = lambda *args: None

# disable Flask/Werkzeug access logging (only show errors)
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

config = utils.load_config()
app = Flask(__name__)

app.json.sort_keys = False 

# directory for web UI static dashboard assets
HTML_DIR = os.path.join(utils.CONFIG_DIR, 'assets', 'html')

@app.route('/')
@app.route('/playlists')
@app.route('/playlists/online')
@app.route('/playlists/custom')
@app.route('/services')
@app.route('/settings')
@app.route('/raw-config')
def index_routes():
    """Serves the main dashboard for all top-level UI paths."""
    if os.path.exists(os.path.join(HTML_DIR, 'index.html')):
        return send_from_directory(HTML_DIR, 'index.html')
    return "<h3>Error: yt-dlna Web UI assets not found.</h3>", 404

@app.route('/icon.png')
def serve_icon():
    """Serves the server logo directly from yt-dlna.conf config without duplication."""
    icon_setting = config.get('dlna', 'icon', fallback='assets/yt-dlna.png').strip()
    if icon_setting:
        icon_path = icon_setting if os.path.isabs(icon_setting) else os.path.join(utils.CONFIG_DIR, icon_setting)
        if os.path.exists(icon_path):
            return send_from_directory(os.path.dirname(icon_path), os.path.basename(icon_path))
    return '', 404

def _render_custom_hierarchy(nodes, parent_mode, proxy_base, cfg, depth=0):
    """Recursively generates nested div structure for custom playlists."""
    html = ""
    if not nodes:
        return html

    for node in nodes:
        name = utils.xml_escape(node.get('name', 'Untitled'))

        if node.get('type') == 'folder':
            folder_mode = node.get('mode') or parent_mode
            
            html += f'<div class="playlist-folder-card">📁 {name}</div>'
            
            # recurse into children
            html += '<div class="hierarchy-level">'
            html += _render_custom_hierarchy(node.get('children', []), folder_mode, proxy_base, cfg)
            html += '</div>'
        
        else:
            target_url = node.get('url', '')
            safe_target = urllib.parse.quote(target_url, safe='')

            mode = node.get('mode') or parent_mode
            reflect_url = f"{proxy_base}/reflect/{safe_target}"
            
            if mode == 'direct':
                proxy_url = target_url
            else:
                proxy_url = f"{proxy_base}/{mode}/{safe_target}"

            html += f"""
            <div class="playlist-view-item">
                <div class="item-main">
                    <a href="{proxy_url}" target="_blank" title="Play (with selected proxy mode)">{utils.xml_escape(name)}</a>
                </div>
                <div class="item-actions">
                    <a href="{reflect_url}" class="action-link" target="_blank" title="Play (reflected via proxy)">Play</a>
                    <span class="sep">|</span>
                    <a href="{target_url}" class="action-link" target="_blank" title="Source URL, right-click to 'Save as...'">Download</a>
                    <span class="sep">|</span>
                    <a href="{target_url}" class="action-link" target="_blank" title="View original web page">Web Page</a>
                </div>
            </div>\n"""
            
    return html

@app.route('/playlist/<path:playlist_name>')
@app.route('/playlist/online/<path:playlist_name>')
@app.route('/playlist/custom/<path:playlist_name>')
def view_playlist(playlist_name):
    """Renders a simple HTML page with clickable stream hyperlinks for a playlist."""
    playlist_name = urllib.parse.unquote(playlist_name)
    is_custom = request.path.startswith('/playlist/custom/')
    
    cfg = utils.load_config()
    base_url = utils.get_stream_base_url()
    
    if is_custom:
        # --- custom playlist hierarchy ---
        pl_type = 'custom'
        registry = utils.get_custom_playlists_registry()
        reg_entry = next((r for r in registry if r['name'] == playlist_name), None)
        if not reg_entry:
            return "Custom playlist not found", 404
            
        file_path = os.path.join(utils.CONFIG_DIR, reg_entry['file'])
        if not os.path.exists(file_path):
            return "Custom playlist file missing", 404

        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            root_mode = data.get('mode') or 'bounce'
            links_html = _render_custom_hierarchy(data.get('children', []), root_mode, base_url, cfg)
            back_url = "/playlists/custom"

    else:
        # --- online playlist flat list ---
        pl_type = 'online'
        library = {}
        if os.path.exists(utils.JSON_PATH):
            with open(utils.JSON_PATH, 'r', encoding='utf-8') as f:
                library = json.load(f)
        
        items = library.get(playlist_name, [])
        redirect_pattern = cfg.get('proxy', 'proxy_url_pattern_redirect', fallback='/redirect/{service}/{video_id}')
        links_html = ""
        back_url = "/playlists/online"

        for idx, item in enumerate(items, 1):
            disp_title = utils.format_item_title(item, enum_idx=idx)
            item_id = str(item.get('id', ''))
            proxy_url = item.get('proxy_url', '#')
            web_url = item.get('web_url', '#')
            
            # fallback for missing web_urls
            if web_url == '#' and item_id.startswith('http'):
                web_url = item_id

            v_id_encoded = urllib.parse.quote(item_id, safe='')
            service = str(item.get('service', 'youtube'))
            download_link = f"{base_url}{redirect_pattern.replace('{service}', service).replace('{video_id}', v_id_encoded)}"

            links_html += f"""
            <div class="playlist-view-item">
                <div class="item-main">
                    <a href="{proxy_url}" target="_blank" title="Play (via proxy)">{utils.xml_escape(disp_title)}</a>
                </div>
                <div class="item-actions">
                    <a href="{proxy_url}" class="action-link" target="_blank" title="Play (via proxy)">Play</a>
                    <span class="sep">|</span>
                    <a href="{download_link}" class="action-link" target="_blank" title="Proxy redirect to CDN URL, right-click to 'Save as...'">Download</a>
                    <span class="sep">|</span>
                    <a href="{web_url}" class="action-link" target="_blank" title="View original video web page">Web Page</a>
                </div>
            </div>\n"""

    return f"""<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>yt-dlna - {utils.xml_escape(playlist_name)} - Playlist View</title>
    <link rel="stylesheet" href="/style.css">
    <link rel="icon" href="/icon.png" type="image/png">
</head>
<body class="playlist-page">
    <div class="container">
        <header class="header">
            <div class="brand">
                <img src="/icon.png" alt="yt-dlna Logo" class="logo">
                <div>
                    <h1>{utils.xml_escape(playlist_name)}</h1>
                    <p class="subtitle"><strong>yt-dlna</strong> &#8226; {'Custom' if pl_type == 'custom' else 'Online'} Playlist View</p>
                </div>
            </div>
            <div class="header-right">
                <button class="btn theme-toggle" id="btn-theme-toggle">☀️ Light Mode</button>
                <div class="actions">
                    <a href="{back_url}" class="btn secondary">Back to Dashboard</a>
                </div>
            </div>
        </header>

        <section class="card">
            <ul class="playlist-view-list">
                {links_html or '<li class="placeholder">This playlist is empty.</li>'}
            </ul>
        </section>
        
    </div>
    <script src="/script.js"></script>
</body>
</html>"""

@app.route('/playlists/custom/edit')
def editor_page():
    return send_from_directory(HTML_DIR, 'editor.html')

@app.route('/<path:filename>')
def static_assets(filename):
    """Serves static asset files (CSS, JS, icons) from assets/html/."""
    return send_from_directory(HTML_DIR, filename)

@app.route('/api/status', methods=['GET'])
def get_status():
    """Returns application status, version, playlist counts, and stream statistics."""
    library = {}
    if os.path.exists(utils.JSON_PATH):
        try:
            with open(utils.JSON_PATH, 'r', encoding='utf-8') as f:
                library = json.load(f)
        except Exception:
            pass

    cache_count = 0
    if os.path.exists(utils.CACHE_PATH):
        try:
            with open(utils.CACHE_PATH, 'r', encoding='utf-8') as f:
                cache_data = json.load(f)
                cache_count = len(cache_data)
        except Exception:
            pass

    playlist_summary = []
    for name, items in library.items():
        playlist_summary.append({
            'title': name,
            'count': len(items) if isinstance(items, list) else 0
        })

    return jsonify({
        'version': utils.__version__,
        'status': 'online',
        'local_ip': utils.get_local_ip(),
        'dlna_port': config.getint('dlna', 'dlna_port', fallback=8200),
        'proxy_port': config.getint('proxy', 'proxy_port', fallback=5000),
        'dashboard_port': config.getint('dashboard', 'dashboard_port', fallback=5001),
        'cache_entries': cache_count,
        'playlists': playlist_summary,
        'stats': utils.STREAM_STATS
    })

@app.route('/api/sync', methods=['POST'])
def trigger_api_sync():
    """Triggers an immediate background sync for all or a specific playlist."""
    data = request.get_json(silent=True) or {}
    target = data.get('target')
    
    print(f"[Dashboard] Sync triggered via Web UI for target: '{target or 'all'}'")
    threading.Thread(target=sync.run_sync, args=(target,), daemon=True).start()
    
    return jsonify({
        'status': 'success',
        'message': f"Sync started for '{target or 'all playlists'}'"
    })

@app.route('/api/reload', methods=['POST'])
def reload_configuration():
    """Forces an in-process reload of yt-dlna.conf configuration."""
    global config
    config = utils.load_config()
    print("[Dashboard] In-process configuration reload triggered.")
    return jsonify({'status': 'success', 'message': 'Configuration reloaded successfully'})

@app.route('/api/restart', methods=['POST'])
def restart_daemon():
    """Triggers a service restart via process termination. Proper service setup is assumed."""
    print("[Dashboard] Daemon restart requested from Web UI...")
    
    def delayed_restart():
        time.sleep(1)
        os._exit(1)

    threading.Thread(target=delayed_restart, daemon=True).start()
    return jsonify({'status': 'success', 'message': 'Daemon restart initiated'})

@app.route('/api/config/parsed', methods=['GET', 'POST'])
def handle_parsed_config():
    """Reads or updates structured configuration preserving INI comments."""
    if request.method == 'GET':
        cfg = utils.load_config()
        parsed_out = {}
        
        # export raw section dicts for form values
        for section in cfg.sections():
            parsed_out[section] = dict(cfg[section])
            
        # attach fully-resolved service configs computed natively by utils.py
        resolved_services = {}
        
        # global defaults, read config for pseudo-service 'global'
        resolved_services['global'] = utils.get_service_config('global')
        
        for section in cfg.sections():
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
            print("[Dashboard] yt-dlna.conf updated via web UI.")
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

        cfg = utils.load_config()
        
        if not cfg.has_section(section):
            return jsonify({'status': 'error', 'message': f'Section [{section}] not found'}), 404

        value = cfg.get(section, key, fallback=None)
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
            print(f"[Dashboard] Config update: [{section}] {key} = {value}")
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
            print("[Dashboard] yt-dlna.conf updated via web UI (raw editor).")
            return jsonify({'status': 'success', 'message': 'Configuration saved successfully'})
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)}), 500

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
            print(f"[Dashboard] Cookie file created via Web UI: {rel_filename}")
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
            print(f"[Dashboard] Cookie text saved via Web UI: {rel_filename}")
            return jsonify({
                'status': 'success', 
                'message': f"Saved to {rel_filename}",
                'path': rel_filename
            })

    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

    return jsonify({'status': 'error', 'message': 'No data provided'}), 400

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

@app.route('/api/playlists/custom', methods=['GET'])
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
    """Registers a new custom playlist and creates the JSON file."""
    d = request.get_json() or {}
    name = d.get('name', '').strip()
    file_rel_path = d.get('file', '').strip()

    if not name or not file_rel_path:
        return jsonify({'status': 'error', 'message': 'Name and File Path are required'}), 400

    # validate path and check for existing file (overwrite lock)
    full_path, error = utils.get_secure_path(file_rel_path, check_exists=True)
    if error:
        return jsonify({'status': 'error', 'message': f"Creation Blocked: {error}"}), 403

    try:
        # create physical file
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, 'w', encoding='utf-8') as f:
            json.dump([], f)
        
        # add to yt-dlna.conf registry
        sec = f"custom_playlists:{name}"
        utils.update_config_single_key(sec, 'playlist_file', file_rel_path)
        utils.update_config_single_key(sec, 'enabled', 'yes')
        
        print(f"[Dashboard] Custom Playlist file '{name}' created at {file_rel_path}.")
        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/playlists/online/delete', methods=['POST'])
def delete_online_playlist():
    name = request.get_json().get('name')
    if not name: return jsonify({'status': 'error', 'message': 'Name missing'}), 400
    
    utils.delete_config_section(f"playlists:{name}")
    print(f"[Dashboard] Online playlist '{name}' deleted.")
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
        print(f"[Dashboard] {pl_type.capitalize()} playlists reordered.")
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
    
    cfg = utils.load_config()
    section = f"custom_playlists:{name}"
    file_rel_path = cfg.get(section, 'playlist_file', fallback=None)
    if file_rel_path:
        full_path, error = utils.get_secure_path(file_rel_path, check_exists=False)

        if error:
            # block the deletion attempt if it points outside data/ directory
            print(f"[Dashboard] Deletion of '{file_rel_path}' blocked: {error}")
            return jsonify({'status': 'error', 'message': f"Access Denied: {error}"}), 403

        if os.path.exists(full_path):
            os.remove(full_path)
            print(f"[Dashboard] Custom Playlist file '{file_rel_path}' deleted.")

    utils.delete_config_section(section)
    return jsonify({'status': 'success'})

@app.route('/api/playlists/custom/data', methods=['GET', 'POST'])
def handle_custom_playlist_data():
    """Reads or overwrites the actual JSON content of a custom playlist file."""
    name = request.args.get('name')
    if not name:
        return jsonify({'status': 'error', 'message': 'Playlist name required'}), 400

    # look up file path
    cfg = utils.load_config()
    section = f"custom_playlists:{name}"
    file_rel_path = cfg.get(section, 'playlist_file', fallback=None)
    
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
            with open(full_path, 'w', encoding='utf-8') as f:
                json.dump(new_data, f, indent=4, ensure_ascii=False)
            return jsonify({'status': 'success'})
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/services/delete', methods=['POST'])
def delete_service():
    name = request.get_json().get('name')
    if not name: return jsonify({'status': 'error', 'message': 'Name missing'}), 400
    
    utils.delete_config_section(f"services:{name}")
    print(f"[Dashboard] Service profile '{name}' deleted.")
    return jsonify({'status': 'success'})

def start_web_server():
    """Launches the Web UI Flask server in a background thread on configured port."""
    bind_ip = config.get('dashboard', 'dashboard_ip', fallback='0.0.0.0')
    port = config.getint('dashboard', 'dashboard_port', fallback=5001)
    app.run(host=bind_ip, port=port, threaded=True)

if __name__ == '__main__':
    start_web_server()
