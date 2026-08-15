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
import threading
import argparse
import utils
import sync
import proxy
import dlna_server
import dashboard

def sync_loop():
    while True:
        config = utils.load_config()
        interval = config.getint('sync', 'sync_interval', fallback=3600)
        
        print("[Daemon] Starting scheduled playlist sync...")
        try:
            sync.run_sync()
        except Exception as e:
            print(f"[Daemon] Sync loop encountered error: {e}")
            
        time.sleep(interval)

def main():
    parser = argparse.ArgumentParser(
        description="yt-dlna: Lightweight media gateway, proxying streaming playlists to DLNA/UPnP clients",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""examples:
  yt-dlna --serve                         launch full background daemons and sync scheduler
  yt-dlna --sync                          perform immediate sync for all playlists and exit
  yt-dlna --sync "YouTube Watch Later"    sync a specific playlist by name and exit
  yt-dlna --sync youtube ard              sync all playlists for specific services and exit
  yt-dlna --version                       display version information and exit
  yt-dlna --help                          show this help message and exit
"""
    )

    parser.add_argument('-?', '--usage', action='help', help=argparse.SUPPRESS)
    parser.add_argument('--version', '-v', action='version', version=f"yt-dlna v{utils.__version__}")
    
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        '--sync', 
        nargs='*', 
        metavar='TARGET', 
        help='perform immediate sync for all or specific playlists/services and exit'
    )
    group.add_argument(
        '--serve', 
        action='store_true', 
        help='launch background proxy, DLNA server, and sync scheduler'
    )
    
    args = parser.parse_args()

    # first-run auto-initialization of configuration file
    if (args.serve or args.sync is not None) and not os.path.exists(utils.CONFIG_FILE):
        example_file = utils.CONFIG_FILE + '.example'
        if os.path.exists(example_file):
            try:
                with open(example_file, 'rb') as f_src:
                    content = f_src.read()
                with open(utils.CONFIG_FILE, 'wb') as f_dst:
                    f_dst.write(content)
                print("[Daemon] Created new configuration file yt-dlna.conf from yt-dlna.conf.example.")
            except Exception as e:
                print(f"[Daemon] !! Failed to initialize config from example file: {e}")
        else:
            print("[Daemon] !! yt-dlna.conf not found, and yt-dlna.conf.example is missing.")
            print("[Daemon] !! Using defaults, but functionality is likely to be limited.")

    if args.sync is not None:
        targets = args.sync
        if targets:
            print(f"[CLI] Executing immediate target sync for: {', '.join(targets)}...")
        else:
            print("[CLI] Executing immediate library sync for all playlists...")
            
        sync.run_sync(targets=targets)
        sys.exit(0)

    if args.serve:
        print("==================================================")
        print(f"         Starting yt-dlna Daemon v{utils.__version__}       ")
        print("==================================================")

        config = utils.load_config()

        proxy_thread = threading.Thread(target=proxy.start_proxy, daemon=True)
        proxy_thread.start()
        print("[Daemon] Proxy server thread active.")

        dlna_thread = threading.Thread(target=dlna_server.start_dlna, daemon=True)
        dlna_thread.start()
        print("[Daemon] UPnP/DLNA server active.")

        if config.getboolean('dashboard', 'enable_dashboard', fallback=True):
            dash_port = config.getint('dashboard', 'dashboard_port', fallback=5001)
            web_thread = threading.Thread(target=dashboard.start_web_server, daemon=True)
            web_thread.start()
            print(f"[Daemon] Web UI administration dashboard active on port {dash_port}.")
        else:
            print("[Daemon] Web UI administration dashboard disabled in configuration.")

        if config.getboolean('sync', 'enable_sync', fallback=True):
            sync_thread = threading.Thread(target=sync_loop, daemon=True)
            sync_thread.start()
            print("[Daemon] Background scheduler thread armed.")
        else:
            print("[Daemon] Scheduled sync disabled in configuration.")

        print("[Daemon] yt-dlna initialization complete. Server loop running...")

        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n[Daemon] Shutdown signal received. Terminating yt-dlna.")

if __name__ == '__main__':
    main()
