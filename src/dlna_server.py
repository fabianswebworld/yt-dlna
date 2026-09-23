# ==============================================================================
# yt-dlna: src/dlna_server.py
# Lightweight media gateway, proxying streaming playlists to DLNA/UPnP clients
#
# Copyright (c) 2026 Fabian Schneider (@fabianswebworld) and contributors
# Licensed under the MIT License - see LICENSE file for details.
# SPDX-License-Identifier: MIT
# ==============================================================================

import socket
import threading
import time
import json
import os
import urllib.parse
import urllib.request
import re
import uuid
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler
import utils
import sync

# ==============================================================================
# --- configuration & constants ---
# ==============================================================================
_LOG_SRC = __name__

config = utils.load_config()
DLNA_IP = config.get('dlna', 'dlna_ip', fallback='0.0.0.0')
DLNA_PORT = config.getint('dlna', 'dlna_port', fallback=8200)
FRIENDLY_NAME = config.get('dlna', 'friendly_name', fallback='yt-dlna Media Server')
DIAL_FRIENDLY_NAME = config.get('dlna', 'dial_friendly_name', fallback='yt-dlna Cast/UPnP Bridge')
ICON_PATH = config.get('dlna', 'icon', fallback='')
SERVER_STRING = f"Python/3.x UPnP/1.0 DLNADOC/1.50 yt-dlna/{utils.__version__}"
DIAL_ST = "urn:dial-multiscreen-org:service:dial:1"
RENDERERS_FILE = os.path.join(utils.DATA_DIR, 'dlna_renderers.json')
_renderers_lock = threading.Lock()
_dial_app_state = "stopped"
_lounge_session_lock = threading.Lock()
_active_lounge_session = None

# Multicast SSDP details for UPnP device discovery on the local network
SSDP_PORT = 1900
SSDP_ADDR = "239.255.255.250"

# Unique Device Name (UDN) UUID; auto-generated if missing in config
UUID = config.get('dlna', 'uuid', fallback='').strip()
if not UUID:
    UUID = f"uuid:{uuid.uuid4()}"
    try:
        utils.update_config_single_key('dlna', 'uuid', UUID)
        utils.log(_LOG_SRC, f"Generated and saved new DLNA server UDN: {UUID}", level=4)
    except Exception as e:
        utils.log(_LOG_SRC, f"Failed to persist DLNA server UDN to config: {e}", level=2, type='W')

# Screen ID for DIAL / Cast pairing; auto-generated if missing
DIAL_SCREEN_ID = config.get('dlna', 'dial_screen_id', fallback='').strip()
if not DIAL_SCREEN_ID:
    DIAL_SCREEN_ID = str(uuid.uuid4())
    try:
        utils.update_config_single_key('dlna', 'dial_screen_id', DIAL_SCREEN_ID)
        utils.log(_LOG_SRC, f"Generated and saved new DIAL Screen ID: {DIAL_SCREEN_ID}", level=4)
    except Exception as e:
        utils.log(_LOG_SRC, f"Failed to persist DIAL Screen ID to config: {e}", level=2, type='W')

# ==============================================================================
# --- UPnP XML definitions (device description & SCPD schemas) ---
# ==============================================================================

# primary device description (desc.xml), generated dynamically
def get_device_desc(is_dial=False):
    """Generates UPnP device description XML (supports DLNA MediaServer and DIAL formats)."""
    dash_enabled = config.getboolean('dashboard', 'enable_dashboard', fallback=True)
    dash_ip = config.get('dashboard', 'dashboard_ip', fallback='0.0.0.0')
    dash_port = config.getint('dashboard', 'dashboard_port', fallback=5001)
    dash_ip = dash_ip if dash_ip != '0.0.0.0' else utils.get_local_ip()
    presentation_url = f"http://{dash_ip}:{dash_port}/" if dash_enabled else ""

    if is_dial:
        # DIAL specific device description (dd.xml)
        return f"""<?xml version="1.0"?>
<root xmlns="urn:schemas-upnp-org:device-1-0" xmlns:dlna="urn:schemas-dlna-org:device-1-0">
  <specVersion><major>1</major><minor>0</minor></specVersion>
  <device>
    <deviceType>urn:schemas-upnp-org:device:tvdevice:1</deviceType>
    <friendlyName>{DIAL_FRIENDLY_NAME}</friendlyName>
    <manufacturer>fabianswebworld</manufacturer>
    <manufacturerURL>https://github.com/fabianswebworld</manufacturerURL>
    <modelDescription>yt-dlna DIAL Receiver</modelDescription>
    <modelName>yt-dlna</modelName>
    <modelNumber>{utils.__version__}</modelNumber>
    <modelURL>https://github.com/fabianswebworld/yt-dlna</modelURL>
    <UDN>{UUID}</UDN>
    <serviceList>
      <service>
        <serviceType>urn:dial-multiscreen-org:service:dial:1</serviceType>
        <serviceId>urn:dial-multiscreen-org:serviceId:dial</serviceId>
        <SCPDURL>/dial.xml</SCPDURL>
        <controlURL>/dial_ctl</controlURL>
        <eventSubURL>/dial_evt</eventSubURL>
      </service>
    </serviceList>
  </device>
</root>"""

    # DLNA MediaServer device description (desc.xml)
    return f"""<?xml version="1.0"?>
<root xmlns="urn:schemas-upnp-org:device-1-0" xmlns:dlna="urn:schemas-dlna-org:device-1-0">
  <specVersion><major>1</major><minor>0</minor></specVersion>
  <device>
    <deviceType>urn:schemas-upnp-org:device:MediaServer:1</deviceType>
    <friendlyName>{FRIENDLY_NAME}</friendlyName>
    <manufacturer>fabianswebworld</manufacturer>
    <manufacturerURL>https://github.com/fabianswebworld</manufacturerURL>
    <modelDescription>yt-dlna Media Server</modelDescription>
    <modelName>yt-dlna</modelName>
    <modelNumber>{utils.__version__}</modelNumber>
    <modelURL>https://github.com/fabianswebworld/yt-dlna</modelURL>
    <UDN>{UUID}</UDN>
    <dlna:X_DLNADOC xmlns:dlna="urn:schemas-dlna-org:device-1-0">DMS-1.50</dlna:X_DLNADOC>
    <presentationURL>{presentation_url}</presentationURL>
    <iconList>
      <icon>
        <mimetype>image/png</mimetype>
        <width>64</width>
        <height>64</height>
        <depth>32</depth>
        <url>/icon.png</url>
      </icon>
    </iconList>
    <serviceList>
      <service>
        <serviceType>urn:schemas-upnp-org:service:ContentDirectory:1</serviceType>
        <serviceId>urn:upnp-org:serviceId:ContentDirectory</serviceId>
        <SCPDURL>/cds.xml</SCPDURL>
        <controlURL>/ctl</controlURL>
        <eventSubURL>/evt</eventSubURL>
      </service>
      <service>
        <serviceType>urn:schemas-upnp-org:service:ConnectionManager:1</serviceType>
        <serviceId>urn:upnp-org:serviceId:ConnectionManager</serviceId>
        <SCPDURL>/cm.xml</SCPDURL>
        <controlURL>/cm_ctl</controlURL>
        <eventSubURL>/cm_evt</eventSubURL>
      </service>
    </serviceList>
  </device>
</root>"""

