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
import re
from http.server import HTTPServer, BaseHTTPRequestHandler
import utils
import sync

# ==============================================================================
# --- configuration & constants ---
# ==============================================================================
config = utils.load_config()
DLNA_IP = config.get('dlna', 'dlna_ip', fallback='0.0.0.0')
DLNA_PORT = config.getint('dlna', 'dlna_port', fallback=8200)
FRIENDLY_NAME = config.get('dlna', 'friendly_name', fallback='yt-dlna Media Server')
ICON_PATH = config.get('dlna', 'icon', fallback='')
SERVER_STRING = f"Python/3.x UPnP/1.0 DLNADOC/1.50 yt-dlna/{utils.__version__}"

# Multicast SSDP details for UPnP device discovery on the local network
SSDP_PORT = 1900
SSDP_ADDR = "239.255.255.250"

# Unique Device Name (UDN) UUID - generating a unique one is recommended
UUID = "uuid:c9a28b74-3e1d-4589-a6f2-890123456789"

# ==============================================================================
# --- UPnP XML definitions (device description & SCPD schemas) ---
# ==============================================================================
# primary device description (desc.xml), generated dynamically
def get_device_desc():
    dash_enabled = config.getboolean('dashboard', 'enable_dashboard', fallback=True)
    dash_ip = config.get('dashboard', 'dashboard_ip', fallback='0.0.0.0')
    dash_port = config.getint('dashboard', 'dashboard_port', fallback=5001)
    dash_ip = dash_ip if dash_ip != '0.0.0.0' else utils.get_local_ip()
    presentation_url = f"http://{dash_ip}:{dash_port}/" if dash_enabled else ""
    
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
        print(f"[DLNA] Error triggering sync: {e}")

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
        # priority: item mode -> folder mode -> default (bounce)
        mode = node.get('mode') or parent_mode or 'bounce'
        target_url = node.get('url', '')

        if mode == 'direct':
            proxy_url = target_url
        else:
            safe_target = urllib.parse.quote(target_url, safe='')
            proxy_url = f"{proxy_base}/{mode}/{safe_target}"

        if mode == 'hit':
            upnp_class = "object.item.audioItem.musicTrack"
            proto = "http-get:*:audio/mpeg:DLNA.ORG_PN=MP3;DLNA.ORG_OP=01;DLNA.ORG_CI=0;DLNA.ORG_FLAGS=01700000000000000000000000000000"
        
        else:
            mime = node.get('mime_type')
            
            if not mime:
                url_low = target_url.lower()
                if any(x in url_low for x in ['mp3', 'm4a', 'aac', '.wav', 'radio', 'stream', 'listen']):
                    mime = "audio/mpeg"
                else:
                    mime = "video/mp4"

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
# --- HTTP request handler (UPnP / DLNA endpoints) ---
# ==============================================================================
class DLNAHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args): 
        pass 

    def do_GET(self):
        """Handle UPnP GET requests."""
        
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
                print(f"[DLNA] Ignored sync request for disabled playlist: {target_pl}")
                self.send_response(403)
                self.end_headers()
                return

            print(f"[DLNA] Refresh stream triggered for '{target_pl or 'all'}'. Launching background sync...")
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
                # Fallback empty response if asset file is missing so the client doesn't hang
                self.send_response(204)
                self.end_headers()

        # --- server icon ---
        elif self.path == "/icon.png" and ICON_PATH and os.path.exists(ICON_PATH):
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.end_headers()
            with open(ICON_PATH, 'rb') as img:
                self.wfile.write(img.read())

        # --- description ---
        elif self.path == "/desc.xml":
            desc_content = get_device_desc().encode('utf-8')
            self.send_response(200)
            self.send_header("CONTENT-TYPE", "text/xml; charset=\"utf-8\"")
            self.send_header("CONTENT-LENGTH", str(len(desc_content)))
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
        """Handle UPnP SOAP POST control requests (Browse & GetProtocolInfo)."""
        
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

                print(f"[DLNA] Browse '{obj_id}' (Start: {start_idx}, Count: {req_count})")
                
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
                    # ID format: cpl_path:[RegistryName]:[0/1/2]
                    parts = obj_id.split(':', 2)
                    reg_name = urllib.parse.unquote(parts[1])
                    internal_path = parts[2] if len(parts) > 2 else ""
                    
                    # find matching file in registry
                    reg_entry = next((r for r in enabled_custom if r['name'] == reg_name), None)
                    if reg_entry:
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
                            remux_to_ts = config.get('proxy', 'remux_target_format', fallback='ts').strip().lower() == 'ts'
                            for idx, entry in enumerate(library[requested_folder]):
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

                # preform slicing, prepare response
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

