# SPDX-License-Identifier: GPL-3.0-or-later
#
# Copyright (C) 2025 Mark Sholund
#
# This file is part of the FastAPI Nexus Proxy project.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.

from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from pathlib import Path

import app.config as config

router = APIRouter(prefix="/ui", tags=["UI"])


# ── Cache scanning helpers ──────────────────────────────────────────────────

def _file_info(path: Path) -> dict:
    st = path.stat()
    return {"name": path.name, "size": st.st_size, "mtime": st.st_mtime}


def _scan_pypi() -> list:
    """List packages cached in pypi/simple/."""
    simple_dir = config.CACHE_DIR / "pypi" / "simple"
    packages = []
    if not simple_dir.exists():
        return packages
    for pkg_dir in sorted(simple_dir.iterdir()):
        if pkg_dir.is_symlink() or not pkg_dir.is_dir():
            continue
        files, total_size, last_modified = [], 0, 0.0
        for f in sorted(pkg_dir.iterdir()):
            if f.is_file() and not f.is_symlink():
                info = _file_info(f)
                files.append(info)
                total_size += info["size"]
                last_modified = max(last_modified, info["mtime"])
        packages.append({
            "name": pkg_dir.name,
            "files": files,
            "file_count": len(files),
            "total_size": total_size,
            "last_modified": last_modified,
        })
    return packages


def _npm_pkg_info(name: str, pkg_dir: Path) -> dict | None:
    """Collect metadata + tarball info for one NPM package directory."""
    files, total_size, last_modified = [], 0, 0.0

    index_file = pkg_dir / "index.json"
    if index_file.exists() and not index_file.is_symlink():
        info = _file_info(index_file)
        files.append(info)
        total_size += info["size"]
        last_modified = max(last_modified, info["mtime"])

    dash_dir = pkg_dir / "-"
    if dash_dir.exists() and dash_dir.is_dir() and not dash_dir.is_symlink():
        for f in sorted(dash_dir.iterdir()):
            if f.is_file() and not f.is_symlink():
                info = _file_info(f)
                files.append(info)
                total_size += info["size"]
                last_modified = max(last_modified, info["mtime"])

    if not files:
        return None
    return {
        "name": name,
        "files": files,
        "file_count": len(files),
        "total_size": total_size,
        "last_modified": last_modified,
    }


def _scan_npm() -> list:
    """List packages cached in npm/, including scoped (@scope/name) packages."""
    npm_dir = config.CACHE_DIR / "npm"
    packages = []
    if not npm_dir.exists():
        return packages
    for entry in sorted(npm_dir.iterdir()):
        if entry.is_symlink() or entry.name == "security":
            continue
        if entry.name.startswith("@") and entry.is_dir():
            for scoped in sorted(entry.iterdir()):
                if scoped.is_dir() and not scoped.is_symlink():
                    info = _npm_pkg_info(f"{entry.name}/{scoped.name}", scoped)
                    if info:
                        packages.append(info)
        elif entry.is_dir():
            info = _npm_pkg_info(entry.name, entry)
            if info:
                packages.append(info)
    return packages


def _find_leaf_dirs(root: Path, depth: int = 0, max_depth: int = 15):
    """Yield directories that directly contain at least one regular file."""
    if depth > max_depth:
        return
    try:
        entries = list(root.iterdir())
    except PermissionError:
        return
    if any(e.is_file() and not e.is_symlink() for e in entries):
        yield root
    for e in entries:
        if e.is_dir() and not e.is_symlink():
            yield from _find_leaf_dirs(e, depth + 1, max_depth)


