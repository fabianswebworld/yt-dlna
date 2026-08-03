# yt-dlna

**yt-dlna** is a lightweight online media gateway used for proxying streaming playlists to DLNA/UPnP clients.

It acts as a bridge between online video platforms (such as YouTube, ARD Mediathek, ZDF and others) and legacy DLNA/UPnP media renderers (like older Smart TVs, media players, or set-top boxes) that no longer have native streaming apps.

To accomplish the "heavy lifting", such as extracting playlists and CDN URLs (and actually for _all_ communication with the video platforms), it - of course - relies on ```yt-dlp``` (see [Prerequisites](#prerequisites) and [Legal Notices](#disclaimers-and-legal-notices)).

---

## Features

* **Smart TV gateway:** Stream online playlists directly to older Smart TVs (as well as new ones, of course!) via built-in DLNA/UPnP players without ads or DASH/HLS demuxing issues.
* **Instant start:** Proactively pre-caches live stream URLs in the background so video playback starts instantly on your TV.
* **Multi-service & multi-account support:** Proxy content from YouTube, ARD Mediathek, and many other platforms (called _services_ in **yt-dlna**), as long as they are supported by `yt-dlp`. Define custom service profiles with different cookie files (e.g. separate YouTube profiles for family members).
* **Cookie-free public playlists:** Access public or unlisted playlists anonymously without needing cookies.
* **Cookie-free playback:** As long as the videos themselves are publicly visible, no cookie is needed for playback; however, for private videos, you can optionally choose to use one.
* **Low hardware requirements:** Optimized specifically for older and low-powered single-board computers, performing well even on a hardware as old as a Raspberry Pi 1.
* **On-demand DLNA refresh:** Trigger a playlist resync directly from your TV menu using virtual `[Click to Refresh]` items.

---

## Prerequisites

* **OS:** Platform-independent; Linux (Raspberry Pi OS, Debian, Ubuntu, etc.) recommended
* **Python:** 3.10 or higher
* **Dependencies:** `yt-dlp`, `flask`, `requests`
* **Permissions:** `yt-dlna` needs to create a `data` folder inside its directory and write to it

---

## How it works

### Basic components and what they do

In short, there are three core components which go hand in hand to accomplish the task to present your online video playlists as DLNA views on your TV:

1. **Sync Engine (`sync.py`):** Periodically indexes configured playlists using `yt-dlp`, extracting raw metadata (titles, channels, durations) and pre-caching direct CDN URLs.
2. **DLNA Engine (`dlna_server.py`):** Broadcasts SSDP discovery beacons and serves a virtual folder tree to your TV via UPnP.
3. **Proxy Engine (`proxy.py`):** Receives play requests from your TV and instantly issues HTTP 302 redirects (or streams bytes) directly to the media CDN.

### Metadata and content data flow in detail

On a regular basis, yt-dlp will sync your favorite playlists from the video services to a local playlists.json file, and then present that file as virtual DLNA/UPnP "folders" to your Smart TV. The key point is that the entries themselves will not point to any external URL, but instead to virtual URLs provided by an internal proxy service, under which a specific video will always be available inside your local network. Here's how the metadata flow looks:

```text
[Streaming Service] ◄─── (yt-dlp) ── [yt-dlna UPnP server] ◄─ (UPnP) ── [Client]
(YouTube, ARD, etc.)   Get entries   (Raspberry Pi, Linux)              (TV)
```

The moment a client selects a video from a playlist, it will hit the corresponding proxy URL, which will then more-or-less immediately redirect it to the actual CDN URL. Here's what the content flow looks like (the numbers are the steps in which the actions are performed):

```text
[Streaming Service] ◄──── (yt-dlp) ──── [yt-dlna Proxy] ◄── (HTTP) ─── [Client]
(YouTube, ARD, etc.)    Get CDN URL  2  (Raspberry Pi, Linux)       1  (TV)
            ▲                                     │                    3 │
            │                                     │ 302 Redirect to      │
            │                                     │ CDN URL              │
            └─────────────────────────────────────┴──────────────────────┘
```
For older TVs that do not support HTTP redirects for UPnP media and/or do not resolve external hostnames, there's another mode available in the configuration which instead tunnels the actual data bytes for the video streams from the CDN to the client. Note that this will increase load on the system, but even this mode has been successfully tested on an old Raspberry Pi 1 even for Full HD videos (~ 3 Mbit/s). In that mode, the content flow looks like this:

```text
[Streaming Service] ◄──── (yt-dlp) ──── [yt-dlna Proxy] ◄── (HTTP) ─── [Client]
(YouTube, ARD, etc.)    Get CDN URL  2  (Raspberry Pi, Linux)       1  (TV)
            ▲                                   3 │
            │                                     │ Active tunneling of
            │                                     │ bytes through proxy
            └─────────────────────────────────────┘
```
In both modes, the proxy will optionally pre-fetch the actual CDN URLs proactively and cache them, so the user will not have to wait for ```yt-dlp``` to complete its job (which is especially slow on old hardware) on first hit of a new playlist item.

---

## Getting Started

1. **Install dependencies via `pip`:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Clone the Repository:**
   ```bash
   git clone https://github.com/fabianswebworld/yt-dlna.git
   cd yt-dlna
   ```

3. **Configure Your Playlists:**
   Copy the example configuration file and edit it to add your playlists, or for a first try, just uncomment one or more of the included example playlists and services (they're mostly German TV news shows, as I am from Germany... sorry for that local bias 😉):
   ```bash
   cp yt-dlna.conf.example yt-dlna.conf
   nano yt-dlna.conf
   ```

4. **Add Cookies (Optional for Private Playlists):**
   If you want to access private playlists like YouTube's "Watch Later" (`WL`):
   * Export your session cookies in Netscape format using a browser extension (e.g. *Get cookies.txt LOCALLY*).
   * Save the file to `data/cookies_yt.txt` (or the path defined in `yt-dlna.conf`).

> **Note on YouTube Cookies:**  
> YouTube cookies may expire periodically due to Google security policies. As a stable alternative for family sharing, you can create a **Public or Unlisted playlist** on YouTube. Unlisted playlists do not require cookie files and will not expire.

5. **Run an Initial Sync:**
   ```bash
   python3 yt-dlna.py --sync
   ```

6. **Start the Server:**
   ```bash
   python3 yt-dlna.py --serve
   ```
   Open your TV's Media player (sometimes called "DLNA" or "UPnP") — **yt-dlna Media Server** will appear in your device list!

> **Important Note:**  
> If your Smart TV refuses to play the videos on your first attempt, fear not, and first try to change the `mode` setting in the `[proxy]` section of `yt-dlna.conf` from `redirect` to `proxy`. This will increase CPU and network load on the system `yt-dlna` is running on (as the streams will have to physically go into and out of the system's network interface), but it improves the chance that it will work on your TV. Just try!

7. **Install as Service _(optional)_:**
   
   See [below](#running-as-a-systemd-service) for instructions on how to install **yt-dlna** as a service so it automatically runs as a background daemon.

---

## Configuration (`yt-dlna.conf`)

`yt-dlna.conf` is fully documented with inline comments. It is divided into four main sections:

* **`[proxy]`**: Controls HTTP bind IP/port, proxying mode (`redirect` vs `proxy`), and CDN URL caching parameters.
* **`[dlna]`**: Controls UPnP server name, bind parameters, and icon paths.
* **`[sync]`**: Controls automatic sync intervals and proactive CDN URL pre-caching.
* **`[yt-dlp]`**: Configures how to load, and where to find, `yt-dlp` (via import, or as an external binary)
* **`[services:...]`**: Configures individual streaming extractors, format selectors, title templates, and cookie paths.
* **`[playlists:...]`**: Defines the virtual DLNA folders served to your clients, item limits, sort criteria, and target URLs.

### Important things to note

Everything is heavily optimized for speed and low hardware requirements. For that reason, the decision which extractor to use for a certain video service is not done through the URL, inside ``yt-dlp``, which in that case would have to load all "Information Extractors" every time, but defined in the configuration file for each service.

Just like `yt-dlp`, `yt-dlna` has its roots in handling YouTube streams. That's why YouTube (i.e., services defined using the 'youtube' extractor) are handled a little bit different than the others: only for YouTube, you can define playlists with just their playlist id as `url` in the config; for all others, you need to specify the full web URL of the playlist.

Similarly, YouTube video URLs are treated differently by the proxy service: you can always access URLs like `http://yt-dlna-host:5000/play/youtube/{video_id}` from inside your network, just providing the YouTube video id.

For all other services than `youtube` (which is a hard-coded default even if nothing is configured in the configuration file), you will have to provide the full, escaped URL after the `/{service}/` part of the URL, e.g. `http://yt-dlna-host:5000/play/ard/https%3A%2F%2Fwww.ardmediathek.de%2Fvideo%2Fswr-aktuell-rheinland-pfalz%2Fsendung-19-45-uhr-vom-12-7-2026%2Fswr-rlp%2FY3JpZDovL3N3ci5kZS9hZXgvbzIzMzU4MDk`.

Additionally, here's some especially neat things about certain configuration combinations:

- If you create multiple services that all use the same extractor (e.g., 'youtube'), but different `cookie_path` options, you can use multiple accounts, e.g. multiple users' "Watch Later" playlists.
- If you do not specify any playlist and set `enable_sync = yes` in the `[sync]` section, you can effectively use `yt-dlna` as just a `yt-dlp`-powered stream proxy, enabling you to watch YouTube or other online videos on any device on your network by just opening e.g. `http://yt-dlna-host:5000/play/youtube/{video_id}` in the player or browser of your choice (it will take some seconds if that video has never been played before, though - but afterwards, it will be available until the CDN URL expires, which is usually 6 hours for YouTube).

---

## Usage & CLI Commands

```text
usage: yt-dlna.py [-h] [--version] (--sync [TARGET ...] | --serve)

yt-dlna: Lightweight media gateway, proxying streaming playlists to DLNA/UPnP clients

options:
  -h, --help           show this help message and exit
  --version, -v        show program's version number and exit
  --sync [TARGET ...]  perform immediate sync for all or specific playlists/services and exit
  --serve              launch background proxy, DLNA server, and sync scheduler

examples:
  yt-dlna --serve                         launch full background daemons and sync scheduler
  yt-dlna --sync                          perform immediate sync for all playlists and exit
  yt-dlna --sync "YouTube Watch Later"    sync a specific playlist by name and exit
  yt-dlna --sync youtube ard              sync all playlists for specific services and exit
  yt-dlna --version                       display version information and exit
  yt-dlna --help                          show this help message and exit
```

---

## Running as a systemd Service

To run **yt-dlna** automatically in the background at boot on Linux:

1. Create a service file `/etc/systemd/system/yt-dlna.service` (it is also included in the root of the repository); adapt the paths to suit your installation (the example shows a typical installation on Raspberry Pi, but maybe you want to e.g. use a different python3 binary):

   ```ini
   [Unit]
   Description=yt-dlna Media Server Daemon
   After=network.target
   
   [Service]
   Type=simple
   User=pi
   WorkingDirectory=/home/pi/yt-dlna
   ExecStart=/usr/bin/python3 /home/pi/yt-dlna/yt-dlna.py --serve
   Environment=PYTHONUNBUFFERED=1
   Restart=on-failure
   RestartSec=5
   StandardOutput=journal
   StandardError=journal
   
   [Install]
   WantedBy=multi-user.target
   ```
2. Enable and start the service:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable yt-dlna
   sudo systemctl start yt-dlna
   ```

3. View live logs via `journalctl`:
   ```bash
   journalctl -u yt-dlna -f
   ```

---

## Limitations and future considerations

Due to the nature of the UPnP/DLNA protocol and especially limitations of older clients (such as TVs), **yt-dlna**'s approach is limited to plain, self-contained (pre-muxed) files accessible via HTTP/HTTPS, using codecs supported by most devices. This basically means: MP4 files with H.264 video and AAC audio, which most streaming services still provide - but for some of them, and most importantly, for YouTube, this unfortunately means limited media quality. For YouTube, the only MP4 stream URLs provided by the CDN are 360p MP4 files, which makes watching much less enjoyable.

For many other services however, such as the German ARD Mediathek, KiKA, ZDF, and possibly others, decently high-quality (Full HD, 1080p) video streams are also provided as plain MP4 files via HTTPS, which is perfect for most DLNA clients.

Maybe, in a future release, a third proxy mode, `remux`, might be implemented which uses the high-quality separated audio/video streams (MPEG-DASH) and dynamically remuxes them to a self-contained MP4 stream on the fly using ``ffmpeg``. This, however, will definitely not work on low-powered devices such as a Raspberry Pi 1, which this project was originally aimed at.

Also, as this is merely a proof-of-concept piece of software, all configuration has to be done manually via a configuration file, there's no web interface (yet). Like the stream remuxing, this is on the "wish list" and might be implemented in the future - and, by the way, pull requests are welcome! 😉

Currently, this has been successfully tested against VLC Player and my 2023 Panasonic TV (TX-55MZT1506). More testing (and issue reports, or even pull requests for compatibility improvements) welcome!

---

## Disclaimers and Legal Notices

### Usage of yt-dlp by yt-dlna

This project (**yt-dlna**) is an experimental proof of concept developed solely for educational and personal interoperability research purposes. It functions as a local network coordination layer and does not directly interface with, scrape, or extract media from any third-party streaming platform. 

All external media resolution relies entirely on the third-party utility `yt-dlp`. Users are explicitly advised that utilizing automated extraction tools to stream or bypass platform native interfaces may conflict with YouTube's Terms of Service and End User License Agreements (EULA). 

The author of this software assumes absolutely no responsibility or liability for how users choose to deploy this script, any potential account restrictions, or violations of third-party platform policies. This software is provided "as-is," without warranty of any kind, and its installation and operation are entirely at the discretion and sole risk of the end-user.

### Trademark Disclaimer

DLNA® is a registered trademark of the Digital Living Network Alliance. "**yt-dlna**" is an independent, non-commercial open-source project. It is not affiliated with, endorsed by, or certified by DLNA or any of its member organizations.

This software uses open UPnP AV standards to stream media to compatible renderers. It does _not_ claim to be "DLNA-compliant"; however, it has been tested to work on several TV models.

---

## License

This project is licensed under the **MIT License** - see the [LICENSE](LICENSE) file for details.

---

## Contact & Support

For questions, suggestions, or bug reports regarding **yt-dlna**, please open a **GitHub Issue** or submit a **Pull Request**. 

Please note that this project is an experimental proof of concept, and I cannot provide individual technical support. In particular, please refrain from submitting inquiries about unexpected playlist sort orders or layout issues on specific streaming services, as these extraction behaviors are handled entirely upstream by `yt-dlp` and I cannot do anything about it. 😉

For general inquiries, feel free to visit my website at [fabianswebworld.de](https://www.fabianswebworld.de) (German) or check out my social media profiles.
