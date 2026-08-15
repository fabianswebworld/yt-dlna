let playlistName = new URLSearchParams(window.location.search).get('name');
let treeData = { mode: 'bounce', children: [] };
let activeNode = null;
let isDirty = false;
let draggedNode = null;

async function init() {
    initTheme();
    const themeBtn = document.getElementById('btn-theme-toggle');
    if (themeBtn) themeBtn.addEventListener('click', toggleTheme);

    if (!playlistName) return;
    document.getElementById('editor-title').textContent = `Editing: ${playlistName}`;
    try {
        const res = await fetch(`/api/playlists/custom/data?name=${encodeURIComponent(playlistName)}`);
        const data = await res.json();
        if (Array.isArray(data)) {
            treeData = { mode: 'bounce', children: data };
        } else {
            treeData = data || { mode: 'bounce', children: [] };
        }
    } catch(e) {
        treeData = { mode: 'bounce', children: [] };
    }
    treeData.collapsed = false;
    activeNode = null;
    document.getElementById('properties-panel').classList.add('hidden');
    document.getElementById('empty-panel').classList.remove('hidden');
    renderTree();
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
    if (btn) btn.textContent = theme === 'dark' ? '☀️ Light Mode' : '🌙 Dark Mode';
}

function renderTree() {
    const container = document.getElementById('tree-root');
    if (!container) return;
    container.innerHTML = '';

    const buildNodeUI = (node, parentEl) => {
        const isRoot = (node === treeData);

        const wrapper = document.createElement('div');
        wrapper.className = `tree-node-wrapper ${node.collapsed ? 'collapsed' : ''}`;

        const label = document.createElement('div');
        label.className = `tree-node ${activeNode === node ? 'active' : ''}`;
        label.draggable = !isRoot;

        const hasChildren = node.children && node.children.length > 0;
        const chevron = (isRoot || node.type === 'folder') 
            ? `<span class="chevron-btn ${node.collapsed ? 'collapsed' : ''}">${hasChildren ? '▼' : ''}</span>`
            : `<span style="width:22px; display:inline-block;"></span>`;
        const icon = isRoot ? '📁' : (node.type === 'folder' ? '📁' : '🔗');
        const name = isRoot ? '[Playlist Root]' : (node.name || 'Untitled');

        label.innerHTML = `${chevron}<span style="margin-left: 4px; margin-right: 4px;">${icon}</span><span style="flex:1">${escapeHtml(name)}</span>`;

        label.onclick = (e) => {
            if (e.target.classList.contains('chevron-btn')) {
                node.collapsed = !node.collapsed;
                renderTree();
            } else {
                isRoot ? selectRoot() : selectNode(node);
            }
        };

        label.ondragstart = (e) => { draggedNode = node; setTimeout(() => label.classList.add('dragging'), 0); };
        label.ondragover = (e) => { 
            e.preventDefault(); 
            const rect = label.getBoundingClientRect();
            const isTop = (e.clientY - rect.top) < (rect.height / 2);
            label.classList.toggle('drag-over-top', isTop);
            label.classList.toggle('drag-over-bottom', !isTop);
        };
        label.ondragleave = () => { 
            label.classList.remove('drag-over-top', 'drag-over-bottom'); 
        };
        label.ondrop = (e) => { 
            label.classList.remove('drag-over-top', 'drag-over-bottom'); 
            handleNodeDrop(e, node); 
        };
        label.ondragend = () => { label.classList.remove('dragging'); draggedNode = null; };

        wrapper.appendChild(label);

        if ((isRoot || node.type === 'folder') && node.children) {
            const childDiv = document.createElement('div');
            childDiv.className = 'node-children';
            node.children.forEach(child => buildNodeUI(child, childDiv));
            wrapper.appendChild(childDiv);
        }
        parentEl.appendChild(wrapper);
    };

    buildNodeUI(treeData, container);
}

function selectRoot() {
    activeNode = treeData;
    document.getElementById('properties-panel').classList.remove('hidden');
    document.getElementById('empty-panel').classList.add('hidden');
    document.getElementById('prop-header').textContent = 'Playlist Root Settings';
    document.getElementById('disp-name-label').textContent = 'Playlist Name (edit in Custom Playlist settings)';
    
    const nameInput = document.getElementById('node-name');
    nameInput.value = playlistName;
    nameInput.disabled = true;
    
    document.getElementById('item-only-fields').classList.add('hidden');
    document.getElementById('mode-inherit-opt').disabled = true; 
    document.getElementById('node-mode').value = treeData.mode || 'bounce';
    
    const deleteBtn = document.querySelector('#properties-panel .btn.danger');
    if (deleteBtn) deleteBtn.classList.add('hidden');
    
    renderTree();
}


