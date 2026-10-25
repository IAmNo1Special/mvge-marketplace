"""Interactive HTML DAG visualization generator using Cytoscape.js."""

from __future__ import annotations

import json

from mvge_runes_okf_bridge.graph import KnowledgeGraph

_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>{title}</title>
  <style>
    body {{ margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; display: flex; height: 100vh; overflow: hidden; background: #1a1a1a; color: #e0e0e0; }}
    #cy {{ flex: 1; height: 100%; }}
    #sidebar {{ width: 360px; height: 100%; border-left: 1px solid #333; background: #222; padding: 20px; overflow-y: auto; box-sizing: border-box; }}
    .badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 12px; font-weight: bold; margin-right: 6px; }}
    .badge-human {{ background: #2e7d32; color: white; }}
    .badge-machine {{ background: #1565c0; color: white; }}
    .badge-unverified {{ background: #e65100; color: white; }}
    .badge-stale {{ background: #c62828; color: white; }}
    h2 {{ margin-top: 0; font-size: 20px; color: #fff; }}
    h3 {{ font-size: 14px; text-transform: uppercase; color: #888; margin-top: 20px; }}
    ul {{ padding-left: 20px; }}
    a {{ color: #64b5f6; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
  </style>
  <script src="https://cdn.jsdelivr.net/npm/cytoscape@3.26.0/dist/cytoscape.min.js"></script>
</head>
<body>
  <div id="cy"></div>
  <div id="sidebar">
    <h2>Knowledge Graph</h2>
    <p>Select any node to view concept details, trust tiers, and relations.</p>
    <div id="details"></div>
  </div>
  <script>
    const elements = {elements_json};
    const cy = cytoscape({{
      container: document.getElementById('cy'),
      elements: elements,
      style: [
        {{
          selector: 'node',
          style: {{
            'background-color': '#4fc3f7',
            'label': 'data(label)',
            'color': '#fff',
            'font-size': '11px',
            'text-valign': 'center',
            'text-halign': 'center',
            'width': 'data(size)',
            'height': 'data(size)'
          }}
        }},
        {{
          selector: 'node[type = "Attested Computation"]',
          style: {{ 'background-color': '#ab47bc' }}
        }},
        {{
          selector: 'node[type = "Service"]',
          style: {{ 'background-color': '#66bb6a' }}
        }},
        {{
          selector: 'edge',
          style: {{
            'width': 2,
            'line-color': '#555',
            'target-arrow-color': '#555',
            'target-arrow-shape': 'triangle',
            'curve-style': 'bezier'
          }}
        }}
      ],
      layout: {{ name: 'cose', animate: false }}
    }});

    cy.on('tap', 'node', function(evt) {{
      const data = evt.target.data();
      let badges = '';
      if (data.trust === 'human-reviewed') badges += '<span class="badge badge-human">Human-Reviewed</span>';
      else if (data.trust === 'machine-confirmed') badges += '<span class="badge badge-machine">Machine-Confirmed</span>';
      else badges += '<span class="badge badge-unverified">Unverified</span>';

      if (data.is_stale) badges += '<span class="badge badge-stale">Stale</span>';

      let html = '<h2>' + (data.label || data.id) + '</h2>' +
                 '<p><strong>Type:</strong> ' + data.type + '</p>' +
                 '<div>' + badges + '</div>' +
                 '<p>' + (data.description || '') + '</p>' +
                 '<h3>Links To</h3><ul>' +
                 (data.links.map(l => '<li>' + l + '</li>').join('') || '<li>None</li>') +
                 '</ul>' +
                 '<h3>Cited By</h3><ul>' +
                 (data.cited_by.map(c => '<li>' + c + '</li>').join('') || '<li>None</li>') +
                 '</ul>';
      document.getElementById('details').innerHTML = html;
    }});
  </script>
</body>
</html>
"""


def generate_html_graph(graph: KnowledgeGraph, title: str = "Knowledge Graph") -> str:
    """Generate standalone Cytoscape.js HTML for a KnowledgeGraph."""
    nodes = []
    edges = []

    for cid, c in graph.concepts.items():
        size = max(30, min(80, 25 + len(c.body) // 80))
        nodes.append(
            {
                "data": {
                    "id": cid,
                    "label": c.title or cid,
                    "type": c.type,
                    "description": c.description,
                    "trust": c.trust_tier.value,
                    "is_stale": c.is_stale,
                    "size": size,
                    "links": list(c.links),
                    "cited_by": graph.cited_by(cid),
                }
            }
        )
        for target in c.links:
            if target in graph.concepts:
                edges.append(
                    {
                        "data": {
                            "id": f"{cid}->{target}",
                            "source": cid,
                            "target": target,
                        }
                    }
                )

    elements = {"nodes": nodes, "edges": edges}
    return _HTML_TEMPLATE.format(
        title=title,
        elements_json=json.dumps(elements, indent=2),
    )
