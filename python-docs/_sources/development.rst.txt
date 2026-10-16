Development and documentation maintenance
=========================================

Documentation layout
--------------------

Sphinx sources live in ``docs/python/`` to keep developer documentation separate
from the research papers and evaluation reports already in ``docs/``. The
``_build/`` directory and the documentation environment are ignored by Git.
The built-in Alabaster theme provides search, module and general indices, and
source links without adding another theme dependency.

API pages use ``automodule`` with public members, including functions whose
source docstrings have not yet been expanded. Module introductions explain their
role and important usage constraints. Add parameter, return and exception notes
to source docstrings as interfaces evolve; Sphinx incorporates them on the next
build. Keep examples read-only unless clearly labelled as operational commands.

When adding a module, create its page under ``api/`` and add it to the API
contents list. Avoid network requests, model loads and worker launch at import
time: autodoc imports modules. The optional MCP imports are mocked for inspection,
so a successful documentation build does not prove that MCP transport is installed
or functional. Runtime protocol tests remain necessary for MCP changes.

Checks
------

.. code-block:: sh

   .venv-docs/bin/python -m sphinx -b html -W --keep-going docs/python docs/python/_build/html
   .venv-docs/bin/python -m sphinx -b doctest -W --keep-going docs/python docs/python/_build/doctest

Run relevant application checks in the analysis environment when changing code:

.. code-block:: sh

   python -m unittest discover -s tests -v
   npm test

The Python tests cover parsing, fetching, topic labels/timelines, citation
matching, staged updates, export and MCP jobs/protocols. Full-suite dependencies
include the relevant optional stacks; the lightweight docs environment is not
intended to execute model tests. Browser checks and static validation are
separate commands documented in the repository README.

Rendering the documentation does not publish it or modify the active release.
To serve a built copy locally:

.. code-block:: sh

   .venv-docs/bin/python -m http.server 8000 --directory docs/python/_build/html