# ==============================================================================
# --- discovery engines (SSDP beacon & active M-SEARCH listener) ---
# ==============================================================================

def run_ssdp_beacon():
    """Periodically broadcasts the 3 UPnP-required NOTIFY packets to the local network."""
    ssdp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    ssdp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, 'SO_REUSEPORT'):
        try:
            ssdp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except Exception:
            pass

    adv_ip = DLNA_IP if DLNA_IP != '0.0.0.0' else utils.get_local_ip()
    location = f"http://{adv_ip}:{DLNA_PORT}/desc.xml"

    targets = [
        ("upnp:rootdevice", f"{UUID}::upnp:rootdevice"),
        (UUID, UUID),
        ("urn:schemas-upnp-org:device:MediaServer:1", f"{UUID}::urn:schemas-upnp-org:device:MediaServer:1")
    ]

    while True:
        for nt, usn in targets:
            ssdp_packet = (
                f"NOTIFY * HTTP/1.1\r\n"
                f"HOST: {SSDP_ADDR}:{SSDP_PORT}\r\n"
                f"NT: {nt}\r\n"
                f"NTS: ssdp:alive\r\n"
                f"USN: {usn}\r\n"
                f"LOCATION: {location}\r\n"
                f"CACHE-CONTROL: max-age=1800\r\n"
                f"SERVER: {SERVER_STRING}\r\n\r\n"
            ).encode('utf-8')
            try:
                ssdp_sock.sendto(ssdp_packet, (SSDP_ADDR, SSDP_PORT))
            except Exception:
                pass
        time.sleep(20)

def run_ssdp_listener():
    """Listens for active M-SEARCH queries from clients and responds with matching ST headers."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, 'SO_REUSEPORT'):
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except Exception:
            pass

    sock.bind(('', SSDP_PORT))
    
    # join multicast group on all interfaces (0.0.0.0 is critical for Linux/Raspberry Pi)
    mreq = socket.inet_aton(SSDP_ADDR) + socket.inet_aton('0.0.0.0')
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

    actual_ip = DLNA_IP if DLNA_IP != '0.0.0.0' else utils.get_local_ip()
    location = f"http://{actual_ip}:{DLNA_PORT}/desc.xml"

    while True:
        try:
            data, addr = sock.recvfrom(2048)
            message = data.decode('utf-8', errors='ignore')
            
            if "M-SEARCH" in message:
                msg_lower = message.lower()
                
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

                for st_val, usn_val in responses:
                    response = (
                        f"HTTP/1.1 200 OK\r\n"
                        f"CACHE-CONTROL: max-age=1800\r\n"
                        f"DATE: {time.strftime('%a, %d %b %Y %H:%M:%S GMT', time.gmtime())}\r\n"
                        f"EXT:\r\n"
                        f"LOCATION: {location}\r\n"
                        f"SERVER: {SERVER_STRING}\r\n"
                        f"ST: {st_val}\r\n"
                        f"USN: {usn_val}\r\n"
                        f"CONTENT-LENGTH: 0\r\n"
                        f"\r\n"
                    )
                    sock.sendto(response.encode('utf-8'), addr)
        except Exception:
            time.sleep(0.5)

# ==============================================================================
# --- server lifecycle initialization ---
# ==============================================================================

def start_dlna():
    """Unified entrypoint launching discovery threads and blocking HTTP server."""
    # fire background SSDP NOTIFY beacon broadcast loop
    threading.Thread(target=run_ssdp_beacon, daemon=True).start()
    
    # fire active SSDP M-SEARCH multicast listener loop
    threading.Thread(target=run_ssdp_listener, daemon=True).start()
    
    # start main blocking HTTP server loop for DLNA requests
    server = HTTPServer((DLNA_IP, DLNA_PORT), DLNAHandler)
    server.serve_forever()

if __name__ == "__main__":
    start_dlna()
