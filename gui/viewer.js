/* 本地 3Dmol 视图；固定状态由 Python 维护，这里只负责显示和把点选/框选回传。
 * 原子实例 kind: atom 胞内原子 / image 晶胞边界上的等价原子 / ghost 跨边界成键的胞外原子。
 * 所有实例的 src 指向胞内原子编号，固定操作一律作用到 src。 */
'use strict';
let viewer, bridge, model, structure = null, generation = 0, mode = 'rotate', masks = [];
const layer = document.getElementById('layer');
const rect = document.getElementById('rect');
const hint = document.getElementById('hint');
const HINTS = {
    rotate: '拖动旋转 · 滚轮缩放 · 右键平移',
    point: '点击原子：固定 ⇄ 弛豫（周期像同步）· 拖动仍可旋转',
    box: '拖动框选固定 · Shift + 拖动解除 · 穿透所有深度',
};
const STICK = 0.12;
let drag = null;

function report(message) {
    hint.textContent = '渲染错误：' + message;
    if (bridge) bridge.reportError(String(message));
}
window.addEventListener('error', e => report(e.message));

function radius(a) { return (structure.radii[a.elem] || 0.5) * (a.kind === 'ghost' ? 0.8 : 1); }

function fade(hex) {  // 胞外原子：与背景混合 60%
    const c = parseInt(hex.slice(1), 16), bg = [0xF7, 0xF7, 0xFA];
    const mix = (v, b) => Math.round(v * 0.4 + b * 0.6);
    return '#' + [mix(c >> 16 & 255, bg[0]), mix(c >> 8 & 255, bg[1]), mix(c & 255, bg[2])]
        .map(v => v.toString(16).padStart(2, '0')).join('');
}

function send(indices, action) {
    if (bridge) bridge.selectAtoms(JSON.stringify({generation, indices: [...new Set(indices)], action}));
}

function drawCell() {
    const c = structure.cell;
    const corner = n => ({
        x: (n & 1 ? c[0][0] : 0) + (n & 2 ? c[1][0] : 0) + (n & 4 ? c[2][0] : 0),
        y: (n & 1 ? c[0][1] : 0) + (n & 2 ? c[1][1] : 0) + (n & 4 ? c[2][1] : 0),
        z: (n & 1 ? c[0][2] : 0) + (n & 2 ? c[1][2] : 0) + (n & 4 ? c[2][2] : 0),
    });
    for (let i = 0; i < 8; i++) for (const bit of [1, 2, 4])
        if (!(i & bit)) viewer.addLine({start: corner(i), end: corner(i | bit), color: '#A7B5C8', linewidth: 1});
    [['a', 1, '#FF3B30'], ['b', 2, '#34C759'], ['c', 4, '#007AFF']].forEach(([name, n, col]) =>
        viewer.addLabel(name, {position: corner(n), fontColor: col, fontSize: 13, backgroundOpacity: 0,
            inFront: true, alignment: 'center'}));
}

/* 静态部分（原子、键、晶胞）每个结构只建一次；固定标记单独增删，避免每次点选都重建整个场景造成闪烁。 */
let markers = [], hoverLabel = null, maskKey = '', pending = false;

function build() {
    viewer.clear();
    markers = []; hoverLabel = null; maskKey = '';
    model = viewer.addModel();
    model.addAtoms(structure.atoms);
    const groups = new Map();
    for (const a of structure.atoms) {
        const key = a.elem + '|' + (a.kind === 'ghost');
        if (!groups.has(key)) groups.set(key, {a, index: []});
        groups.get(key).index.push(a.index);
    }
    for (const {a, index} of groups.values()) {
        const base = structure.colors[a.elem], color = a.kind === 'ghost' ? fade(base) : base;
        model.setStyle({index}, {sphere: {radius: radius(a), color}, stick: {radius: STICK, color}});
    }
    for (const [x1, y1, z1, x2, y2, z2, elem] of structure.half)
        viewer.addCylinder({start: {x: x1, y: y1, z: z1}, end: {x: x2, y: y2, z: z2}, radius: STICK,
            color: structure.colors[elem], fromCap: 0, toCap: 1});
    model.setClickable({}, true, clicked);
    model.setHoverable({}, true, hoverOn, hoverOff);
    drawCell();
    paintMasks(true);
}

function hoverOn(a) {
    hoverOff();
    hoverLabel = viewer.addLabel(
        `${a.elem} ${a.src + 1}` + (a.kind === 'image' ? ' · 边界像' : a.kind === 'ghost' ? ' · 胞外像' : ''),
        {position: a, fontSize: 12, fontColor: '#FFFFFF', backgroundColor: '#1C1C1E', backgroundOpacity: 0.85,
         borderRadius: 6, inFront: true});
}

function hoverOff() {
    if (hoverLabel) { viewer.removeLabel(hoverLabel); hoverLabel = null; }
}

/* 只替换固定标记（黑色 / 橙色网格）；固定状态没变时什么也不做。 */
function paintMasks(force) {
    const key = JSON.stringify(masks);
    if (!force && key === maskKey) return;
    maskKey = key;
    for (const s of markers) viewer.removeShape(s);
    markers = [];
    for (const a of structure.atoms) {
        const m = masks[a.src] || [false, false, false];
        const full = m.every(Boolean), part = !full && m.some(Boolean);
        if (full || part)  // 不设 clickable：点击穿过网格落到原子本身
            markers.push(viewer.addSphere({center: a, radius: radius(a) * 1.14, color: full ? '#1C1C1E' : '#FF9500',
                wireframe: true, linewidth: 1, quality: 1}));
    }
    viewer.render();
}