function selectNode(node) {
    activeNode = node;

    document.getElementById('properties-panel').classList.remove('hidden');
    document.getElementById('empty-panel').classList.add('hidden');
    document.getElementById('disp-name-label').textContent = 'Display Name';
    
    document.getElementById('prop-header').textContent = node.type === 'folder' ? 'Folder Settings' : 'Item Settings';
    const nameInput = document.getElementById('node-name');
    nameInput.value = node.name || '';
    nameInput.disabled = false;
    
    document.getElementById('mode-inherit-opt').disabled = false;
    document.getElementById('node-mode').value = node.mode || '';
    
    const itemFields = document.getElementById('item-only-fields');
    if (node.type === 'item') {
        itemFields.classList.remove('hidden');
        document.getElementById('node-url').value = node.url || '';
    } else {
        itemFields.classList.add('hidden');
    }

    const mimeSelect = document.getElementById('node-mime');
    if (node.type === 'item') {
        document.getElementById('item-mime-group').classList.remove('hidden');
        mimeSelect.value = node.mime_type || ''; 
    } else {
        document.getElementById('item-mime-group').classList.add('hidden');
    }
    document.querySelector('#properties-panel .btn.danger').classList.remove('hidden');
    renderTree();
}

function updateActiveNode() {
    if (!activeNode) return;
    activeNode.name = document.getElementById('node-name').value;
    activeNode.mode = document.getElementById('node-mode').value;
    if (activeNode.type === 'item') {
        activeNode.url = document.getElementById('node-url').value;
        activeNode.mime_type = document.getElementById('node-mime').value;
    }
    isDirty = true;
    renderTree();
}

function addNode(input, autoSelect = false, skipRender = false) {
    let node;

    if (typeof input === 'string') {
        node = { 
            type: input, 
            name: `New ${input}`, 
            mode: '', 
            mime_type: input === 'item' ? '' : undefined 
        };
        if (input === 'folder') node.children = [];
    } else {
        node = input;
    }
    const target = (activeNode && activeNode.type === 'folder') ? activeNode : treeData;
    if (!target.children) target.children = [];
    target.children.push(node);

    isDirty = true;
    if (autoSelect) selectNode(node);
    if (!skipRender) renderTree();
    return node;
}

function deleteActiveNode() {
    if (!activeNode || activeNode === treeData) return;
    if (!confirm(`Delete "${activeNode.name}" and all its contents?`)) return;
    const removeRecursive = (list) => {
        for (let i = 0; i < list.length; i++) {
            if (list[i] === activeNode) {
                list.splice(i, 1);
                return true;
            }
            if (list[i].children && removeRecursive(list[i].children)) return true;
        }
        return false;
    };
    if (removeRecursive(treeData.children)) {
        isDirty = true;
        activeNode = null;
        selectRoot();
    }
}

function handleNodeDragStart(e, node) {
    if (node === treeData) { e.preventDefault(); return; }
    draggedNode = node;
    e.dataTransfer.effectAllowed = 'move';
    setTimeout(() => e.target.classList.add('dragging'), 0);
}

function handleNodeDragOver(e) {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
}

function handleNodeDrop(e, targetNode) {
    e.preventDefault();
    e.stopPropagation();
    
    if (!draggedNode || draggedNode === targetNode) return;
    if (isDescendant(draggedNode, targetNode)) {
        alert("Cannot move a folder into its own subfolder.");
        return;
    }
    const oldParent = findParent(treeData, draggedNode);
    if (!oldParent) return; 
    oldParent.children.splice(oldParent.children.indexOf(draggedNode), 1);

    const rect = e.currentTarget.getBoundingClientRect();
    const isTopHalf = (e.clientY - rect.top) < (rect.height / 2);

    if (targetNode === treeData) {
        treeData.children.unshift(draggedNode);
    } 
    else if (targetNode.type === 'folder' && !isTopHalf) {
        if (!targetNode.children) targetNode.children = [];
        targetNode.children.unshift(draggedNode);
    } 
    else {
        const newParent = findParent(treeData, targetNode);
        if (newParent) {
            const targetIdx = newParent.children.indexOf(targetNode);
            const insertIdx = isTopHalf ? targetIdx : targetIdx + 1;
            newParent.children.splice(insertIdx, 0, draggedNode);
        } else {
            treeData.children.push(draggedNode);
        }
    }
    isDirty = true;
    renderTree();
}

