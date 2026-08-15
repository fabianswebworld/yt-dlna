/* ==============================================================================
 * yt-dlna: assets/html/script.js
 * JS REST API interactions and UI functions for yt-dlna
 * ============================================================================== */

let parsedConfigData = {};
let pendingDeletion = { type: null, id: null };
let initialSyncSchedulerState = null;
let playlistCounts = {};
let draggedElement = null;

document.addEventListener('DOMContentLoaded', () => {
    initTheme();
    
    const themeBtn = document.getElementById('btn-theme-toggle');
    if (themeBtn) {
        themeBtn.addEventListener('click', toggleTheme);
    }

    const isDashboard = document.querySelector('.nav-tabs');

    if (isDashboard) {
        initDashboard();
    }

    refreshUI(true);
    setInterval(() => {
        refreshUI(false); 
    }, 10000);
});

function initDashboard() {
    initTabs();
    fetchStatus();
    fetchParsedConfig();
    fetchConfig();
    fetchCustomPlaylists();

    document.getElementById('btn-sync-all').addEventListener('click', () => triggerSync(null));
    document.getElementById('btn-reload-config').addEventListener('click', reloadConfig);
    document.getElementById('btn-save-config').addEventListener('click', saveRawConfig);
    document.getElementById('settings-form').addEventListener('submit', saveParsedSettings);

    document.querySelectorAll('.sub-tab-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            switchSubTab(btn.getAttribute('data-subtab'));
        });
    });

    const playlistModal = document.getElementById('modal-playlist');
    if (playlistModal) {
        document.getElementById('btn-add-playlist-modal').addEventListener('click', () => playlistModal.classList.remove('hidden'));
        document.getElementById('btn-cancel-playlist').addEventListener('click', () => playlistModal.classList.add('hidden'));
        document.getElementById('add-playlist-form').addEventListener('submit', handleAddPlaylist);
    }

    const customModal = document.getElementById('modal-add-custom');
    if (customModal) {
        document.getElementById('btn-show-add-custom-modal').addEventListener('click', () => customModal.classList.remove('hidden'));
        document.getElementById('btn-cancel-custom-modal').addEventListener('click', () => customModal.classList.add('hidden'));
        document.getElementById('add-custom-playlist-form').addEventListener('submit', handleAddCustomPlaylist);
    }

    const restartModal = document.getElementById('modal-restart');
    if (restartModal) {
        document.getElementById('btn-show-restart-modal').addEventListener('click', () => restartModal.classList.remove('hidden'));
        document.getElementById('btn-cancel-restart').addEventListener('click', () => restartModal.classList.add('hidden'));
        document.getElementById('btn-confirm-restart').addEventListener('click', restartDaemon);
    }

    const serviceModal = document.getElementById('modal-service');
    if (serviceModal) {
        document.getElementById('btn-add-service-modal').addEventListener('click', () => serviceModal.classList.remove('hidden'));
        document.getElementById('btn-cancel-service').addEventListener('click', () => serviceModal.classList.add('hidden'));
        document.getElementById('add-service-form').addEventListener('submit', handleAddService);
    }

    const deleteModal = document.getElementById('modal-delete');
    if (deleteModal) {
        document.getElementById('btn-cancel-delete').addEventListener('click', () => deleteModal.classList.add('hidden'));
        document.getElementById('btn-confirm-delete').addEventListener('click', confirmDeletion);
    }

    const alertModal = document.getElementById('modal-alert');
    if (alertModal) {
        document.getElementById('btn-close-alert').addEventListener('click', () => alertModal.classList.add('hidden'));
    }

    const cookieModal = document.getElementById('modal-cookie');
    if (cookieModal) {
        document.getElementById('btn-cancel-cookie').addEventListener('click', () => cookieModal.classList.add('hidden'));
        document.getElementById('cookie-import-form').addEventListener('submit', handleCookieImportSubmit);
    }

    document.querySelectorAll('.sub-tab-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            switchSubTab(btn.getAttribute('data-subtab'));
        });
    });

    const refreshBtn = document.getElementById('btn-refresh-status');
    if (refreshBtn) {
        refreshBtn.addEventListener('click', async () => {
            refreshBtn.classList.add('spinning');
            await refreshUI(false);
            setTimeout(() => refreshBtn.classList.remove('spinning'), 2000);
    });
}

}

function initTheme() {
    const savedTheme = localStorage.getItem('yt-dlna-theme') || 'dark';
    document.documentElement.setAttribute('data-theme', savedTheme);
    updateThemeButtonText(savedTheme);
}

function toggleTheme() {
    const currentTheme = document.documentElement.getAttribute('data-theme') || 'dark';
    const newTheme = currentTheme === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', newTheme);
    localStorage.setItem('yt-dlna-theme', newTheme);
    updateThemeButtonText(newTheme);
}

function updateThemeButtonText(theme) {
    const btn = document.getElementById('btn-theme-toggle');
    btn.textContent = theme === 'dark' ? '☀️ Light Mode' : '🌙 Dark Mode';
}

function initTabs() {
    const tabBtns = document.querySelectorAll('.tab-btn');
    
    const routeMap = {
        '/': { tab: 'tab-status' },
        '/playlists': { tab: 'tab-playlists', sub: 'online' },
        '/playlists/online': { tab: 'tab-playlists', sub: 'online' },
        '/playlists/custom': { tab: 'tab-playlists', sub: 'custom' },
        '/services': { tab: 'tab-services' },
        '/settings': { tab: 'tab-settings' },
        '/raw-config': { tab: 'tab-raw' }
    };

    const switchTab = (path, updateHistory = true) => {
        const route = routeMap[path] || routeMap['/'];
        const tabId = route.tab;

        document.querySelectorAll('.tab-btn, .tab-content').forEach(el => el.classList.remove('active'));

        // main tab
        const activeBtn = document.querySelector(`.tab-btn[data-tab="${tabId}"]`);
        if (activeBtn) activeBtn.classList.add('active');
        const activeContent = document.getElementById(tabId);
        if (activeContent) activeContent.classList.add('active');

        // sub-tabs for playlist tab
        if (route.sub) {
            switchSubTab(route.sub, false); // Don't update history again
        }

        // update browser history
        if (updateHistory) {
            window.history.pushState(null, null, path);
        }
    };

    tabBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            const tabId = btn.getAttribute('data-tab');
            // Find the first matching path for this tabId
            const path = Object.keys(routeMap).find(p => routeMap[p].tab === tabId);
            switchTab(path);
        });
    });

    switchTab(window.location.pathname, false);

    // handle browser back/forward nav
    window.addEventListener('popstate', () => {
        switchTab(window.location.pathname, false);
    });
}

function switchSubTab(subId, updateHistory = true) {
    document.querySelectorAll('.sub-tab-btn').forEach(btn => {
        btn.classList.toggle('active', btn.getAttribute('data-subtab') === subId);
    });

    document.querySelectorAll('.sub-tab-pane').forEach(pane => {
        const isActive = (pane.id === `subtab-${subId}`);
        pane.classList.toggle('active', isActive);
        pane.style.display = isActive ? 'block' : 'none';
    });

    if (updateHistory) {
        window.history.pushState(null, null, `/playlists/${subId}`);
    }
}

function showToast(message, isError = false) {
    const toast = document.getElementById('toast');
    if (!toast) return;

    if (toast.timer) clearTimeout(toast.timeout);
    if (toast.exitTimer) clearTimeout(toast.exitTimer);

    toast.textContent = message;
    toast.className = `toast ${isError ? 'error' : 'success'}`;

    toast.classList.remove('hidden', 'visible', 'exit');
    
    setTimeout(() => {
        toast.classList.add('visible');
    }, 10);

    toast.timeout = setTimeout(() => {
        toast.classList.remove('visible');
        toast.classList.add('exit');
        toast.exitTimer = setTimeout(() => {
            toast.classList.add('hidden');
            toast.classList.remove('exit');
        }, 400);
    }, 4000);
}