def _scan_maven() -> list:
    """List Maven version directories as artifacts: group:artifact:version."""
    maven_dir = config.CACHE_DIR / "maven"
    artifacts = []
    if not maven_dir.exists():
        return artifacts
    for leaf in _find_leaf_dirs(maven_dir):
        rel = leaf.relative_to(maven_dir)
        parts = rel.parts
        if len(parts) < 2:
            continue
        version = parts[-1]
        artifact_id = parts[-2]
        group_id = ".".join(parts[:-2])
        files, total_size, last_modified = [], 0, 0.0
        for f in sorted(leaf.iterdir()):
            if f.is_file() and not f.is_symlink():
                info = _file_info(f)
                files.append(info)
                total_size += info["size"]
                last_modified = max(last_modified, info["mtime"])
        if files:
            artifacts.append({
                "group": group_id,
                "artifact": artifact_id,
                "version": version,
                "path": "/".join(parts),
                "files": files,
                "file_count": len(files),
                "total_size": total_size,
                "last_modified": last_modified,
            })
    return sorted(artifacts, key=lambda x: (x["group"], x["artifact"], x["version"]))


# ── API routes ────────────────────────────────────────────────────────────────

@router.get("/api/pypi")
async def api_pypi():
    packages = _scan_pypi()
    return {"repo": "pypi", "count": len(packages), "packages": packages,
            "total_size": sum(p["total_size"] for p in packages)}


@router.get("/api/npm")
async def api_npm():
    packages = _scan_npm()
    return {"repo": "npm", "count": len(packages), "packages": packages,
            "total_size": sum(p["total_size"] for p in packages)}


@router.get("/api/maven")
async def api_maven():
    artifacts = _scan_maven()
    return {"repo": "maven", "count": len(artifacts), "artifacts": artifacts,
            "total_size": sum(a["total_size"] for a in artifacts)}


@router.get("/api/stats")
async def api_stats():
    pypi = _scan_pypi()
    npm = _scan_npm()
    maven = _scan_maven()
    return {
        "pypi":  {"count": len(pypi),  "total_size": sum(p["total_size"] for p in pypi)},
        "npm":   {"count": len(npm),   "total_size": sum(p["total_size"] for p in npm)},
        "maven": {"count": len(maven), "total_size": sum(a["total_size"] for a in maven)},
    }


# ── UI HTML ───────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse, include_in_schema=False)
@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def ui_index():
    return _UI_HTML


