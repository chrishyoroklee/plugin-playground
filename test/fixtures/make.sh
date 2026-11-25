#!/bin/sh
# Small stand-ins for Plugin Playground's three installers, laid out as scripts/plugin.py
# lays out the real ones, for the tests of the page's Download (test/stamp.test.ts) and
# for sitecheck when the site has no real installers built. The .pkg is made by Apple's
# own pkgbuild and productbuild, so it needs a Mac; run this from the repository root:
#
#   sh test/fixtures/make.sh
#
# Then commit the three files. Nothing here is installed or run.
set -e
here=$(cd "$(dirname "$0")" && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

# macOS: a product archive holding one component package with a Payload, Scripts
# (postinstall) and a Distribution with a title and a welcome page.
mkdir -p "$work/root/Applications/Stand-in.app/Contents" "$work/scripts" "$work/resources" "$work/parts"
printf 'stand-in\n' > "$work/root/Applications/Stand-in.app/Contents/Info.txt"
printf '#!/bin/sh\necho "stand-in postinstall"\nexit 0\n' > "$work/scripts/postinstall"
chmod 755 "$work/scripts/postinstall"
printf '<html><body>Plugin Playground</body></html>' > "$work/resources/welcome.html"
pkgbuild --root "$work/root" --scripts "$work/scripts" --identifier com.plugin-playground.helper \
    --version 0.1.0 --install-location / "$work/parts/plugins.pkg" >/dev/null
cat > "$work/distribution.xml" <<XML
<?xml version="1.0" encoding="utf-8"?>
<installer-gui-script minSpecVersion="2">
  <title>Plugin Playground</title>
  <welcome file="welcome.html" mime-type="text/html"/>
  <domains enable_localSystem="true" enable_currentUserHome="true"/>
  <choices-outline><line choice="plugins"/></choices-outline>
  <choice id="plugins" visible="false"><pkg-ref id="com.plugin-playground.helper"/></choice>
  <pkg-ref id="com.plugin-playground.helper" version="0.1.0">plugins.pkg</pkg-ref>
</installer-gui-script>
XML
productbuild --distribution "$work/distribution.xml" --package-path "$work/parts" --resources "$work/resources" \
    "$here/runtime-macos.pkg" >/dev/null

# Windows: what a program starts with, and some bytes. The setup's own code is tested on
# Windows itself, in CI (scripts/plugin.py --check-stamped).
printf 'MZ stand-in for the setup program\n' > "$here/runtime-windows.exe"

# Linux: the folder the real archive holds, with an install.sh.
mkdir -p "$work/tar/plugin-playground"
printf '#!/bin/sh\necho "stand-in install.sh"\n' > "$work/tar/plugin-playground/install.sh"
printf 'stand-in\n' > "$work/tar/plugin-playground/README.txt"
(cd "$work/tar" && COPYFILE_DISABLE=1 tar -czf "$here/runtime-linux.tar.gz" --format ustar plugin-playground)
echo "wrote $here/runtime-macos.pkg, runtime-windows.exe and runtime-linux.tar.gz"
