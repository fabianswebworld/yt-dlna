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

# Multicast SSDP details for UPnP device discovery on the local network
SSDP_PORT = 1900
SSDP_ADDR = "239.255.255.250"

# Unique Device Name (UDN) UUID - generating a unique one is recommended
UUID = "uuid:c9a28b74-3e1d-4589-a6f2-890123456789"

# ==============================================================================
# --- UPnP XML definitions (device description & SCPD schemas) ---
# ==============================================================================

# primary device description (desc.xml) advertises our server services to clients
DEVICE_DESC = f"""<?xml version="1.0"?>
<root xmlns="urn:schemas-upnp-org:device-1-0" xmlns:dlna="urn:schemas-dlna-org:device-1-0">
  <specVersion><major>1</major><minor>0</minor></specVersion>
  <device>
    <deviceType>urn:schemas-upnp-org:device:MediaServer:1</deviceType>
    <friendlyName>{FRIENDLY_NAME}</friendlyName>
    <manufacturer>fabianswebworld</manufacturer>
    <manufacturerURL>https://github.com/fabianswebworld/yt-dlna</manufacturerURL>
    <modelDescription>yt-dlna Media Server</modelDescription>
    <modelName>yt-dlna</modelName>
    <modelNumber>{utils.__version__}</modelNumber>
    <UDN>{UUID}</UDN>
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
CDS_XML = """<?xml version="1.0"?><scpd xmlns="urn:schemas-upnp-org:service-1-0"><specVersion><major>1</major><minor>0</minor></specVersion><actionList><action><name>Browse</name><argumentList><argument><name>ObjectID</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_ObjectID</relatedStateVariable></argument><argument><name>BrowseFlag</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_BrowseFlag</relatedStateVariable></argument><argument><name>Filter</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_Filter</relatedStateVariable></argument><argument><name>StartingIndex</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_Index</relatedStateVariable></argument><argument><name>RequestedCount</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_Count</relatedStateVariable></argument><argument><name>SortCriteria</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_SortCriteria</relatedStateVariable></argument><argument><name>Result</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_Result</relatedStateVariable></argument><argument><name>NumberReturned</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_Count</relatedStateVariable></argument><argument><name>TotalMatches</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_Count</relatedStateVariable></argument><argument><name>UpdateID</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_UpdateID</relatedStateVariable></argument></argumentList></action></actionList><serviceStateTable><stateVariable sendEvents="no"><name>A_ARG_TYPE_ObjectID</name><dataType>string</dataType></stateVariable><stateVariable sendEvents="no"><name>A_ARG_TYPE_BrowseFlag</name><dataType>string</dataType></stateVariable><stateVariable sendEvents="no"><name>A_ARG_TYPE_Filter</name><dataType>string</dataType></stateVariable><stateVariable sendEvents="no"><name>A_ARG_TYPE_Index</name><dataType>ui4</dataType></stateVariable><stateVariable sendEvents="no"><name>A_ARG_TYPE_Count</name><dataType>ui4</dataType></stateVariable><stateVariable sendEvents="no"><name>A_ARG_TYPE_SortCriteria</name><dataType>string</dataType></stateVariable><stateVariable sendEvents="no"><name>A_ARG_TYPE_Result</name><dataType>string</dataType></stateVariable><stateVariable sendEvents="no"><name>A_ARG_TYPE_UpdateID</name><dataType>ui4</dataType></stateVariable></serviceStateTable></scpd>"""

# Service Control Protocol Document (SCPD) for ConnectionManager
CM_XML = """<?xml version="1.0"?><scpd xmlns="urn:schemas-upnp-org:service-1-0"><specVersion><major>1</major><minor>0</minor></specVersion><actionList><action><name>GetProtocolInfo</name><argumentList><argument><name>Source</name><direction>out</direction><relatedStateVariable>SourceProtocolInfo</relatedStateVariable></argument><argument><name>Sink</name><direction>out</direction><relatedStateVariable>SinkProtocolInfo</relatedStateVariable></argument></argumentList></action></actionList><serviceStateTable><stateVariable sendEvents="yes"><name>SourceProtocolInfo</name><dataType>string</dataType></stateVariable><stateVariable sendEvents="yes"><name>SinkProtocolInfo</name><dataType>string</dataType></stateVariable></serviceStateTable></scpd>"""

def trigger_sync(playlist_name=None):
    """Helper function to safely run the sync script in the background when requested via DLNA."""
    try:
        threading.Thread(target=sync.run_sync, args=(playlist_name,), daemon=True).start()
    except Exception as e:
        print(f"[DLNA] Error triggering sync: {e}")

# ==============================================================================
# --- HTTP request handler (UPnP / DLNA endpoints) ---
# ==============================================================================
class DLNAHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args): 
        pass 

    def xml_escape(self, text):
        if not text:
            return ""
        return (str(text)
                .replace('&', '&amp;')
                .replace('<', '&lt;')
                .replace('>', '&gt;')
                .replace('"', '&quot;'))

    def do_GET(self):
        """Handle UPnP GET requests."""
        
        # --- virtual refresh trigger item
        if self.path.startswith("/virtual_refresh_stream"):
            # parse optional playlist query parameter for targeted folder refresh
            parsed_url = urllib.parse.urlparse(self.path)
            query_params = urllib.parse.parse_qs(parsed_url.query)
            target_pl = query_params.get('playlist', [None])[0]
            if target_pl:
                target_pl = urllib.parse.unquote(target_pl)
                
            print(f"[DLNA] Refresh stream triggered for '{target_pl or 'all'}'. Launching background sync...")
            trigger_sync(target_pl)
            
            dummy_path = os.path.join("assets", "dummy.m4a")
            if os.path.exists(dummy_path):
                self.send_response(200)
                self.send_header("Content-Type", "audio/mp4")
                self.send_header("Content-Length", str(os.path.getsize(dummy_path)))
                self.end_headers()
                with open(dummy_path, 'rb') as f:
                    self.wfile.write(f.read())
            else:
                # Fallback empty response if asset file is missing so the client doesn't hang
                self.send_response(204)
                self.end_headers()
        
        # --- server icon
        elif self.path == "/icon.png" and ICON_PATH and os.path.exists(ICON_PATH):
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.end_headers()
            with open(ICON_PATH, 'rb') as img:
                self.wfile.write(img.read())

        # --- description
        elif self.path == "/desc.xml":
            self.send_response(200)
            self.send_header("Content-Type", "text/xml; charset=utf-8")
            self.send_header("SERVER", f"Python/3.x UPnP/1.0 DLNADOC/1.50 yt-dlna/{utils.__version__}")
            self.end_headers()
            self.wfile.write(DEVICE_DESC.encode('utf-8'))

        # --- ContentDirectory SCPD XML (cds.xml)
        elif self.path == "/cds.xml":
            self.send_response(200)
            self.send_header("Content-Type", "text/xml; charset=utf-8")
            self.end_headers()
            self.wfile.write(CDS_XML.encode('utf-8'))

        # --- ConnectionManager SCPD XML (cm.xml)
        elif self.path == "/cm.xml":
            self.send_response(200)
            self.send_header("Content-Type", "text/xml; charset=utf-8")
            self.end_headers()
            self.wfile.write(CM_XML.encode('utf-8'))

    def do_POST(self):
        """Handle UPnP SOAP POST control requests (Browse & GetProtocolInfo)."""
        
        # --- ConnectionManager control endpoint (/cm_ctl)
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
            self.send_header("Content-Type", 'text/xml; charset="utf-8"')
            self.send_header("Content-Length", str(len(response_bytes)))
            self.send_header("EXT", "")
            self.send_header("SERVER", f"Python/3.x UPnP/1.0 DLNADOC/1.50 yt-dlna/{utils.__version__}")
            self.end_headers()
            self.wfile.write(response_bytes)

        # --- ContentDirectory control endpoint (/ctl)
        elif self.path == "/ctl":
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length).decode('utf-8', errors='ignore')
            
            # Extract requested ObjectID from SOAP payload (0 = Root folder)
            obj_id = "0"
            match = re.search(r'<ObjectID[^>]*>(.*?)</ObjectID>', post_data, re.IGNORECASE)
            if match:
                obj_id = match.group(1).strip()

            print(f"[DLNA] Client requested Browse for ObjectID: '{obj_id}'")
            
            items_xml, count = "", 0

            # load playlists database from json
            library = {}
            if os.path.exists(utils.JSON_PATH):
                try:
                    with open(utils.JSON_PATH, "r", encoding="utf-8") as f:
                        library = json.load(f)
                except Exception as e:
                    print(f"[DLNA] Could not read playlist library: {e}")

            # --- client is browsing the root directory (ObjectID "0")
            if obj_id == "0":
                host_ip = self.headers.get('Host', f"{DLNA_IP}:{DLNA_PORT}")
                refresh_url = f"http://{host_ip}/virtual_refresh_stream"
                audio_proto = "http-get:*:audio/mp4:DLNA.ORG_OP=01;DLNA.ORG_CI=0;DLNA.ORG_FLAGS=01700000000000000000000000000000"

                # inject playable dummy item at top of root directory to trigger all-playlists resync
                items_xml += f"""
                <item id="virtual_refresh_item" parentID="0" restricted="1">
                    <dc:title>[Click to Refresh All Playlists]</dc:title>
                    <upnp:class>object.item.audioItem.musicTrack</upnp:class>
                    <res protocolInfo="{audio_proto}">{refresh_url}</res>
                </item>"""
                count += 1

                # render container folders for each playlist
                for folder_name in library.keys():
                    safe_id = urllib.parse.quote(folder_name)
                    items_xml += f"""
                    <container id="{safe_id}" parentID="0" restricted="1" searchable="0">
                        <dc:title>{self.xml_escape(folder_name)}</dc:title>
                        <upnp:class>object.container.storageFolder</upnp:class>
                    </container>"""
                    count += 1

            # --- client is selecting or browsing the root virtual refresh item directly
            elif obj_id == "virtual_refresh_item":
                print("[DLNA] Root refresh item selected by client. Launching background sync...")
                trigger_sync(None)
                
                host_ip = self.headers.get('Host', f"{DLNA_IP}:{DLNA_PORT}")
                refresh_url = f"http://{host_ip}/virtual_refresh_stream"
                audio_proto = "http-get:*:audio/mp4:DLNA.ORG_OP=01;DLNA.ORG_CI=0;DLNA.ORG_FLAGS=01700000000000000000000000000000"

                items_xml += f"""
                <item id="virtual_refresh_item" parentID="0" restricted="1">
                    <dc:title>[Click to Refresh All Playlists]</dc:title>
                    <upnp:class>object.item.audioItem.musicTrack</upnp:class>
                    <res protocolInfo="{audio_proto}">{refresh_url}</res>
                </item>"""
                count += 1

            # --- client is selecting or browsing a folder-specific virtual refresh item
            elif obj_id.startswith("virtual_refresh_folder_"):
                encoded_folder = obj_id.replace("virtual_refresh_folder_", "", 1)
                target_folder = urllib.parse.unquote(encoded_folder)
                
                print(f"[DLNA] Folder refresh item selected for '{target_folder}'. Launching targeted sync...")
                trigger_sync(target_folder)
                
                host_ip = self.headers.get('Host', f"{DLNA_IP}:{DLNA_PORT}")
                refresh_url = f"http://{host_ip}/virtual_refresh_stream?playlist={encoded_folder}"
                audio_proto = "http-get:*:audio/mp4:DLNA.ORG_OP=01;DLNA.ORG_CI=0;DLNA.ORG_FLAGS=01700000000000000000000000000000"

                items_xml += f"""
                <item id="{obj_id}" parentID="{encoded_folder}" restricted="1">
                    <dc:title>[Click to Refresh Playlist]</dc:title>
                    <upnp:class>object.item.audioItem.musicTrack</upnp:class>
                    <res protocolInfo="{audio_proto}">{refresh_url}</res>
                </item>"""
                count += 1

            # --- client is browsing inside a specific playlist folder
            else:
                requested_folder = urllib.parse.unquote(obj_id)
                safe_folder_id = urllib.parse.quote(requested_folder)
                
                host_ip = self.headers.get('Host', f"{DLNA_IP}:{DLNA_PORT}")
                folder_refresh_url = f"http://{host_ip}/virtual_refresh_stream?playlist={safe_folder_id}"
                audio_proto = "http-get:*:audio/mp4:DLNA.ORG_OP=01;DLNA.ORG_CI=0;DLNA.ORG_FLAGS=01700000000000000000000000000000"

                # inject targeted folder refresh trigger item at position #1 inside playlist folder
                items_xml += f"""
                <item id="virtual_refresh_folder_{safe_folder_id}" parentID="{obj_id}" restricted="1">
                    <dc:title>[Click to Refresh Playlist]</dc:title>
                    <upnp:class>object.item.audioItem.musicTrack</upnp:class>
                    <res protocolInfo="{audio_proto}">{folder_refresh_url}</res>
                </item>"""
                count += 1

                if requested_folder in library:
                    for enum_idx, entry in enumerate(library[requested_folder]):
                        v_id = entry.get('id')
                        raw_title = entry.get('title', 'Video')
                        raw_channel = entry.get('channel', '')
                        duration_sec = entry.get('duration')
                        service_name = entry.get('service', 'youtube')
                        proxy_url = entry.get('proxy_url')
                        is_error = entry.get('is_error', False)
                        
                        # handle error notification entries
                        if is_error:
                            items_xml += f"""
                            <item id="{v_id}" parentID="{obj_id}" restricted="1">
                                <dc:title>{self.xml_escape(raw_title)}</dc:title>
                                <upnp:class>object.item.audioItem.musicTrack</upnp:class>
                                <res protocolInfo="{audio_proto}">{proxy_url}</res>
                            </item>"""
                            count += 1
                            continue

                        srv_cfg = utils.get_service_config(service_name)
                        fmt_template = srv_cfg['title_format']

                        # determine playlist index (use pl_index from json if available, else loop counter)
                        idx_num = entry.get('pl_index')
                        if idx_num is None:
                            idx_num = enum_idx + 1
                        idx_str = f"{idx_num:02d}"

                        disp_duration = utils.format_duration_display(duration_sec)
                        dlna_duration = utils.format_duration_dlna(duration_sec)

                        # assemble display title dynamically using service title_format template
                        try:
                            formatted_title = fmt_template.format(
                                index=idx_str,
                                channel=raw_channel,
                                title=raw_title,
                                duration=disp_duration
                            ).replace('()', '').replace('[]', '').strip()
                        except Exception:
                            formatted_title = f"{idx_str}. {raw_title}"

                        title = self.xml_escape(formatted_title)
                        channel = self.xml_escape(raw_channel)
                        
                        # Strict DLNA protocol info string for H264 MP4 videos
                        dlna_proto = "http-get:*:video/mp4:DLNA.ORG_OP=01;DLNA.ORG_CI=0;DLNA.ORG_FLAGS=01700000000000000000000000000000"
                        
                        creator_tags = ""
                        if channel:
                            creator_tags = f"""
                            <dc:creator>{channel}</dc:creator>
                            <upnp:artist>{channel}</upnp:artist>
                            <upnp:author>{channel}</upnp:author>"""

                        duration_attr = f' duration="{dlna_duration}"' if dlna_duration else ""

                        items_xml += f"""
                        <item id="{v_id}" parentID="{obj_id}" restricted="1">
                            <dc:title>{title}</dc:title>{creator_tags}
                            <upnp:class>object.item.videoItem.movie</upnp:class>
                            <res protocolInfo="{dlna_proto}"{duration_attr}>{proxy_url}</res>
                        </item>"""
                        count += 1

            didl_xml = (
                f'<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
                f'xmlns:dc="http://purl.org/dc/elements/1.1/" '
                f'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/" '
                f'xmlns:dlna="urn:schemas-dlna-org:metadata-1-0/">'
                f'{items_xml}'
                f'</DIDL-Lite>'
            )
            
            escaped_didl = self.xml_escape(didl_xml)

            # construct SOAP browse response
            response_body = (
                f'<?xml version="1.0" encoding="utf-8"?>\n'
                f'<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">\n'
                f'  <s:Body>\n'
                f'    <u:BrowseResponse xmlns:u="urn:schemas-upnp-org:service:ContentDirectory:1">\n'
                f'      <Result>{escaped_didl}</Result>\n'
                f'      <NumberReturned>{count}</NumberReturned>\n'
                f'      <TotalMatches>{count}</TotalMatches>\n'
                f'      <UpdateID>1</UpdateID>\n'
                f'    </u:BrowseResponse>\n'
                f'  </s:Body>\n'
                f'</s:Envelope>'
            ).strip()

            response_bytes = response_body.encode('utf-8')
            
            self.send_response(200)
            self.send_header("Content-Type", 'text/xml; charset="utf-8"')
            self.send_header("Content-Length", str(len(response_bytes)))
            self.send_header("EXT", "")
            self.send_header("SERVER", f"Python/3.x UPnP/1.0 DLNADOC/1.50 yt-dlna/{utils.__version__}")
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
                f"SERVER: Python/3.x UPnP/1.0 DLNADOC/1.50 yt-dlna/{utils.__version__}\r\n\r\n"
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
                        f"SERVER: Python/3.x UPnP/1.0 DLNADOC/1.50 yt-dlna/{utils.__version__}\r\n"
                        f"ST: {st_val}\r\n"
                        f"USN: {usn_val}\r\n"
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