function updatePlaylistCardCounts() {
    const countSpans = document.querySelectorAll('.playlist-count[data-pl-title]');
    countSpans.forEach(span => {
        const title = span.getAttribute('data-pl-title');
        if (playlistCounts[title] !== undefined) {
            span.textContent = `(${playlistCounts[title]} items)`;
        }
    });
}

function showAlertModal(title, message) {
    document.getElementById('modal-alert-title').textContent = title;
    document.getElementById('modal-alert-desc').innerHTML = message;
    document.getElementById('modal-alert').classList.remove('hidden');
}

function markCardDirty(cardElement) {
    const saveBtn = cardElement.querySelector('.btn-save-card');
    if (saveBtn) {
        saveBtn.disabled = false;
    }
}

async function fetchStatus() {
    const statusEl = document.getElementById('server-status');
    if (!statusEl) return;

    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 3000);

    try {
        const res = await fetch('/api/status', { signal: controller.signal });
        clearTimeout(timeoutId);
        
        const data = await res.json();

        document.getElementById('version-badge').textContent = `v${data.version}`;
        document.getElementById('server-status').textContent = data.status;
        document.getElementById('local-ip').textContent = data.local_ip;
        document.getElementById('dlna-port').textContent = data.dlna_port;
        document.getElementById('proxy-port').textContent = data.proxy_port;
        document.getElementById('dashboard-port').textContent = data.dashboard_port;
        document.getElementById('cache-count').textContent = data.cache_entries;

        const onlineCount = (data.playlists && Array.isArray(data.playlists)) ? data.playlists.length : 0;
        document.getElementById('stat-online-playlists').textContent = onlineCount;

        let totalSum = 0;
        if (data.playlists) {
            data.playlists.forEach(pl => {
                playlistCounts[pl.title] = pl.count;
                totalSum += pl.count;
            });
        }
        
        const totalTile = document.getElementById('stat-indexed-items');
        if (totalTile) totalTile.textContent = totalSum;

        updatePlaylistCardCounts();

        if (data.stats) {
            document.getElementById('stat-total').textContent = data.stats.total_served || 0;
            document.getElementById('stat-redirects').textContent = data.stats.redirects || 0;
            document.getElementById('stat-proxied').textContent = data.stats.proxied || 0;
            document.getElementById('stat-remuxed').textContent = data.stats.remuxed || 0;
        }

        statusEl.textContent = 'Online';
        statusEl.className = 'value success';

    } catch (err) {
        console.error("Status fetch failed", err);
        statusEl.textContent = 'Offline';
        statusEl.className = 'value error';
    }
}

async function refreshUI(includeConfig = false) {
    await fetchStatus();

    if (includeConfig) {
        await fetchParsedConfig();
        await fetchCustomPlaylists();
        await fetchConfig();
    }
}

async function getSingleConfig(section, key) {
    try {
        const url = `/api/config/single?section=${encodeURIComponent(section)}&key=${encodeURIComponent(key)}`;
        const res = await fetch(url);
        const data = await res.json();
        return data;
    } catch (err) {
        console.error("Error fetching single config:", err);
        return null;
    }
}

async function setSingleConfig(section, key, value) {
    return await fetch('/api/config/single', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ section, key, value })
    });
}

async function togglePlaylistEnabled(title, isEnabled) {
    const section = `playlists:${title}`;
    const value = isEnabled ? 'yes' : 'no';

    const card = document.getElementById(`playlist-card-${title}`);
    if (card) {
        card.classList.toggle('disabled-card', !isEnabled);
        const aView = card.querySelector('.btn-view');
        if (aView) {
            if (isEnabled) aView.classList.remove('disabled');
            else aView.classList.add('disabled');
        }
        const btnSync = card.querySelector('.btn-sync');
        const btnView = card.querySelector('.btn-view');
        if (btnView) {
            btnView.classList.toggle('disabled', !isEnabled);
            btnView.setAttribute('tabindex', isEnabled ? '0' : '-1');
        }        if (btnSync) btnSync.disabled = !isEnabled;
    }

    try {
        const res = await setSingleConfig(section, 'enabled', value);
        const data = await res.json();
        
        if (data.status === 'success') {
            showToast(`Playlist '${title}' has been ${isEnabled ? 'enabled' : 'disabled'}.`);
            if (parsedConfigData[section]) {
                parsedConfigData[section]['enabled'] = value;
            }
        } else {
            showToast(data.message, true);
        }
    } catch (err) {
        showToast('Error updating status', true);
    }
}

async function toggleCustomEnabled(name, isEnabled) {
    const section = `custom_playlists:${name}`;
    const value = isEnabled ? 'yes' : 'no';

    const card = document.getElementById(`custom-card-${name}`);
    if (card) {
        card.classList.toggle('disabled-card', !isEnabled);
        const viewLink = card.querySelector('.btn-view');
        if (viewLink) {
            viewLink.classList.toggle('disabled', !isEnabled);
            viewLink.setAttribute('tabindex', isEnabled ? '0' : '-1');
        }
    }

    try {
        const res = await setSingleConfig(section, 'enabled', value);
        const data = await res.json();
        if (data.status !== 'success') {
            showToast(data.message, true);
        }
    } catch (err) {
        showToast('Error updating status', true);
    }
}

function toggleOverride(checkbox) {
    const row = checkbox.closest('.field-row');
    const content = row.querySelector('.field-content');
    const input = content.querySelector('input, select, textarea');

    if (!input) return;

    row.classList.toggle('disabled', !checkbox.checked);
    input.disabled = !checkbox.checked;

    const rowButtons = content.querySelectorAll('button');
    rowButtons.forEach(btn => btn.disabled = !checkbox.checked);

    const fieldName = input.id.split('-')[1]; // e.g., 'cookiepath'
    const sectionType = input.id.startsWith('pl-') ? 'playlists' : 'services';

    if (!checkbox.checked) {
        const currentVal = (input.type === 'checkbox') ? (input.checked ? 'yes' : 'no') : input.value;
        input.setAttribute('data-stashed-val', currentVal);
        const keyMap = { 
            'limit': 'limit_items', 'sort': 'sort_by', 'srv': 'service', 'ttl': 'cache_ttl',
            'cookiespl': 'use_cookies_for_playlists', 'cookiespb': 'use_cookies_for_playback',
            'cookiepath': 'cookie_path', 'fmt': 'format', 'fmtdash': 'format_dash', 'titlefmt': 'title_format'
        };
        const globalVal = getGlobalValue(sectionType, keyMap[fieldName] || fieldName);
        if (input.type === 'checkbox') input.checked = (globalVal === 'yes');
        else input.value = globalVal;
    } else {
        const stashed = input.getAttribute('data-stashed-val');
        if (stashed !== null) {
            if (input.type === 'checkbox') input.checked = (stashed === 'yes');
            else input.value = stashed;
        } else if (fieldName === 'cookiepath') {
            const sName = input.id.split('-')[2]; 
            input.value = `data/cookies-${sName}.txt`;
        }
    }
    markCardDirty(checkbox.closest('.playlist-card, .service-card'));
}

function openCustomEditor(name) {
    window.location.href = `/playlists/custom/edit?name=${encodeURIComponent(name)}`;
}

function openDeleteModal(type, id) {
    pendingDeletion = { type, id };
    const titleEl = document.getElementById('modal-delete-title');
    const descEl = document.getElementById('modal-delete-desc');
    
    if (type === 'playlist') {
        titleEl.textContent = 'Delete playlist';
        descEl.textContent = `Are you sure you want to delete the playlist '${id}'?`;
    } else if (type === 'service') {
        const serviceName = id.replace('services:', '');
        titleEl.textContent = 'Delete service';
        descEl.textContent = `Are you sure you want to delete the service profile '${serviceName}'?`;
    }
    document.getElementById('modal-delete').classList.remove('hidden');
}

