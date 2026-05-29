"""TNBC Atlas — Build self-contained read-only HTML browser.

Reads bibliography_records from Postgres, projects a slim record per row,
and emits a single HTML file with embedded JSON, Grid.js + Tailwind via CDN,
and filter chips for year / OA status / source presence / citation threshold.

Output: bibliography_browser.html (alongside this folder).
"""

from __future__ import annotations

import html
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import db, log


def slim(r: dict) -> dict:
    """Project a record down to what the browser needs (~200-350 bytes JSON each)."""
    authors = r.get("authors") or []
    sp = r.get("source_provenance") or {}
    first = authors[0]["name"] if authors and authors[0].get("name") else ""
    last_first = ""
    if first:
        parts = first.split()
        if len(parts) >= 2:
            last_first = f"{parts[-1]}, {' '.join(parts[:-1])}"
        else:
            last_first = first
    sources = []
    if "pubmed" in sp:    sources.append("PM")
    if "europepmc" in sp: sources.append("EP")
    if "openalex" in sp:  sources.append("OA")
    if "crossref" in sp:  sources.append("CR")
    if "unpaywall" in sp: sources.append("UP")
    pubdate = r.get("publication_date")
    return {
        "t":  r.get("title") or "",
        "fa": last_first,
        "na": len(authors),
        "j":  r.get("journal") or "",
        "y":  r.get("publication_year"),
        "d":  str(pubdate) if pubdate else None,
        "ct": r.get("crossref_type"),
        "c":  r.get("citation_count"),
        "rc": r.get("references_count"),
        "oa": r.get("oa_status") or "unknown",
        "lc": (r.get("license") or "").replace("http://", "").replace("https://", "") or None,
        "rt": r.get("retraction_status") or "active",
        "doi": r.get("canonical_doi"),
        "pmid": r.get("pmid"),
        "url": r.get("oa_url"),
        "src": sources,
    }


