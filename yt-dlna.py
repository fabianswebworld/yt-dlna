# ==============================================================================
# yt-dlna: yt-dlna.py
# Lightweight media gateway, proxying streaming playlists to DLNA/UPnP clients
#
# Copyright (c) 2026 Fabian Schneider (@fabianswebworld) and contributors
# Licensed under the MIT License - see LICENSE file for details.
# SPDX-License-Identifier: MIT
# ==============================================================================

import os
import sys

# add src directory to sys.path so worker modules import seamlessly
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(SCRIPT_DIR, 'src')
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

import time
import argparse
import utils

_LOG_SRC = "daemon"

def sync_loop():
    import sync

    time.sleep(5)
    utils.log(_LOG_SRC, "Sync scheduler initialized.", "scheduler")

    while True:
        if sync.is_busy():
            time.sleep(30)
            continue

        config = utils.load_config()
        if not config.getboolean('sync', 'enable_sync', fallback=True):
            time.sleep(60)
            continue

        library = utils.get_library() 
        playlists = utils.get_playlists_config()
        now = time.time()

        for pl in playlists:
            if not pl.get('enabled', True):
                continue

            title = pl['title']
            is_sync_enabled = pl.get('enable_sync', True)
            if not is_sync_enabled: continue

            pl_data = library.get(title, {})
            last_sync = pl_data.get('last_sync', 0) if isinstance(pl_data, dict) else 0
            interval = int(pl.get('sync_interval', config.getint('sync', 'sync_interval', fallback=3600)))

            if now - last_sync >= interval:
                utils.log(_LOG_SRC, f"Starting scheduled playlist sync for '{title}'.", "scheduler")
                try:
                    sync.run_sync(targets=[title])
                except Exception as e:
                    utils.log(_LOG_SRC, f"Sync loop encountered error: {e}", "scheduler", level=1, type='E')
                time.sleep(5)

        time.sleep(60)