async function confirmDeletion() {
    const { type, id } = pendingDeletion;
    if (type === 'playlist') {
        await deletePlaylist(id);
    } else if (type === 'service') {
        await deleteService(id);
    } else if (type === 'custom') {
        await deleteCustomPlaylist(id);
    }
    document.getElementById('modal-delete').classList.add('hidden');
    pendingDeletion = { type: null, id: null };
}

async function saveCustomCard(oldName) {
    const card = document.getElementById(`custom-card-${oldName}`);
    const newName = document.getElementById(`cpl-rename-${oldName}`).value.trim();
    const newFile = document.getElementById(`cpl-path-${oldName}`).value.trim();
    
    const isEnabled = card.querySelector('input[type="checkbox"]').checked;
    const enabledVal = isEnabled ? 'yes' : 'no';

    // handle potential rename
    if (newName !== oldName) {
        const success = await handleRename('custom', oldName, newName);
        if (!success) { showToast("Rename failed. Check if name already exists, of new name is invalid or empty.", true); return; }
    }

    const section = `custom_playlists:${newName}`;

    await setSingleConfig(section, 'playlist_file', newFile);
    await setSingleConfig(section, 'enabled', enabledVal);
    
    showToast(`Saved changes for Custom Playlist '${newName}'.`);
    fetchCustomPlaylists();
}

async function handleAddCustomPlaylist(e) {
    e.preventDefault();
    const name = document.getElementById('cpl-name').value.trim();
    const file = document.getElementById('cpl-file').value.trim();

    const res = await fetch('/api/playlists/custom/add', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ name, file })
    });

    if (res.ok) {
        document.getElementById('modal-add-custom').classList.add('hidden');
        document.getElementById('add-custom-playlist-form').reset();
        fetchCustomPlaylists();
        showToast(`Created Custom Playlist '${name}'`);
    } else {
        const data = await res.json();
        showToast(data.message, true);
    }
}

function validateNoSpaces(value, fieldName) {
    if (/\s/.test(value)) {
        showAlertModal('Invalid input', `The ${fieldName} may not contain spaces.`);
        return false;
    }
    return true;
}

function togglePlaylistCard(element) {
    const card = element.closest('.playlist-card');
    if (card) { card.classList.toggle('collapsed'); }
}

function toggleServiceCard(element) {
    const card = element.closest('.service-card');
    if (card) { card.classList.toggle('collapsed'); }
}

async function fetchParsedConfig() {
    try {
        const res = await fetch('/api/config/parsed');
        const data = await res.json();
        if (data.status === 'success') {
            parsedConfigData = data.config;
            renderPlaylistsTab(parsedConfigData);
            renderServicesTab(parsedConfigData);
            renderSettingsForm(parsedConfigData);
            populateServiceDropdowns(parsedConfigData);
        }
    } catch (err) { console.error("Failed to fetch parsed config. Is the API offline? Error: ", err); }
}

function populateServiceDropdowns(configData) {
    const services = ['youtube'];
    Object.keys(configData).forEach(sec => {
        if (sec.startsWith('services:')) {
            services.push(sec.replace('services:', ''));
        }
    });

    const uniqueServices = [...new Set(services)];
    const optionsHtml = uniqueServices.map(s => `<option value="${s}">${s}</option>`).join('');
    const plDropdown = document.getElementById('pl-service');
    if (plDropdown) {
        plDropdown.innerHTML = optionsHtml;
    }
    const defaultSelect = document.getElementById('pl-def-service');
    if (defaultSelect) {
        defaultSelect.innerHTML = optionsHtml;
        const currentDefault = (configData.playlists && configData.playlists.default_service) || 'youtube';
        defaultSelect.value = currentDefault;
    }
}