function findParent(parent, target) {
    if (parent.children && parent.children.includes(target)) {
        return parent;
    }
    if (parent.children) {
        for (let child of parent.children) {
            let result = findParent(child, target);
            if (result) return result;
        }
    }
    return null;
}

function isDescendant(parent, target) {
    if (!parent.children) return false;
    if (parent.children.includes(target)) return true;
    return parent.children.some(child => isDescendant(child, target));
}

function findNodeInMemory(parent, target) {
    if (parent.name === target.name && parent.url === target.url && parent.type === target.type) return parent;
    if (!parent.children) return null;
    for (let child of parent.children) {
        let res = findNodeInMemory(child, target);
        if (res) return res;
    }
    return null;
}

// --- M3U Import Logic ---
function triggerM3UImport() { 
    document.getElementById('m3u-input').click(); 
}

function handleImportFile(input) {
    const file = input.files[0];
    if (!file) return;

    const fileName = file.name.toLowerCase();
    const isWinamp = fileName.endsWith('.bm') || fileName.endsWith('.bm8');
    const isUtf8 = fileName.endsWith('8'); // .m3u8 or .bm8
    const encoding = isUtf8 ? 'utf-8' : 'windows-1252';

    const reader = new FileReader();
    reader.onload = (e) => {
        const lines = e.target.result.split(/\r?\n/).map(l => l.trim());
        
        if (isWinamp) {
            parseWinamp(lines);
        } else {
            parseM3U(lines);
        }
        isDirty = true;
        renderTree();
        input.value = ''; // Reset input
    };
    reader.readAsText(file, encoding);
}

function parseM3U(lines) {
    let currentTitle = "";
    lines.forEach(line => {
        if (!line) return;
        if (line.startsWith('#EXTINF:')) {
            currentTitle = line.split(',').pop().trim();
        } else if (!line.startsWith('#')) {
            const finalTitle = currentTitle || deriveTitleFromUrl(line);
            const newItem = {
                type: 'item',
                name: finalTitle,
                url: line,
                mode: '',
                mime_type: inferMimeType(line)
            };
            addNode(newItem, false, true);
            currentTitle = "";
        }
    });
}

function parseWinamp(lines) {
    // Winamp.bm format
    const cleanLines = lines.filter(l => l.length > 0);
    for (let i = 0; i < cleanLines.length; i += 2) {
        const url = cleanLines[i];
        const title = cleanLines[i + 1] || deriveTitleFromUrl(url);
        const newItem = {
            type: 'item',
            name: title,
            url: url,
            mode: '',
            mime_type: inferMimeType(url)
        };
        addNode(newItem, false, true);
    }
}

function deriveTitleFromUrl(url) {
    try {
        let filename = url.split('/').pop().split('?')[0].split('#')[0] || "Stream";
        filename = filename.replace(/\.(mp3|m4a|mp4|wav|m3u|m3u8|aac|ogg|flac)$/i, '');
        return filename.replace(/[_-]/g, ' ').trim() || "Untitled Stream";
    } catch (e) {
        return "Untitled Stream";
    }
}

function inferMimeType(url) {
    const u = url.toLowerCase();
    if (u.includes('mp3') || u.endsWith('.m3u')) return 'audio/mpeg';
    if (u.endsWith('.m4a') || u.includes('aac')) return 'audio/mp4';
    if (u.endsWith('.aac')) return 'audio/aac';
    if (u.endsWith('.ts')) return 'video/mpeg';
    if (u.endsWith('.mp4')) return 'video/mp4';
    return '';
}

function closeEditor() {
    if (isDirty) {
        if (!confirm("You have unsaved changes. Are you sure you want to close and discard them?")) {
            return;
        }
    }
    window.location.href = '/playlists/custom';
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
    }, 3000);
}

async function saveData() {
    delete treeData.collapsed;
    delete treeData.isRoot;
    try {
        const res = await fetch(`/api/playlists/custom/data?name=${encodeURIComponent(playlistName)}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(treeData)
        });
        
        if (res.ok) {
            isDirty = false;
            showToast('Changes saved successfully.');
        } else {
            showToast('Failed to save changes.', true);
        }
    } catch (err) {
        showToast('Error communicating with server.', true);
    }
}

function escapeHtml(str) {
    if (!str) return "";
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

function escapeJs(str) {
    if (!str) return "";
    return String(str).replace(/'/g, "\\'");
}

init();