/* 连续的固定操作（撤销 / 重做连按等）合并到同一帧绘制。 */
function schedulePaint() {
    if (pending) return;
    pending = true;
    requestAnimationFrame(() => { pending = false; if (structure) paintMasks(false); });
}

function legend() {
    const box = document.getElementById('legend'), entries = document.getElementById('entries');
    entries.replaceChildren();
    if (!structure) { box.hidden = true; return; }
    const counts = new Map();
    for (const a of structure.atoms) if (a.kind === 'atom') counts.set(a.elem, (counts.get(a.elem) || 0) + 1);
    for (const [elem, n] of counts) {
        const row = document.createElement('div'); row.className = 'row';
        const ball = document.createElement('span'); ball.className = 'ball'; ball.style.backgroundColor = structure.colors[elem];
        const name = document.createElement('strong'); name.textContent = elem;
        const cnt = document.createElement('span'); cnt.className = 'n'; cnt.textContent = n;
        row.append(ball, name, cnt); entries.append(row);
    }
    const extra = [];
    const nImg = structure.atoms.filter(a => a.kind === 'image').length;
    const nGhost = structure.atoms.filter(a => a.kind === 'ghost').length;
    if (nImg) extra.push(`边界等价原子 ${nImg}`);
    if (nGhost) extra.push(`浅色 = 胞外成键原子 ${nGhost}`);
    if (structure.half.length) extra.push(`半键 = 跨边界成键 ${structure.half.length}`);
    const key = document.getElementById('pbckey');
    key.textContent = extra.join(' · ');
    key.hidden = !extra.length;
    box.hidden = false;
}

function clicked(a) { if (mode === 'point') send([a.src], 'toggle'); }

function setMode(value) {
    mode = value;
    drag = null; rect.style.display = 'none';
    layer.style.display = mode === 'box' ? 'block' : 'none';
    hint.textContent = HINTS[mode];
}

function applyState(state) {
    try {
        if (state.clear) {
            structure = null; model = null; markers = []; hoverLabel = null; maskKey = '';
            viewer.clear(); viewer.render();
            document.getElementById('empty').style.display = 'flex'; legend(); return;
        }
        if (state.masks) masks = state.masks;
        if (state.mode) setMode(state.mode);
        if (state.structure) {
            const view = state.keepView && model ? viewer.getView() : null;
            generation = state.generation;
            structure = state.structure;
            document.getElementById('empty').style.display = 'none';
            legend();
            build();
            if (view) viewer.setView(view);
            else if (state.view) orient(state.view);
            else { viewer.zoomTo(); viewer.rotate(-70, 'x'); viewer.rotate(20, 'y'); }
            viewer.render();
        } else if (structure && state.masks) {
            schedulePaint();
        }
    } catch (e) { report(e.stack || e.message); }
}

function orient(quaternion) {  // 先按晶胞居中缩放，再设朝向（与标准视图按钮一致）
    viewer.zoomTo();
    const view = viewer.getView();
    view.splice(4, 4, ...quaternion);
    viewer.setView(view);
}

function applyView(req) {
    if (!model || req.generation !== generation) return;
    orient(req.quaternion);
    viewer.render();
}

layer.addEventListener('pointerdown', e => {
    if (e.button !== 0 || !structure) return;
    e.preventDefault();
    layer.setPointerCapture(e.pointerId);
    drag = {x: e.pageX, y: e.pageY, release: e.shiftKey};
    Object.assign(rect.style, {display: 'block', left: drag.x + 'px', top: drag.y + 'px', width: '0px', height: '0px'});
});
layer.addEventListener('pointermove', e => {
    if (!drag) return;
    Object.assign(rect.style, {left: Math.min(drag.x, e.pageX) + 'px', top: Math.min(drag.y, e.pageY) + 'px',
        width: Math.abs(drag.x - e.pageX) + 'px', height: Math.abs(drag.y - e.pageY) + 'px'});
});
layer.addEventListener('pointerup', e => {
    if (!drag) return;
    const [l, r] = [Math.min(drag.x, e.pageX), Math.max(drag.x, e.pageX)];
    const [t, b] = [Math.min(drag.y, e.pageY), Math.max(drag.y, e.pageY)];
    if (Math.hypot(r - l, b - t) > 3) {
        const screen = viewer.modelToScreen(structure.atoms);
        const ids = structure.atoms.filter((a, i) => {
            const p = screen[i];
            return p.x >= l && p.x <= r && p.y >= t && p.y <= b;
        }).map(a => a.src);
        send(ids, drag.release ? 'release' : 'fix');
    }
    drag = null; rect.style.display = 'none';
});
['pointercancel', 'lostpointercapture'].forEach(ev => layer.addEventListener(ev, () => { drag = null; rect.style.display = 'none'; }));
document.addEventListener('keydown', e => {
    const keys = {'1': 'rotate', '2': 'point', '3': 'box', Escape: 'rotate'};
    if (keys[e.key] && bridge) bridge.requestMode(keys[e.key]);
});
window.addEventListener('resize', () => { if (viewer) { viewer.resize(); viewer.render(); } });

try {
    viewer = $3Dmol.createViewer(document.getElementById('viewer'), {backgroundColor: '#F7F7FA', antialias: true});
    viewer.setProjection('orthographic');
    new QWebChannel(qt.webChannelTransport, channel => {
        bridge = channel.objects.bridge;
        bridge.stateChanged.connect(json => applyState(JSON.parse(json)));
        bridge.viewRequested.connect(json => applyView(JSON.parse(json)));
        bridge.ready();
    });
} catch (e) { report(e.message); }
