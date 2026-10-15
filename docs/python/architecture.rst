Architecture
============

Data flow
---------

.. code-block:: text

   ReDIF file / fixed RePEc FTP source
       -> parse and validate -> frozen workspace inputs
       -> train topics OR classify changes with the saved model
       -> collect Crossref evidence and build citation graph (optional)
       -> validate snapshots and export a static dataset
       -> Node website build -> browser verification
       -> release manifest -> current.json

A local release becoming current and a website being published are separate
operations. Python orchestration calls the Node builder and browser verifier,
but does not push to GitHub Pages. Documentation generation performs none of
these pipeline stages.

Module responsibilities
-----------------------

**Catalogue and provenance.** :mod:`app.redif` parses bibliographic records;
:mod:`app.repec` validates and fetches the fixed FTP source. :mod:`app.store`
provides catalogue grouping and search-text composition, not database storage.

**Topic analysis.** :mod:`app.topics` owns embedding, parameter search, clustering,
quality metrics and initial snapshot construction. :mod:`app.update_topics`
implements saved-model training and incremental classification. New or corrected
abstracts are compared with frozen centroids; existing assignments and coordinates
are preserved for unaffected records. :mod:`app.topic_labels` binds display names
to reviewed model evidence. :mod:`app.topic_timeline` derives time bins and keyword
representations without fitting new clusters.

**Citation analysis.** :mod:`app.citation_data` collects Crossref records and
retains matching/eligibility evidence. :mod:`app.citations` builds the directed
graph, its undirected clustering projection, diagnostics and downloadable exports.
Citation communities describe citation-linked groups and are distinct from
BERTopic themes. Names are keyed by membership hashes, not numeric group IDs.

**Validation and export.** :mod:`app.topic_store` and :mod:`app.citation_store`
validate snapshots. Despite their historical names, neither is a running storage
service. :mod:`app.theme_filter` builds filter metadata, while
:mod:`app.export_static` writes the website's JSON and download contract.
:mod:`app.retrospectives` compares a specifically audited model with the three
editorial retrospectives; it does not silently relabel a different active model.

**Orchestration.** :mod:`app.update` freezes inputs, runs resumable stages, checks
artifact hashes and selects completed releases. :mod:`app.mcp_jobs` manages
persistent background workers and a single MCP update slot. :mod:`app.mcp_server`
exposes four asynchronous tools over stdio, delegating blocking work to threads
and persistent workers.

Side effects and boundaries
---------------------------

Pure parsing, catalogue grouping, fingerprints and validation can be used without
model packages. Calling analysis functions can import large dependencies, allocate
accelerator memory and write caches. Fetching functions use the network. Calling
``prepare`` creates and freezes a workspace; it is not a filesystem dry run, even
though it does not execute expensive model stages. ``run`` writes releases and can
update the current pointer after successful completion.

The topic encoder and browser-search encoder are separate. Python uses Qwen for
complete-abstract topic analysis. The Node build and browser use the pinned BGE
search encoder with matching document/query encoding conventions. The Sphinx
API describes the Python side; JavaScript implementation remains in ``site/``
and ``scripts/``.