function renderPlaylistsTab(configData) {
    const container = document.getElementById('playlists-list');
    if (!container) return;

    const playlistSections = Object.keys(configData).filter(s => s.startsWith('playlists:'));
    if (playlistSections.length === 0) {
        container.innerHTML = '<p class="placeholder">No active playlists configured.</p>';
        return;
    }
    const globalOpts = configData['playlists'] || {};
    const serviceOptions = ['youtube'];
    Object.keys(configData).forEach(sec => {
        if (sec.startsWith('services:')) serviceOptions.push(sec.replace('services:', ''));
    });
    const uniqueServices = [...new Set(serviceOptions)];

    let html = `
        <div class="playlist-card collapsed" id="playlists-defaults-card">
            <div class="playlist-card-header">
                <div class="service-title-group" onclick="togglePlaylistCard(this)">
                    <span class="chevron">▲</span>
                    <span class="playlist-title playlist-title-global">Default settings for all playlists</span>
                </div>
            </div>
            <div class="playlist-card-body">
                <div class="card-body-content">
                    <p class="card-desc">These are the default settings for playlists. Uncheck the override box in any individual playlist to inherit these settings.</p>
                    <div class="form-stack">
                        <div class="form-group">
                            <label>Default Service Profile</label>
                            <select id="pl-def-service" oninput="markCardDirty(this.closest('.playlist-card'))">
                                ${uniqueServices.map(s => `<option value="${s}" ${s === (globalOpts.default_service || 'youtube') ? 'selected' : ''}>${s}</option>`).join('')}
                            </select>
                        </div>
                        <div class="form-row">
                            <div class="form-group">
                                <label>Default Item Limit</label>
                                <input type="number" id="pl-def-limit" value="${globalOpts.limit_items || 0}" min="0" oninput="markCardDirty(this.closest('.playlist-card'))">
                            </div>
                            <div class="form-group">
                                <label>Default Sorting Order</label>
                                <select id="pl-def-sort" oninput="markCardDirty(this.closest('.playlist-card'))">
                                    <option value="none" ${globalOpts.sort_by === 'none' ? 'selected' : ''}>none</option>
                                    <option value="reverse" ${globalOpts.sort_by === 'reverse' ? 'selected' : ''}>reverse</option>
                                    <option value="date" ${globalOpts.sort_by === 'date' ? 'selected' : ''}>date</option>
                                    <option value="title" ${globalOpts.sort_by === 'title' ? 'selected' : ''}>title</option>
                                    <option value="duration" ${globalOpts.sort_by === 'duration' ? 'selected' : ''}>duration</option>
                                </select>
                            </div>
                        </div>
                    </div>
                    <div class="actions margin-top" style="justify-content: flex-end;">
                        <button class="btn primary btn-small btn-save-card" disabled onclick="savePlaylistDefaults()">Save Defaults</button>
                    </div>
                </div>
            </div>
        </div>
    `;

    html += playlistSections.map(sec => {
        const title = sec.replace('playlists:', '');
        const opts = configData[sec] || {};
        const isEnabled = opts.enabled !== 'no';
        const viewUrl = `/playlist/${encodeURIComponent(title)}`;
        const count = playlistCounts[title] !== undefined ? playlistCounts[title] : '?';

        const hasSrvOverride = isOverridden(sec, 'service');
        const hasLimitOverride = isOverridden(sec, 'limit_items');
        const hasSortOverride = isOverridden(sec, 'sort_by');

        return `
            <div class="playlist-card collapsed ${isEnabled ? '' : 'disabled-card'}" id="playlist-card-${escapeJs(title)}" ondragover="handleDragOver(event)">
                <div class="playlist-card-header">
                    <div class="playlist-title-group" onclick="togglePlaylistCard(this)">
                        <span class="chevron">▲</span>
                        <span class="playlist-title">${escapeHtml(title)}</span>
                        <span class="playlist-count" data-pl-title="${escapeHtml(title)}">(${count} items)</span>
                    </div>
                    <div class="header-controls">
                        <label class="switch switch-small" title="Enable/disable playlist">
                            <input type="checkbox" onchange="togglePlaylistEnabled('${escapeJs(title)}', this.checked)" ${isEnabled ? 'checked' : ''}>
                            <span class="slider"></span>
                        </label>
                        <div class="actions">
                            <a href="${viewUrl}" class="btn primary btn-small btn-view ${isEnabled ? '' : 'disabled'}" ${isEnabled ? '' : 'tabindex="-1" aria-disabled="true"'}>View</a>
                            <button class="btn secondary btn-small btn-sync" onclick="triggerSync('${escapeJs(title)}')" ${isEnabled ? '' : 'disabled'}>Sync</button>
                            <button class="btn danger btn-small btn-delete" onclick="openDeleteModal('playlist', '${escapeJs(title)}')" title="Delete" aria-label="Delete"><svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align: middle;"><path d="M3 6h18"></path><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"></path><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"></path><line x1="10" y1="11" x2="10" y2="17"></line><line x1="14" y1="11" x2="14" y2="17"></line></svg></button>
                        </div>
                    </div>
                    <div class="drag-handle" draggable="true" ondragstart="handleDragStart(event)" ondragend="handleDragEnd(event)">⋮⋮</div>
                </div>
                <div class="playlist-card-body">
                    <div class="card-body-content">
                        <div class="form-stack">
                            <div class="form-group">
                                <label>Playlist name (DLNA folder name)</label>
                                <input type="text" id="pl-rename-${escapeJs(title)}" value="${escapeHtml(title)}" oninput="markCardDirty(this.closest('.playlist-card'))">
                            </div>

                            <div class="field-row ${hasSrvOverride ? '' : 'disabled'}">
                                <input type="checkbox" class="override-checkbox" onchange="toggleOverride(this)" ${hasSrvOverride ? 'checked' : ''}>
                                <div class="field-content form-group">
                                    <label>Service profile</label>
                                    <select id="pl-srv-${escapeJs(title)}" oninput="markCardDirty(this.closest('.playlist-card'))" ${hasSrvOverride ? '' : 'disabled'}>
                                        ${uniqueServices.map(s => `<option value="${s}" ${s === (hasSrvOverride ? opts.service : globalOpts.default_service) ? 'selected' : ''}>${s}</option>`).join('')}
                                    </select>
                                </div>
                            </div>

                            <div class="form-group">
                                <label>Playlist URL / ID</label>
                                <input type="text" id="pl-url-${escapeJs(title)}" value="${escapeHtml(opts.url || '')}" oninput="markCardDirty(this.closest('.playlist-card'))">
                            </div>

                            <div class="form-row">
                                <div class="field-row ${hasLimitOverride ? '' : 'disabled'}">
                                    <input type="checkbox" class="override-checkbox" onchange="toggleOverride(this)" ${hasLimitOverride ? 'checked' : ''}>
                                    <div class="field-content form-group">
                                        <label>Item Limit</label>
                                        <input type="number" id="pl-limit-${escapeJs(title)}" value="${hasLimitOverride ? opts.limit_items : globalOpts.limit_items}" min="0" oninput="markCardDirty(this.closest('.playlist-card'))" ${hasLimitOverride ? '' : 'disabled'}>
                                    </div>
                                </div>
                                <div class="field-row ${hasSortOverride ? '' : 'disabled'}">
                                    <input type="checkbox" class="override-checkbox" onchange="toggleOverride(this)" ${hasSortOverride ? 'checked' : ''}>
                                    <div class="field-content form-group">
                                        <label>Sorting order</label>
                                        <select id="pl-sort-${escapeJs(title)}" oninput="markCardDirty(this.closest('.playlist-card'))" ${hasSortOverride ? '' : 'disabled'}>
                                            <option value="none" ${ (hasSortOverride ? opts.sort_by : globalOpts.sort_by) === 'none' ? 'selected' : ''}>none</option>
                                            <option value="reverse" ${ (hasSortOverride ? opts.sort_by : globalOpts.sort_by) === 'reverse' ? 'selected' : ''}>reverse</option>
                                            <option value="date" ${ (hasSortOverride ? opts.sort_by : globalOpts.sort_by) === 'date' ? 'selected' : ''}>date</option>
                                            <option value="title" ${ (hasSortOverride ? opts.sort_by : globalOpts.sort_by) === 'title' ? 'selected' : ''}>title</option>
                                            <option value="duration" ${ (hasSortOverride ? opts.sort_by : globalOpts.sort_by) === 'duration' ? 'selected' : ''}>duration</option>
                                        </select>
                                    </div>
                                </div>
                            </div>
                        </div>
                        <div class="actions margin-top" style="justify-content: flex-end;">
                            <button class="btn primary btn-small btn-save-card" disabled onclick="savePlaylistCard('${escapeJs(title)}')">Save Changes</button>
                        </div>
                    </div>
                </div>
            </div>
        `;
    }).join('');

    container.innerHTML = html;
}

function renderCustomPlaylistsTab(registry) {
    const container = document.getElementById('custom-playlists-list');
    if (!container) return;

    if (!registry || registry.length === 0) {
        container.innerHTML = '<p class="placeholder">No custom playlists registered.</p>';
        return;
    }

    container.innerHTML = registry.map(reg => {
        const isEnabled = reg.enabled !== false;
        const viewUrl = `/playlist/custom/${encodeURIComponent(reg.name)}`;

        return `
            <div class="playlist-card collapsed ${isEnabled ? '' : 'disabled-card'}" id="custom-card-${escapeJs(reg.name)}" ondragover="handleDragOver(event)">
                <div class="playlist-card-header">
                    <div class="playlist-title-group" onclick="togglePlaylistCard(this)">
                        <span class="chevron">▲</span>
                        <span class="playlist-title">${escapeHtml(reg.name)}</span>
                    </div>
                    <div class="header-controls">
                        <label class="switch switch-small">
                            <input type="checkbox" onchange="toggleCustomEnabled('${escapeJs(reg.name)}', this.checked)" ${isEnabled ? 'checked' : ''}>
                            <span class="slider"></span>
                        </label>
                        <div class="actions">
                            <a href="${viewUrl}" class="btn primary btn-small btn-view ${isEnabled ? '' : 'disabled'}">View</a>
                            <button class="btn secondary btn-small" onclick="openCustomEditor('${escapeJs(reg.name)}')">Edit</button>
                            <button class="btn danger btn-small" onclick="openDeleteModal('custom', '${escapeJs(reg.name)}')" title="Delete" aria-label="Delete"><svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align: middle;"><path d="M3 6h18"></path><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"></path><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"></path><line x1="10" y1="11" x2="10" y2="17"></line><line x1="14" y1="11" x2="14" y2="17"></line></svg></button>
                        </div>
                    </div>
                    <div class="drag-handle" draggable="true" ondragstart="handleDragStart(event)" ondragend="handleDragEnd(event)">⋮⋮</div>   
             </div>
                <div class="playlist-card-body">
                    <div class="card-body-content">
                        <div class="form-stack">
                            <div class="form-group">
                                <label>Registry Name (Folder Name)</label>
                                <input type="text" id="cpl-rename-${escapeJs(reg.name)}" value="${escapeHtml(reg.name)}" oninput="markCardDirty(this.closest('.playlist-card'))">
                            </div>
                            <div class="form-group">
                                <label>JSON File Path</label>
                                <input type="text" id="cpl-path-${escapeJs(reg.name)}" value="${escapeHtml(reg.file)}" oninput="markCardDirty(this.closest('.playlist-card'))">
                            </div>
                        </div>
                        <div class="actions margin-top" style="justify-content: flex-end;">
                            <button class="btn primary btn-small btn-save-card" disabled onclick="saveCustomCard('${escapeJs(reg.name)}')">Save Changes</button>
                        </div>
                    </div>
                </div>
            </div>
        `;
    }).join('');
}