_UI_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>PyNexus</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    :root {
      --bg: #f1f5f9;
      --surface: #fff;
      --border: #e2e8f0;
      --text: #1e293b;
      --muted: #64748b;
      --accent: #2563eb;
      --row-hover: #f8fafc;
      --expand-bg: #f8fafc;
      --font: system-ui, -apple-system, sans-serif;
      --mono: 'SF Mono', ui-monospace, 'Cascadia Code', monospace;
    }
    body { font-family: var(--font); background: var(--bg); color: var(--text); min-height: 100vh; }

    /* ── Header ── */
    .hdr {
      background: #0f172a; color: #f8fafc;
      height: 54px; display: flex; align-items: center;
      padding: 0 1.5rem; gap: 2rem;
      position: sticky; top: 0; z-index: 20;
      box-shadow: 0 1px 4px rgba(0,0,0,.4);
    }
    .hdr-brand { font-size: 1.1rem; font-weight: 700; letter-spacing: -.02em; white-space: nowrap; }
    .hdr-brand span { color: #60a5fa; }
    .hdr-stats { display: flex; gap: 1.5rem; flex: 1; font-size: .8rem; color: #94a3b8; }
    .hdr-stat { display: flex; align-items: center; gap: .4rem; }
    .hdr-stat strong { color: #f8fafc; font-weight: 600; }
    .dot { width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; }
    .dot-pypi { background: #4ade80; }
    .dot-npm  { background: #f87171; }
    .dot-mvn  { background: #fb923c; }

    /* ── Layout ── */
    .wrap { max-width: 1200px; margin: 0 auto; padding: 1.25rem 1rem; }

    /* ── Controls ── */
    .ctrl {
      display: flex; align-items: stretch;
      background: var(--surface); border: 1px solid var(--border);
      border-radius: 8px; overflow: hidden;
      box-shadow: 0 1px 2px rgba(0,0,0,.05);
      margin-bottom: .875rem;
    }
    .tabs { display: flex; border-right: 1px solid var(--border); }
    .tab {
      padding: 0 1.1rem; height: 42px;
      font-size: .85rem; font-weight: 600;
      cursor: pointer; border: none; background: none;
      color: var(--muted); border-right: 1px solid var(--border);
      transition: background .12s, color .12s;
      display: flex; align-items: center;
    }
    .tab:last-child { border-right: none; }
    .tab:hover:not(.active) { background: var(--row-hover); color: var(--text); }
    .tab.active { background: var(--accent); color: #fff; }
    .srch {
      flex: 1; display: flex; align-items: center;
      padding: 0 .75rem; gap: .5rem; color: var(--muted);
    }
    .srch input {
      flex: 1; border: none; outline: none; background: none;
      font-size: .875rem; font-family: var(--font); color: var(--text);
      padding: .5rem 0;
    }
    .srch input::placeholder { color: var(--muted); }
    .cnt { font-size: .75rem; color: var(--muted); padding-right: .75rem; white-space: nowrap; }

    /* ── Table ── */
    .tbl-wrap {
      background: var(--surface); border: 1px solid var(--border);
      border-radius: 8px; overflow: hidden;
      box-shadow: 0 1px 2px rgba(0,0,0,.05);
    }
    table { width: 100%; border-collapse: collapse; font-size: .875rem; }
    thead th {
      padding: .55rem 1rem; text-align: left;
      font-size: .72rem; font-weight: 600;
      text-transform: uppercase; letter-spacing: .06em;
      color: var(--muted); background: #f8fafc;
      border-bottom: 1px solid var(--border);
    }
    thead th:not(:first-child) { text-align: right; }

    .pr { border-bottom: 1px solid var(--border); cursor: pointer; }
    .pr:hover { background: var(--row-hover); }
    .pr td { padding: .65rem 1rem; vertical-align: middle; }
    .pr td:not(:first-child) { text-align: right; color: var(--muted); font-size: .82rem; font-variant-numeric: tabular-nums; }

    .pname { display: flex; align-items: center; gap: .5rem; }
    .chev {
      width: 14px; height: 14px; flex-shrink: 0;
      color: #cbd5e1; transition: transform .15s;
    }
    .pr.open .chev { transform: rotate(90deg); color: var(--accent); }

    .pkg-a {
      color: var(--accent); text-decoration: none;
      font-family: var(--mono); font-size: .825rem; font-weight: 500;
    }
    .pkg-a:hover { text-decoration: underline; }
    .group-lbl {
      display: block; font-family: var(--mono);
      font-size: .68rem; color: var(--muted); margin-bottom: 1px;
    }

    /* ── Expanded files row ── */
    .fr { display: none; }
    .fr.open { display: table-row; }
    .fr td { background: var(--expand-bg); border-bottom: 1px solid var(--border); padding: 0; }
    .flist { padding: .35rem 1rem .5rem 2.5rem; }
    .fi {
      display: flex; align-items: center; justify-content: space-between;
      padding: .3rem 0; border-bottom: 1px solid #f1f5f9; gap: 1rem;
    }
    .fi:last-child { border-bottom: none; }
    .fa {
      color: var(--text); text-decoration: none;
      font-family: var(--mono); font-size: .79rem;
    }
    .fa:hover { color: var(--accent); text-decoration: underline; }
    .fsz { color: var(--muted); font-size: .79rem; white-space: nowrap; font-variant-numeric: tabular-nums; }

    /* ── State rows ── */
    .state td {
      padding: 3.5rem 1rem; text-align: center;
      color: var(--muted); font-size: .875rem;
    }
    .spin {
      width: 22px; height: 22px;
      border: 3px solid var(--border); border-top-color: var(--accent);
      border-radius: 50%; animation: sp .65s linear infinite;
      margin: 0 auto .75rem;
    }
    @keyframes sp { to { transform: rotate(360deg); } }

    @media (max-width: 640px) {
      .hdr-stats { display: none; }
      .hide-sm { display: none; }
    }
  </style>
</head>
<body>

<header class="hdr">
  <div class="hdr-brand">Py<span>Nexus</span></div>
  <div class="hdr-stats" id="hdr-stats">
    <span class="hdr-stat"><span class="dot dot-pypi"></span>Loading…</span>
  </div>
</header>

<div class="wrap">
  <div class="ctrl">
    <div class="tabs">
      <button class="tab active" data-repo="pypi">PyPI</button>
      <button class="tab" data-repo="npm">NPM</button>
      <button class="tab" data-repo="maven">Maven</button>
    </div>
    <div class="srch">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
        <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
      </svg>
      <input id="srch" type="search" placeholder="Filter packages\u2026" autocomplete="off" spellcheck="false">
    </div>
    <span class="cnt" id="cnt"></span>
  </div>

  <div class="tbl-wrap">
    <table>
      <thead id="thead"></thead>
      <tbody id="tbody"></tbody>
    </table>
  </div>
</div>

<script>
// ── Utilities ────────────────────────────────────────────────────────────────
const esc = s => String(s)
  .replace(/&/g, '&amp;').replace(/</g, '&lt;')
  .replace(/>/g, '&gt;').replace(/"/g, '&quot;');

function fmtSz(b) {
  if (!b) return '0\u00a0B';
  const u = ['B', 'KB', 'MB', 'GB'];
  const i = Math.min(Math.floor(Math.log2(b) / 10), 3);
  return (b / 1024 ** i).toFixed(i ? 1 : 0) + '\u00a0' + u[i];
}

function fmtDt(ts) {
  if (!ts) return '\u2014';
  return new Date(ts * 1000).toLocaleDateString(undefined,
    { year: 'numeric', month: 'short', day: 'numeric' });
}

// ── State ────────────────────────────────────────────────────────────────────
let repo = 'pypi', data = [], exp = new Set();

// ── Header stats ─────────────────────────────────────────────────────────────
async function loadStats() {
  try {
    const d = await fetch('/ui/api/stats').then(r => r.json());
    document.getElementById('hdr-stats').innerHTML =
      `<span class="hdr-stat"><span class="dot dot-pypi"></span><strong>${d.pypi.count}</strong>&nbsp;PyPI&nbsp;&middot;&nbsp;${fmtSz(d.pypi.total_size)}</span>` +
      `<span class="hdr-stat"><span class="dot dot-npm"></span><strong>${d.npm.count}</strong>&nbsp;NPM&nbsp;&middot;&nbsp;${fmtSz(d.npm.total_size)}</span>` +
      `<span class="hdr-stat"><span class="dot dot-mvn"></span><strong>${d.maven.count}</strong>&nbsp;Maven&nbsp;&middot;&nbsp;${fmtSz(d.maven.total_size)}</span>`;
  } catch (_) {
    document.getElementById('hdr-stats').textContent = '';
  }
}

// ── File URL construction ─────────────────────────────────────────────────────
function fileUrl(item, f) {
  if (repo === 'pypi') {
    return '/pypi/simple/' + encodeURIComponent(item.name) + '/';
  }
  if (repo === 'npm') {
    const base = '/npm/' + item.name.replace('@', '%40');
    return f.name === 'index.json' ? base : base + '/-/' + encodeURIComponent(f.name);
  }
  if (repo === 'maven') {
    return '/maven2/' + item.path + '/' + encodeURIComponent(f.name);
  }
  return '#';
}

// ── Stable row key ────────────────────────────────────────────────────────────
function rowKey(item) {
  return repo === 'maven'
    ? item.group + ':' + item.artifact + ':' + item.version
    : item.name;
}

// ── Table header ──────────────────────────────────────────────────────────────
function renderHead() {
  const isMaven = repo === 'maven';
  document.getElementById('thead').innerHTML =
    `<tr>
      <th style="width:${isMaven ? 46 : 58}%">Package</th>
      ${isMaven ? '<th class="hide-sm">Version</th>' : ''}
      <th class="hide-sm">Files</th>
      <th class="hide-sm">Size</th>
      <th>Updated</th>
    </tr>`;
}

// ── Table body ────────────────────────────────────────────────────────────────
function renderTable() {
  const q = document.getElementById('srch').value.trim().toLowerCase();
  const rows = data.filter(item => {
    const s = repo === 'maven'
      ? item.group + ':' + item.artifact + ':' + item.version
      : (item.name || '');
    return !q || s.toLowerCase().includes(q);
  });

  const total = data.length;
  document.getElementById('cnt').textContent = rows.length < total
    ? rows.length + ' of ' + total
    : total + (repo === 'maven' ? ' artifacts' : ' packages');

  if (!rows.length) {
    document.getElementById('tbody').innerHTML =
      `<tr class="state"><td colspan="5">${
        q ? 'No matches for <strong>' + esc(q) + '</strong>.'
          : 'No cached packages yet. Browse a repository to fill the cache.'
      }</td></tr>`;
    return;
  }

  const colSpan = repo === 'maven' ? 5 : 4;
  document.getElementById('tbody').innerHTML = rows.flatMap(item => {
    const key = rowKey(item);
    const isOpen = exp.has(key);

    // Name cell
    let nameTd;
    if (repo === 'maven') {
      nameTd =
        `<td>
          <div class="pname">
            <svg class="chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><polyline points="9 18 15 12 9 6"/></svg>
            <div>
              <span class="group-lbl">${esc(item.group)}</span>
              <span style="font-family:var(--mono);font-size:.825rem;font-weight:500">${esc(item.artifact)}</span>
            </div>
          </div>
        </td>
        <td class="hide-sm">${esc(item.version)}</td>`;
    } else {
      const href = repo === 'pypi'
        ? '/pypi/simple/' + encodeURIComponent(item.name) + '/'
        : '/npm/' + item.name.replace('@', '%40');
      nameTd =
        `<td>
          <div class="pname">
            <svg class="chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><polyline points="9 18 15 12 9 6"/></svg>
            <a class="pkg-a" href="${esc(href)}">${esc(item.name)}</a>
          </div>
        </td>`;
    }

    // Files in expanded row
    const filesHtml = (item.files || []).map(f =>
      `<div class="fi">
        <a class="fa" href="${esc(fileUrl(item, f))}" target="_blank">${esc(f.name)}</a>
        <span class="fsz">${fmtSz(f.size)}</span>
      </div>`
    ).join('') || `<div class="fi"><span style="color:var(--muted);font-size:.8rem">No cached files</span></div>`;

    return [
      `<tr class="pr${isOpen ? ' open' : ''}" data-key="${esc(key)}">
        ${nameTd}
        <td class="hide-sm">${item.file_count}</td>
        <td class="hide-sm">${fmtSz(item.total_size)}</td>
        <td>${fmtDt(item.last_modified)}</td>
      </tr>`,
      `<tr class="fr${isOpen ? ' open' : ''}">
        <td colspan="${colSpan}"><div class="flist">${filesHtml}</div></td>
      </tr>`
    ];
  }).join('');
}

// ── Data loading ──────────────────────────────────────────────────────────────
async function loadRepo(r) {
  document.getElementById('tbody').innerHTML =
    '<tr class="state"><td colspan="5"><div class="spin"></div>Loading\u2026</td></tr>';
  document.getElementById('cnt').textContent = '';
  try {
    const d = await fetch('/ui/api/' + r).then(x => x.json());
    data = r === 'maven' ? (d.artifacts || []) : (d.packages || []);
    exp.clear();
    renderTable();
  } catch (_) {
    document.getElementById('tbody').innerHTML =
      '<tr class="state"><td colspan="5">Failed to load. Check server logs.</td></tr>';
  }
}

// ── Event wiring ──────────────────────────────────────────────────────────────
document.querySelectorAll('.tab').forEach(t => t.addEventListener('click', () => {
  document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
  t.classList.add('active');
  repo = t.dataset.repo;
  document.getElementById('srch').value = '';
  renderHead();
  loadRepo(repo);
}));

document.getElementById('srch').addEventListener('input', renderTable);

document.getElementById('tbody').addEventListener('click', e => {
  if (e.target.closest('a')) return;   // let link clicks through
  const row = e.target.closest('.pr');
  if (!row) return;
  const key = row.dataset.key;
  exp.has(key) ? exp.delete(key) : exp.add(key);
  renderTable();
});

// ── Boot ──────────────────────────────────────────────────────────────────────
renderHead();
loadStats();
loadRepo('pypi');
</script>
</body>
</html>"""