def main():
    parser = argparse.ArgumentParser(
        description="yt-dlna: Lightweight media gateway, proxying streaming playlists to DLNA/UPnP clients",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""examples:
  yt-dlna --serve                launch background daemons and sync scheduler
  yt-dlna --serve --sync         perform initial sync for all playlists, then serve
  yt-dlna --serve --verbosity 4  launch background daemons with verbose logging
  yt-dlna --sync                 perform immediate sync for all playlists and exit
  yt-dlna --sync "Watch Later"   sync a specific playlist by name and exit
  yt-dlna --sync youtube ard     sync all playlists for specific services and exit
  yt-dlna --purge-all            purge both CDN cache and playlist library
  yt-dlna --version              display version information and exit
  yt-dlna --help                 show this help message and exit
"""
    )

    parser.add_argument('--verbosity', metavar='LEVEL', type=int, choices=range(0, 6), help='override config verbosity level (0-5)')
    parser.add_argument('-?', '--usage', action='help', help=argparse.SUPPRESS)
    parser.add_argument('--version', '-v', action='version', version=f"yt-dlna v{utils.__version__}")
    
    parser.add_argument('--sync', nargs='*', metavar='TARGET', help='perform immediate sync for all or specific playlists/services')
    parser.add_argument('--serve', action='store_true', help='launch background proxy, DLNA server, and sync scheduler')
    parser.add_argument('--purge-cache', action='store_true', help='purge all cached CDN streaming URLs (urlcache.json)')
    parser.add_argument('--purge-library', action='store_true', help='purge all indexed playlist metadata (playlists.json)')
    parser.add_argument('--purge-all', action='store_true', help='purge both CDN URL cache and playlist library')
    
    args = parser.parse_args()

    has_action = (args.serve or args.sync is not None or args.purge_cache or args.purge_library or args.purge_all)

    # print help if neither a sync, serve or purge argument are given
    if not has_action:
        parser.print_help()
        sys.exit(1)

    if args.verbosity is not None:
        utils.set_verbosity(args.verbosity)

    # first-run auto-initialization of configuration file
    if (args.serve or args.sync is not None) and not os.path.exists(utils.CONFIG_FILE):
        example_file = utils.CONFIG_FILE + '.example'
        if os.path.exists(example_file):
            try:
                with open(example_file, 'rb') as f_src:
                    content = f_src.read()
                with open(utils.CONFIG_FILE, 'wb') as f_dst:
                    f_dst.write(content)
                utils.log(_LOG_SRC, "Created new config file 'yt-dlna.conf' from 'yt-dlna.conf.example'.", "init")
            except Exception as e:
                utils.log(_LOG_SRC, f"Failed to initialize config from example file: {e}", "init", level=1, type='E')
        else:
            utils.log(_LOG_SRC, "'yt-dlna.conf' and 'yt-dlna.conf.example' are missing.", "init", level=2, type='W')
            utils.log(_LOG_SRC, "Using defaults, but functionality is likely limited.", "init", level=2, type='W')

    # execute purge actions
    if args.purge_all or args.purge_cache:
        print("[yt-dlna] Purging CDN URL cache...")
        utils.purge_cdn_cache()

    if args.purge_all or args.purge_library:
        print("[yt-dlna] Purging playlist library...")
        utils.purge_playlist_library()

    # exit after purge if no server or sync arguments given
    if not args.serve and args.sync is None:
        sys.exit(0)

    # settings auto-migration
    if args.serve or args.sync is not None:
        config = utils.load_config()

        # parse config schema version (major, minor)
        raw_ver = config.get('general', 'config_version', fallback='').strip().lstrip('v')
        try:
            cfg_version = tuple(int(x) for x in raw_ver.split('.')[:2])
        except Exception:
            cfg_version = (0, 0)
        old_cfg_version = cfg_version

        # v1.3: migrate old format_dash selectors and add auto service
        if cfg_version < (1, 3):
            old_dash = "(137/136/135)+140"
            new_dash = "bv*[vcodec^=avc][height<=1080]+ba[acodec^=mp4a]"
            dash_replaced = False
            for section in config.sections():
                if section == 'services' or section.startswith('services:'):
                    if config.get(section, 'format_dash', fallback='').strip() == old_dash:
                        utils.update_config_single_key(section, 'format_dash', new_dash)
                        dash_replaced = True

            if dash_replaced:
                utils.log(_LOG_SRC, "Upgrade notice: Replaced old format_dash selectors with new selector.", "init", level=2, type='S')
                config = utils.load_config(force_reload=True)

            if not config.has_section('services:auto'):
                utils.update_config_single_key('services:auto', 'format_dash', new_dash)
                utils.update_config_single_key('services:auto', 'cache_ttl', '18000')
                utils.update_config_single_key('services:auto', 'title_format', '{index}. {channel}: {title} ({duration})')

                existing_services = [s.replace('services:', '') for s in config.sections() if s.startswith('services:')]
                reordered = ['auto'] + [s for s in existing_services if s != 'auto']
                utils.reorder_config_sections('services', reordered)

                utils.log(_LOG_SRC, "Upgrade notice: Created 'auto' service profile.", "init", level=2, type='S')
                config = utils.load_config(force_reload=True)

            if config.get('playlists', 'default_service', fallback='').strip().lower() == 'youtube':
                utils.update_config_single_key('playlists', 'default_service', 'auto')
                pinned_count = 0
                for sec in config.sections():
                    if sec.startswith('playlists:'):
                        if 'service' not in config[sec]:
                            utils.update_config_single_key(sec, 'service', 'youtube', add_to_top=True)
                            pinned_count += 1

                utils.log(_LOG_SRC, f"Upgrade notice: Changed default_service from 'youtube' to 'auto' and updated {pinned_count} playlist(s).", "init", level=2, type='S')
                config = utils.load_config(force_reload=True)

            utils.update_config_single_key('general', 'config_version', '1.3', add_to_top=True)
            cfg_version = (1, 3)

        if cfg_version > old_cfg_version:
            utils.log(_LOG_SRC, f"Upgrade notice: Settings migrated, config_version set to {cfg_version[0]}.{cfg_version[1]}.", "init", level=2, type='S')
            config = utils.load_config(force_reload=True)

    # execute immediate/startup sync if requested
    if args.sync is not None:
        import sync

        targets = args.sync
        if targets:
            print(f"[yt-dlna] Executing immediate target sync for: {', '.join(targets)}...")
        else:
            print("[yt-dlna] Executing immediate library sync for all playlists...")

        if args.serve:
            print("[yt-dlna] --serve: yt-dlna Daemon will start up after sync operation is finished.")

        sync.run_sync(targets=targets)
        
        # only exit if we are NOT also starting the server
        if not args.serve:
            sys.exit(0)

    if args.serve:
        import threading
        import proxy
        import dlna_server
        import dashboard

        print("==================================================")
        print(f"         Starting yt-dlna Daemon v{utils.__version__}       ")
        print("==================================================")

        config = utils.load_config()

        proxy_thread = threading.Thread(target=proxy.start_proxy, daemon=True)
        proxy_thread.start()
        utils.log(_LOG_SRC, "Proxy server thread active.", "init")

        dlna_thread = threading.Thread(target=dlna_server.start_dlna, daemon=True)
        dlna_thread.start()
        utils.log(_LOG_SRC, "UPnP/DLNA server active.", "init")

        if config.getboolean('dashboard', 'enable_dashboard', fallback=True):
            dash_port = config.getint('dashboard', 'dashboard_port', fallback=5001)
            web_thread = threading.Thread(target=dashboard.start_web_server, daemon=True)
            web_thread.start()
            utils.log(_LOG_SRC, f"Web UI administration dashboard active on port {dash_port}.", "init")
        else:
            utils.log(_LOG_SRC, "Web UI administration dashboard disabled in configuration.", "init")

        if config.getboolean('sync', 'enable_sync', fallback=True):
            sync_thread = threading.Thread(target=sync_loop, daemon=True)
            sync_thread.start()
            utils.log(_LOG_SRC, "Background scheduler thread active.", "init")
        else:
            utils.log(_LOG_SRC, "Scheduled sync disabled in configuration.", "init")

        utils.log(_LOG_SRC, "yt-dlna initialization complete. Server loop running...", level=0, type='S')

        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print(f"\n[daemon] Shutdown signal received. Terminating yt-dlna.")

if __name__ == '__main__':
    main()
