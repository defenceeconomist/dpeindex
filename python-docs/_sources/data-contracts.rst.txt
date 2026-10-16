Data contracts and artifacts
============================

Article dictionaries
--------------------

:func:`app.redif.parse_redif` returns a list of dictionaries. ``handle`` is the
stable identity, ``authors`` is a list, and the remaining parsed fields are
strings. Missing string values are empty strings, not ``None``.

.. list-table:: Principal fields
   :header-rows: 1
   :widths: 30 70

   * - Fields
     - Meaning
   * - ``handle``, ``title``, ``authors``
     - RePEc identity, decoded title and repeated author names.
   * - ``abstract``, ``keywords``
     - Normalised input text; missing abstracts remain browsable.
   * - ``volume``, ``issue``, ``year``, ``month``, ``pages``
     - Publication metadata retained as strings, including combined issues.
   * - ``doi``, ``doi_url``, ``repec_url``
     - Identifiers and links derived from the source record.
   * - ``journal``, ``file_url``, ``file_format``, ``file_restriction``
     - Source journal and publisher file metadata.

:func:`app.topic_store.corpus_fingerprint` hashes the sorted article summaries and
abstracts, binding related artifacts to the same analytical corpus. It is distinct
from the SHA-256 of the original ReDIF byte stream.

.. doctest::

   >>> from app.topic_store import corpus_fingerprint
   >>> a = {'handle': 'a', 'title': 'Peace', 'abstract': 'Conflict and growth.'}
   >>> b = {'handle': 'b', 'title': 'Defence', 'abstract': 'Public spending.'}
   >>> corpus_fingerprint([a, b]) == corpus_fingerprint([b, a])
   True
   >>> corpus_fingerprint([a]) == corpus_fingerprint([{**a, 'abstract': 'Corrected.'}])
   False

Topic snapshots
---------------

A ready snapshot includes ``metadata``, ``topics``, ``documents``, ``hierarchy``
and training ``report`` data; release snapshots also include a ``timeline``.
Topic entries contain IDs, labels, keywords, counts and representative handles.
Document entries include the article summary, topic assignment and map coordinates.
An assignment of ``-1`` means unassigned; a record without an abstract has no
model document entry. Topic shares should state whether their denominator
includes unassigned abstracts.

``metadata.run_id`` identifies a snapshot, whereas ``metadata.model_id`` tracks
the trained model across incremental refreshes. Readable labels do not change
assignments or scores. :func:`app.topic_store.validate_snapshot` rejects
inconsistent counts, invalid geometry and other contract violations.
:func:`app.topic_timeline.validate_timeline` checks time-bin consistency.

Citation snapshots
------------------

A ready citation snapshot includes ``nodes``, ``edges``, ``communities``,
``coverage``, ``network``, ``baseline``, ``sensitivity``, ``comparison``, review
status and download metadata. Edges point from citing source to cited target.
``baseline`` on each edge records whether it is also in the DOI-only graph.
Node degrees are observed internal degrees, not total citations from all journals.
Community ``-1`` denotes an isolate outside the connected-paper partition.

``comparison`` cross-tabulates current topic assignments against citation
communities, including missing and unassigned abstracts. It requires compatible
corpus fingerprints. A ``not_built`` snapshot is an explicit absence of analysis,
not an empty validated network. Validators for ready snapshots should not be
called on that status-only representation.

:func:`app.citation_data.build_edges` returns eligible records, edges, audit rows,
pending review and unmatched references. :func:`app.citations.build_snapshot`
returns the analytical snapshot, evidence and review structures.
:func:`app.citations.exports` produces the downloadable artifacts defined by
``app.citation_store.DOWNLOADS``. Exported bytes are checked against the snapshot's
recorded hashes and sizes before publication into the static dataset.

Workspace layout
----------------

.. code-block:: text

   <workspace>/
     inputs/                  frozen source, corpus, decisions and provenance
     state.json               completed stages and integrity hashes
     review.json              citation review, when needed
     website.log              Node build log
     browsers.log             browser verification log
     release/
       corpus.json, source.json
       topics.json, coverage.json
       model/                 centroids, saved map, definition and trials
       citations.json
       citations/             evidence, review, graphs and download bundle
       dataset/               validated JSON and immutable downloads
       site/                  complete static website
       update-report.json
       release-manifest.json

MCP workers add job/process metadata and ``worker.log``. The global
``data/local-artifacts/current.json`` stores the selected release path. Large
artifacts are local and ignored by Git; preserve or back them up separately.
Saved projection files are trusted local model artifacts and should not be
loaded from untrusted sources.

The exported dataset manifest maps issues to their hashed filenames and records
file hashes, corpus identity and model identities. Its ``api/citations/`` paths
are static files, despite the ``api`` directory name. The Node builder adds
browser-search vectors, model assets and the website-level integrity manifest.

Retrospective comparisons
-------------------------

The comparison in :mod:`app.retrospectives` is bound to a reviewed historical
snapshot in ``data/retrospectives.json``. A different current run can produce
``review_required`` with links disabled, or ``unavailable`` if the audited
snapshot cannot be retrieved. Periods use volumes and issue cutoffs. Editorial
counts and abstract-based topic counts describe different populations; overlapping
crosswalk rows must not be summed. The separate manuscript's reviewed crosswalk
does not overwrite this application audit.