function collectField(updateData, inputId, iniKey, isGlobal = false, isCheckbox = false) {
    const input = document.getElementById(inputId);
    if (!input) return;

    const row = input.closest('.field-row');
    const isOverridden = isGlobal || (row && row.querySelector('.override-checkbox').checked);

    if (isOverridden) {
        if (isCheckbox) {
            updateData[iniKey] = input.checked ? 'yes' : 'no';
        } else {
            updateData[iniKey] = input.value.trim();
        }
    } else {
        updateData[iniKey] = null;
    }
}

function handleDragStart(e) {
    draggedElement = e.target.closest('.playlist-card');
    const rect = draggedElement.getBoundingClientRect();
    const offsetX = e.clientX - rect.left;
    const offsetY = e.clientY - rect.top;

    if (e.dataTransfer.setDragImage) {
        e.dataTransfer.setDragImage(draggedElement, offsetX, offsetY);
    }
    e.dataTransfer.effectAllowed = 'move';
    setTimeout(() => {
        draggedElement.classList.add('dragging');
    }, 0);
}

function handleDragOver(e) {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    const targetCard = e.target.closest('.playlist-card');
    const container = draggedElement.parentNode;
    if (targetCard && targetCard !== draggedElement) {
        const rect = targetCard.getBoundingClientRect();
        const next = (e.clientY - rect.top) / (rect.bottom - rect.top) > 0.5;
        container.insertBefore(draggedElement, next ? targetCard.nextSibling : targetCard);
    }
}

async function handleDragEnd(e) {
    if (draggedElement) {
        draggedElement.classList.remove('dragging');
        
        const container = draggedElement.parentNode;
        const titles = Array.from(container.querySelectorAll('.playlist-title'))
                            .map(el => el.textContent.trim());

        const isCustom = container.id === 'custom-playlists-list';
        const endpoint = isCustom ? '/api/playlists/custom/reorder' : '/api/playlists/online/reorder';

        try {
            const res = await fetch(endpoint, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ order: titles })
            });
            const data = await res.json();
            if (data.status === 'success') {
                showToast(`Reordered ${isCustom ? 'Custom' : 'Online'} Playlists.`);
                if (isCustom) await fetchCustomPlaylists();
                else await fetchParsedConfig();
            }
        } catch (err) {
            showToast("Failed to save new order.", true);
        }
        draggedElement = null;
    }
}

async function handleRename(type, oldName, newName) {
    if (!newName || newName.trim().length === 0) {
        showAlertModal('Invalid Name', `The new name cannot be empty.`);
        return false; 
    }

    if (oldName === newName) return true;
    
    const urlMap = {
        'online': '/api/playlists/online/rename',
        'custom': '/api/playlists/custom/rename',
        'service': '/api/services/rename'
    };
    
    try {
        const res = await fetch(urlMap[type] || '/api/config/rename', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ old: oldName, new: newName })
        });
        
        const data = await res.json();
        if (data.status !== 'success') {
            showAlertModal('Rename Failed', data.message || 'An error occurred.');
            return false;
        }
        return true;
    } catch (err) {
        console.error("Rename Error: ", err);
        return false;
    }
}

function isOverridden(section, key) {
    return parsedConfigData[section] && parsedConfigData[section].hasOwnProperty(key);
}

function getGlobalValue(sectionType, key) {
    return (parsedConfigData[sectionType] && parsedConfigData[sectionType][key]) || "";
}

async function savePlaylistDefaults() {
    const updateData = {};
    collectField(updateData, 'pl-def-service', 'default_service', true);
    collectField(updateData, 'pl-def-limit', 'limit_items', true);
    collectField(updateData, 'pl-def-sort', 'sort_by', true);

    parsedConfigData['playlists'] = updateData;
    await saveParsedConfigData('Playlist defaults saved successfully!');
}

async function savePlaylistCard(oldTitle) {
    const newTitle = document.getElementById(`pl-rename-${oldTitle}`).value.trim();
    if (newTitle !== oldTitle) {
        const success = await handleRename('online', oldTitle, newTitle);
        if (!success) { showToast("Rename failed", true); return; }
    }
    const currentSec = `playlists:${newTitle}`;
    const updateData = {};
    updateData['url'] = document.getElementById(`pl-url-${oldTitle}`).value.trim();
    
    collectField(updateData, `pl-srv-${oldTitle}`, 'service', false);
    collectField(updateData, `pl-limit-${oldTitle}`, 'limit_items', false);
    collectField(updateData, `pl-sort-${oldTitle}`, 'sort_by', false);

    updateData['enabled'] = parsedConfigData[`playlists:${oldTitle}`]?.enabled || 'yes';
    parsedConfigData[currentSec] = updateData;
    if (newTitle !== oldTitle) delete parsedConfigData[`playlists:${oldTitle}`];

    await saveParsedConfigData(`Saved changes for playlist '${newTitle}'`);
}

async function saveServiceCard(sec, oldSName) {
    const isGlobal = (sec === 'services');
    let currentSec = sec;
    let currentSName = oldSName;

    if (!isGlobal) {
        const newSName = document.getElementById(`srv-rename-${oldSName}`).value.trim();
        if (newSName !== oldSName) {
            if (!validateNoSpaces(newSName, 'service name')) return;
            const success = await handleRename('service', oldSName, newSName);
            if (!success) { showToast("Rename failed", true); return; }
            currentSName = newSName;
            currentSec = `services:${newSName}`;
        }
    }
    const updateData = {};
    if (!isGlobal) {
        updateData['extractor'] = document.getElementById(`srv-ext-${oldSName}`).value.trim();
    }

    collectField(updateData, `srv-cookiespl-${oldSName}`, 'use_cookies_for_playlists', isGlobal, true);
    collectField(updateData, `srv-cookiespb-${oldSName}`, 'use_cookies_for_playback', isGlobal, true);
    collectField(updateData, `srv-cookiepath-${oldSName}`, 'cookie_path', isGlobal);
    collectField(updateData, `srv-fmt-${oldSName}`, 'format', isGlobal);
    collectField(updateData, `srv-fmtdash-${oldSName}`, 'format_dash', isGlobal);
    collectField(updateData, `srv-ttl-${oldSName}`, 'cache_ttl', isGlobal);
    collectField(updateData, `srv-titlefmt-${oldSName}`, 'title_format', isGlobal);

    parsedConfigData[currentSec] = updateData;
    if (!isGlobal && currentSName !== oldSName) delete parsedConfigData[sec];

    await saveParsedConfigData(`Saved service profile '${currentSName}'`);
}

