Supporting Python utilities
===========================

Static preview
--------------

``scripts/preview_static.py`` serves a directory at both ``/`` and ``/dpeindex/``.
The default directory is ``dist`` and the default port is 8790.

.. code-block:: sh

   python scripts/preview_static.py --directory dist --port 8790

It uses the standard-library HTTP server for local preview. It does not build
the website or start an application API.

MCP launcher
------------

``scripts/run-mcp.py`` inserts the repository root into the import path, imports
the server from ``app.mcp_server``, and starts its stdio transport. It does not
change the working directory. Use the interpreter from the environment that
contains the MCP and required analysis dependencies.

Manuscript preparation
----------------------

``manuscript/prepare.py`` regenerates tables, figures and the retrospective
crosswalk from the manuscript's frozen ``data/evidence.json``. NumPy and
Matplotlib are needed for figure creation. The default command performs
integrity and count checks but does not train models or fetch live data:

.. code-block:: sh

   python manuscript/prepare.py

Its optional ``--freeze <release-directory>`` extracts evidence from an explicit
release, and refuses to overwrite existing frozen evidence. Creating an updated
edition requires a separate manuscript directory and review of its narrative.
These scripts are documented as utilities rather than imported as members of
``app``.

``manuscript/validate.py`` checks the Quarto source, BibTeX keys, rendered HTML/PDF,
figure/table references and crosswalk. It uses Poppler's ``pdftotext`` and
``pdfinfo`` commands and writes ``generated/validation.json``. Run it after
rendering both formats:

.. code-block:: sh

   quarto render manuscript/manuscript.qmd --to all
   python manuscript/validate.py

See ``manuscript/README.md`` for the evidence provenance and full reproduction
instructions. The manuscript and developer API documentation have different
purposes and separate build processes.