# Service Control Protocol Document (SCPD) for ContentDirectory
CDS_XML = """<?xml version="1.0" encoding="utf-8"?>
<scpd xmlns="urn:schemas-upnp-org:service-1-0">
  <specVersion><major>1</major><minor>0</minor></specVersion>
  <actionList>
    <action>
      <name>GetSearchCapabilities</name>
      <argumentList>
        <argument><name>SearchCaps</name><direction>out</direction><relatedStateVariable>SearchCapabilities</relatedStateVariable></argument>
      </argumentList>
    </action>
    <action>
      <name>GetSortCapabilities</name>
      <argumentList>
        <argument><name>SortCaps</name><direction>out</direction><relatedStateVariable>SortCapabilities</relatedStateVariable></argument>
      </argumentList>
    </action>
    <action>
      <name>GetSystemUpdateID</name>
      <argumentList>
        <argument><name>Id</name><direction>out</direction><relatedStateVariable>SystemUpdateID</relatedStateVariable></argument>
      </argumentList>
    </action>
    <action>
      <name>Browse</name>
      <argumentList>
        <argument><name>ObjectID</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_ObjectID</relatedStateVariable></argument>
        <argument><name>BrowseFlag</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_BrowseFlag</relatedStateVariable></argument>
        <argument><name>Filter</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_Filter</relatedStateVariable></argument>
        <argument><name>StartingIndex</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_Index</relatedStateVariable></argument>
        <argument><name>RequestedCount</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_Count</relatedStateVariable></argument>
        <argument><name>SortCriteria</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_SortCriteria</relatedStateVariable></argument>
        <argument><name>Result</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_Result</relatedStateVariable></argument>
        <argument><name>NumberReturned</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_Count</relatedStateVariable></argument>
        <argument><name>TotalMatches</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_Count</relatedStateVariable></argument>
        <argument><name>UpdateID</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_UpdateID</relatedStateVariable></argument>
      </argumentList>
    </action>
  </actionList>
  <serviceStateTable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_ObjectID</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_BrowseFlag</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_Filter</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_Index</name><dataType>ui4</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_Count</name><dataType>ui4</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_SortCriteria</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_Result</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_UpdateID</name><dataType>ui4</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>SearchCapabilities</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>SortCapabilities</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="yes"><name>SystemUpdateID</name><dataType>ui4</dataType></stateVariable>
  </serviceStateTable>
</scpd>"""

# Service Control Protocol Document (SCPD) for ConnectionManager
CM_XML = """<?xml version="1.0"?><scpd xmlns="urn:schemas-upnp-org:service-1-0"><specVersion><major>1</major><minor>0</minor></specVersion><actionList><action><name>GetProtocolInfo</name><argumentList><argument><name>Source</name><direction>out</direction><relatedStateVariable>SourceProtocolInfo</relatedStateVariable></argument><argument><name>Sink</name><direction>out</direction><relatedStateVariable>SinkProtocolInfo</relatedStateVariable></argument></argumentList></action></actionList><serviceStateTable><stateVariable sendEvents="yes"><name>SourceProtocolInfo</name><dataType>string</dataType></stateVariable><stateVariable sendEvents="yes"><name>SinkProtocolInfo</name><dataType>string</dataType></stateVariable></serviceStateTable></scpd>"""

def trigger_sync(playlist_name=None):
    """Helper function to safely run the sync script in the background when requested via DLNA."""
    try:
        threading.Thread(target=sync.run_sync, args=(playlist_name,), daemon=True).start()
    except Exception as e:
        utils.log(_LOG_SRC, f"Error triggering sync: {e}", level=1, type='E')

def get_custom_node_and_mode(file_path, internal_path):
    """
    Navigates the JSON tree using a path like '0/2/1' (indices).
    Returns (list_of_nodes, inherited_mode). Used for custom playlists.
    """
    if not os.path.exists(file_path):
        return None, 'bounce'
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # if data is a list, wrap it.
        if isinstance(data, list):
            current_level = data
            active_mode = 'bounce'
        else:
            current_level = data.get('children', [])
            active_mode = data.get('mode') or 'bounce'
        
        if not internal_path:
            return current_level, active_mode
            
        indices = [int(x) for x in internal_path.split('/')]
        for idx in indices:
            node = current_level[idx]
            active_mode = node.get('mode') or active_mode
            current_level = node.get('children', [])
            
        return current_level, active_mode
    except:
        return None, 'bounce'

def build_custom_item_xml(node, item_id, parent_id, proxy_base, parent_mode):
    """Constructs the XML for a single custom item or folder."""
    name = utils.xml_escape(node.get('name', 'Untitled'))

    if node.get('type') == 'folder':
        return f"""
        <container id="{item_id}" parentID="{parent_id}" restricted="1" searchable="0">
            <dc:title>{name}</dc:title>
            <upnp:class>object.container.storageFolder</upnp:class>
        </container>"""

    else:
        mode = node.get('mode') or parent_mode or 'default'
        target_url = node.get('url', '')
        service = node.get('service') or 'auto'
        safe_target = urllib.parse.quote(target_url, safe='')

        resolving_modes = {'default', 'redirect', 'proxy', 'remux', 'remux_mp4', 'remux_ts'}

        if mode in resolving_modes:
            config = utils.load_config()
            remux_to_ts = config.get('proxy', 'remux_target_format', fallback='ts').strip().lower() == 'ts'

            if mode == 'default':
                proxy_url = f"{proxy_base}/play/{service}/{safe_target}"
            elif mode == 'remux_mp4':
                proxy_url = f"{proxy_base}/remux/mp4/{service}/{safe_target}"
            elif mode == 'remux_ts':
                proxy_url = f"{proxy_base}/remux/ts/{service}/{safe_target}"
            else:
                proxy_url = f"{proxy_base}/{mode}/{service}/{safe_target}"

            is_ts = (mode == 'remux_ts') or (mode in ('remux', 'default') and remux_to_ts)
            if is_ts:
                mime = "video/mpeg"
                proto = "http-get:*:video/mpeg:DLNA.ORG_PN=AVC_TS_HD_EU;DLNA.ORG_OP=00;DLNA.ORG_CI=0;DLNA.ORG_FLAGS=01700000000000000000000000000000"
            else:
                mime = "video/mp4"
                proto = "http-get:*:video/mp4:DLNA.ORG_OP=01;DLNA.ORG_CI=0;DLNA.ORG_FLAGS=01700000000000000000000000000000"

            upnp_class = "object.item.videoItem.movie"

        elif mode == 'direct':
            proxy_url = target_url
            upnp_class = "object.item.videoItem.movie"
            proto = "http-get:*:video/mp4:DLNA.ORG_OP=01;DLNA.ORG_CI=0;DLNA.ORG_FLAGS=01700000000000000000000000000000"

        elif mode == 'hit':
            proxy_url = f"{proxy_base}/hit/{safe_target}"
            upnp_class = "object.item.audioItem.musicTrack"
            proto = "http-get:*:audio/mpeg:DLNA.ORG_PN=MP3;DLNA.ORG_OP=01;DLNA.ORG_CI=0;DLNA.ORG_FLAGS=01700000000000000000000000000000"

        else:
            # bounce / reflect
            proxy_url = f"{proxy_base}/{mode}/{safe_target}"
            mime = node.get('mime_type')
            if not mime:
                url_low = target_url.lower()
                mime = "audio/mpeg" if any(x in url_low for x in ['mp3', 'm4a', 'aac', '.wav', 'radio', 'stream', 'listen']) else "video/mp4"

            is_live = any(x in target_url.lower() for x in ['radio', 'stream', 'listen', 'live', 'icecast'])
            op_flag = "00" if is_live else "01"

            if mime == "audio/mpeg":
                upnp_class = "object.item.audioItem.musicTrack"
                proto = f"http-get:*:audio/mpeg:DLNA.ORG_PN=MP3;DLNA.ORG_OP={op_flag};DLNA.ORG_CI=0;DLNA.ORG_FLAGS=01700000000000000000000000000000"
            elif mime == "video/mpeg":
                upnp_class = "object.item.videoItem.movie"
                proto = "http-get:*:video/mpeg:DLNA.ORG_PN=AVC_TS_HD_EU;DLNA.ORG_OP=01;DLNA.ORG_CI=0;DLNA.ORG_FLAGS=01700000000000000000000000000000"
            else:
                # default handling for video/mp4, audio/mp4, etc.
                upnp_class = "object.item.audioItem.musicTrack" if "audio" in mime else "object.item.videoItem.movie"
                proto = f"http-get:*:{mime}:DLNA.ORG_OP={op_flag};DLNA.ORG_CI=0;DLNA.ORG_FLAGS=01700000000000000000000000000000"

        return f"""
        <item id="{item_id}" parentID="{parent_id}" restricted="1">
            <dc:title>{name}</dc:title>
            <upnp:class>{upnp_class}</upnp:class>
            <res protocolInfo="{proto}">{proxy_url}</res>
        </item>"""

