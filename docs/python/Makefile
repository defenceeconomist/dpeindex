SPHINXBUILD ?= sphinx-build
SPHINXOPTS ?= -W --keep-going
SOURCEDIR = .
BUILDDIR = _build

.PHONY: html doctest clean
html:
	$(SPHINXBUILD) -b html $(SPHINXOPTS) $(SOURCEDIR) $(BUILDDIR)/html

doctest:
	$(SPHINXBUILD) -b doctest $(SPHINXOPTS) $(SOURCEDIR) $(BUILDDIR)/doctest

clean:
	rm -rf $(BUILDDIR)
