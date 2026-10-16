Operational workflows
=====================

Run commands below from the repository root. Each new operation needs a fresh
output directory. These examples describe actions; they are not executed by
Sphinx. Install the topic stack and, unless omitted explicitly, the citation
stack before running a model workflow.

Train a baseline
----------------

.. code-block:: sh

   python -m app.update train --source data/defpea.redif \
     --exclude 37:5 37:6 --labels data/topic-labels.json \
     --output data/local-artifacts/documented-baseline

The default training exclusion is ``37:5 37:6``. Passing ``--exclude`` with no
values requests no exclusions. Training discovers a new model, tunes the
clustering configuration and creates a new model identity. The supplied label
manifest is valid only for matching training evidence; omit ``--labels`` when
training a different corpus and review the resulting keywords before naming it.
``--labels`` is accepted only for training, not incremental refreshes.

The grid and scoring logic are implemented in :mod:`app.topics`. Calibration
measures agreement with discovered cluster labels, not independently measured
classification accuracy. Inspect the quality report and representative abstracts
before interpreting a newly trained structure.

Incremental refresh
-------------------

.. code-block:: sh

   python -m app.update refresh --source data/defpea.redif --through 37:5 \
     --previous data/local-artifacts/documented-baseline/release \
     --output data/local-artifacts/documented-issue-5

Supply a complete catalogue, not an issue-only file. ``--through`` caps inclusion
by volume and issue. Removals fail unless explicitly allowed with
``--allow-removals``. Corrected abstracts are reclassified; unchanged historical
assignments, model identity, reviewed labels and map coordinates are retained.
A frozen model can leave new abstracts unassigned; discovering new clusters
requires another training run.

To fetch the fixed complete RePEc catalogue instead of reading a local source:

.. code-block:: sh

   python -m app.update refresh --fetch \
     --previous data/local-artifacts/documented-issue-5/release \
     --output data/local-artifacts/documented-refresh

``--device`` accepts ``auto``, ``cpu``, ``mps`` and ``cuda``. Automatic selection
uses MPS when available, otherwise CPU; CUDA must be selected explicitly.
``--without-citations`` omits citation analysis and supplies an explanatory
``not_built`` snapshot. ``--skip-browser-tests`` is a development escape hatch;
normal runs retain the browser gate. Use ``--help`` for the authoritative argument
list and cache locations:

.. code-block:: sh

   python -m app.update --help
   python -m app.update train --help
   python -m app.update refresh --help

Review, status and resume
-------------------------

.. code-block:: sh

   python -m app.update status data/local-artifacts/documented-refresh
   python -m app.update resume data/local-artifacts/documented-refresh

Stages record checksums in ``state.json``. Resume verifies completed artifacts
before reusing them. Damaged completed artifacts require a fresh workspace,
rather than editing manifests to bypass integrity checks. A failed pipeline does
not select a new current release. Exact unchanged refreshes can reuse a verified
release without re-encoding documents.

For pending citation review, inspect the workspace's ``review.json`` and correct
``inputs/decisions.json`` before resuming. Reference records are frozen in
``inputs/records.json``. Decisions may be edited before the citation stage
completes; changing an already completed stage requires a fresh workspace.
Unresolved references are retained as evidence, not promoted automatically to
citation edges. Reviewed community labels are bound to exact membership hashes.
``CROSSREF_MAILTO`` optionally supplies contact information for collection.

Export an existing release
--------------------------

.. code-block:: sh

   python -m app.export_static \
     --release data/local-artifacts/documented-issue-5/release \
     --output data/local-artifacts/documented-export

The output must not already exist. Omitting ``--release`` uses the current local
release. Export validates corpus alignment, snapshots, timeline and citation
download integrity. It does not train models or produce the final JavaScript
bundle. The website build is a separate step:

.. code-block:: sh

   npm run static:build -- --dataset data/local-artifacts/documented-export
   npm run static:validate

The build also packages the existing rendered manuscript. After editing that
manuscript, render both Quarto formats before building the website. Publishing
remains the explicit ``npm run static:publish -- <site-directory>`` command.

MCP lifecycle
-------------

Launch ``python scripts/run-mcp.py`` with the Python environment containing
``requirements-mcp.txt``. The launcher can be invoked by absolute path from any
working directory. It starts stdio transport, not an HTTP server.

1. Call :func:`app.mcp_server.dpe_fetch_redif` to obtain a validated snapshot ID.
2. Pass that ID to :func:`app.mcp_server.dpe_update_model`. A trained current
   release must already exist. The call returns a workspace ID while a detached
   worker continues the update.
3. Call :func:`app.mcp_server.dpe_update_status` with that workspace ID to inspect
   stages, failures, logs and review paths. Omit the ID to inspect the current model.
4. Resolve errors or review decisions and call
   :func:`app.mcp_server.dpe_resume_update` with the same workspace ID.

Workers persist across client disconnects. One MCP update can hold the global
update slot at a time. If the current release changes before a failed update is
resumed, start a fresh update. Status can report ``not_trained``, ``queued``,
``running``, ``interrupted``, ``review_required``, ``failed`` or ``ready`` depending
on context. No MCP tool retrains the topic structure or publishes the website.
