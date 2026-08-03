# ==============================================================================
# yt-dlna: src/proxy.py
# Lightweight media gateway, proxying streaming playlists to DLNA/UPnP clients
#
# Copyright (c) 2026 Fabian Schneider (@fabianswebworld) and contributors
# Licensed under the MIT License - see LICENSE file for details.
# SPDX-License-Identifier: MIT
# ==============================================================================

import os
import urllib.parse
import requests
from flask import Flask, redirect, request, Response, stream_with_context
import utils

config = utils.load_config()
app = Flask(__name__)

# parse configuration route pattern using {service} and {video_id}
route_pattern = config.get('proxy', 'proxy_url_pattern', fallback='/play/{service}/{video_id}')
flask_route = route_pattern.replace('{service}', '<service>').replace('{video_id}', '<path:video_id>')

def resolve_cdn_url(video_id, service_name='youtube', min_remaining_ttl=0):
    """Helper to fetch URL from cache or extract fresh via yt-dlp."""
    video_id = urllib.parse.unquote(video_id)
    srv_cfg = utils.get_service_config(service_name)
    
    cached = utils.get_cached_url(video_id, service_name=service_name, min_remaining_ttl=min_remaining_ttl)
    if cached:
        print(f"[Proxy] Cache HIT for {service_name}:{video_id}")
        return cached, True

    print(f"[Proxy] Cache MISS for {service_name}:{video_id}. Resolving via yt-dlp...")
    
    # check extractor name for special handling of yt video ids
    if video_id.startswith('http://') or video_id.startswith('https://'):
        video_url = video_id
    elif srv_cfg['extractor'] == 'youtube':
        video_url = f"https://www.youtube.com/watch?v={video_id}"
    else:
        video_url = video_id

    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False,
        'ies': [srv_cfg['extractor']],
        'format': srv_cfg['format'],
        'skip_download': True,
        'youtube_include_dash_manifest': False,
        'youtube_include_hls_manifest': False,
    }

    info = utils.extract_youtube_info(
        video_url, 
        extra_opts=ydl_opts, 
        use_cookies=srv_cfg['use_cookies_for_playback'],
        cookie_path=srv_cfg['cookie_path']
    )
    cdn_url = info['url']
    
    utils.set_cached_url(video_id, cdn_url, service_name=service_name)
    return cdn_url, False

@app.route(flask_route)
def get_stream(service, video_id):
    mode = config.get('proxy', 'mode', fallback='redirect').strip().lower()

    try:
        cdn_url, is_cached = resolve_cdn_url(video_id, service_name=service)

        # MODE 'redirect': 302 Redirect (ultra lightweight for old hardware)
        if mode == 'redirect':
            print(f"[Proxy] 302 Redirecting {service}:{video_id} to CDN...")
            return redirect(cdn_url, code=302)

        # MODE 'proxy': active proxying (fallback for TVs that don't follow 302)
        print(f"[Proxy] Tunneling stream bytes for {service}:{video_id}...")
        
        req_headers = {}
        if 'Range' in request.headers:
            req_headers['Range'] = request.headers['Range']

        upstream_res = requests.get(cdn_url, headers=req_headers, stream=True)

        # If cached link expired early (403/410), purge cache & retry once with fresh URL
        if is_cached and upstream_res.status_code in (403, 404, 410):
            print(f"[Proxy] Cached URL expired for {service}:{video_id} ({upstream_res.status_code}). Refreshing...")
            utils.invalidate_cached_url(video_id)
            cdn_url, _ = resolve_cdn_url(video_id, service_name=service)
            upstream_res = requests.get(cdn_url, headers=req_headers, stream=True)

        response_headers = {
            'Content-Type': 'video/mp4',
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

    except Exception as e:
        print(f"[Proxy] Error ({mode} mode): {e}")
        return f"Proxy Error ({mode} mode): {str(e)}", 500

def start_proxy():
    bind_ip = config.get('proxy', 'proxy_ip', fallback='0.0.0.0')
    port = config.getint('proxy', 'proxy_port', fallback=5000)
    app.run(host=bind_ip, port=port, threaded=True)

if __name__ == '__main__':
    start_proxy()