function renderServicesTab(configData) {
    const container = document.getElementById('services-list');
    if (!container) return;

    const resolved = configData.resolved_services || {};
    const serviceSections = Object.keys(configData).filter(s => s === 'services' || s.startsWith('services:'));

    container.innerHTML = serviceSections.map(sec => {
        const isGlobal = sec === 'services';
        const sName = isGlobal ? 'Default settings for all services' : sec.replace('services:', '');
        const keyName = isGlobal ? 'global' : sName;
        const resOpts = resolved[keyName] || {};
        const opts = configData[sec] || {};

        const checkOverride = (key) => isGlobal || isOverridden(sec, key);
        const getVal = (key) => checkOverride(key) ? (opts[key] ?? resOpts[key]) : getGlobalValue('services', key);

        return `
            <div class="service-card collapsed" id="service-card-${escapeJs(sName)}">
                <div class="service-card-header">
                    <div class="service-title-group" onclick="toggleServiceCard(this)">
                        <span class="chevron">▲</span>
                        <span class="service-title ${isGlobal ? `service-title-global` : ''}">${escapeHtml(sName)}</span>
                    </div>
                    ${!isGlobal ? `
                        <div class="actions">
                            <button class="btn danger btn-small" onclick="openDeleteModal('service', '${escapeJs(keyName)}')" title="Delete" aria-label="Delete"><svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align: middle;"><path d="M3 6h18"></path><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"></path><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"></path><line x1="10" y1="11" x2="10" y2="17"></line><line x1="14" y1="11" x2="14" y2="17"></line></svg></button>
                        </div>
                    ` : ''}
                </div>
                <div class="service-card-body">
                    <div class="card-body-content">
                        ${isGlobal ? `
                            <p class="card-desc-small">These are the default settings for services. Uncheck the override box in any individual service to inherit these settings.</p>
                        ` : `
                            <p class="card-desc-small">Check the override checkbox for any setting you want to define for this service individually, or uncheck it to inherit that setting from the global defaults.</p>
                        `}
                        <div class="form-stack">
                            ${!isGlobal ? `
                                <div class="form-group">
                                    <label>Service Name</label>
                                    <input type="text" id="srv-rename-${escapeJs(keyName)}" value="${escapeHtml(sName)}" oninput="markCardDirty(this.closest('.service-card'))">
                                </div>
                                <div class="form-group">
                                    <label>Extractor Name</label>
                                    <input type="text" id="srv-ext-${escapeJs(keyName)}" value="${escapeHtml(resOpts.extractor || keyName)}" oninput="markCardDirty(this.closest('.service-card'))">
                                </div>
                            ` : ''}
                            <div class="field-row ${checkOverride('use_cookies_for_playlists') ? '' : 'disabled'}">
                                ${!isGlobal ? `<input type="checkbox" class="override-checkbox" onchange="toggleOverride(this)" ${isOverridden(sec, 'use_cookies_for_playlists') ? 'checked' : ''} title="Override global setting">` : ''}
                                <div class="field-content switch-container">
                                    <span class="switch-label">Use cookies for playlist indexing</span>
                                    <label class="switch">
                                        <input type="checkbox" id="srv-cookiespl-${escapeJs(keyName)}" ${getVal('use_cookies_for_playlists') === 'yes' ? 'checked' : ''} onchange="markCardDirty(this.closest('.service-card'))" ${checkOverride('use_cookies_for_playlists') ? '' : 'disabled'}>
                                        <span class="slider"></span>
                                    </label>
                                </div>
                            </div>
                            <div class="field-row ${checkOverride('use_cookies_for_playback') ? '' : 'disabled'}">
                                ${!isGlobal ? `<input type="checkbox" class="override-checkbox" onchange="toggleOverride(this)" ${isOverridden(sec, 'use_cookies_for_playback') ? 'checked' : ''} title="Override global setting">` : ''}
                                <div class="field-content switch-container">
                                    <span class="switch-label">Use cookies for CDN stream resolving (playback)</span>
                                    <label class="switch">
                                        <input type="checkbox" id="srv-cookiespb-${escapeJs(keyName)}" ${getVal('use_cookies_for_playback') === 'yes' ? 'checked' : ''} onchange="markCardDirty(this.closest('.service-card'))" ${checkOverride('use_cookies_for_playback') ? '' : 'disabled'}>
                                        <span class="slider"></span>
                                    </label>
                                </div>
                            </div>
                            <div class="field-row ${checkOverride('cookie_path') ? '' : 'disabled'}">
                                ${!isGlobal ? `<input type="checkbox" class="override-checkbox" onchange="toggleOverride(this)" ${isOverridden(sec, 'cookie_path') ? 'checked' : ''} title="Override global setting">` : ''}
                                <div class="field-content form-group">
                                    <label>Cookie file path</label>
                                    <div class="input-button-row">
                                        <input type="text" id="srv-cookiepath-${escapeJs(keyName)}" value="${escapeHtml(getVal('cookie_path') || '')}" oninput="markCardDirty(this.closest('.service-card'))" ${checkOverride('cookie_path') ? '' : 'disabled'}>
                                        <button class="btn secondary btn-small" onclick="openCookieModal('${escapeJs(keyName)}')" ${checkOverride('cookie_path') ? '' : 'disabled'}>Import...</button>
                                    </div>
                                </div>
                            </div>
                            <div class="field-row ${checkOverride('format') ? '' : 'disabled'}">
                                ${!isGlobal ? `<input type="checkbox" class="override-checkbox" onchange="toggleOverride(this)" ${isOverridden(sec, 'format') ? 'checked' : ''} title="Override global setting">` : ''}
                                <div class="field-content form-group">
                                    <label>Format selector for single-file MP4 (preferred)</label>
                                    <input type="text" id="srv-fmt-${escapeJs(keyName)}" value="${escapeHtml(getVal('format') || '')}" oninput="markCardDirty(this.closest('.service-card'))" ${checkOverride('format') ? '' : 'disabled'}>
                                </div>
                            </div>
                            <div class="field-row ${checkOverride('format_dash') ? '' : 'disabled'}">
                                ${!isGlobal ? `<input type="checkbox" class="override-checkbox" onchange="toggleOverride(this)" ${isOverridden(sec, 'format_dash') ? 'checked' : ''} title="Override global setting">` : ''}
                                <div class="field-content form-group">
                                    <label>Format selector for DASH streams (if remuxing is needed)</label>
                                    <input type="text" id="srv-fmtdash-${escapeJs(keyName)}" value="${escapeHtml(getVal('format_dash') || '')}" oninput="markCardDirty(this.closest('.service-card'))" ${checkOverride('format_dash') ? '' : 'disabled'}>
                                </div>
                            </div>
                            <div class="field-row ${checkOverride('cache_ttl') ? '' : 'disabled'}">
                                ${!isGlobal ? `<input type="checkbox" class="override-checkbox" onchange="toggleOverride(this)" ${isOverridden(sec, 'cache_ttl') ? 'checked' : ''} title="Override global setting">` : ''}
                                <div class="field-content form-group">
                                    <label>Cache TTL (seconds, 0 = permanent)</label>
                                    <input type="number" id="srv-ttl-${escapeJs(keyName)}" value="${getVal('cache_ttl')}" oninput="markCardDirty(this.closest('.service-card'))" ${checkOverride('cache_ttl') ? '' : 'disabled'}>
                                </div>
                            </div>
                            <div class="field-row ${checkOverride('title_format') ? '' : 'disabled'}">
                                ${!isGlobal ? `<input type="checkbox" class="override-checkbox" onchange="toggleOverride(this)" ${isOverridden(sec, 'title_format') ? 'checked' : ''} title="Override global setting">` : ''}
                                <div class="field-content form-group">
                                    <label>Title template</label>
                                    <input type="text" id="srv-titlefmt-${escapeJs(keyName)}" value="${escapeHtml(getVal('title_format') || '')}" oninput="markCardDirty(this.closest('.service-card'))" ${checkOverride('title_format') ? '' : 'disabled'}>
                                </div>
                            </div>
                        </div>
                        <div class="actions margin-top" style="justify-content: flex-end;">
                            <button class="btn primary btn-small btn-save-card" disabled onclick="saveServiceCard('${escapeJs(sec)}', '${escapeJs(keyName)}')">Save Changes</button>
                        </div>
                    </div>
                </div>
            </div>
        `;
    }).join('');
}

function openCookieModal(serviceName) {
    document.getElementById('modal-cookie-service').value = serviceName;
    document.getElementById('modal-cookie-label').innerHTML = `Target Service: <strong>${serviceName}</strong>`;
    document.getElementById('modal-cookie').classList.remove('hidden');
}

async function handleCookieImportSubmit(e) {
    e.preventDefault();
    const serviceName = document.getElementById('modal-cookie-service').value;
    const pathInput = document.getElementById(`srv-cookiepath-${serviceName}`);
    const targetFilename = pathInput ? pathInput.value.trim() : "";
    const fileInput = document.getElementById('modal-cookie-file');
    const textInput = document.getElementById('modal-cookie-text').value.trim();

    const formData = new FormData();
    formData.append('service', serviceName);
    formData.append('filename', targetFilename);

    if (fileInput.files.length > 0) {
        formData.append('file', fileInput.files[0]);
    } else if (textInput) {
        formData.append('cookie_text', textInput);
    } else {
        showToast('Please select a file or paste cookie text', true);
        return;
    }

    try {
        const res = await fetch('/api/upload-cookies', { method: 'POST', body: formData });
        const data = await res.json();
        if (data.status === 'success') {
            showToast(data.message);
            document.getElementById('modal-cookie').classList.add('hidden');
            document.getElementById('cookie-import-form').reset();
        } else {
            showToast(data.message || 'Cookie upload failed.', true);
        }
    } catch (err) {
        showToast('Error uploading cookies.', true);
    }
}