# ==============================================================================
# --- renderer discovery & AVTransport controller (for play-to) ---
# ==============================================================================

def load_renderers():
    """Thread-safely reads cached renderers from data/dlna_renderers.json."""
    if not os.path.exists(RENDERERS_FILE):
        return {}
    with _renderers_lock:
        try:
            with open(RENDERERS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}

def save_renderers(data):
    """Thread-safely saves discovered renderers to data/dlna_renderers.json."""
    with _renderers_lock:
        utils.replace_save_json(RENDERERS_FILE, data, indent=2)

def resolve_target_renderer(renderer_key=None):
    """
    Resolves target renderer with fallback priority (specified key ->
    config file setting -> last used -> first found)
    """
    renderers = load_renderers()
    if not renderers:
        return None

    config = utils.load_config()
    config_default = config.get('dlna', 'default_renderer', fallback='').strip()

    # explicit key given
    if renderer_key and renderer_key not in ('default-renderer', 'default', 'last-renderer', 'last'):
        if renderer_key in renderers:
            return renderers[renderer_key]
        for r in renderers.values():
            if r.get('name', '').lower() == renderer_key.lower():
                return r

    # 'default-renderer' requested or key omitted; check yt-dlna.conf
    if renderer_key in ('default-renderer', 'default', None, '') and config_default:
        if config_default in renderers:
            return renderers[config_default]
        for r in renderers.values():
            if r.get('name', '').lower() == config_default.lower():
                return r

    # 'last-renderer' requested or default unavailable; check highest last_used
    sorted_by_used = sorted(renderers.values(), key=lambda x: x.get('last_used', 0), reverse=True)
    if sorted_by_used and sorted_by_used[0].get('last_used', 0) > 0:
        return sorted_by_used[0]

    # fallback to first available renderer
    return list(renderers.values())[0]

