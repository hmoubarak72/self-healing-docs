#!/bin/sh
set -e
# The workspace is owned by the runner user, not root: let git operate on it.
git config --global --add safe.directory '*'
exec docheal "$@"
