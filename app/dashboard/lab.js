/* PaperLab sidebar icons, shared by the public and private dashboards.
   (The Programs view that lived here was removed on 2026-10-21.) */
'use strict';

const LAB_SVG = 'http://www.w3.org/2000/svg';
const LAB_ICONS = {      // lucide-style 24px strokes
  dashboard: 'M3 3h7v9H3z M14 3h7v5h-7z M14 12h7v9h-7z M3 16h7v5H3z',
  bots: 'M12 8V4H8 M4 8h16v12H4z M2 14h2 M20 14h2 M15 13v2 M9 13v2',
  divergence: 'M3 17l5-6 5 3 8-11 M3 21h18 M8 11v6 M13 14v4',
  desk: 'M3 17l5-6 5 3 8-11 M3 21h18 M8 11v6 M13 14v4',
  ytcheck: 'M9 11l3 3 8-8 M20 12v7a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h9',   // a checked box: claims checked
  system: 'M12 2l8 4v6c0 5-3.5 8.5-8 10c-4.5-1.5-8-5-8-10V6z M9 12l2 2 4-4',
};

/** A sidebar icon for a nav id (falls back to a dot). */
function navIcon(id) {
  const svg = document.createElementNS(LAB_SVG, 'svg');
  for (const [k, v] of Object.entries({ viewBox: '0 0 24 24', width: 16, height: 16, fill: 'none', stroke: 'currentColor',
    'stroke-width': 2, 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true', class: 'nav-ico' })) svg.setAttribute(k, v);
  const path = document.createElementNS(LAB_SVG, 'path');
  path.setAttribute('d', LAB_ICONS[id] || 'M12 11a1 1 0 1 0 0 2a1 1 0 1 0 0-2z');
  svg.append(path);
  return svg;
}

/** A nav button: icon + label (sentence case, as in the Lovable sidebar). */
function navButton(id, label, onclick) {
  return h('button', { role: 'tab', type: 'button', 'data-nav': id, onclick }, navIcon(id), h('span', { text: label }));
}
