"""Sphinx configuration; importing the API must not run an analysis or an MCP server."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
project = 'DPE Python documentation'
author = 'DPE contributors'
extensions = ['sphinx.ext.autodoc', 'sphinx.ext.viewcode', 'sphinx.ext.doctest']
# The optional MCP transport is not needed to inspect the source. Sphinx preserves
# decorated functions when mocking its registration decorator. No ML module is
# imported at module scope, so the large analysis dependencies need no mocks.
autodoc_mock_imports = ['mcp', 'anyio', 'fcntl']
autodoc_default_options = {'members': True, 'undoc-members': True, 'member-order': 'bysource'}
autodoc_typehints = 'signature'
autodoc_preserve_defaults = True
# Keep source links in the documented app modules, rather than following mocked
# MCP registration objects into nonexistent third-party source pages.
viewcode_follow_imported_members = False
exclude_patterns = ['_build', 'Thumbs.db', '.DS_Store']
html_theme = 'alabaster'
html_title = project
html_theme_options = {'description': 'Offline catalogue, topic and citation pipelines', 'fixed_sidebar': True}
html_sidebars = {'**': ['about.html', 'navigation.html', 'searchfield.html']}
html_show_sourcelink = True
html_static_path = []
# Read-only examples are self-contained and do not depend on local release data.
doctest_global_setup = 'from pathlib import Path'