def send_soap_action(control_url, service_type, action_name, args_dict):
    """Sends a UPnP SOAP control action to a device endpoint."""
    args_xml = "".join([f"<{k}>{utils.xml_escape(v)}</{k}>" for k, v in args_dict.items()])
    soap_body = (
        f'<?xml version="1.0" encoding="utf-8"?>\n'
        f'<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">\n'
        f'  <s:Body>\n'
        f'    <u:{action_name} xmlns:u="{service_type}">\n'
        f'      {args_xml}\n'
        f'    </u:{action_name}>\n'
        f'  </s:Body>\n'
        f'</s:Envelope>'
    ).strip().encode('utf-8')

    headers = {
        'Content-Type': 'text/xml; charset="utf-8"',
        'SOAPAction': f'"{service_type}#{action_name}"',
        'Content-Length': str(len(soap_body))
    }

    req = urllib.request.Request(control_url, data=soap_body, headers=headers, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.read().decode('utf-8', errors='ignore')
    except urllib.error.HTTPError as e:
        fault = e.read().decode('utf-8', errors='ignore') if hasattr(e, 'read') else str(e)
        raise Exception(f"Renderer rejected SOAP action (HTTP {e.code}): {fault[:1000]}")

def play_to_renderer(renderer_key, video_url_or_id, service='auto', mode='default'):
    """Sends SetAVTransportURI and Play commands to play a video stream on a TV."""
    renderer = resolve_target_renderer(renderer_key)
    if not renderer:
        raise Exception("No active DLNA/UPnP MediaRenderer found on local network.")

    control_url = renderer.get('control_url')
    if not control_url:
        raise Exception(f"Renderer '{renderer.get('name')}' lacks AVTransport control URL.")

    # construct the proxy stream URI
    proxy_base = utils.get_stream_base_url()
    safe_target = urllib.parse.quote(video_url_or_id, safe='')
    service_name = service or 'auto'

    if mode in ('remux', 'remux_ts'):
        route = f"/remux/ts/{service_name}/{safe_target}"
        mime = "video/mpeg"
        pn = "DLNA.ORG_PN=AVC_TS_HD_EU;"
    elif mode == 'remux_mp4':
        route = f"/remux/mp4/{service_name}/{safe_target}"
        mime = "video/mp4"
        pn = ""
    elif mode in ('no-remux', 'redirect-no-remux', 'proxy-no-remux'):
        route = f"/proxy-no-remux/{service_name}/{safe_target}"
        mime = "video/mp4"
        pn = ""
    elif mode in ('default', 'redirect'):
        route = f"/proxy/{service_name}/{safe_target}"
        mime = "video/mp4"
        pn = ""
    else:
        route = f"/{mode}/{service_name}/{safe_target}"
        mime = "video/mp4"
        pn = ""

    stream_uri = f"{proxy_base}{route}"

    # check if video title is already cached in urlcache.json, else use ID/URL
    cached = utils.get_cached_url(video_url_or_id, service_name=service_name)
    if cached and cached.get('title'):
        display_title = cached['title']
    elif video_url_or_id.startswith('http'):
        domain = urllib.parse.urlparse(video_url_or_id).netloc.replace('www.', '')
        display_title = f"{domain} Video"
    else:
        display_title = f"Video {video_url_or_id}"

    didl_metadata = (
        f'<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
        f'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
        f'<item id="1" parentID="0" restricted="1">'
        f'<dc:title>{utils.xml_escape(display_title)}</dc:title>'
        f'<upnp:class>object.item.videoItem.movie</upnp:class>'
        f'<res protocolInfo="http-get:*:{mime}:{pn}DLNA.ORG_OP=01;DLNA.ORG_CI=0;DLNA.ORG_FLAGS=01700000000000000000000000000000">{stream_uri}</res>'
        f'</item></DIDL-Lite>'
    )

    avt_service = "urn:schemas-upnp-org:service:AVTransport:1"

    # always unlock AVTransport first by stopping
    try:
        send_soap_action(control_url, avt_service, "Stop", {
            "InstanceID": "0"
        })
        time.sleep(0.2)
    except Exception:
        pass

    utils.log(_LOG_SRC, f"Sending SetAVTransportURI to '{renderer.get('name')}'...")
    send_soap_action(control_url, avt_service, "SetAVTransportURI", {
        "InstanceID": "0",
        "CurrentURI": stream_uri,
        "CurrentURIMetaData": didl_metadata
    })

    utils.log(_LOG_SRC, f"Sending Play command to '{renderer.get('name')}'...", type='S')
    send_soap_action(control_url, avt_service, "Play", {
        "InstanceID": "0",
        "Speed": "1"
    })

    # update last_used in registry
    renderers = load_renderers()
    for r_udn, r_data in renderers.items():
        if r_data.get('control_url') == control_url:
            r_data['last_used'] = time.time()
            save_renderers(renderers)
            break

    return {
        "status": "success",
        "renderer": renderer.get('name'),
        "stream_uri": stream_uri
    }

# ==============================================================================
# --- DIAL/Cast Lounge pairing and message bus listener ---
# ==============================================================================

def start_lounge_listener(pairing_code):
    """Registers pairing code with YouTube Cast Lounge and listens for video playback events."""
    global _active_lounge_session
    session_id = str(uuid.uuid4())
    with _lounge_session_lock:
        _active_lounge_session = session_id

    try:
        utils.log(_LOG_SRC, f"Initiating DIAL/Cast Lounge pairing with code: {pairing_code[:8]}...", "dial")
        
        # obtain Lounge token for our DIAL_SCREEN_ID
        token_url = "https://www.youtube.com/api/lounge/pairing/get_lounge_token_batch"
        token_resp = requests.post(token_url, data={'screen_ids': DIAL_SCREEN_ID}, timeout=8)
        if token_resp.status_code != 200:
            utils.log(_LOG_SRC, f"Lounge token request failed (HTTP {token_resp.status_code}): {token_resp.text}", "dial", level=1, type='E')
            return

        token_data = token_resp.json()
        screens = token_data.get('screens', [])
        if not screens:
            utils.log(_LOG_SRC, "No screens returned by Lounge token service", "dial", level=2, type='W')
            return

        lounge_token = screens[0].get('loungeToken')
        if not lounge_token:
            utils.log(_LOG_SRC, "Missing loungeToken in YouTube response", "dial", level=1, type='E')
            return

        # register pairing code with YouTube Lounge API
        register_url = "https://www.youtube.com/api/lounge/pairing/register_pairing_code"
        reg_payload = {
            'pairing_code': pairing_code,
            'screen_id': DIAL_SCREEN_ID,
            'screen_name': DIAL_FRIENDLY_NAME,
            'lounge_token': lounge_token,
            'access_type': 'permanent',
            'app': 'yt-dlna'
        }
        reg_resp = requests.post(register_url, data=reg_payload, timeout=8)
        utils.log(_LOG_SRC, f"Registered pairing code with YouTube Lounge (HTTP {reg_resp.status_code})", "dial", type='S')

        # bind to Lounge message bus and listen for video events
        bind_url = "https://www.youtube.com/api/lounge/bc/bind"
        headers = {
            'X-YouTube-LoungeId-Token': lounge_token,
            'Content-Type': 'application/x-www-form-urlencoded'
        }

        # initial bind handshake (RID 0)
        init_params = {
            'device': 'LOUNGE_SCREEN',
            'app': 'yt-dlna',
            'name': DIAL_FRIENDLY_NAME,
            'id': DIAL_SCREEN_ID,
            'loungeIdToken': lounge_token,
            'VER': '8',
            'v': '2',
            'RID': '0',
            'CVER': '1',
            'TYPE': 'xmlhttp'
        }
        init_resp = requests.post(bind_url, params=init_params, data={'count': 0}, headers=headers, timeout=10)

        # extract SID and gsessionid from response
        m_sid = re.search(r'\[\s*\d+\s*,\s*\[\s*"c"\s*,\s*"([^"]+)"', init_resp.text)
        m_gsession = re.search(r'\[\s*\d+\s*,\s*\[\s*"S"\s*,\s*"([^"]+)"', init_resp.text)
        if not m_sid:
            utils.log(_LOG_SRC, f"Could not extract Lounge SID: {init_resp.text[:200]}", "dial", level=2, type='W')
            return

        sid = m_sid.group(1)

        gsessionid = m_gsession.group(1) if m_gsession else None
        utils.log(_LOG_SRC, f"Lounge session established (SID: {sid[:8]}..., gsessionid: {gsessionid[:8] if gsessionid else 'none'})", "dial", type='S')

        # send initial screen readiness notification (RID 1)
        ready_params = dict(init_params)
        ready_params['RID'] = '1'
        ready_params['SID'] = sid
        if gsessionid:
            ready_params['gsessionid'] = gsessionid

        ready_data = {
            'count': 1,
            'ofs': 0,
            'req0__sc': 'nowPlaying',
            'req0_state': -1
        }
        try:
            requests.post(bind_url, params=ready_params, data=ready_data, headers=headers, timeout=5)
        except Exception:
            pass

        # long-polling listener stream
        poll_params = {
            'device': 'LOUNGE_SCREEN',
            'app': 'yt-dlna',
            'id': DIAL_SCREEN_ID,
            'loungeIdToken': lounge_token,
            'SID': sid,
            'RID': 'rpc',
            'AID': '0',
            'VER': '8',
            'v': '2',
            'CI': '0',
            'TYPE': 'xmlhttp'
        }

        if gsessionid:
            poll_params['gsessionid'] = gsessionid

        last_played_id = None
        disconnected_at = None
        clean_uuid = UUID.replace('uuid:', '')

        with requests.get(bind_url, params=poll_params, headers=headers, stream=True, timeout=120) as stream_resp:
            for line in stream_resp.iter_lines():
                if _active_lounge_session != session_id:
                    break

                now = time.time()
                # if remote app has been disconnected for more than 5 minutes, close session
                if disconnected_at and (now - disconnected_at > 300):
                    utils.log(_LOG_SRC, "Lounge session closed due to inactivity timeout.", "dial", level=3)
                    break

                if not line:
                    continue
                text = line.decode('utf-8', errors='ignore')
                utils.log(_LOG_SRC, f"Lounge bus data: {text[:1000]}", "dial", level=5, type='D')

                if '"getDiscoveryDeviceId"' in text:
                    def reply_discovery_id():
                        try:
                            r_params = dict(init_params)
                            r_params['RID'] = '2'
                            r_params['SID'] = sid
                            if gsessionid: r_params['gsessionid'] = gsessionid
                            requests.post(bind_url, params=r_params, data={
                                'count': 1, 'ofs': 1,
                                'req0__sc': 'discoveryDeviceIdResponse',
                                'req0_discoveryDeviceId': UUID
                            }, headers=headers, timeout=5)
                            utils.log(_LOG_SRC, "Verified receiver identity with YouTube Lounge.", "dial", level=4)
                        except Exception: pass
                    threading.Thread(target=reply_discovery_id, daemon=True).start()

                if '"getNowPlaying"' in text:
                    def reply_now_playing():
                        try:
                            r_params = dict(init_params)
                            r_params['RID'] = '3'
                            r_params['SID'] = sid
                            if gsessionid: r_params['gsessionid'] = gsessionid
                            requests.post(bind_url, params=r_params, data={
                                'count': 1, 'ofs': 1,
                                'req0__sc': 'nowPlaying',
                                'req0_state': -1
                            }, headers=headers, timeout=5)
                            utils.log(_LOG_SRC, "Reported player readiness to YouTube Lounge.", "dial", level=4)
                        except Exception: pass
                    threading.Thread(target=reply_now_playing, daemon=True).start()

                # close session 5 minutes after mobile app disconnects
                if '"remoteDisconnected"' in text:
                    disconnected_at = time.time()
                    utils.log(_LOG_SRC, "Cast remote app disconnected. Keeping Lounge session alive for 5 minutes.", "dial")
                elif '"remoteConnected"' in text:
                    disconnected_at = None
                    utils.log(_LOG_SRC, "Cast remote app reconnected.", "dial", level=4)
                
                # match videoId in nowPlaying or setPlaylist commands
                m_vid = re.search(r'"videoId"\s*:\s*"([a-zA-Z0-9_-]{11})"', text) or re.search(r'"videoIds"\s*:\s*\[\s*"([a-zA-Z0-9_-]{11})"', text)
                if m_vid:
                    vid = m_vid.group(1)
                    if vid != last_played_id:
                        last_played_id = vid
                        utils.log(_LOG_SRC, f"DIAL Lounge event received: videoId '{vid}'. Playing to DLNA renderer...", "dial", type='S')
                        def run_background_play_to():
                            try:
                                play_to_renderer('default-renderer', vid, 'youtube', 'default')
                            except Exception as e:
                                utils.log(_LOG_SRC, f"Play-To failed: {e}", level=1, type='E')
                        threading.Thread(target=run_background_play_to, daemon=True).start()

    except Exception as e:
        utils.log(_LOG_SRC, f"Lounge session ended or error: {e}", "dial", level=1)
    finally:
        global _dial_app_state
        _dial_app_state = "stopped"

# ==============================================================================
# --- HTTP request handler (UPnP / DLNA endpoints) ---
# ==============================================================================
class DLNAHandler(BaseHTTPRequestHandler):
    timeout = 10

    def log_message(self, format, *args): 
        pass 

    def do_OPTIONS(self):
        """Handle CORS preflight requests from mobile Cast apps (like YouTube app)."""
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Origin")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        """Handle UPnP (DLNA and DIAL) GET requests."""
        host_ip = self.headers.get('Host', f"{DLNA_IP}:{DLNA_PORT}")
        
        # --- virtual refresh trigger item ---
        if self.path.startswith("/virtual_refresh_stream"):
            # parse optional playlist query parameter for targeted folder refresh
            parsed_url = urllib.parse.urlparse(self.path)
            query_params = urllib.parse.parse_qs(parsed_url.query)
            target_pl = query_params.get('playlist', [None])[0]
            if target_pl:
                target_pl = urllib.parse.unquote(target_pl)

            # check if playlist is enabled
            active_configs = utils.get_playlists_config()
            enabled_titles = [pl['title'] for pl in active_configs if pl.get('enabled', True)]

            if target_pl and target_pl not in enabled_titles:
                utils.log(_LOG_SRC, f"Ignored sync request for disabled playlist: {target_pl}", level=2, type='W')
                self.send_response(403)
                self.end_headers()
                return

            utils.log(_LOG_SRC, f"Refresh stream triggered for '{target_pl or 'all'}'. Launching background sync...")
            trigger_sync(target_pl)

            dummy_path = os.path.join("assets", "dummy.mp3")
            if os.path.exists(dummy_path):
                self.send_response(200)
                self.send_header("Content-Type", "audio/mpeg")
                self.send_header("Content-Length", str(os.path.getsize(dummy_path)))
                self.end_headers()
                with open(dummy_path, 'rb') as f:
                    self.wfile.write(f.read())
            else:
                # fallback empty response if asset file is missing so the client doesn't hang
                self.send_response(204)
                self.end_headers()

        # --- server icon ---
        elif self.path == "/icon.png" and ICON_PATH and os.path.exists(ICON_PATH):
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.end_headers()
            with open(ICON_PATH, 'rb') as img:
                self.wfile.write(img.read())

        # --- DLNA MediaServer description (desc.xml) ---
        elif self.path == "/desc.xml":
            desc_content = get_device_desc(is_dial=False).encode('utf-8')
            self.send_response(200)
            self.send_header("CONTENT-TYPE", "text/xml; charset=\"utf-8\"")
            self.send_header("CONTENT-LENGTH", str(len(desc_content)))
            self.send_header("APPLICATION-URL", f"http://{host_ip}/apps/")
            self.send_header("SERVER", SERVER_STRING)
            self.send_header("EXT", "")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(desc_content)

        # --- DIAL Receiver description (dd.xml) ---
        elif self.path == "/dd.xml":
            desc_content = get_device_desc(is_dial=True).encode('utf-8')
            self.send_response(200)
            self.send_header("CONTENT-TYPE", "text/xml; charset=\"utf-8\"")
            self.send_header("CONTENT-LENGTH", str(len(desc_content)))
            self.send_header("APPLICATION-URL", f"http://{host_ip}/apps/")
            self.send_header("SERVER", SERVER_STRING)
            self.send_header("EXT", "")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(desc_content)

        # --- ContentDirectory SCPD XML (cds.xml) ---
        elif self.path == "/cds.xml":
            self.send_response(200)
            self.send_header("Content-Type", "text/xml; charset=\"utf-8\"")
            self.end_headers()
            self.wfile.write(CDS_XML.encode('utf-8'))

        # --- ConnectionManager SCPD XML (cm.xml) ---
        elif self.path == "/cm.xml":
            self.send_response(200)
            self.send_header("Content-Type", "text/xml; charset=\"utf-8\"")
            self.end_headers()
            self.wfile.write(CM_XML.encode('utf-8'))

        # --- DIAL YouTube app (Cast V1) status query ---
        elif self.path.startswith("/apps/YouTube"):
            state_body = (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<service xmlns="urn:dial-multiscreen-org:schemas:dial">\n'
                '  <name>YouTube</name>\n'
                '  <options allowStop="true"/>\n'
                f'  <state>{_dial_app_state}</state>\n'
                f'  {"""<link rel="run" href="run"/>""" if _dial_app_state == "running" else ""}\n'
                '  <additionalData>\n'
                f'    <screenId>{DIAL_SCREEN_ID}</screenId>\n'
                '  </additionalData>\n'
                '</service>'
            ).strip().encode('utf-8')
            self.send_response(200)
            self.send_header("Content-Type", "text/xml; charset=\"utf-8\"")
            self.send_header("Content-Length", str(len(state_body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Expose-Headers", "Location")
            self.end_headers()
            self.wfile.write(state_body)
            return

    def do_HEAD(self):
        """Respond to HTTP HEAD requests (some clients ping this before GET)."""
        self.send_response(200)
        self.send_header("SERVER", SERVER_STRING)
        self.send_header("EXT", "")
        self.send_header("CONTENT-LENGTH", "0")
        self.end_headers()

    def do_SUBSCRIBE(self):
        """Accept event subscriptions. Older players abort if this returns 501."""
        self.send_response(200)
        self.send_header("SERVER", SERVER_STRING)
        self.send_header("EXT", "")
        # provide a fake subscription ID (SID) and timeout to keep the player happy
        self.send_header("SID", "uuid:11112222-3333-4444-5555-666677778888")
        self.send_header("TIMEOUT", "Second-1800")
        self.send_header("CONTENT-LENGTH", "0")
        self.end_headers()

    def do_UNSUBSCRIBE(self):
        """Acknowledge when the client cancels the subscription."""
        self.send_response(200)
        self.send_header("CONTENT-LENGTH", "0")
        self.end_headers()

    def do_POST(self):
        """Handle UPnP SOAP POST control requests and DIAL app launches."""
        
        # --- ConnectionManager control endpoint (/cm_ctl) ---
        if self.path == "/cm_ctl":
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length).decode('utf-8', errors='ignore')
            
            response_body = (
                '<?xml version="1.0" encoding="utf-8"?>\n'
                '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">\n'
                '  <s:Body>\n'
                '    <u:GetProtocolInfoResponse xmlns:u="urn:schemas-upnp-org:service:ConnectionManager:1">\n'
                '      <Source>http-get:*:video/mp4:DLNA.ORG_OP=01;DLNA.ORG_CI=0;DLNA.ORG_FLAGS=01700000000000000000000000000000</Source>\n'
                '      <Sink></Sink>\n'
                '    </u:GetProtocolInfoResponse>\n'
                '  </s:Body>\n'
                '</s:Envelope>'
            ).strip()
            
            response_bytes = response_body.encode('utf-8')
            self.send_response(200)
            self.send_header("CONTENT-TYPE", "text/xml; charset=\"utf-8\"")
            self.send_header("CONTENT-LENGTH", str(len(response_bytes)))
            self.send_header("SERVER", SERVER_STRING)
            self.send_header("EXT", "")
            self.send_header("DATE", self.date_time_string())
            self.end_headers()
            self.wfile.write(response_bytes)

        # --- DIAL YouTube app launch request (/apps/YouTube) ---
        elif self.path.startswith("/apps/YouTube"):
            global _dial_app_state
            _dial_app_state = "running"

            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length).decode('utf-8', errors='ignore') if content_length > 0 else ""
            
            # parse parameters from both URL and POST body
            parsed_url = urllib.parse.urlparse(self.path)
            url_params = urllib.parse.parse_qs(parsed_url.query)
            body_params = urllib.parse.parse_qs(post_data)

            video_id = url_params.get('v', [None])[0] or body_params.get('v', [None])[0]
            pairing_code = url_params.get('pairingCode', [None])[0] or body_params.get('pairingCode', [None])[0] or body_params.get('pairing_code', [None])[0]

            utils.log(_LOG_SRC, f"DIAL connection received. Data: '{post_data or parsed_url.query}'", "dial", level=4)

            # direct video ID passed
            if video_id:
                utils.log(_LOG_SRC, f"DIAL cast received for video: '{video_id}'. Playing to UPnP renderer...", "dial", type='S')
                threading.Thread(target=play_to_renderer, args=('default', video_id, 'youtube', 'default'), daemon=True).start()

            # modern YouTube Lounge pairing code passed
            elif pairing_code:
                threading.Thread(target=start_lounge_listener, args=(pairing_code,), daemon=True).start()

            host_ip = self.headers.get('Host', f"{DLNA_IP}:{DLNA_PORT}")
            self.send_response(201)
            self.send_header("Location", f"http://{host_ip}/apps/YouTube/run")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        # --- ContentDirectory control endpoint (/ctl) ---
        elif self.path == "/ctl":
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length).decode('utf-8', errors='ignore')
            soap_action = self.headers.get('SOAPACTION', '')

            # --- handle GetSearchCapabilities
            if "GetSearchCapabilities" in soap_action or "<u:GetSearchCapabilities" in post_data:
                response_body = (
                    '<?xml version="1.0" encoding="utf-8"?>\n'
                    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">\n'
                    '  <s:Body>\n'
                    '    <u:GetSearchCapabilitiesResponse xmlns:u="urn:schemas-upnp-org:service:ContentDirectory:1">\n'
                    '      <SearchCaps></SearchCaps>\n'
                    '    </u:GetSearchCapabilitiesResponse>\n'
                    '  </s:Body>\n'
                    '</s:Envelope>'
                ).strip()

            # --- handle GetSortCapabilities
            elif "GetSortCapabilities" in soap_action or "<u:GetSortCapabilities" in post_data:
                response_body = (
                    '<?xml version="1.0" encoding="utf-8"?>\n'
                    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">\n'
                    '  <s:Body>\n'
                    '    <u:GetSortCapabilitiesResponse xmlns:u="urn:schemas-upnp-org:service:ContentDirectory:1">\n'
                    '      <SortCaps></SortCaps>\n'
                    '    </u:GetSortCapabilitiesResponse>\n'
                    '  </s:Body>\n'
                    '</s:Envelope>'
                ).strip()

            # --- handle GetSystemUpdateID
            elif "GetSystemUpdateID" in soap_action or "<u:GetSystemUpdateID" in post_data:
                response_body = (
                    '<?xml version="1.0" encoding="utf-8"?>\n'
                    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">\n'
                    '  <s:Body>\n'
                    '    <u:GetSystemUpdateIDResponse xmlns:u="urn:schemas-upnp-org:service:ContentDirectory:1">\n'
                    '      <Id>1</Id>\n'
                    '    </u:GetSystemUpdateIDResponse>\n'
                    '  </s:Body>\n'
                    '</s:Envelope>'
                ).strip()

            # --- handle Browse
            else:
                # extract requested ObjectID from SOAP payload (0 = root folder)
                obj_id = "0"
                # extract start index and requested items count
                start_idx = 0
                req_count = 0

                m_id = re.search(r'<ObjectID[^>]*>(.*?)</ObjectID>', post_data, re.IGNORECASE)
                if m_id: obj_id = m_id.group(1).strip()

                m_start = re.search(r'<StartingIndex[^>]*>(\d+)</StartingIndex>', post_data, re.IGNORECASE)
                if m_start: start_idx = int(m_start.group(1))

                m_count = re.search(r'<RequestedCount[^>]*>(\d+)</RequestedCount>', post_data, re.IGNORECASE)
                if m_count: req_count = int(m_count.group(1))

                utils.log(_LOG_SRC, f"Browse '{obj_id}' (Start: {start_idx}, Count: {req_count})", level=4)

                # collect all potential items in a list for slicing
                all_items = []

                # --- fetch current configuration to check which playlists are enabled
                # online playlists
                active_configs = utils.get_playlists_config()
                enabled_playlist_titles = [pl['title'] for pl in active_configs if pl.get('enabled', True)]
                # custom playlists
                custom_registry = utils.get_custom_playlists_registry()
                enabled_custom = [r for r in custom_registry if r['enabled']]

                # load playlist data from json
                library = {}
                if os.path.exists(utils.JSON_PATH):
                    try:
                        with open(utils.JSON_PATH, "r", encoding="utf-8") as f:
                            library = json.load(f)
                    except Exception: pass

                # pre-calculate stream address bases
                proxy_base = utils.get_stream_base_url()
                host_ip = self.headers.get('Host', f"{DLNA_IP}:{DLNA_PORT}")
                audio_proto = "http-get:*:audio/mpeg:DLNA.ORG_PN=MP3;DLNA.ORG_OP=01;DLNA.ORG_CI=0;DLNA.ORG_FLAGS=01700000000000000000000000000000"

                # --- root directory ---
                if obj_id == "0":
                    
                    # global refresh item
                    all_items.append(f"""
                    <item id="virtual_refresh_item" parentID="0" restricted="1">
                        <dc:title>[Click to Refresh All Playlists]</dc:title>
                        <upnp:class>object.item.audioItem.musicTrack</upnp:class>
                        <res protocolInfo="{audio_proto}">http://{host_ip}/virtual_refresh_stream</res>
                    </item>""")

                    # custom playlist folders
                    for reg in enabled_custom:
                        safe_reg_name = urllib.parse.quote(reg['name'])
                        all_items.append(f"""
                        <container id="cpl_file:{safe_reg_name}" parentID="0" restricted="1" searchable="0">
                            <dc:title>{utils.xml_escape(reg['name'])}</dc:title>
                            <upnp:class>object.container.storageFolder</upnp:class>
                        </container>""")

                    # online playlist folders
                    for folder_name in library.keys():
                        if folder_name in enabled_playlist_titles:
                            safe_id = urllib.parse.quote(folder_name)
                            all_items.append(f"""
                            <container id="{safe_id}" parentID="0" restricted="1" searchable="0">
                                <dc:title>{utils.xml_escape(folder_name)}</dc:title>
                                <upnp:class>object.container.storageFolder</upnp:class>
                            </container>""")

                # --- browse root refresh trigger ---
                elif obj_id == "virtual_refresh_item":
                    all_items.append(f"""
                    <item id="virtual_refresh_item" parentID="0" restricted="1">
                        <dc:title>[Click to Refresh All Playlists]</dc:title>
                        <upnp:class>object.item.audioItem.musicTrack</upnp:class>
                        <res protocolInfo="{audio_proto}">http://{host_ip}/virtual_refresh_stream</res>
                    </item>""")

                # --- browse playlist refresh trigger ---
                elif obj_id.startswith("virtual_refresh_folder_"):
                    encoded_folder = obj_id.replace("virtual_refresh_folder_", "", 1)
                    target_folder = urllib.parse.unquote(encoded_folder)
                    all_items.append(f"""
                    <item id="{obj_id}" parentID="0" restricted="1">
                        <dc:title>[Click to Refresh Playlist]</dc:title>
                        <upnp:class>object.item.audioItem.musicTrack</upnp:class>
                        <res protocolInfo="{audio_proto}">http://{host_ip}/virtual_refresh_stream?playlist={encoded_folder}</res>
                    </item>""")

                # --- inside custom playlist folder ---
                elif obj_id.startswith("cpl_file:") or obj_id.startswith("cpl_path:"):
                    # format: cpl_path:[RegistryName]:[0/1/2]
                    parts = obj_id.split(':', 2)
                    reg_name = urllib.parse.unquote(parts[1])
                    internal_path = parts[2] if len(parts) > 2 else ""
                    
                    # find matching file in registry
                    reg_entry = next((r for r in enabled_custom if r['name'] == reg_name), None)
                    if reg_entry:
                        safe_reg_name = urllib.parse.quote(reg_name)
                        
                        # virtual refresh item for custom playlists with precache enabled
                        if not internal_path and reg_entry.get('precache'):
                            all_items.append(f"""
                            <item id="virtual_refresh_folder_{safe_reg_name}" parentID="{obj_id}" restricted="1">
                                <dc:title>[Click to Refresh / Resolve Playlist]</dc:title>
                                <upnp:class>object.item.audioItem.musicTrack</upnp:class>
                                <res protocolInfo="{audio_proto}">http://{host_ip}/virtual_refresh_stream?playlist={safe_reg_name}</res>
                            </item>""")

                        file_path = os.path.join(utils.CONFIG_DIR, reg_entry['file'])
                        nodes, inherited_mode = get_custom_node_and_mode(file_path, internal_path)
                        
                        if nodes and isinstance(nodes, list):
                            for i, node in enumerate(nodes):
                                # build child ID using stable index path
                                new_internal = f"{internal_path}/{i}" if internal_path else str(i)
                                child_id = f"cpl_path:{urllib.parse.quote(reg_name)}:{new_internal}"
                                # convert node to DIDL XML and add to list for slicing
                                item_xml = build_custom_item_xml(node, child_id, obj_id, proxy_base, inherited_mode)
                                all_items.append(item_xml)

                # --- inside online playlist folder ---
                else:
                    requested_folder = urllib.parse.unquote(obj_id)
                    if requested_folder in enabled_playlist_titles:
                        safe_folder_id = urllib.parse.quote(requested_folder)

                        # folder specific refresh item
                        all_items.append(f"""
                        <item id="virtual_refresh_folder_{safe_folder_id}" parentID="{obj_id}" restricted="1">
                            <dc:title>[Click to Refresh Playlist]</dc:title>
                            <upnp:class>object.item.audioItem.musicTrack</upnp:class>
                            <res protocolInfo="{audio_proto}">http://{host_ip}/virtual_refresh_stream?playlist={safe_folder_id}</res>
                        </item>""")

                        # playlist items
                        if requested_folder in library:
                            pl_data = library[requested_folder]
                            items = pl_data.get('items', []) if isinstance(pl_data, dict) else pl_data
                            config = utils.load_config()
                            remux_to_ts = config.get('proxy', 'remux_target_format', fallback='ts').strip().lower() == 'ts'
                            for idx, entry in enumerate(items):
                                v_id = entry.get('id')
                                proxy_url = entry.get('proxy_url')
                                
                                if entry.get('is_error'):
                                    all_items.append(f"""
                                    <item id="{v_id}" parentID="{obj_id}" restricted="1">
                                        <dc:title>{utils.xml_escape(entry.get('title'))}</dc:title>
                                        <upnp:class>object.item.audioItem.musicTrack</upnp:class>
                                        <res protocolInfo="{audio_proto}">{proxy_url}</res>
                                    </item>""")
                                    continue

                                # format title dynamically using shared utils helper
                                title = utils.xml_escape(utils.format_item_title(entry, enum_idx=idx+1))
                                channel = utils.xml_escape(entry.get('channel', ''))
                                creator_tags = f"<dc:creator>{channel}</dc:creator><upnp:artist>{channel}</upnp:artist><upnp:author>{channel}</upnp:author>" if channel else ""
                                
                                dur = utils.format_duration_dlna(entry.get('duration'))
                                dur_attr = f' duration="{dur}"' if dur else ""

                                is_remux = "/remux/" in proxy_url or entry.get('is_dash', False)

                                if is_remux and remux_to_ts:
                                    mime = "video/mpeg"
                                    proto = "http-get:*:video/mpeg:DLNA.ORG_PN=AVC_TS_HD_EU;DLNA.ORG_OP=01;DLNA.ORG_CI=0;DLNA.ORG_FLAGS=01700000000000000000000000000000"
                                else:
                                    mime = "video/mp4"
                                    proto = "http-get:*:video/mp4:DLNA.ORG_OP=01;DLNA.ORG_CI=0;DLNA.ORG_FLAGS=01700000000000000000000000000000"

                                all_items.append(f"""
                                <item id="{v_id}" parentID="{obj_id}" restricted="1">
                                    <dc:title>{title}</dc:title>{creator_tags}
                                    <upnp:class>object.item.videoItem.movie</upnp:class>
                                    <res protocolInfo="{proto}"{dur_attr}>{proxy_url}</res>
                                </item>""")

                total_matches = len(all_items)
                # if req_count is 0, take everything from start_idx
                end_idx = total_matches if req_count == 0 else start_idx + req_count
                sliced_list = all_items[start_idx : end_idx]
                number_returned = len(sliced_list)

                # construct inner XML
                inner_didl = (
                    f'<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
                    f'xmlns:dc="http://purl.org/dc/elements/1.1/" '
                    f'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/" '
                    f'xmlns:dlna="urn:schemas-dlna-org:metadata-1-0/">'
                    f'{"".join(sliced_list)}'
                    f'</DIDL-Lite>'
                )

                response_body = (
                    f'<?xml version="1.0" encoding="utf-8"?>\n'
                    f'<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">\n'
                    f'  <s:Body>\n'
                    f'    <u:BrowseResponse xmlns:u="urn:schemas-upnp-org:service:ContentDirectory:1">\n'
                    f'      <Result>{utils.xml_escape(inner_didl)}</Result>\n'
                    f'      <NumberReturned>{number_returned}</NumberReturned>\n'
                    f'      <TotalMatches>{total_matches}</TotalMatches>\n'
                    f'      <UpdateID>1</UpdateID>\n'
                    f'    </u:BrowseResponse>\n'
                    f'  </s:Body>\n'
                    f'</s:Envelope>'
                ).strip()

            # --- send the final response (all SOAP actions use these headers) ---
            response_bytes = response_body.encode('utf-8')
            self.send_response(200)
            self.send_header("CONTENT-TYPE", "text/xml; charset=\"utf-8\"")
            self.send_header("CONTENT-LENGTH", str(len(response_bytes)))
            self.send_header("SERVER", SERVER_STRING)
            self.send_header("EXT", "")
            self.send_header("DATE", self.date_time_string())
            self.end_headers()
            self.wfile.write(response_bytes)

    def do_DELETE(self):
        """Handle DIAL application stop requests."""
        if self.path.startswith("/apps/YouTube"):
            global _dial_app_state, _active_lounge_session
            _dial_app_state = "stopped"
            with _lounge_session_lock:
                _active_lounge_session = None
            utils.log(_LOG_SRC, "DIAL session ended by client.", "dial")
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(404)
        self.end_headers()

# ==============================================================================
# --- discovery engines (SSDP beacon & active M-SEARCH listener) ---
# ==============================================================================

def run_ssdp_beacon():
    """Periodically broadcasts the 3 UPnP-required NOTIFY packets to the local network."""
    config = utils.load_config()
    ssdp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    ssdp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, 'SO_REUSEPORT'):
        try:
            ssdp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except Exception:
            pass

    adv_ip = DLNA_IP if DLNA_IP != '0.0.0.0' else utils.get_local_ip()
    location_dlna = f"http://{adv_ip}:{DLNA_PORT}/desc.xml"
    location_dial = f"http://{adv_ip}:{DLNA_PORT}/dd.xml"

    targets = [
        ("upnp:rootdevice", f"{UUID}::upnp:rootdevice"),
        (UUID, UUID),
        ("urn:schemas-upnp-org:device:MediaServer:1", f"{UUID}::urn:schemas-upnp-org:device:MediaServer:1")
    ]

    dial_enabled = config.getboolean('dlna', 'enable_dial_server', fallback=True)

    while True:
        # send DLNA beacons
        for nt, usn in targets:
            dlna_packet = (
                f"NOTIFY * HTTP/1.1\r\n"
                f"HOST: {SSDP_ADDR}:{SSDP_PORT}\r\n"
                f"NT: {nt}\r\n"
                f"NTS: ssdp:alive\r\n"
                f"USN: {usn}\r\n"
                f"LOCATION: {location_dlna}\r\n"
                f"CACHE-CONTROL: max-age=1800\r\n"
                f"SERVER: {SERVER_STRING}\r\n\r\n"
            ).encode('utf-8')
            try:
                ssdp_sock.sendto(dlna_packet, (SSDP_ADDR, SSDP_PORT))
            except Exception:
                pass

        # send DIAL beacons
        if dial_enabled:
            dial_packet = (
                f"NOTIFY * HTTP/1.1\r\n"
                f"HOST: {SSDP_ADDR}:{SSDP_PORT}\r\n"
                f"NT: {DIAL_ST}\r\n"
                f"NTS: ssdp:alive\r\n"
                f"USN: {UUID}::{DIAL_ST}\r\n"
                f"LOCATION: {location_dial}\r\n"
                f"CACHE-CONTROL: max-age=1800\r\n"
                f"SERVER: {SERVER_STRING}\r\n"
                f"BOOTID.UPNP.ORG: 1\r\n\r\n"
            ).encode('utf-8')
            try:
                ssdp_sock.sendto(dial_packet, (SSDP_ADDR, SSDP_PORT))
            except Exception:
                pass

        time.sleep(20)

def run_ssdp_listener():
    """Listens for active M-SEARCH queries from clients and responds with matching ST headers."""
    config = utils.load_config()
    ssdp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    ssdp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, 'SO_REUSEPORT'):
        try:
            ssdp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except Exception:
            pass

    ssdp_sock.bind(('', SSDP_PORT))
    
    # join multicast group on all interfaces (0.0.0.0 is critical for Linux/Raspberry Pi)
    mreq = socket.inet_aton(SSDP_ADDR) + socket.inet_aton('0.0.0.0')
    ssdp_sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

    actual_ip = DLNA_IP if DLNA_IP != '0.0.0.0' else utils.get_local_ip()
    location_dlna = f"http://{actual_ip}:{DLNA_PORT}/desc.xml"
    location_dial = f"http://{actual_ip}:{DLNA_PORT}/dd.xml"

    dial_enabled = config.getboolean('dlna', 'enable_dial_server', fallback=True)

    while True:
        try:
            data, addr = ssdp_sock.recvfrom(2048)
            message = data.decode('utf-8', errors='ignore')
            
            if "M-SEARCH" in message:
                msg_lower = message.lower()
                
                # handle DIAL queries
                if "service:dial:1" in msg_lower and dial_enabled:
                    dial_response = (
                        f"HTTP/1.1 200 OK\r\n"
                        f"CACHE-CONTROL: max-age=1800\r\n"
                        f"DATE: {time.strftime('%a, %d %b %Y %H:%M:%S GMT', time.gmtime())}\r\n"
                        f"EXT:\r\n"
                        f"LOCATION: {location_dial}\r\n"
                        f"SERVER: {SERVER_STRING}\r\n"
                        f"ST: {DIAL_ST}\r\n"
                        f"USN: {UUID}::{DIAL_ST}\r\n"
                        f"BOOTID.UPNP.ORG: 1\r\n"
                        f"CONTENT-LENGTH: 0\r\n\r\n"
                    ).encode('utf-8')
                    ssdp_sock.sendto(dial_response, addr)
                    continue

                # build list of target responses matching client request
                responses = []
                
                if "ssdp:all" in msg_lower:
                    responses = [
                        ("upnp:rootdevice", f"{UUID}::upnp:rootdevice"),
                        (UUID, UUID),
                        ("urn:schemas-upnp-org:device:MediaServer:1", f"{UUID}::urn:schemas-upnp-org:device:MediaServer:1")
                    ]
                elif "rootdevice" in msg_lower:
                    responses = [("upnp:rootdevice", f"{UUID}::upnp:rootdevice")]
                elif "mediaserver" in msg_lower:
                    responses = [("urn:schemas-upnp-org:device:MediaServer:1", f"{UUID}::urn:schemas-upnp-org:device:MediaServer:1")]
                elif UUID.lower() in msg_lower:
                    responses = [(UUID, UUID)]
                else:
                    responses = [("upnp:rootdevice", f"{UUID}::upnp:rootdevice")]

                # handle DLNA queries
                for st_val, usn_val in responses:
                    dlna_response = (
                        f"HTTP/1.1 200 OK\r\n"
                        f"CACHE-CONTROL: max-age=1800\r\n"
                        f"DATE: {time.strftime('%a, %d %b %Y %H:%M:%S GMT', time.gmtime())}\r\n"
                        f"EXT:\r\n"
                        f"LOCATION: {location_dlna}\r\n"
                        f"SERVER: {SERVER_STRING}\r\n"
                        f"ST: {st_val}\r\n"
                        f"USN: {usn_val}\r\n"
                        f"CONTENT-LENGTH: 0\r\n"
                        f"\r\n"
                    )
                    ssdp_sock.sendto(dlna_response.encode('utf-8'), addr)
        except Exception:
            time.sleep(0.5)

def scan_for_renderers():
    """Broadcasts an M-SEARCH query to locate all active UPnP MediaRenderers."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.settimeout(2.5)

    search_msg = (
        f"M-SEARCH * HTTP/1.1\r\n"
        f"HOST: {SSDP_ADDR}:{SSDP_PORT}\r\n"
        f"MAN: \"ssdp:discover\"\r\n"
        f"MX: 2\r\n"
        f"ST: urn:schemas-upnp-org:service:AVTransport:1\r\n\r\n"
    ).encode('utf-8')

    try:
        sock.sendto(search_msg, (SSDP_ADDR, SSDP_PORT))
    except Exception:
        return

    discovered = load_renderers()
    changed = False
    start_time = time.time()

    while time.time() - start_time < 3.0:
        try:
            data, addr = sock.recvfrom(2048)
            resp = data.decode('utf-8', errors='ignore')
            m_loc = re.search(r'LOCATION:\s*(http[^\r\n]+)', resp, re.IGNORECASE)
            if not m_loc:
                continue

            location_url = m_loc.group(1).strip()

            # fetch desc.xml and extract AVTransport control URL
            req = urllib.request.Request(location_url, headers={'User-Agent': SERVER_STRING})
            with urllib.request.urlopen(req, timeout=2.5) as r_xml:
                xml_text = r_xml.read().decode('utf-8', errors='ignore')

            # parse friendlyName
            m_name = re.search(r'<friendlyName[^>]*>(.*?)</friendlyName>', xml_text, re.IGNORECASE)
            friendly_name = m_name.group(1).strip() if m_name else addr[0]

            # parse UDN
            m_udn = re.search(r'<UDN[^>]*>(.*?)</UDN>', xml_text, re.IGNORECASE)
            udn = m_udn.group(1).strip() if m_udn else f"uuid:{addr[0]}"

            # parse AVTransport service controlURL
            m_srv = re.search(r'<serviceType[^>]*>urn:schemas-upnp-org:service:AVTransport:1</serviceType>.*?<controlURL[^>]*>(.*?)</controlURL>', xml_text, re.IGNORECASE | re.DOTALL)
            if not m_srv:
                continue

            raw_ctrl = m_srv.group(1).strip()
            ctrl_url = urllib.parse.urljoin(location_url, raw_ctrl)

            existing = discovered.get(udn, {})
            discovered[udn] = {
                'name': friendly_name,
                'control_url': ctrl_url,
                'location': location_url,
                'ip': addr[0],
                'last_seen': time.time(),
                'last_used': existing.get('last_used', 0)
            }
            changed = True
        except Exception:
            break

    sock.close()
    config = utils.load_config()
    config_default = config.get('dlna', 'default_renderer', fallback='').strip().lower()
    now = time.time()
    # inactivity timeout of 7 days
    ttl_limit = 7 * 86400

    pruned_renderers = {}
    for udn_key, r_info in discovered.items():
        is_default = (udn_key.lower() == config_default) or (r_info.get('name', '').lower() == config_default)
        last_seen = r_info.get('last_seen', 0)

        # prune renderes not seen in the last 7 days, except configured default renderer
        if is_default or (now - last_seen <= ttl_limit):
            pruned_renderers[udn_key] = r_info
        else:
            changed = True
            utils.log(_LOG_SRC, f"UPnP renderer '{r_info.get('name', udn_key)}' gone for 7 days, pruned from list.")

    if changed:
        save_renderers(pruned_renderers)

def run_renderer_scanner():
    """Background worker that periodically refreshes the active renderers list."""
    time.sleep(3)
    while True:
        config = utils.load_config()
        if config.getboolean('dlna', 'enable_renderer_discovery', fallback=True):
            try:
                scan_for_renderers()
            except Exception as e:
                utils.log(_LOG_SRC, f"Renderer scan error: {e}", level=2, type='W')
        time.sleep(60)

# ==============================================================================
# --- server lifecycle initialization ---
# ==============================================================================

def start_dlna():
    """Unified entrypoint launching discovery threads and blocking HTTP server."""
    # fire background SSDP NOTIFY beacon broadcast loop
    threading.Thread(target=run_ssdp_beacon, daemon=True).start()
    
    # fire active SSDP M-SEARCH multicast listener loop
    threading.Thread(target=run_ssdp_listener, daemon=True).start()

    # fire active MediaRenderer discovery scanner loop
    threading.Thread(target=run_renderer_scanner, daemon=True).start()
    
    # start main blocking HTTP server loop for DLNA requests
    server = HTTPServer((DLNA_IP, DLNA_PORT), DLNAHandler)
    server.serve_forever()

if __name__ == "__main__":
    start_dlna()
