Getting started
===============

Build these docs
----------------

From the repository root, create a separate environment and install only the
Sphinx dependency:

.. code-block:: sh

   python3 -m venv .venv-docs
   .venv-docs/bin/python -m pip install -r requirements-docs.txt
   .venv-docs/bin/python -m sphinx -b html -W --keep-going docs/python docs/python/_build/html

Open ``docs/python/_build/html/index.html``. The strict build fails on warnings,
including unresolved internal references and failed API imports. It does not
fetch RePEc, read a trained release, download encoders or launch the MCP server.
The optional MCP transport and platform locking dependency are mocked only for
API inspection. Model dependencies are imported inside analysis functions and
are not needed to build the documentation.

With the environment activated, ``make -C docs/python html`` provides the same
strict build. ``make -C docs/python doctest`` executes the small read-only examples.

Python and optional stacks
--------------------------

Use Python 3.12 for the pinned analysis environment described in the repository
README. Orchestration uses POSIX file locks and detached worker processes, so the
operational workflows target macOS or Linux. Documentation can be built without
those services or analysis packages.

.. list-table:: Dependency files
   :header-rows: 1
   :widths: 30 70

   * - File
     - Purpose
   * - ``requirements.txt``
     - Standard-library parsing, snapshots and orchestration; no third-party pins.
   * - ``requirements-topics.txt``
     - BERTopic, Qwen embedding inference, dimensionality reduction and clustering.
   * - ``requirements-citations.txt``
     - NetworkX analysis, NumPy and Matplotlib figure exports.
   * - ``requirements-mcp.txt``
     - Optional local MCP transport; updating still needs the analysis stacks.
   * - ``requirements-docs.txt``
     - Sphinx only; independent of the analysis environment.

Website assembly and browser verification additionally require Node 24, the
repository's npm dependencies, and Playwright browser installations. The first
analysis/build can download model weights and other assets. Subsequent builds
reuse verified local artifacts and text-keyed caches where applicable.

Use the pure parser
-------------------

The parser returns ordinary dictionaries. It accepts multiline fields, repeated
authors and HTML entities. Publication fields remain strings, including compound
issue numbers such as ``5-6``.

.. doctest::

   >>> from app.redif import parse_redif
   >>> source = "\n".join([
   ...     "Template-Type: ReDIF-Article 1.0",
   ...     "Author-Name: A. Researcher",
   ...     "Title: Peace &amp; security",
   ...     "Abstract: A short example.",
   ...     "Volume: 21", "Issue: 5-6", "Year: 2010",
   ...     "Handle: RePEc:taf:defpea:v:21:y:2010:i:5-6:p:1-2",
   ... ])
   >>> article = parse_redif(source)[0]
   >>> article['title'], article['authors'], article['issue']
   ('Peace & security', ['A. Researcher'], '5-6')
   >>> from app.store import catalog_for
   >>> catalog_for([article])['count']
   1
   >>> from app.repec import validate_redif
   >>> len(validate_redif(source.encode('utf-8')))
   1

:func:`app.redif.parse_redif` is deliberately permissive and drops records without
a title or handle. Use :func:`app.repec.validate_redif` for complete downloaded
catalogues: it rejects invalid templates, incomplete publication metadata,
duplicate handles and records from another series. It also enforces the input
size limit. Neither of the calls above accesses the network.