function renderSettingsForm(configData) {
    const container = document.getElementById('settings-form-container');
    if (!container) return;

    const proxy = configData.proxy || {};
    const dlna = configData.dlna || {};
    const syncCfg = configData.sync || {};
    const ytdlp = configData['yt-dlp'] || {};
    const ffmpeg = configData.ffmpeg || {};
    const dash = configData.dashboard || {};

    initialSyncSchedulerState = (syncCfg.enable_sync !== 'no');

    container.innerHTML = `
        <div class="card">
            <h3>Proxy settings</h3>
            <div class="form-stack">
                <div class="form-group">
                    <label>Proxy Bind IP (0.0.0.0 = default)</label>
                    <input type="text" name="proxy.proxy_ip" value="${escapeHtml(proxy.proxy_ip || '0.0.0.0')}">
                </div>
                <div class="form-group">
                    <label>Proxy Port</label>
                    <input type="number" name="proxy.proxy_port" value="${proxy.proxy_port || 5000}">
                </div>
                <div class="form-group">
                    <label>Proxy URL pattern</label>
                    <input type="text" name="proxy.proxy_url_pattern" value="${escapeHtml(proxy.proxy_url_pattern || '/play/{service}/{video_id}')}">
                </div>
                <div class="form-group">
                    <label>Default operating mode</label>
                    <select name="proxy.mode">
                        <option value="redirect" ${proxy.mode === 'redirect' ? 'selected' : ''}>redirect (HTTP 302)</option>
                        <option value="proxy" ${proxy.mode === 'proxy' ? 'selected' : ''}>proxy (Byte Tunneling)</option>
                    </select>
                </div>
                <div class="switch-container margin-top">
                    <span class="switch-label">Enable on-the-fly remuxing with FFmpeg</span>
                    <label class="switch">
                        <input type="checkbox" name="proxy.enable_remux" ${proxy.enable_remux !== 'no' ? 'checked' : ''}>
                        <span class="slider"></span>
                    </label>
                </div>
                <div class="form-group">
                    <label>Remux threshold (video height in px)</label>
                    <input type="number" name="proxy.remux_threshold" value="${proxy.remux_threshold || 720}">
                </div>
                <div class="form-group">
                    <label>Remux target format</label>
                    <select name="proxy.remux_target_format">
                        <option value="ts" ${proxy.remux_target_format === 'ts' ? 'selected' : ''}>ts (MPEG-TS)</option>
                        <option value="mp4" ${proxy.remux_target_format === 'mp4' ? 'selected' : ''}>mp4 (MP4)</option>
                    </select>
                </div>
                <div class="switch-container margin-top">
                    <span class="switch-label">Enable CDN URL caching</span>
                    <label class="switch">
                        <input type="checkbox" name="proxy.enable_cache" ${proxy.enable_cache !== 'no' ? 'checked' : ''}>
                        <span class="slider"></span>
                    </label>
                </div>
                <div class="form-group">
                    <label>Default cache TTL (seconds)</label>
                    <input type="number" name="proxy.default_cache_ttl" value="${proxy.default_cache_ttl || 14400}">
                </div>
            </div>
        </div>

        <div class="card margin-top">
            <h3>DLNA / UPnP settings</h3>
            <div class="form-stack">
                <div class="form-group">
                    <label>DLNA Bind IP (0.0.0.0 = default)</label>
                    <input type="text" name="dlna.dlna_ip" value="${escapeHtml(dlna.dlna_ip || '0.0.0.0')}">
                </div>
                <div class="form-group">
                    <label>DLNA Port</label>
                    <input type="number" name="dlna.dlna_port" value="${dlna.dlna_port || 8200}">
                </div>
                <div class="form-group">
                    <label>Server display name</label>
                    <input type="text" name="dlna.friendly_name" value="${escapeHtml(dlna.friendly_name || 'yt-dlna Media Server')}">
                </div>
                <div class="form-group">
                    <label>Icon Path</label>
                    <input type="text" name="dlna.icon" value="${escapeHtml(dlna.icon || 'assets/yt-dlna.png')}">
                </div>
            </div>
        </div>

        <div class="card margin-top">
            <h3>Playlist synchronization settings</h3>
            <div class="switch-container margin-top">
                <span class="switch-label">Enable automatic playlist sync scheduler</span>
                <label class="switch">
                    <input type="checkbox" name="sync.enable_sync" ${syncCfg.enable_sync !== 'no' ? 'checked' : ''}>
                    <span class="slider"></span>
                </label>
            </div>
            <div class="form-stack">
                <div class="form-group">
                    <label>Sync interval (seconds)</label>
                    <input type="number" name="sync.sync_interval" value="${syncCfg.sync_interval || 3600}">
                </div>
            </div>
            <div class="switch-container">
                <span class="switch-label">Proactively pre-cache CDN URLs during sync</span>
                <label class="switch">
                    <input type="checkbox" name="sync.precache_cdn_urls" ${syncCfg.precache_cdn_urls !== 'no' ? 'checked' : ''}>
                    <span class="slider"></span>
                </label>
            </div>
        </div>

        <div class="card margin-top">
            <h3>Dashboard settings</h3>
            <div class="switch-container margin-top">
                <span class="switch-label">Enable Dashboard (Web UI)</span>
                <label class="switch">
                    <input type="checkbox" name="dashboard.enable_dashboard" ${dash.enable_dashboard !== 'no' ? 'checked' : ''}>
                    <span class="slider"></span>
                </label>
            </div>
            <div class="form-stack">
                <div class="form-group">
                    <label>Dashboard Bind IP (0.0.0.0 = default)</label>
                    <input type="text" name="dashboard.dashboard_ip" value="${escapeHtml(dash.dashboard_ip || '0.0.0.0')}">
                </div>
                <div class="form-group">
                    <label>Dashboard Port</label>
                    <input type="number" name="dashboard.dashboard_port" value="${dash.dashboard_port || 5001}">
                </div>
            </div>
        </div>

        <div class="card margin-top">
            <h3>Engine Executables (yt-dlp & FFmpeg)</h3>
            <div class="form-stack">
                <div class="form-group">
                    <label>yt-dlp invocation mode</label>
                    <select name="yt-dlp.mode">
                        <option value="import" ${ytdlp.mode === 'import' ? 'selected' : ''}>import (as a module, keeps in memory, fastest)</option>
                        <option value="exec" ${ytdlp.mode === 'exec' ? 'selected' : ''}>exec (external binary, if different Python is needed, slower)</option>
                    </select>
                </div>
                <div class="form-group">
                    <label>yt-dlp executable path (only for &apos;exec&apos; mode)</label>
                    <input type="text" name="yt-dlp.executable_path" value="${escapeHtml(ytdlp.executable_path || '/usr/local/bin/yt-dlp')}">
                </div>
                <div class="form-group">
                    <label>FFmpeg executable path (only needed for remultiplexing)</label>
                    <input type="text" name="ffmpeg.executable_path" value="${escapeHtml(ffmpeg.executable_path || '/usr/bin/ffmpeg')}">
                </div>
                <div class="form-group">
                    <label>FFmpeg additional command-line options</label>
                    <input type="text" name="ffmpeg.add_opts" value="${escapeHtml(ffmpeg.add_opts || '')}">
                </div>
            </div>
        </div>
    `;
}

