VERSION ?= $(shell python3 -c "import hwcompat; print(hwcompat.__version__)")
NAME    := hw-compat-check
STAGE   := dist/stage/$(NAME)-$(VERSION)
TARBALL := dist/$(NAME)-$(VERSION).tar.gz
TARBALL_LATEST := dist/$(NAME).tar.gz

.PHONY: release clean smoke

release: $(TARBALL) dist/install.sh
	@echo "Built $(TARBALL)"

$(TARBALL):
	rm -rf dist/stage
	mkdir -p $(STAGE)/bin $(STAGE)/hwcompat
	cp -R bin/hw-compat-check $(STAGE)/bin/
	cp -R hwcompat/* $(STAGE)/hwcompat/
	test -f README.md && cp README.md $(STAGE)/ || true
	test -f LICENSE   && cp LICENSE   $(STAGE)/ || true
	find $(STAGE) -name __pycache__ -type d -exec rm -rf {} +
	# Suppress macOS extended attributes (._/.DS_Store) and AppleDouble headers
	# so the tarball untars cleanly on Linux without xattr warnings.
	find $(STAGE) -name '.DS_Store' -delete
	COPYFILE_DISABLE=1 tar --no-xattrs -czf $(TARBALL) -C dist/stage $(NAME)-$(VERSION) 2>/dev/null \
		|| COPYFILE_DISABLE=1 tar -czf $(TARBALL) -C dist/stage $(NAME)-$(VERSION)
	cp $(TARBALL) $(TARBALL_LATEST)
	rm -rf dist/stage

dist/install.sh: install.sh
	mkdir -p dist
	cp install.sh dist/install.sh
	chmod +x dist/install.sh

smoke:
	python3 -m hwcompat --version

clean:
	rm -rf dist