def fetch_all() -> list[dict]:
    with db() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT canonical_doi, pmid, openalex_id,
                   title, authors, journal, publication_year, publication_date,
                   oa_status, oa_url, citation_count, source_provenance,
                   crossref_type, license, references_count, retraction_status
            FROM bibliography_records
            ORDER BY (retraction_status='retracted')::int DESC,
                     citation_count DESC NULLS LAST,
                     publication_year DESC NULLS LAST
        """)
        return [slim(dict(r)) for r in cur.fetchall()]


HTML_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>TNBC Atlas — Bibliography Browser (Pilot)</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <link href="https://unpkg.com/gridjs/dist/theme/mermaid.min.css" rel="stylesheet">
  <style>
    body { font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
    .gridjs-table { font-size: 13px; }
    .gridjs-th, .gridjs-td { padding: 6px 10px !important; vertical-align: top; }
    .gridjs-search input { width: 100%; }
    .title-cell { max-width: 480px; }
    .pill { display:inline-block; font-size:10px; padding:1px 6px; border-radius:4px; margin-right:4px; }
    .pill-pm { background:#dbeafe; color:#1e3a8a; }
    .pill-ep { background:#dcfce7; color:#14532d; }
    .pill-oa { background:#fef3c7; color:#78350f; }
    .pill-cr { background:#e0e7ff; color:#312e81; }
    .pill-up { background:#fce7f3; color:#831843; }
    .pill-gold { background:#fde68a; color:#78350f; }
    .pill-green { background:#bbf7d0; color:#14532d; }
    .pill-hybrid { background:#e9d5ff; color:#581c87; }
    .pill-bronze { background:#fed7aa; color:#7c2d12; }
    .pill-diamond { background:#bae6fd; color:#075985; }
    .pill-closed { background:#e5e7eb; color:#374151; }
    .pill-unknown { background:#f3f4f6; color:#6b7280; }
    .pill-open { background:#bbf7d0; color:#14532d; }
    .pill-retracted { background:#fee2e2; color:#7f1d1d; font-weight:600; }
    .pill-concern { background:#fef3c7; color:#78350f; font-weight:600; }
    tr.row-retracted { background:#fef2f2; }
    tr.row-retracted .gridjs-td { border-left:3px solid #b91c1c; }
  </style>
</head>
<body class="bg-gray-50 text-gray-900">
  <header class="border-b bg-white">
    <div class="max-w-7xl mx-auto px-6 py-4">
      <h1 class="text-xl font-semibold">TNBC Atlas — Bibliography Browser</h1>
      <p class="text-sm text-gray-600 mt-1">
        Read-only pilot browser over the Phase 1 harvest, with Crossref + Unpaywall + Retraction Watch enrichment applied.
        <span class="text-gray-500">Window 2024-05-10 → 2026-05-10. Sources: PubMed (PM), Europe PMC (EP), OpenAlex (OA), Crossref (CR), Unpaywall (UP). All client-side; no network calls after page load.</span>
      </p>
    </div>
  </header>

  <main class="max-w-7xl mx-auto px-6 py-6">
    <section class="bg-white border rounded-lg p-4 mb-4 shadow-sm">
      <div class="grid grid-cols-1 md:grid-cols-3 lg:grid-cols-7 gap-3 items-end">
        <div class="lg:col-span-2">
          <label class="text-xs font-medium text-gray-700 block">Free text (title / journal / author)</label>
          <input id="q" type="text" placeholder="e.g. sacituzumab"
                 class="mt-1 w-full border rounded px-3 py-2 text-sm focus:outline-none focus:ring focus:ring-blue-200">
        </div>
        <div>
          <label class="text-xs font-medium text-gray-700 block">Year</label>
          <select id="f-year" class="mt-1 w-full border rounded px-3 py-2 text-sm">
            <option value="">All</option>
            <option value="2026">2026</option>
            <option value="2025">2025</option>
            <option value="2024">2024</option>
            <option value="pre-2024">pre-2024 (Crossref re-dated)</option>
          </select>
        </div>
        <div>
          <label class="text-xs font-medium text-gray-700 block">OA status</label>
          <select id="f-oa" class="mt-1 w-full border rounded px-3 py-2 text-sm">
            <option value="">All</option>
            <option value="gold">Gold</option>
            <option value="green">Green</option>
            <option value="hybrid">Hybrid</option>
            <option value="bronze">Bronze</option>
            <option value="open">Open (other)</option>
            <option value="closed">Closed</option>
            <option value="unknown">Unknown</option>
          </select>
        </div>
        <div>
          <label class="text-xs font-medium text-gray-700 block">Type</label>
          <select id="f-type" class="mt-1 w-full border rounded px-3 py-2 text-sm">
            <option value="">All</option>
            <option value="journal-article">Journal article</option>
            <option value="posted-content">Preprint</option>
            <option value="proceedings-article">Proceedings</option>
            <option value="book-chapter">Book chapter</option>
            <option value="dissertation">Dissertation</option>
            <option value="dataset">Dataset</option>
            <option value="peer-review">Peer review</option>
            <option value="component">Component</option>
          </select>
        </div>
        <div>
          <label class="text-xs font-medium text-gray-700 block">In source</label>
          <select id="f-src" class="mt-1 w-full border rounded px-3 py-2 text-sm">
            <option value="">Any</option>
            <option value="PM">PubMed</option>
            <option value="EP">Europe PMC</option>
            <option value="OA">OpenAlex</option>
            <option value="ALL3">All three primary</option>
            <option value="OA-only">OpenAlex-only</option>
          </select>
        </div>
        <div>
          <label class="text-xs font-medium text-gray-700 block">Min cites</label>
          <input id="f-min-cites" type="number" min="0" step="1" placeholder="0"
                 class="mt-1 w-full border rounded px-3 py-2 text-sm">
        </div>
        <div class="md:col-span-3 lg:col-span-7 flex flex-wrap gap-4 items-center pt-1">
          <label class="text-xs font-medium text-gray-700 inline-flex items-center gap-2">
            <input id="f-retracted" type="checkbox" class="rounded">
            Retracted only
          </label>
          <label class="text-xs font-medium text-gray-700 inline-flex items-center gap-2">
            <input id="f-hide-pre" type="checkbox" class="rounded">
            Hide pre-window (Crossref-corrected dates &lt; 2024-05-10)
          </label>
          <label class="text-xs font-medium text-gray-700 inline-flex items-center gap-2">
            <input id="f-with-license" type="checkbox" class="rounded">
            Has license URL
          </label>
        </div>
      </div>
      <div class="mt-3 flex items-center justify-between text-xs text-gray-600">
        <div>
          Showing <span id="visible-count" class="font-semibold">…</span> of
          <span id="total-count" class="font-semibold">…</span> records.
        </div>
        <button id="reset" class="px-3 py-1 border rounded text-gray-700 hover:bg-gray-100">Reset filters</button>
      </div>
    </section>

    <section class="bg-white border rounded-lg shadow-sm">
      <div id="grid"></div>
    </section>

    <p class="text-xs text-gray-500 mt-4">
      Tip: clicking the <strong>DOI</strong> link opens
      <code>doi.org/&lt;doi&gt;</code>; <strong>PMID</strong> opens PubMed;
      <strong>OA</strong> opens the freely available full text where one was found.
      All in a new tab.
    </p>
  </main>

  <script src="https://unpkg.com/gridjs/dist/gridjs.umd.js"></script>
  <script id="data" type="application/json">__DATA_PLACEHOLDER__</script>
  <script>
  (function () {
    const data = JSON.parse(document.getElementById('data').textContent);
    const total = data.length;
    document.getElementById('total-count').textContent = total.toLocaleString();

    const oaPill = (s) => `<span class="pill pill-${s}">${s}</span>`;
    const srcPills = (arr) =>
      (arr || []).map(s => `<span class="pill pill-${s.toLowerCase()}">${s}</span>`).join('');

    const cellLinks = (r) => {
      const links = [];
      if (r.doi)  links.push(`<a class="text-blue-600 hover:underline" target="_blank" href="https://doi.org/${r.doi}">DOI</a>`);
      if (r.pmid) links.push(`<a class="text-blue-600 hover:underline" target="_blank" href="https://pubmed.ncbi.nlm.nih.gov/${r.pmid}/">PMID</a>`);
      if (r.url)  links.push(`<a class="text-emerald-700 hover:underline" target="_blank" href="${r.url}">OA</a>`);
      return links.join(' · ');
    };

    const fmtAuthor = (r) => {
      const extra = r.na > 1 ? ` <span class="text-gray-500">+${r.na - 1}</span>` : '';
      return (r.fa || '<span class="text-gray-400">—</span>') + extra;
    };

    let grid = null;

    function buildRows(filtered) {
      return filtered.map(r => {
        const titleExtra = r.rt === 'retracted' ? ' <span class="pill pill-retracted">RETRACTED</span>' : '';
        return [
          gridjs.html(`<div class="title-cell" title="${(r.t||'').replace(/"/g,'&quot;')}">${escapeHtml(r.t)}${titleExtra}</div>`),
          gridjs.html(fmtAuthor(r)),
          r.y || '',
          r.ct || '',
          r.j || '',
          r.c == null ? '' : r.c,
          gridjs.html(oaPill(r.oa)),
          gridjs.html(srcPills(r.src)),
          gridjs.html(cellLinks(r)),
        ];
      });
    }

    function escapeHtml(s) {
      return (s||'').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    }

    function applyFilters() {
      const q = document.getElementById('q').value.trim().toLowerCase();
      const fy = document.getElementById('f-year').value;
      const foa = document.getElementById('f-oa').value;
      const ftype = document.getElementById('f-type').value;
      const fsrc = document.getElementById('f-src').value;
      const fmin = parseInt(document.getElementById('f-min-cites').value || '0', 10);
      const fretracted = document.getElementById('f-retracted').checked;
      const fhidePre = document.getElementById('f-hide-pre').checked;
      const fwithLicense = document.getElementById('f-with-license').checked;

      const filtered = data.filter(r => {
        if (fy === 'pre-2024') {
          if (!r.d || r.d >= '2024-05-10') return false;
        } else if (fy && String(r.y) !== fy) return false;
        if (foa && r.oa !== foa) return false;
        if (ftype && r.ct !== ftype) return false;
        if (fmin && (r.c || 0) < fmin) return false;
        if (fretracted && r.rt !== 'retracted') return false;
        if (fhidePre && r.d && r.d < '2024-05-10') return false;
        if (fwithLicense && !r.lc) return false;
        if (fsrc) {
          const s = r.src || [];
          if (fsrc === 'ALL3') {
            if (!(s.includes('PM') && s.includes('EP') && s.includes('OA'))) return false;
          } else if (fsrc === 'OA-only') {
            const primary = s.filter(x => x === 'PM' || x === 'EP' || x === 'OA');
            if (!(primary.length === 1 && primary[0] === 'OA')) return false;
          } else {
            if (!s.includes(fsrc)) return false;
          }
        }
        if (q) {
          const hay = ((r.t||'') + ' ' + (r.j||'') + ' ' + (r.fa||'')).toLowerCase();
          if (!hay.includes(q)) return false;
        }
        return true;
      });

      document.getElementById('visible-count').textContent = filtered.length.toLocaleString();
      const rows = buildRows(filtered);
      grid.updateConfig({ data: rows }).forceRender();
    }

    grid = new gridjs.Grid({
      columns: [
        { name: 'Title',     sort: true,  width: '34%' },
        { name: 'Author',    sort: true,  width: '12%' },
        { name: 'Year',      sort: true,  width: '5%' },
        { name: 'Type',      sort: true,  width: '10%' },
        { name: 'Journal',   sort: true,  width: '15%' },
        { name: 'Cites',     sort: true,  width: '5%' },
        { name: 'OA',        sort: true,  width: '6%' },
        { name: 'Sources',   sort: false, width: '7%' },
        { name: 'Links',     sort: false, width: '6%' },
      ],
      data: buildRows(data),
      pagination: { limit: 50, summary: true },
      sort: true,
      resizable: true,
      style: { table: { 'white-space': 'normal' } },
    }).render(document.getElementById('grid'));

    const inputs = ['q','f-year','f-oa','f-type','f-src','f-min-cites','f-retracted','f-hide-pre','f-with-license'];
    inputs.forEach(id => {
      const el = document.getElementById(id);
      el.addEventListener('input', applyFilters);
      el.addEventListener('change', applyFilters);
    });
    document.getElementById('reset').addEventListener('click', () => {
      inputs.forEach(id => {
        const el = document.getElementById(id);
        if (el.type === 'checkbox') el.checked = false;
        else el.value = '';
      });
      applyFilters();
    });

    document.getElementById('visible-count').textContent = total.toLocaleString();
  })();
  </script>
</body>
</html>
"""


def main():
    records = fetch_all()
    log(f"slimmed {len(records)} records", "browser")
    json_blob = json.dumps(records, separators=(",", ":"), ensure_ascii=False, default=str)
    log(f"JSON size: {len(json_blob)/1024/1024:.1f} MB", "browser")
    out = HTML_TEMPLATE.replace("__DATA_PLACEHOLDER__", json_blob)
    out_path = Path(__file__).resolve().parents[1] / "bibliography_browser.html"
    out_path.write_text(out, encoding="utf-8")
    log(f"wrote {out_path} ({out_path.stat().st_size/1024/1024:.1f} MB)", "browser")


if __name__ == "__main__":
    main()