async function saveParsedSettings(e) {
    e.preventDefault();
    const form = document.getElementById('settings-form');

    const syncCheckbox = form.querySelector('input[name="sync.enable_sync"]');
    const newSyncState = syncCheckbox ? syncCheckbox.checked : true;
    const schedulerStateChanged = (initialSyncSchedulerState !== newSyncState);

    const inputs = form.querySelectorAll('input, select');
    inputs.forEach(input => {
        const parts = input.name.split('.');
        if (parts.length === 2) {
            const sec = parts[0], key = parts[1];
            if (!parsedConfigData[sec]) parsedConfigData[sec] = {};
            if (input.type === 'checkbox') {
                parsedConfigData[sec][key] = input.checked ? 'yes' : 'no';
            } else { parsedConfigData[sec][key] = input.value; }
        }
    });
    await saveParsedConfigData('System settings saved successfully!');

    // check if user toggled the scheduler state, needing daemon restart!
    if (schedulerStateChanged) {
        const restartModal = document.getElementById('modal-restart');
        restartModal.querySelector('h2').textContent = "Restart required";
        restartModal.querySelector('.modal-desc').innerHTML = 
            "You have changed the <strong>Enable background playlist sync scheduler</strong> setting. Enabling or disabling scheduled sync will only take effect after the daemon is restarted.<br /><br />Restart daemon now?";
        restartModal.classList.remove('hidden');
        initialSyncSchedulerState = newSyncState;
    }
}

async function handleAddPlaylist(e) {
    e.preventDefault();
    const name = document.getElementById('pl-name').value.trim();
    const url = document.getElementById('pl-url').value.trim();
    if (!name || !url) return;

    const secName = `playlists:${name}`;
    parsedConfigData[secName] = { 'url': url };
    document.getElementById('modal-playlist').classList.add('hidden');
    document.getElementById('add-playlist-form').reset();
    await saveParsedConfigData(`Added playlist '${name}'.`);
    showAlertModal('Playlist added', "<p>Your playlist was added successfully. Remember to click the <strong>'Sync'</strong> button for that playlist now if you want to sync its content to your library.</p><p>If you don't sync it now, it will be automatically synced at the next scheduled automatic sync if <strong>'Enable automatic playlist sync scheduler'</strong> is enabled on the Settings tab.</p>");
}

async function handleAddService(e) {
    e.preventDefault();
    const sName = document.getElementById('srv-name').value.trim().toLowerCase();
    const extractor = document.getElementById('srv-extractor').value.trim().toLowerCase();

    if (!validateNoSpaces(sName, 'service name')) return;
    if (!validateNoSpaces(extractor, 'extractor name')) return;

    const secName = `services:${sName}`;
    parsedConfigData[secName] = { 'extractor': extractor };

    document.getElementById('modal-service').classList.add('hidden');
    document.getElementById('add-service-form').reset();
    await saveParsedConfigData(`Added service profile '${sName}'!`);
}

async function saveParsedConfigData(successMsg) {
    try {
        const res = await fetch('/api/config/parsed', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ config: parsedConfigData })
        });
        const data = await res.json();
        if (data.status === 'success') {
            showToast(successMsg || 'Configuration saved successfully!');
            await reloadConfig();
            await fetchParsedConfig();
            fetchConfig();
        } else { showToast(data.message || 'Save failed', true); }
    } catch (err) { showToast('Error saving configuration', true); }
}

async function deletePlaylist(title) {
    try {
        const res = await fetch('/api/playlists/online/delete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: title })
        });
        const data = await res.json();
        if (data.status === 'success') {
            showToast(`Deleted playlist '${title}'.`);
            await fetchParsedConfig(); // Refresh UI
        } else {
            showToast(data.message, true);
        }
    } catch (err) { showToast('Error deleting playlist', true); }
}

async function deleteService(name) {
    try {
        const res = await fetch('/api/services/delete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: name })
        });
        const data = await res.json();
        if (data.status === 'success') {
            showToast(`Deleted service '${name}'.`);
            await fetchParsedConfig();
        } else {
            showToast(data.message, true);
        }
    } catch (err) { showToast('Error deleting service', true); }
}

async function deleteCustomPlaylist(name) {
    try {
        const res = await fetch('/api/playlists/custom/delete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: name })
        });
        const data = await res.json();
        if (data.status === 'success') {
            showToast(`Deleted Custom Playlist '${name}'.`);
            showAlertModal('Custom Playlist deleted', '<strong>Note:</strong> If the playlist file was created manually and/or resided outside the <strong>data/</strong> folder, it has not been removed.');
            await fetchCustomPlaylists();
        } else {
            showToast(data.message, true);
        }
    } catch (err) { showToast('Error deleting Custom Playlist', true); }
}

async function triggerSync(target) {
    try {
        const res = await fetch('/api/sync', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ target: target })
        });
        const data = await res.json();
        if (data.status === 'success') {
            showToast(`Sync started for '${target || 'all playlists'}'`);
            setTimeout(fetchStatus, 2500);
        } else { showToast(data.message || 'Sync failed', true); }
    } catch (err) { showToast('Error triggering sync', true); }
}

async function reloadConfig() {
    try {
        const res = await fetch('/api/reload', { method: 'POST' });
        const data = await res.json();
        
        if (data.status === 'success') {
            showToast('Configuration updated!');
            
            setTimeout(async () => {
                await fetchStatus();
                await fetchParsedConfig();
                await fetchConfig();
                await refreshUI(true);
                
                console.log("UI synchronized with backend config.");
            }, 1000);
            
        } else {
            showToast(data.message || 'Reload failed', true);
        }
    } catch (err) {
        showToast('Error reloading configuration', true);
    }
}

async function fetchConfig() {
    try {
        const res = await fetch('/api/config/raw');
        const data = await res.json();
        if (data.status === 'success') {
            document.getElementById('config-editor').value = data.config;
        }
    } catch (err) { console.log('API offline'); }
}

async function fetchCustomPlaylists() {
    try {
        const res = await fetch('/api/playlists/custom');
        const data = await res.json();
        
        if (data.status === 'success') {
            renderCustomPlaylistsTab(data.registry);
        } else {
            console.error("Failed to fetch custom playlists: ", data.message);
        }
    } catch (err) {
        console.error("Error communicating with server: ", err);
    }
}

async function saveRawConfig() {
    const configText = document.getElementById('config-editor').value;
    try {
        const res = await fetch('/api/config/raw', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ config: configText })
        });
        const data = await res.json();
        if (data.status === 'success') {
            showToast('Configuration saved successfully!');
            await reloadConfig();
            fetchParsedConfig();
        } else { showToast(data.message || 'Save failed', true); }
    } catch (err) { showToast('Error saving configuration', true); }
}

async function restartDaemon() {
    document.getElementById('modal-restart').classList.add('hidden');
    try {
        const res = await fetch('/api/restart', { method: 'POST' });
        const data = await res.json();
        showToast(data.message || 'Daemon restart initiated...');

        const statusEl = document.getElementById('server-status');
        if (statusEl) {
            statusEl.textContent = 'Restarting...';
            statusEl.className = 'value warning';
        }
        pollForServerReturn();

    } catch (err) { showToast('Error initiating daemon restart', true); }
}

async function pollForServerReturn() {
    const checkInterval = 2000;
    const check = async () => {
        try {
            const res = await fetch('/api/status');
            
            if (res.ok) {
                showToast("Successfully restarted daemon! Reloading...", false);
                setTimeout(() => {
                    window.location.href = window.location.pathname + window.location.hash;
                    window.location.reload();
                }, 1000);
            } else {
                setTimeout(check, checkInterval);
            }
        } catch (err) {
            console.log("API still offline, retrying...");
            setTimeout(check, checkInterval);
        }
    };
    setTimeout(check, 5000);
}

function escapeHtml(str) {
    return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function escapeJs(str) {
    return String(str).replace(/'/g, "\\'");
}
